#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for prune_plugin_cache.py. Zero deps."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "prune_plugin_cache.py"


def env_without_plugin_roots(**overrides: str) -> dict[str, str]:
    """A copy of the ambient env with the host plugin-root vars cleared, so a test
    controls in-use detection explicitly and an ambient CLAUDE_PLUGIN_ROOT (the
    test may run inside a live Claude Code session) can't leak into the result."""
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.pop("CODEX_PLUGIN_ROOT", None)
    env.update(overrides)
    return env


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_source(root: Path, *, name: str = "build-loop", version: str = "1.2.0") -> None:
    write(root / ".codex-plugin/plugin.json", json.dumps({"name": name, "version": version}))
    write(root / ".claude-plugin/plugin.json", json.dumps({"name": name, "version": version}))


def write_cache(cache_root: Path, host: str, marketplace: str, name: str, version: str) -> Path:
    root = cache_root / marketplace / name / version
    manifest_dir = ".codex-plugin" if host == "codex" else ".claude-plugin"
    write(root / manifest_dir / "plugin.json", json.dumps({"name": name, "version": version}))
    write(root / "payload.txt", f"{host}:{version}")
    return root


def run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=env,
    )


class PrunePluginCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.codex_cache = self.root / "codex-cache"
        self.claude_cache = self.root / "claude-cache"
        write_source(self.source)

        self.codex_current = write_cache(self.codex_cache, "codex", "ross-labs-local", "build-loop", "1.2.0")
        self.codex_old = write_cache(self.codex_cache, "codex", "ross-labs-local", "build-loop", "1.1.0")
        self.claude_current = write_cache(self.claude_cache, "claude", "rosslabs-ai-toolkit", "build-loop", "1.2.0")
        self.claude_old = write_cache(self.claude_cache, "claude", "rosslabs-ai-toolkit", "build-loop", "1.0.0")
        self.other_plugin = write_cache(self.claude_cache, "claude", "rosslabs-ai-toolkit", "research", "1.0.0")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_host_all_apply_deletes_stale_versions_in_both_caches(self) -> None:
        result = run([
            "--source", str(self.source),
            "--codex-cache-root", str(self.codex_cache),
            "--claude-cache-root", str(self.claude_cache),
            "--apply",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual({r["host"] for r in data["reports"]}, {"codex", "claude"})
        self.assertFalse(self.codex_old.exists())
        self.assertFalse(self.claude_old.exists())
        self.assertTrue(self.codex_current.exists())
        self.assertTrue(self.claude_current.exists())
        self.assertTrue(self.other_plugin.exists())

    def test_single_host_json_keeps_back_compatible_shape(self) -> None:
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["host"], "claude")
        self.assertIn(str(self.claude_old.resolve()), data["stale"])

    def test_marketplace_can_be_host_specific(self) -> None:
        extra = write_cache(self.claude_cache, "claude", "other-market", "build-loop", "0.9.0")

        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--claude-marketplace", "rosslabs-ai-toolkit",
            "--apply",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(self.claude_old.exists())
        self.assertTrue(extra.exists())

    def test_unverified_host_manifest_is_skipped(self) -> None:
        unverified = self.claude_cache / "rosslabs-ai-toolkit" / "build-loop" / "0.8.0"
        write(unverified / ".codex-plugin/plugin.json", json.dumps({"name": "build-loop", "version": "0.8.0"}))

        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertIn(str(unverified.resolve()), data["skipped_unverified"])
        self.assertTrue(unverified.exists())

    def test_stale_symlink_entry_is_unlinked_not_followed(self) -> None:
        source_target = self.root / "linked-source"
        write_source(source_target, version="1.2.0")
        symlink_entry = self.claude_cache / "rosslabs-ai-toolkit" / "build-loop" / "1.1.5"
        symlink_entry.parent.mkdir(parents=True, exist_ok=True)
        symlink_entry.symlink_to(source_target, target_is_directory=True)

        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
            "--json",
        ])

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(symlink_entry.exists())
        self.assertTrue(source_target.exists())
        self.assertTrue((source_target / ".claude-plugin/plugin.json").exists())

    def test_in_use_version_from_env_is_protected(self) -> None:
        # The version a live session is loaded from must survive a prune even
        # though the manifest now points at a newer keep_version.
        env = env_without_plugin_roots(CLAUDE_PLUGIN_ROOT=str(self.claude_old))
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
            "--json",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertIn("1.0.0", data["protected"])
        self.assertNotIn(str(self.claude_old.resolve()), data["stale"])
        self.assertTrue(self.claude_old.exists())
        self.assertTrue(self.claude_current.exists())

    def test_in_use_symlink_version_is_protected(self) -> None:
        # Mirrors the real incident: the in-use version dir is a symlink to a
        # working tree (local-dev override). It must NOT be unlinked, and the
        # path is matched by cache name (unresolved), not the symlink target.
        source_target = self.root / "live-source"
        write_source(source_target, version="1.2.0")
        in_use = self.claude_cache / "rosslabs-ai-toolkit" / "build-loop" / "1.1.0-dev"
        in_use.parent.mkdir(parents=True, exist_ok=True)
        in_use.symlink_to(source_target, target_is_directory=True)
        env = env_without_plugin_roots(CLAUDE_PLUGIN_ROOT=str(in_use))

        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--apply",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertTrue(in_use.is_symlink())
        self.assertTrue(source_target.exists())
        # the genuinely-stale old version is still pruned
        self.assertFalse(self.claude_old.exists())

    def test_protect_flag_preserves_named_version(self) -> None:
        env = env_without_plugin_roots()  # in-use detection finds nothing
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--protect", "1.0.0",
            "--apply",
            "--json",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertIn("1.0.0", data["protected"])
        self.assertTrue(self.claude_old.exists())

    def test_no_detect_in_use_allows_pruning_env_version(self) -> None:
        # Escape hatch / back-compat: with detection off, even the env-pointed
        # in-use version is treated as stale.
        env = env_without_plugin_roots(CLAUDE_PLUGIN_ROOT=str(self.claude_old))
        result = run([
            "--host", "claude",
            "--source", str(self.source),
            "--cache-root", str(self.claude_cache),
            "--no-detect-in-use",
            "--apply",
            "--json",
        ], env=env)

        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["protected"], [])
        self.assertFalse(self.claude_old.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
