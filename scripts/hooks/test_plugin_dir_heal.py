#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/hooks/plugin_dir_heal.py. Zero deps.

Covers both heal paths:
  A) restore-from-removed/ (registry's installPath is missing and an archived
     copy exists under ~/.claude/plugins/removed/).
  B) symlink-old→new (installPath is missing and NO archive exists, but a
     newer sibling version of the same plugin lives in the same cache
     parent — the successful-update hard-delete case the bug exists for).

Plus safety: kill switch, idempotent re-run, no-recovery-path counts,
malformed registry handled gracefully, and the wrapper script exits 0 under
``env -i PATH=/usr/bin:/bin``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "plugin_dir_heal.py"
WRAPPER = HERE.parent.parent / "hooks" / "session-start-plugin-heal.sh"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_manifest(version_dir: Path, name: str, version: str) -> None:
    write(
        version_dir / ".claude-plugin" / "plugin.json",
        json.dumps({"name": name, "version": version}),
    )


def make_claude_home(tmp: Path) -> Path:
    """Return a fake ~/.claude rooted at `tmp/claude-home` with the
    plugins/ subdir structure ready."""
    home = tmp / "claude-home"
    (home / "plugins").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    return home


def run_heal(home: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_HOME_OVERRIDE"] = str(home)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True,
        text=True,
        env=env,
    )


def read_registry(home: Path) -> dict:
    return json.loads(
        (home / "plugins" / "installed_plugins.json").read_text(encoding="utf-8")
    )


def write_registry(
    home: Path,
    plugin_key: str,
    install_path: Path | None,
    version: str,
) -> None:
    entry = {"version": version}
    if install_path is not None:
        entry["installPath"] = str(install_path)
    registry = {"plugins": {plugin_key: [entry]}}
    write(home / "plugins" / "installed_plugins.json", json.dumps(registry))


class PluginDirHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.home = make_claude_home(self.tmp_path)
        self.plugin_name = "build-loop"
        self.marketplace = "rosslabs-ai-toolkit"
        self.cache_dir = (
            self.home / "plugins" / "cache" / self.marketplace / self.plugin_name
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ------ Case A: restore from removed/ ------

    def test_restores_archived_version_from_removed(self) -> None:
        old_version = "0.29.3"
        # Registered installPath that no longer exists on disk.
        missing_install = self.cache_dir / old_version
        # Archived copy under removed/<tag>/<plugin>/<version>/
        archived = (
            self.home / "plugins" / "removed" / "tag-2026-06-06" / self.plugin_name / old_version
        )
        write_manifest(archived, self.plugin_name, old_version)
        write(archived / "payload.txt", "archived")

        write_registry(
            self.home,
            f"{self.plugin_name}@{self.marketplace}",
            missing_install,
            old_version,
        )

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        # installPath is now a real directory containing the archived contents.
        self.assertTrue(missing_install.is_dir())
        self.assertFalse(missing_install.is_symlink())
        self.assertEqual(
            (missing_install / "payload.txt").read_text(encoding="utf-8"),
            "archived",
        )
        # Source got moved away.
        self.assertFalse(archived.exists())
        self.assertIn("restored_from_removed=1", result.stdout)

    # ------ Case B: symlink old→new ------

    def test_symlinks_missing_to_live_sibling_when_no_archive(self) -> None:
        old_version = "0.29.3"
        new_version = "0.30.2"
        missing_install = self.cache_dir / old_version
        live_sibling = self.cache_dir / new_version
        write_manifest(live_sibling, self.plugin_name, new_version)
        write(live_sibling / "payload.txt", "live")
        # No archive under removed/.
        write_registry(
            self.home,
            f"{self.plugin_name}@{self.marketplace}",
            missing_install,
            old_version,
        )

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        # installPath is a symlink to the live sibling, exposing live scripts.
        self.assertTrue(missing_install.is_symlink())
        # CC's pre-validate "directory does not exist" check passes.
        self.assertTrue(missing_install.exists())
        # Resolves to the live sibling.
        self.assertTrue((missing_install / "payload.txt").exists())
        self.assertEqual(
            (missing_install / "payload.txt").read_text(encoding="utf-8"),
            "live",
        )
        # The link is RELATIVE (sibling.name) — survives a cache-root move.
        self.assertEqual(os.readlink(missing_install), new_version)
        self.assertIn("symlinked_to_sibling=1", result.stdout)

    def test_picks_newest_sibling_by_mtime(self) -> None:
        old_version = "0.29.3"
        missing_install = self.cache_dir / old_version
        older_sib = self.cache_dir / "0.30.0"
        newer_sib = self.cache_dir / "0.31.0"
        write_manifest(older_sib, self.plugin_name, "0.30.0")
        write_manifest(newer_sib, self.plugin_name, "0.31.0")
        # Force older sibling to have an older mtime.
        import time
        old_mtime = time.time() - 86400
        os.utime(older_sib, (old_mtime, old_mtime))
        write_registry(
            self.home,
            f"{self.plugin_name}@{self.marketplace}",
            missing_install,
            old_version,
        )

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual(os.readlink(missing_install), "0.31.0")

    # ------ Safety / fail-open ------

    def test_kill_switch_skips_run(self) -> None:
        # Touch the kill-switch file.
        (self.home / "settings.json.disable-plugin-dir-heal").write_text("", encoding="utf-8")
        old_version = "0.29.3"
        new_version = "0.30.2"
        missing_install = self.cache_dir / old_version
        live_sibling = self.cache_dir / new_version
        write_manifest(live_sibling, self.plugin_name, new_version)
        write_registry(
            self.home,
            f"{self.plugin_name}@{self.marketplace}",
            missing_install,
            old_version,
        )

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        # No symlink should have been created.
        self.assertFalse(missing_install.exists())
        self.assertIn("kill-switch present", result.stdout)

    def test_idempotent_rerun_does_not_clobber_existing_symlink(self) -> None:
        old_version = "0.29.3"
        new_version = "0.30.2"
        missing_install = self.cache_dir / old_version
        live_sibling = self.cache_dir / new_version
        write_manifest(live_sibling, self.plugin_name, new_version)
        write_registry(
            self.home,
            f"{self.plugin_name}@{self.marketplace}",
            missing_install,
            old_version,
        )

        run_heal(self.home)
        self.assertTrue(missing_install.is_symlink())
        target_before = os.readlink(missing_install)

        # Second run: registry entry is a symlink → counted under symlink_skip.
        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertEqual(os.readlink(missing_install), target_before)
        self.assertIn("symlink_skip=1", result.stdout)

    def test_no_archive_and_no_sibling_is_no_op(self) -> None:
        old_version = "0.29.3"
        missing_install = self.cache_dir / old_version
        # No archive, no sibling.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        write_registry(
            self.home,
            f"{self.plugin_name}@{self.marketplace}",
            missing_install,
            old_version,
        )

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(missing_install.exists())
        self.assertIn("no_recovery_path=1", result.stdout)

    def test_dry_run_does_not_touch_filesystem(self) -> None:
        old_version = "0.29.3"
        new_version = "0.30.2"
        missing_install = self.cache_dir / old_version
        live_sibling = self.cache_dir / new_version
        write_manifest(live_sibling, self.plugin_name, new_version)
        write_registry(
            self.home,
            f"{self.plugin_name}@{self.marketplace}",
            missing_install,
            old_version,
        )

        result = run_heal(self.home, "--dry-run", "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertFalse(missing_install.exists())
        self.assertIn("symlinked_to_sibling=1", result.stdout)

    def test_missing_registry_is_fail_open(self) -> None:
        # No installed_plugins.json — script exits 0 with no-action.
        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("no-action", result.stdout)

    def test_malformed_registry_is_fail_open(self) -> None:
        write(self.home / "plugins" / "installed_plugins.json", "{not valid json")
        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("no-action", result.stdout)

    def test_skips_already_present_install_path(self) -> None:
        # installPath exists already (the common-case "healthy" branch).
        old_version = "0.30.2"
        present = self.cache_dir / old_version
        write_manifest(present, self.plugin_name, old_version)
        write_registry(
            self.home,
            f"{self.plugin_name}@{self.marketplace}",
            present,
            old_version,
        )
        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        self.assertIn("healthy=1", result.stdout)

    def test_repairs_missing_install_path_from_matching_cache_dir(self) -> None:
        version = "0.33.0"
        cache_install = self.cache_dir / version
        write_manifest(cache_install, self.plugin_name, version)
        key = f"{self.plugin_name}@{self.marketplace}"
        write_registry(self.home, key, None, version)

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        registry = read_registry(self.home)
        self.assertEqual(registry["plugins"][key][0]["installPath"], str(cache_install))
        self.assertTrue(Path(registry["plugins"][key][0]["installPath"]).is_absolute())
        self.assertIn("installpath_repaired=1", result.stdout)
        self.assertIn("healthy=1", result.stdout)

    def test_missing_install_path_dry_run_does_not_mutate_registry(self) -> None:
        version = "0.33.0"
        cache_install = self.cache_dir / version
        write_manifest(cache_install, self.plugin_name, version)
        key = f"{self.plugin_name}@{self.marketplace}"
        write_registry(self.home, key, None, version)

        result = run_heal(self.home, "--dry-run", "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        registry = read_registry(self.home)
        self.assertNotIn("installPath", registry["plugins"][key][0])
        self.assertIn("installpath_repaired=1", result.stdout)

    def test_missing_install_path_rejects_mismatched_manifest_version(self) -> None:
        version = "0.33.0"
        cache_install = self.cache_dir / version
        write_manifest(cache_install, self.plugin_name, "9.9.9")
        key = f"{self.plugin_name}@{self.marketplace}"
        write_registry(self.home, key, None, version)

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        registry = read_registry(self.home)
        self.assertNotIn("installPath", registry["plugins"][key][0])
        self.assertIn("missing_installpath_field=1", result.stdout)
        self.assertNotIn("installpath_repaired=1", result.stdout)

    def test_missing_install_path_does_not_use_other_marketplace_cache(self) -> None:
        version = "0.33.0"
        other_marketplace = (
            self.home
            / "plugins"
            / "cache"
            / "other-marketplace"
            / self.plugin_name
            / version
        )
        write_manifest(other_marketplace, self.plugin_name, version)
        key = f"{self.plugin_name}@{self.marketplace}"
        write_registry(self.home, key, None, version)

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        registry = read_registry(self.home)
        self.assertNotIn("installPath", registry["plugins"][key][0])
        self.assertIn("missing_installpath_field=1", result.stdout)
        self.assertNotIn("installpath_repaired=1", result.stdout)

    def test_repairs_missing_install_path_from_backup_registry(self) -> None:
        version = "0.33.0"
        backup_install = self.tmp_path / "trusted-backup" / self.plugin_name / version
        write_manifest(backup_install, self.plugin_name, version)
        key = f"{self.plugin_name}@{self.marketplace}"
        write_registry(self.home, key, None, version)
        backup_registry = (
            self.home
            / "plugins"
            / "_manual-reinstall-backup-1"
            / "installed_plugins.json"
        )
        write(
            backup_registry,
            json.dumps(
                {
                    "plugins": {
                        key: [
                            {
                                "installPath": str(backup_install),
                                "version": version,
                            }
                        ]
                    }
                }
            ),
        )

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        registry = read_registry(self.home)
        self.assertEqual(registry["plugins"][key][0]["installPath"], str(backup_install))
        self.assertIn("installpath_repaired=1", result.stdout)

    def test_missing_install_path_archive_restore_failure_does_not_mutate_registry(
        self,
    ) -> None:
        version = "0.33.0"
        archived = (
            self.home
            / "plugins"
            / "removed"
            / "tag-2026-06-11"
            / self.plugin_name
            / version
        )
        write_manifest(archived, self.plugin_name, version)
        # Make the canonical plugin cache parent impossible to create.
        self.cache_dir.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.write_text("not a directory", encoding="utf-8")
        key = f"{self.plugin_name}@{self.marketplace}"
        write_registry(self.home, key, None, version)

        result = run_heal(self.home, "--verbose")
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        registry = read_registry(self.home)
        self.assertNotIn("installPath", registry["plugins"][key][0])
        self.assertIn("installpath_repaired=1", result.stdout)
        self.assertIn("errors=1", result.stdout)


class WrapperSmokeTests(unittest.TestCase):
    """The shell wrapper must exit 0 under a minimal PATH and not depend on
    login-shell setup (per the hooks-minimal-PATH lesson)."""

    def test_wrapper_exits_zero_under_minimal_path(self) -> None:
        # Run with env -i style PATH; the wrapper should still exit 0 even
        # if the heal script does nothing (no registry in the test env).
        result = subprocess.run(
            ["bash", str(WRAPPER)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

    def test_wrapper_respects_opt_out_env(self) -> None:
        result = subprocess.run(
            ["bash", str(WRAPPER)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "BUILDLOOP_NO_PLUGIN_HEAL": "1"},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
