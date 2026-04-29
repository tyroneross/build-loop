#!/usr/bin/env python3
"""Tests for check_cache_sync.py. Zero deps. Run: python3 test_check_cache_sync.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "check_cache_sync.py"


def run(args: list[str], *, home: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_codex_source(root: Path, *, version: str = "1.0.0") -> None:
    write(root / ".codex-plugin/plugin.json", json.dumps({"name": "build-loop", "version": version}))
    write(root / "AGENTS.md", "# Agents\n")
    write(root / "README.md", "# Readme\n")
    write(root / "commands/build-loop.md", "---\ndescription: build\n---\n")
    write(root / "skills/build-loop/SKILL.md", "---\nname: build-loop\n---\n")


class CheckCacheSyncCodexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.cache = self.root / "cache"
        write_codex_source(self.source)
        for path in (
            ".codex-plugin/plugin.json",
            "AGENTS.md",
            "README.md",
            "commands/build-loop.md",
            "skills/build-loop/SKILL.md",
        ):
            write(self.cache / path, (self.source / path).read_text())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_codex_cache_passes_when_visible_surfaces_match(self) -> None:
        result = run(["--host", "codex", "--source", str(self.source), "--cache", str(self.cache)])
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("codex build-loop@1.0.0", result.stdout)

    def test_codex_cache_reports_diverged_surface(self) -> None:
        write(self.source / "skills/build-loop/SKILL.md", "---\nname: build-loop\n---\nchanged\n")
        result = run(["--host", "codex", "--source", str(self.source), "--cache", str(self.cache)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("[DIVERGED]         skills/build-loop/SKILL.md", result.stdout)

    def test_codex_cache_reports_missing_surface(self) -> None:
        write(self.source / "skills/build-loop/templates/codex-worker-prompt.md", "# Template\n")
        result = run(["--host", "codex", "--source", str(self.source), "--cache", str(self.cache)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("[MISSING IN CACHE] skills/build-loop/templates/codex-worker-prompt.md", result.stdout)

    def test_codex_cache_reports_stale_installed_versions(self) -> None:
        home = self.root / "home"
        stale = home / ".codex/plugins/cache/ross-labs-local/build-loop/0.2.0"
        write(stale / ".codex-plugin/plugin.json", json.dumps({"name": "build-loop", "version": "0.2.0"}))

        result = run(["--host", "codex", "--source", str(self.source)], home=home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cache for build-loop@1.0.0 not installed", result.stderr)
        self.assertIn("0.2.0", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

