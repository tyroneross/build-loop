#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for atomic_io.py — the shared write/lock primitive.

15 modules import this, so a defect here corrupts state at every call site while
each caller's own tests stay green. The tests below grade the properties that make
the write ATOMIC and DURABLE, not merely "the bytes came back":

  - the temp file is created in the TARGET'S directory. os.replace is only atomic
    within one filesystem; a temp in /tmp silently degrades to a copy that can be
    observed half-written.
  - fsync happens BEFORE replace. Without that ordering the rename can land while
    the data is still in the page cache, and a crash leaves a present-but-empty
    file — the failure this module exists to prevent.
  - a failed write leaves the ORIGINAL intact and no .tmp litter behind.

flock semantics were measured on this platform rather than assumed: a second
exclusive lock raises BlockingIOError both in-process (separate fds) and
cross-process.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import atomic_io  # noqa: E402
import content_index  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.target = self.dir / "state.json"


class TestAtomicWrite(Base):
    def test_writes_the_bytes(self):
        atomic_io.atomic_write_bytes(self.target, b'{"a": 1}')
        self.assertEqual(self.target.read_bytes(), b'{"a": 1}')

    def test_creates_missing_parent_directories(self):
        nested = self.dir / "a" / "b" / "c.json"
        atomic_io.atomic_write_bytes(nested, b"x")
        self.assertEqual(nested.read_bytes(), b"x")

    def test_overwrite_replaces_content_entirely(self):
        atomic_io.atomic_write_bytes(self.target, b"aaaaaaaaaa")
        atomic_io.atomic_write_bytes(self.target, b"bb")
        self.assertEqual(self.target.read_bytes(), b"bb", "stale tail survived the overwrite")

    def test_leaves_no_temp_files_behind(self):
        atomic_io.atomic_write_bytes(self.target, b"x")
        leftovers = [p.name for p in self.dir.iterdir() if ".tmp." in p.name]
        self.assertEqual(leftovers, [])

    def test_temp_file_is_created_in_the_target_directory(self):
        """os.replace is atomic only within a filesystem. A temp created elsewhere
        (e.g. /tmp) degrades the rename to a copy that a reader can catch
        half-written — the whole guarantee, silently gone."""
        seen: list[str] = []
        real = tempfile.mkstemp

        def spy(*a, **kw):
            seen.append(kw.get("dir"))
            return real(*a, **kw)

        with mock.patch.object(atomic_io.tempfile, "mkstemp", side_effect=spy):
            atomic_io.atomic_write_bytes(self.target, b"x")
        self.assertEqual(seen, [str(self.target.parent)])

    def test_fsync_runs_before_replace(self):
        """Ordering is the durability guarantee. Replace-then-fsync can leave a
        present-but-empty file after a crash."""
        order: list[str] = []
        with mock.patch.object(atomic_io.os, "fsync", side_effect=lambda fd: order.append("fsync")), \
             mock.patch.object(atomic_io.os, "replace",
                               side_effect=lambda a, b: (order.append("replace"), os.rename(a, b))[0]):
            atomic_io.atomic_write_bytes(self.target, b"x")
        self.assertEqual(order, ["fsync", "replace"], f"bad ordering: {order}")

    def test_failed_write_preserves_the_original(self):
        atomic_io.atomic_write_bytes(self.target, b"ORIGINAL")
        with mock.patch.object(atomic_io.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_io.atomic_write_bytes(self.target, b"NEW")
        self.assertEqual(self.target.read_bytes(), b"ORIGINAL",
                         "a failed write destroyed the previous good content")

    def test_failed_write_cleans_up_its_temp_file(self):
        with mock.patch.object(atomic_io.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_io.atomic_write_bytes(self.target, b"NEW")
        leftovers = [p.name for p in self.dir.iterdir() if ".tmp." in p.name]
        self.assertEqual(leftovers, [], "failed write littered a temp file")


class TestContentIndexWritethrough(unittest.TestCase):
    """atomic_write_bytes upserts .md writes into the FTS index (plan chunk 2)."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.store = self.dir / "store"
        self.store.mkdir()
        self._env_backup = {
            key: os.environ.get(key)
            for key in ("BUILD_LOOP_CONTENT_INDEX_WRITETHROUGH", "BUILD_LOOP_MEMORY_CONTENT")
        }
        self.addCleanup(self._restore_env)
        self._patcher = mock.patch("_paths.memory_store_root", return_value=self.store)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _restore_env(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _db_path(self) -> Path:
        return content_index.default_db_path(self.store)

    def test_md_write_under_store_root_is_immediately_queryable(self) -> None:
        content_index.build(self.store, db_path=self._db_path())  # database must already exist
        target = self.store / "decision.md"
        atomic_io.atomic_write_bytes(target, b"needlephrase body text")
        rows = content_index.query("needlephrase", db_path=self._db_path())
        self.assertEqual([row["path"] for row in rows], [str(target.resolve())])

    def test_md_write_outside_store_root_leaves_index_untouched(self) -> None:
        content_index.build(self.store, db_path=self._db_path())
        outside_dir = self.dir / "outside"
        target = outside_dir / "decision.md"
        atomic_io.atomic_write_bytes(target, b"outsideneedlephrase body text")
        self.assertEqual(
            content_index.query("outsideneedlephrase", db_path=self._db_path()), []
        )

    def test_md_write_with_no_database_yet_creates_none_but_file_still_lands(self) -> None:
        """The write-through hook must never create the database (that's build()'s
        job alone — see index_paths' own no-create test); a store with no index
        yet degrades to a write-through no-op, not a silently-seeded one-doc db."""
        self.assertFalse(self._db_path().exists())
        target = self.store / "decision.md"
        atomic_io.atomic_write_bytes(target, b"neverbuiltneedle body text")
        self.assertFalse(self._db_path().exists(), "write-through must not create the database")
        self.assertEqual(target.read_bytes(), b"neverbuiltneedle body text")

    def test_non_md_write_leaves_index_untouched(self) -> None:
        target = self.store / "state.json"
        atomic_io.atomic_write_bytes(target, b'{"a": 1}')
        self.assertFalse(self._db_path().exists())

    def test_raising_index_paths_does_not_fail_the_write(self) -> None:
        target = self.store / "decision.md"
        with mock.patch.object(content_index, "index_paths", side_effect=RuntimeError("boom")):
            atomic_io.atomic_write_bytes(target, b"body text")
        self.assertEqual(target.read_bytes(), b"body text")

    def test_writethrough_does_not_block_on_a_locked_index(self) -> None:
        """The write-through hook must fail fast under index contention, not
        inherit sqlite3's 5.0s default busy timeout. Confirmed to FAIL before
        the fix: pre-fix this blocked for the full default timeout (measured
        by the auditor at 12.83s in a fuller repro) before silently dropping
        the document from the index."""
        content_index.build(self.store, db_path=self._db_path())  # database must already exist
        blocker = sqlite3.connect(self._db_path())
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            target = self.store / "decision.md"
            started = time.monotonic()
            atomic_io.atomic_write_bytes(target, b"locked write body text")
            elapsed = time.monotonic() - started
        finally:
            blocker.rollback()
            blocker.close()
        self.assertLess(elapsed, 1.0, f"write-through blocked for {elapsed:.2f}s under index contention")
        self.assertEqual(target.read_bytes(), b"locked write body text")

    def test_env_switch_disables_the_hook(self) -> None:
        os.environ["BUILD_LOOP_CONTENT_INDEX_WRITETHROUGH"] = "0"
        target = self.store / "decision.md"
        atomic_io.atomic_write_bytes(target, b"disabledneedlephrase body text")
        self.assertFalse(self._db_path().exists())


class TestLockedFile(Base):
    def test_lock_path_is_the_target_plus_lock(self):
        lf = atomic_io.LockedFile(self.target)
        self.assertEqual(lf.lock_path, self.dir / "state.json.lock")

    def test_acquires_and_releases(self):
        with atomic_io.LockedFile(self.target):
            pass
        with atomic_io.LockedFile(self.target):
            pass  # a leaked lock would make this hang then raise

    def test_second_holder_times_out(self):
        """Measured: a second exclusive flock raises BlockingIOError even in-process."""
        with atomic_io.LockedFile(self.target):
            with self.assertRaises(TimeoutError):
                with atomic_io.LockedFile(self.target, timeout_s=0.1):
                    pass

    def test_timeout_does_not_leak_the_descriptor(self):
        with atomic_io.LockedFile(self.target):
            second = atomic_io.LockedFile(self.target, timeout_s=0.1)
            with self.assertRaises(TimeoutError):
                second.__enter__()
            self.assertIsNone(second._fd, "descriptor leaked after a failed acquire")

    def test_lock_is_released_when_the_body_raises(self):
        with self.assertRaises(ValueError):
            with atomic_io.LockedFile(self.target):
                raise ValueError("boom")
        with atomic_io.LockedFile(self.target, timeout_s=0.1):
            pass  # would time out if the exception path skipped release

    def test_creates_the_lock_directory(self):
        deep = self.dir / "x" / "y" / "state.json"
        with atomic_io.LockedFile(deep):
            self.assertTrue((self.dir / "x" / "y" / "state.json.lock").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
