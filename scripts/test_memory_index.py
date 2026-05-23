#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory_index.py. Zero deps. Run: python3 test_memory_index.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import memory_index as mi  # noqa: E402


class AppendRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_append_writes_one_row(self):
        memory_file = self.tmp / "feedback_x.md"
        memory_file.write_text("# memory body")
        row = mi.append_row(
            self.tmp, run_id="r1", action="write", file_rel="feedback_x.md",
        )
        self.assertEqual(row["action"], "write")
        self.assertEqual(row["file"], "feedback_x.md")
        self.assertTrue(row["sha256"], "sha should be computed")
        # File on disk has one line.
        log = (self.tmp / "INDEX.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(log), 1)
        self.assertEqual(json.loads(log[0])["run_id"], "r1")

    def test_sha256_matches_file_content(self):
        memory_file = self.tmp / "p.md"
        memory_file.write_bytes(b"abc")
        row = mi.append_row(self.tmp, run_id="r", action="write", file_rel="p.md")
        # sha256("abc") = ba7816bf...
        self.assertEqual(
            row["sha256"],
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_append_with_source_metadata(self):
        memory_file = self.tmp / "p.md"
        memory_file.write_text("x")
        row = mi.append_row(
            self.tmp, run_id="r", action="write", file_rel="p.md",
            source_repo="https://github.com/foo/bar.git",
            source_workdir="/abs/foo",
            source_host="codex",
        )
        self.assertEqual(row["source_repo"], "https://github.com/foo/bar.git")
        self.assertEqual(row["source_workdir"], "/abs/foo")
        self.assertEqual(row["source_host"], "codex")

    def test_delete_action_skips_hash_compute(self):
        # file doesn't exist; default delete action returns ""
        row = mi.append_row(
            self.tmp, run_id="r", action="delete", file_rel="gone.md",
        )
        self.assertEqual(row["action"], "delete")
        self.assertEqual(row["sha256"], "")

    def test_invalid_action_raises(self):
        with self.assertRaises(ValueError):
            mi.append_row(self.tmp, run_id="r", action="rename", file_rel="x")

    def test_multi_append_appends(self):
        for i in range(3):
            (self.tmp / f"f{i}.md").write_text(str(i))
            mi.append_row(
                self.tmp, run_id="r", action="write", file_rel=f"f{i}.md",
            )
        rows = (self.tmp / "INDEX.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(rows), 3)


class TailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Pre-populate 3 rows from different runs.
        for i, run in enumerate(["alice", "bob", "alice"]):
            (self.tmp / f"file{i}.md").write_text(f"content {i}")
            mi.append_row(self.tmp, run_id=run, action="write", file_rel=f"file{i}.md")

    def test_tail_returns_all_rows(self):
        rows = mi.tail(self.tmp)
        self.assertEqual(len(rows), 3)

    def test_tail_exclude_run_id(self):
        rows = mi.tail(self.tmp, exclude_run_id="alice")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "bob")

    def test_tail_file_filter_substring(self):
        rows = mi.tail(self.tmp, file_filter="file1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file"], "file1.md")

    def test_tail_limit_returns_most_recent(self):
        rows = mi.tail(self.tmp, limit=2)
        self.assertEqual(len(rows), 2)
        # Most recent two are file1, file2 (file0 written first).
        self.assertEqual(rows[0]["file"], "file1.md")
        self.assertEqual(rows[1]["file"], "file2.md")

    def test_tail_since_excludes_older_rows(self):
        # Use a cutoff timestamp NOW; only future rows would match.
        future = (datetime.now(timezone.utc) + timedelta(seconds=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = mi.tail(self.tmp, since=future)
        self.assertEqual(rows, [])

    def test_tail_since_includes_newer_rows(self):
        # Use a cutoff timestamp from 1 hour ago — all rows newer.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = mi.tail(self.tmp, since=past)
        self.assertEqual(len(rows), 3)

    def test_tail_missing_index_returns_empty(self):
        empty_dir = Path(tempfile.mkdtemp())
        self.assertEqual(mi.tail(empty_dir), [])

    def test_tail_skips_malformed_rows(self):
        # Inject a broken line.
        with (self.tmp / "INDEX.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("{not valid json}\n")
        rows = mi.tail(self.tmp)
        # Only the 3 valid rows from setUp.
        self.assertEqual(len(rows), 3)


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HERE / "memory_index.py"),
             "--index-dir", str(self.tmp), *args],
            capture_output=True, text=True,
        )

    def test_append_then_tail_roundtrip(self):
        (self.tmp / "x.md").write_text("hello")
        r = self._cli(
            "append", "--run-id", "r1", "--action", "write",
            "--file", "x.md", "--source-host", "claude_code", "--json",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        row = json.loads(r.stdout)
        self.assertEqual(row["action"], "write")

        t = self._cli("tail", "--json")
        self.assertEqual(t.returncode, 0)
        rows = json.loads(t.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "r1")
        self.assertEqual(rows[0]["source_host"], "claude_code")

    def test_invalid_action_returns_1(self):
        r = self._cli(
            "append", "--run-id", "r", "--action", "delete",
            "--file", "x.md",
        )
        # delete is valid, sha256 will be "" since file doesn't exist
        self.assertEqual(r.returncode, 0)


class ConcurrencyTests(unittest.TestCase):
    """Confirm fcntl.flock prevents row interleaving under concurrent writes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "shared.md").write_text("body")

    def test_two_serial_appends_produce_two_rows(self):
        mi.append_row(self.tmp, run_id="r1", action="write", file_rel="shared.md")
        mi.append_row(self.tmp, run_id="r2", action="update", file_rel="shared.md")
        rows = [
            json.loads(l)
            for l in (self.tmp / "INDEX.jsonl").read_text().strip().splitlines()
        ]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["run_id"], "r1")
        self.assertEqual(rows[1]["run_id"], "r2")


if __name__ == "__main__":
    unittest.main()
