#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for prune_codex_plugin_cache.py. Zero deps."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "prune_codex_plugin_cache.py"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_source(root: Path, *, name: str = "build-loop", version: str = "1.2.0") -> None:
    write(root / ".codex-plugin/plugin.json", json.dumps({"name": name, "version": version}))


def write_cache(cache_root: Path, marketplace: str, name: str, version: str) -> Path:
    root = cache_root / marketplace / name / version
    write(root / ".codex-plugin/plugin.json", json.dumps({"name": name, "version": version}))
    write(root / "payload.txt", version)
    return root


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
    )


class PruneCodexPluginCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.cache = self.root / "cache"
        write_source(self.source)
        self.current = write_cache(self.cache, "ross-labs-local", "build-loop", "1.2.0")
        self.old_a = write_cache(self.cache, "ross-labs-local", "build-loop", "1.1.0")
        self.old_b = write_cache(self.cache, "other-market", "build-loop", "1.0.0")
        self.other_plugin = write_cache(self.cache, "ross-labs-local", "research", "1.0.0")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_lists_stale_versions_without_deleting(self) -> None:
        result = run(["--source", str(self.source), "--cache-root", str(self.cache)])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("1.1.0", result.stdout)
        self.assertIn("1.0.0", result.stdout)
        self.assertTrue(self.old_a.exists())
        self.assertTrue(self.old_b.exists())
        self.assertTrue(self.current.exists())

    def test_apply_deletes_only_stale_versions_for_plugin(self) -> None:
        result = run(["--source", str(self.source), "--cache-root", str(self.cache), "--apply"])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(self.old_a.exists())
        self.assertFalse(self.old_b.exists())
        self.assertTrue(self.current.exists())
        self.assertTrue(self.other_plugin.exists())

    def test_marketplace_restricts_prune_scope(self) -> None:
        result = run([
            "--source", str(self.source),
            "--cache-root", str(self.cache),
            "--marketplace", "ross-labs-local",
            "--apply",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(self.old_a.exists())
        self.assertTrue(self.old_b.exists())
        self.assertTrue(self.current.exists())

    def test_unverified_cache_dir_is_skipped(self) -> None:
        unverified = self.cache / "ross-labs-local" / "build-loop" / "0.9.0"
        write(unverified / "payload.txt", "no manifest")

        result = run([
            "--source", str(self.source),
            "--cache-root", str(self.cache),
            "--apply",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertIn(str(unverified.resolve()), data["skipped_unverified"])
        self.assertTrue(unverified.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
