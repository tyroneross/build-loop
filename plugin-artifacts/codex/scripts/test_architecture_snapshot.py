#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for architecture_snapshot.py — live .navgator → memory-lane promotion (WP-H/G4).

Stdlib only. Monkeypatches the path resolver so no test touches the real
build-loop-memory store or the real .navgator data.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import architecture_snapshot as arch  # noqa: E402


class ArchSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.workdir = root / "repo"
        self.live = self.workdir / ".navgator" / "architecture"
        self.canonical = root / "memory" / "projects" / "demo" / "architecture"
        self.live.mkdir(parents=True)
        self._orig = arch._resolve_paths
        arch._resolve_paths = lambda wd, slug=None: (self.live, self.canonical)  # type: ignore

    def tearDown(self) -> None:
        arch._resolve_paths = self._orig  # type: ignore
        self.tmp.cleanup()

    def _seed_live(self, sha: str, dirty: int = 0) -> None:
        (self.live / "graph.json").write_text(
            json.dumps({"schema_version": 1, "nodes": [{"id": "a"}], "edges": []}), encoding="utf-8")
        (self.live / "file_map.json").write_text(
            json.dumps({"schema_version": 1, "files": {}}), encoding="utf-8")
        (self.live / "freshness.json").write_text(
            json.dumps({"commit_sha": sha, "branch": "main", "dirty_count": dirty,
                        "dirty_files": []}), encoding="utf-8")

    def test_promote_noop_when_no_navgator(self) -> None:
        r = arch.promote(self.workdir)
        self.assertEqual(r["action"], "noop_no_navgator")
        self.assertFalse((self.canonical / "snapshot.json").exists())

    def test_promote_writes_snapshot_with_provenance(self) -> None:
        self._seed_live("abc1234")
        r = arch.promote(self.workdir)
        self.assertEqual(r["action"], "promoted")
        meta = json.loads((self.canonical / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["commit_sha"], "abc1234")
        self.assertEqual(meta["provenance"], "navgator")
        self.assertIn("graph.json", meta["files"])
        self.assertTrue((self.canonical / "graph.json").is_file())

    def test_promote_is_noop_when_sha_unchanged(self) -> None:
        self._seed_live("abc1234")
        arch.promote(self.workdir)
        r = arch.promote(self.workdir)
        self.assertEqual(r["action"], "noop_unchanged")

    def test_promote_force_overrides_unchanged(self) -> None:
        self._seed_live("abc1234")
        arch.promote(self.workdir)
        r = arch.promote(self.workdir, force=True)
        self.assertEqual(r["action"], "promoted")

    def test_promote_refreshes_on_new_sha(self) -> None:
        self._seed_live("abc1234")
        arch.promote(self.workdir)
        self._seed_live("def5678")
        r = arch.promote(self.workdir)
        self.assertEqual(r["action"], "promoted")
        meta = json.loads((self.canonical / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["commit_sha"], "def5678")

    def test_navgator_dirty_count_marks_dirty(self) -> None:
        self._seed_live("abc1234", dirty=3)
        d = arch.is_dirty(self.workdir)
        self.assertTrue(d["dirty"])
        self.assertTrue(any("navgator_dirty_count" in r for r in d["reasons"]))

    def test_mark_dirty_then_is_dirty(self) -> None:
        self._seed_live("abc1234", dirty=0)
        self.assertFalse(arch.is_dirty(self.workdir)["dirty"])
        arch.mark_dirty(self.workdir, "new dependency: networkx")
        d = arch.is_dirty(self.workdir)
        self.assertTrue(d["dirty"])
        self.assertIn("new dependency: networkx", d["reasons"])

    def test_promote_clears_dirty_marker(self) -> None:
        self._seed_live("abc1234")
        arch.mark_dirty(self.workdir, "new LLM provider")
        self.assertTrue(arch.is_dirty(self.workdir)["dirty"])
        arch.promote(self.workdir)
        self.assertFalse(arch.is_dirty(self.workdir)["dirty"])

    def test_status_reports_needs_promote(self) -> None:
        self._seed_live("abc1234")
        st = arch.status(self.workdir)
        self.assertTrue(st["needs_promote"])
        self.assertTrue(st["live_present"])
        self.assertFalse(st["canonical_present"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
