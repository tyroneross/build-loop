#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Tests for _db_url.resolve_db_url. Zero deps. Run: python3 -m pytest scripts/test__db_url.py

Covers the four resolution cases. Each test fully isolates env + HOME so it
never reads the developer's real ~/.config/agent-memory/connection.env.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _db_url  # noqa: E402


class ResolveDbUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        # Snapshot + clear both env vars and HOME; restore in tearDown.
        self._saved = {
            k: os.environ.get(k)
            for k in ("BUILD_LOOP_DATABASE_URL", "DATABASE_URL", "HOME")
        }
        for k in ("BUILD_LOOP_DATABASE_URL", "DATABASE_URL"):
            os.environ.pop(k, None)
        # Point HOME at an empty tmp dir so the connection.env probe finds
        # nothing unless a test deliberately creates the file.
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self._tmp.name

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _write_conn_env(self, value: str) -> None:
        p = Path(self._tmp.name) / ".config" / "agent-memory" / "connection.env"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# comment\nDATABASE_URL={value}\n", encoding="utf-8")

    def test_build_loop_var_wins(self) -> None:
        os.environ["BUILD_LOOP_DATABASE_URL"] = "postgres://bl-wins/db"
        os.environ["DATABASE_URL"] = "postgres://should-not-be-used/db"
        self._write_conn_env("postgres://file-should-not-be-used/db")
        self.assertEqual(_db_url.resolve_db_url(), "postgres://bl-wins/db")

    def test_database_url_fallback(self) -> None:
        os.environ["DATABASE_URL"] = "postgres://legacy/db"
        self._write_conn_env("postgres://file-should-not-be-used/db")
        self.assertEqual(_db_url.resolve_db_url(), "postgres://legacy/db")

    def test_connection_env_fallback(self) -> None:
        self._write_conn_env("postgres://from-file/db")
        self.assertEqual(_db_url.resolve_db_url(), "postgres://from-file/db")

    def test_none_configured_returns_empty(self) -> None:
        self.assertEqual(_db_url.resolve_db_url(), "")

    def test_db_py_raises_when_empty(self) -> None:
        """db.py:_read_db_url preserves raise-on-missing on top of the
        resolver. Patch db's bound resolver to avoid importing psycopg."""
        import importlib

        # db.py imports psycopg at module top; skip if unavailable.
        try:
            db = importlib.import_module("db")
        except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
            if "psycopg" in str(exc):
                self.skipTest("psycopg not installed in this env")
            raise
        orig = db.resolve_db_url
        try:
            db.resolve_db_url = lambda: ""
            with self.assertRaises(RuntimeError):
                db._read_db_url()
            db.resolve_db_url = lambda: "postgres://ok/db"
            self.assertEqual(db._read_db_url(), "postgres://ok/db")
        finally:
            db.resolve_db_url = orig

    def test_whitespace_only_var_is_ignored(self) -> None:
        os.environ["BUILD_LOOP_DATABASE_URL"] = "   "
        os.environ["DATABASE_URL"] = "postgres://real/db"
        self.assertEqual(_db_url.resolve_db_url(), "postgres://real/db")


if __name__ == "__main__":
    unittest.main()
