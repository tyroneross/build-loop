#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/sync_plugin_cache.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "sync_plugin_cache.py"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def git(repo: Path, args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def init_repo(repo: Path) -> None:
    git(repo, ["init"])
    git(repo, ["config", "user.email", "test@example.com"])
    git(repo, ["config", "user.name", "Test User"])


def commit_all(repo: Path, message: str = "initial") -> None:
    git(repo, ["add", "."])
    git(repo, ["commit", "-m", message])


def write_dual_plugin(repo: Path, version: str = "1.0.0") -> None:
    write(repo / ".claude-plugin/plugin.json", json.dumps({"name": "build-loop", "version": version}))
    write(
        repo / ".codex-plugin/plugin.json",
        json.dumps({"name": "build-loop", "version": version, "skills": "./skills"}),
    )
    write(repo / "AGENTS.md", "# Agents\n")
    write(repo / "README.md", "committed\n")
    write(repo / "commands/build-loop.md", "---\ndescription: build\n---\n")
    write(repo / "skills/build-loop/SKILL.md", "---\nname: build-loop\n---\n")
    write(repo / "hooks/hooks.json", "{}\n")


class SyncPluginCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        init_repo(self.source)
        write_dual_plugin(self.source)
        commit_all(self.source)
        self.claude_cache = self.root / "claude-cache"
        self.codex_cache = self.root / "codex-cache"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_default_sync_uses_committed_head_not_dirty_worktree(self) -> None:
        write(self.source / "README.md", "dirty\n")

        result = run([
            "--host", "codex",
            "--source", str(self.source),
            "--cache", str(self.codex_cache),
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual((self.codex_cache / "README.md").read_text(encoding="utf-8"), "committed\n")
        data = json.loads(result.stdout)
        self.assertEqual(data["source_mode"], "head")

    def test_default_sync_can_archive_plugin_subdirectory(self) -> None:
        artifact = self.source / "plugin-artifacts/codex"
        write(artifact / ".codex-plugin/plugin.json", json.dumps({
            "name": "build-loop",
            "version": "1.0.0",
            "skills": "./skills",
        }))
        write(artifact / "skills/build-loop/SKILL.md", "---\nname: build-loop\n---\nartifact\n")
        write(self.source / "skills/internal/SKILL.md", "---\nname: internal\n---\nnoisy\n")
        commit_all(self.source, "add codex artifact")

        result = run([
            "--host", "codex",
            "--source", str(artifact),
            "--cache", str(self.codex_cache),
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertTrue((self.codex_cache / ".codex-plugin/plugin.json").is_file())
        self.assertTrue((self.codex_cache / "skills/build-loop/SKILL.md").is_file())
        self.assertFalse((self.codex_cache / "skills/internal/SKILL.md").exists())

    def test_dirty_sync_is_explicit(self) -> None:
        write(self.source / "README.md", "dirty\n")

        result = run([
            "--host", "codex",
            "--source", str(self.source),
            "--cache", str(self.codex_cache),
            "--dirty",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual((self.codex_cache / "README.md").read_text(encoding="utf-8"), "dirty\n")
        self.assertEqual(json.loads(result.stdout)["source_mode"], "dirty")

    def test_host_all_syncs_claude_and_codex(self) -> None:
        result = run([
            "--host", "all",
            "--source", str(self.source),
            "--claude-cache", str(self.claude_cache),
            "--codex-cache", str(self.codex_cache),
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertTrue((self.claude_cache / ".claude-plugin/plugin.json").is_file())
        self.assertTrue((self.codex_cache / ".codex-plugin/plugin.json").is_file())
        hosts = {item["host"] for item in json.loads(result.stdout)["results"]}
        self.assertEqual(hosts, {"claude", "codex"})

    def test_targeted_file_sync_does_not_delete_existing_cache(self) -> None:
        write(self.codex_cache / "keep.txt", "keep\n")
        write(self.source / "README.md", "dirty target\n")

        result = run([
            "--host", "codex",
            "--source", str(self.source),
            "--cache", str(self.codex_cache),
            "--dirty",
            "--file", "README.md",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual((self.codex_cache / "README.md").read_text(encoding="utf-8"), "dirty target\n")
        self.assertEqual((self.codex_cache / "keep.txt").read_text(encoding="utf-8"), "keep\n")

    def test_dry_run_does_not_write_cache(self) -> None:
        result = run([
            "--host", "codex",
            "--source", str(self.source),
            "--cache", str(self.codex_cache),
            "--dry-run",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(self.codex_cache.exists())
        self.assertEqual(json.loads(result.stdout)["results"][0]["action"], "dry_run")

    def test_hook_install_is_idempotent(self) -> None:
        first = run(["--source", str(self.source), "--host", "all", "--install-git-hooks", "--json"])
        second = run(["--source", str(self.source), "--host", "all", "--install-git-hooks", "--json"])

        self.assertEqual(first.returncode, 0, msg=first.stderr + first.stdout)
        self.assertEqual(second.returncode, 0, msg=second.stderr + second.stdout)
        hook = self.source / ".git/hooks/post-commit"
        text = hook.read_text(encoding="utf-8")
        self.assertEqual(text.count("# --- BEGIN build-loop plugin-cache-sync ---"), 1)
        self.assertIn("--host all", text)

    def test_hook_install_inserts_before_terminal_exit_zero(self) -> None:
        hook = self.source / ".git/hooks/post-commit"
        write(hook, "#!/bin/sh\necho existing\nexit 0\n")

        result = run(["--source", str(self.source), "--host", "all", "--install-git-hooks", "--json"])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        text = hook.read_text(encoding="utf-8")
        self.assertLess(
            text.index("# --- BEGIN build-loop plugin-cache-sync ---"),
            text.index("exit 0"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
