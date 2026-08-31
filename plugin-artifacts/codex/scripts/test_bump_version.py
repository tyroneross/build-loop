#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/bump_version.py.

Each test builds a throwaway manifest tree and repoints the module's REPO_ROOT
at it, so nothing here reads or writes the real repo.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import bump_version


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


class ManifestFixture(unittest.TestCase):
    """Rebuilds bump_version's module-level path table against a temp tree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self._saved = (bump_version.REPO_ROOT, bump_version.CANONICAL, bump_version.MIRRORS)
        bump_version.REPO_ROOT = self.root
        bump_version.CANONICAL = (
            "Claude plugin manifest", self.root / ".claude-plugin" / "plugin.json", ("version",),
        )
        bump_version.MIRRORS = [
            ("Codex plugin manifest", self.root / ".codex-plugin" / "plugin.json", ("version",)),
            ("Claude marketplace metadata", self.root / ".claude-plugin" / "marketplace.json", ("metadata", "version")),
            ("Claude marketplace entry", self.root / ".claude-plugin" / "marketplace.json", ("plugins", 0, "version")),
            ("open-agents marketplace mirror", self.root / ".agents" / "plugins" / "marketplace.json", ("version",)),
        ]

        def restore() -> None:
            bump_version.REPO_ROOT, bump_version.CANONICAL, bump_version.MIRRORS = self._saved

        self.addCleanup(restore)

    def seed(self, canonical: str, codex: str, meta: str, entry: str, agents: str) -> None:
        write(self.root / ".claude-plugin" / "plugin.json", {"name": "build-loop", "version": canonical})
        write(self.root / ".codex-plugin" / "plugin.json", {"name": "build-loop", "version": codex})
        write(self.root / ".claude-plugin" / "marketplace.json",
              {"metadata": {"version": meta}, "plugins": [{"name": "build-loop", "version": entry}]})
        write(self.root / ".agents" / "plugins" / "marketplace.json", {"version": agents})

    def versions(self) -> tuple[str, str, str, str, str]:
        c = json.loads((self.root / ".claude-plugin" / "plugin.json").read_text())["version"]
        x = json.loads((self.root / ".codex-plugin" / "plugin.json").read_text())["version"]
        mk = json.loads((self.root / ".claude-plugin" / "marketplace.json").read_text())
        a = json.loads((self.root / ".agents" / "plugins" / "marketplace.json").read_text())["version"]
        return c, x, mk["metadata"]["version"], mk["plugins"][0]["version"], a


class CheckTests(ManifestFixture):
    def test_all_aligned_reports_no_drift(self):
        self.seed("1.2.3", "1.2.3", "1.2.3", "1.2.3", "1.2.3")
        state = bump_version.read_state()
        self.assertTrue(state["ok"])
        self.assertEqual(state["canonical"], "1.2.3")
        self.assertTrue(all(m["matches"] for m in state["mirrors"]))

    def test_detects_the_real_world_skew(self):
        """The exact shape found on 2026-07-26: canonical bumped, three mirrors left behind."""
        self.seed("0.36.8", "0.36.7", "0.36.7", "0.36.7", "0.36.7")
        drifted = [m for m in bump_version.read_state()["mirrors"] if not m["matches"]]
        self.assertEqual(len(drifted), 4, "both marketplace fields drift, not just one")

    def test_missing_mirror_is_reported_not_raised(self):
        self.seed("1.0.0", "1.0.0", "1.0.0", "1.0.0", "1.0.0")
        (self.root / ".codex-plugin" / "plugin.json").unlink()
        state = bump_version.read_state()
        self.assertTrue(state["ok"])
        codex = next(m for m in state["mirrors"] if "Codex" in m["label"])
        self.assertEqual(codex["error"], "file missing")
        self.assertFalse(codex["matches"])

    def test_missing_canonical_is_reported_not_raised(self):
        self.seed("1.0.0", "1.0.0", "1.0.0", "1.0.0", "1.0.0")
        (self.root / ".claude-plugin" / "plugin.json").unlink()
        state = bump_version.read_state()
        self.assertFalse(state["ok"])
        self.assertIn("missing", state["error"])

    def test_malformed_json_is_reported_not_raised(self):
        self.seed("1.0.0", "1.0.0", "1.0.0", "1.0.0", "1.0.0")
        (self.root / ".codex-plugin" / "plugin.json").write_text("{not json")
        state = bump_version.read_state()
        self.assertTrue(state["ok"])
        codex = next(m for m in state["mirrors"] if "Codex" in m["label"])
        self.assertIsNotNone(codex["error"])


class SyncTests(ManifestFixture):
    def test_sync_propagates_canonical_to_all_mirrors(self):
        self.seed("0.36.8", "0.36.7", "0.36.7", "0.36.7", "0.36.7")
        changed = bump_version.apply_version("0.36.8", set_canonical=False)
        self.assertEqual(len(changed), 4)
        self.assertEqual(self.versions(), ("0.36.8",) * 5)

    def test_sync_is_idempotent(self):
        self.seed("2.0.0", "2.0.0", "2.0.0", "2.0.0", "2.0.0")
        self.assertEqual(bump_version.apply_version("2.0.0", set_canonical=False), [])

    def test_set_moves_canonical_too(self):
        self.seed("0.36.8", "0.36.7", "0.36.7", "0.36.7", "0.36.7")
        changed = bump_version.apply_version("0.37.0", set_canonical=True)
        self.assertEqual(len(changed), 5, "canonical + 4 mirrors")
        self.assertEqual(self.versions(), ("0.37.0",) * 5)

    def test_both_fields_in_one_marketplace_file_are_written(self):
        """marketplace.json carries TWO version fields; a per-file write must not clobber one."""
        self.seed("3.0.0", "3.0.0", "1.0.0", "2.0.0", "3.0.0")
        bump_version.apply_version("3.0.0", set_canonical=False)
        mk = json.loads((self.root / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(mk["metadata"]["version"], "3.0.0")
        self.assertEqual(mk["plugins"][0]["version"], "3.0.0")

    def test_sync_preserves_unrelated_keys(self):
        self.seed("1.1.0", "1.0.0", "1.0.0", "1.0.0", "1.0.0")
        p = self.root / ".codex-plugin" / "plugin.json"
        write(p, {"name": "build-loop", "version": "1.0.0", "description": "keep me", "keywords": ["a"]})
        bump_version.apply_version("1.1.0", set_canonical=False)
        after = json.loads(p.read_text())
        self.assertEqual(after["version"], "1.1.0")
        self.assertEqual(after["description"], "keep me")
        self.assertEqual(after["keywords"], ["a"])

    def test_written_files_keep_indent_and_trailing_newline(self):
        self.seed("1.1.0", "1.0.0", "1.0.0", "1.0.0", "1.0.0")
        bump_version.apply_version("1.1.0", set_canonical=False)
        text = (self.root / ".codex-plugin" / "plugin.json").read_text()
        self.assertTrue(text.endswith("\n"), "trailing newline keeps diffs clean")
        self.assertIn('\n  "version"', text, "2-space indent preserved")

    def test_missing_mirror_does_not_block_the_others(self):
        self.seed("5.0.0", "4.0.0", "4.0.0", "4.0.0", "4.0.0")
        (self.root / ".agents" / "plugins" / "marketplace.json").unlink()
        bump_version.apply_version("5.0.0", set_canonical=False)
        # Read the surviving manifests directly — `versions()` would trip on the deleted one.
        codex = json.loads((self.root / ".codex-plugin" / "plugin.json").read_text())["version"]
        mk = json.loads((self.root / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(codex, "5.0.0")
        self.assertEqual(mk["metadata"]["version"], "5.0.0")
        self.assertEqual(mk["plugins"][0]["version"], "5.0.0")


class ReleaseOperationTests(ManifestFixture):
    def test_next_version_uses_declared_kind(self):
        self.assertEqual(bump_version.next_version("1.2.3", "patch"), "1.2.4")
        self.assertEqual(bump_version.next_version("1.2.3", "minor"), "1.3.0")
        self.assertEqual(bump_version.next_version("1.2.3", "major"), "2.0.0")

    def test_tag_points_at_already_committed_head(self):
        self.seed("1.2.4", "1.2.4", "1.2.4", "1.2.4", "1.2.4")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "release 1.2.4"], check=True)
        head = subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()

        result = bump_version.create_tag("1.2.4")
        tagged = subprocess.check_output(
            ["git", "-C", str(self.root), "rev-list", "-n", "1", "v1.2.4"], text=True
        ).strip()
        self.assertEqual(result, {"tag": "v1.2.4", "created": True})
        self.assertEqual(tagged, head)
        self.assertEqual(
            bump_version.create_tag("1.2.4"),
            {"tag": "v1.2.4", "created": False, "reason": "already exists"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
