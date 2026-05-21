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


def write_dual_host_source(root: Path, *, version: str = "1.0.0") -> None:
    write(root / ".claude-plugin/plugin.json", json.dumps({"name": "build-loop", "version": version}))
    write_codex_source(root, version=version)
    write(root / "scripts/rally_point/post.py", "print('post')\n")
    write(root / "scripts/coordination_status.py", "print('status')\n")
    write(root / "scripts/check_cache_sync.py", "print('sync')\n")
    write(root / "commands/agent-rally-point.md", "# Rally\n")
    write(root / "references/coordination-rules.md", "# Rules\n")


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


class CoordinationCacheParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.claude_cache = self.root / "claude-cache"
        self.codex_cache = self.root / "codex-cache"
        write_dual_host_source(self.source)
        for ref in (
            "scripts/rally_point/post.py",
            "scripts/coordination_status.py",
            "scripts/check_cache_sync.py",
            "commands/agent-rally-point.md",
            "references/coordination-rules.md",
        ):
            text = (self.source / ref).read_text()
            write(self.claude_cache / ref, text)
            write(self.codex_cache / ref, text)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_parity(self) -> subprocess.CompletedProcess:
        return run([
            "--source", str(self.source),
            "--coordination-cache-parity",
            "--claude-cache", str(self.claude_cache),
            "--codex-cache", str(self.codex_cache),
        ])

    def test_coordination_cache_parity_passes_when_host_caches_match(self) -> None:
        result = self._run_parity()

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("coordination cache parity", result.stdout)

    def test_coordination_cache_parity_fails_when_coordination_script_diverges(self) -> None:
        write(self.codex_cache / "scripts/rally_point/post.py", "print('newer')\n")

        result = self._run_parity()

        self.assertEqual(result.returncode, 1)
        self.assertIn("[HOST CACHE DIVERGED]     scripts/rally_point/post.py", result.stdout)

    def test_coordination_cache_parity_fails_when_coordination_script_missing(self) -> None:
        (self.codex_cache / "scripts/coordination_status.py").unlink()

        result = self._run_parity()

        self.assertEqual(result.returncode, 1)
        self.assertIn("[MISSING IN CODEX CACHE]  scripts/coordination_status.py", result.stdout)

    def test_coordination_cache_parity_json_reports_refs_checked(self) -> None:
        result = run([
            "--source", str(self.source),
            "--coordination-cache-parity",
            "--claude-cache", str(self.claude_cache),
            "--codex-cache", str(self.codex_cache),
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["mode"], "coordination-cache-parity")
        self.assertIn("scripts/rally_point/post.py", data["refs_checked"])

    def test_coordination_cache_parity_json_failure_emits_one_report(self) -> None:
        write(self.codex_cache / "scripts/rally_point/post.py", "print('newer')\n")

        result = run([
            "--source", str(self.source),
            "--coordination-cache-parity",
            "--claude-cache", str(self.claude_cache),
            "--codex-cache", str(self.codex_cache),
            "--json",
        ])

        self.assertEqual(result.returncode, 1)
        data = json.loads(result.stdout)
        self.assertEqual(len(data["diffs"]), 1)
        self.assertEqual(data["diffs"][0]["status"], "host_cache_diverged")

    def test_coordination_cache_parity_fails_when_host_cache_missing(self) -> None:
        result = run([
            "--source", str(self.source),
            "--coordination-cache-parity",
            "--claude-cache", str(self.claude_cache),
            "--codex-cache", str(self.root / "missing-codex-cache"),
        ])

        self.assertEqual(result.returncode, 1)
        self.assertIn("cache for codex build-loop@1.0.0 not installed", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
