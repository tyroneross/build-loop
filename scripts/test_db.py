#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for db.py — the psycopg helper 18 modules import.

Graded against a FAKE connection, never a live database. These assert the
transaction contract, which is where a defect is both most damaging and least
visible: a write helper that fails to roll back leaves a poisoned transaction, and
the NEXT caller's commit silently persists whatever the failed statement left
behind. Every caller's own tests stay green while data is corrupted.

The properties under test:
  - execute / execute_many / execute_script COMMIT on success
  - each of them ROLLS BACK and re-raises on failure
  - query / query_one never commit (read-only must not end a caller's transaction)
  - get_connection caches, and reopens a closed connection
  - close_connection is idempotent and survives a close() that raises
  - vector_literal emits the exact pgvector text form, since it is interpolated
    into SQL with a ::vector cast rather than passed as a typed parameter

No live DB is used on purpose. A test that needed one would be non-hermetic and
would fail under concurrency rather than on defect.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db  # noqa: E402


class FakeCursor:
    def __init__(self, conn: "FakeConn", rows=None, raises: Exception | None = None):
        self.conn, self.rows, self.raises = conn, rows or [], raises
        self.rowcount = 7

    def __enter__(self): return self
    def __exit__(self, *exc): return False

    def execute(self, sql, params=None):
        self.conn.statements.append(sql)
        if self.raises:
            raise self.raises

    def executemany(self, sql, seq):
        self.conn.statements.append(sql)
        if self.raises:
            raise self.raises

    def fetchall(self): return list(self.rows)
    def fetchone(self): return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, rows=None, raises: Exception | None = None, close_raises=False):
        self.rows, self.raises, self.close_raises = rows, raises, close_raises
        self.commits = self.rollbacks = 0
        self.closed = False
        self.statements: list[str] = []

    def cursor(self, row_factory=None): return FakeCursor(self, self.rows, self.raises)
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1

    def close(self):
        if self.close_raises:
            raise RuntimeError("close failed")
        self.closed = True


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = db._CONN
        db._CONN = None
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        db._CONN = self._saved

    def install(self, conn: FakeConn) -> FakeConn:
        db._CONN = conn
        return conn


class TestWriteHelpersCommit(Base):
    def test_execute_commits_and_returns_rowcount(self):
        c = self.install(FakeConn())
        self.assertEqual(db.execute("UPDATE t SET a=1"), 7)
        self.assertEqual((c.commits, c.rollbacks), (1, 0))

    def test_execute_many_commits(self):
        c = self.install(FakeConn())
        db.execute_many("INSERT INTO t VALUES (%s)", [(1,), (2,)])
        self.assertEqual((c.commits, c.rollbacks), (1, 0))

    def test_execute_script_commits(self):
        c = self.install(FakeConn())
        db.execute_script("DELETE FROM t; INSERT INTO t VALUES (1);")
        self.assertEqual((c.commits, c.rollbacks), (1, 0))


class TestWriteHelpersRollBack(Base):
    """The contract that matters. Without rollback the transaction stays poisoned
    and a later commit by an unrelated caller persists the wreckage."""

    def _assert_rolls_back(self, fn):
        c = self.install(FakeConn(raises=RuntimeError("constraint violation")))
        with self.assertRaises(RuntimeError):
            fn()
        self.assertEqual(c.rollbacks, 1, "failure did not roll back")
        self.assertEqual(c.commits, 0, "failure committed anyway")

    def test_execute_rolls_back(self):
        self._assert_rolls_back(lambda: db.execute("UPDATE t SET a=1"))

    def test_execute_many_rolls_back(self):
        self._assert_rolls_back(lambda: db.execute_many("INSERT INTO t VALUES (%s)", [(1,)]))

    def test_execute_script_rolls_back(self):
        self._assert_rolls_back(lambda: db.execute_script("DELETE FROM t;"))

    def test_failure_is_re_raised_not_swallowed(self):
        """A swallowed error reports success for a write that never landed."""
        self.install(FakeConn(raises=ValueError("boom")))
        with self.assertRaises(ValueError):
            db.execute("UPDATE t SET a=1")


class TestReadsAreReadOnly(Base):
    def test_query_does_not_commit(self):
        c = self.install(FakeConn(rows=[{"id": 1}]))
        self.assertEqual(db.query("SELECT 1"), [{"id": 1}])
        self.assertEqual((c.commits, c.rollbacks), (0, 0),
                         "a SELECT ended the caller's transaction")

    def test_query_one_returns_first_row(self):
        self.install(FakeConn(rows=[{"id": 1}, {"id": 2}]))
        self.assertEqual(db.query_one("SELECT 1"), {"id": 1})

    def test_query_one_returns_none_when_empty(self):
        self.install(FakeConn(rows=[]))
        self.assertIsNone(db.query_one("SELECT 1"))


class TestConnectionLifecycle(Base):
    def test_cached_connection_is_reused(self):
        c = self.install(FakeConn())
        self.assertIs(db.get_connection(), c)
        self.assertIs(db.get_connection(), c)

    def test_closed_connection_is_reopened(self):
        stale = self.install(FakeConn())
        stale.closed = True
        fresh = FakeConn()
        with mock.patch.object(db, "_require_psycopg"), \
             mock.patch.object(db, "_read_db_url", return_value="postgres://x"), \
             mock.patch.object(db, "psycopg", mock.Mock(connect=mock.Mock(return_value=fresh))):
            self.assertIs(db.get_connection(), fresh, "kept using a closed connection")

    def test_close_connection_is_idempotent(self):
        self.install(FakeConn())
        db.close_connection()
        db.close_connection()  # must not raise
        self.assertIsNone(db._CONN)

    def test_close_survives_a_raising_close(self):
        """atexit runs this during interpreter teardown; an exception there is noise
        the user cannot act on."""
        self.install(FakeConn(close_raises=True))
        db.close_connection()
        self.assertIsNone(db._CONN)


class TestFailFastGuards(Base):
    def test_require_psycopg_raises_actionably_when_absent(self):
        with mock.patch.object(db, "_PSYCOPG_AVAILABLE", False):
            with self.assertRaises(ImportError) as ctx:
                db._require_psycopg()
        self.assertIn(".[db]", str(ctx.exception), "error does not name the fix")

    def test_missing_dsn_raises_runtime_error(self):
        with mock.patch.object(db, "resolve_db_url", return_value=""):
            with self.assertRaises(RuntimeError) as ctx:
                db._read_db_url()
        self.assertIn("DATABASE_URL", str(ctx.exception))


class TestVectorLiteral(unittest.TestCase):
    """Interpolated into SQL with a ::vector cast, not passed as a typed param —
    so the exact text form is the contract."""

    def test_exact_pgvector_text_form(self):
        self.assertEqual(db.vector_literal([0.1, 0.2]), "[0.100000,0.200000]")

    def test_no_spaces_after_commas(self):
        self.assertNotIn(" ", db.vector_literal([1.0, 2.0, 3.0]))

    def test_negatives_and_zero_render(self):
        self.assertEqual(db.vector_literal([-1.5, 0.0]), "[-1.500000,0.000000]")

    def test_empty_embedding_is_empty_brackets(self):
        self.assertEqual(db.vector_literal([]), "[]")


if __name__ == "__main__":
    unittest.main(verbosity=2)
