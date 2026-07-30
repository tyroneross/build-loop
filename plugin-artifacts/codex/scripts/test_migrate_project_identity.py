#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the v2 registry migration doctor.

Runs entirely against a FIXTURE store in a tempdir — never the real store.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import migrate_project_identity as mig  # type: ignore  # noqa: E402
import project_registry as pr  # type: ignore  # noqa: E402


def _make_fixture_store(root: Path) -> None:
    """A v1 store with folders, a v1 projects.yaml, and a pinned repo."""
    projects = root / "projects"
    for slug in ("build-loop", "rosslabs-ai-assistant", "agent-astronomer", "_unscoped"):
        (projects / slug / "lessons").mkdir(parents=True, exist_ok=True)
    (projects / "rosslabs-ai-assistant" / "lessons" / "l.md").write_text("x", encoding="utf-8")
    (projects / "README.md").write_text("not a project", encoding="utf-8")

    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "projects.yaml").write_text(
        "default: _unscoped\n"
        "projects:\n"
        "  - path: ~/dev/git-folder/build-loop\n"
        "    project: build-loop\n",
        encoding="utf-8",
    )


def _make_pinned_repo(scan_root: Path, dirname: str, pin: str) -> Path:
    repo = scan_root / dirname
    (repo / ".build-loop").mkdir(parents=True, exist_ok=True)
    (repo / ".build-loop" / "config.json").write_text(
        json.dumps({"memoryProjectSlug": pin}), encoding="utf-8")
    return repo


class MigrationPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "store"
        self.scan = Path(self.tmp) / "repos"
        _make_fixture_store(self.store)
        self.scan.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_folders_frozen_as_ids(self) -> None:
        target, actions = mig.plan_migration(self.store, self.scan)
        ids = {p["id"] for p in target["projects"]}
        self.assertIn("build-loop", ids)
        self.assertIn("rosslabs-ai-assistant", ids)
        self.assertIn("agent-astronomer", ids)
        # Non-project entries excluded.
        self.assertNotIn("_unscoped", ids)
        self.assertNotIn("README.md", ids)

    def test_known_rename_seeded(self) -> None:
        target, _ = mig.plan_migration(self.store, self.scan)
        canon = next(p for p in target["projects"] if p["id"] == "rosslabs-ai-assistant")
        self.assertIn("ai-assistant", canon["aliases"])
        # No standalone ai-assistant node.
        self.assertNotIn("ai-assistant", {p["id"] for p in target["projects"]})

    def test_existing_v1_path_preserved(self) -> None:
        target, _ = mig.plan_migration(self.store, self.scan)
        bl = next(p for p in target["projects"] if p["id"] == "build-loop")
        self.assertEqual(bl["paths"], ["~/dev/git-folder/build-loop"])

    def test_pin_converted_to_path_and_alias(self) -> None:
        # A repo whose dirname derives to 'legacy-name' but pins to 'build-loop'.
        _make_pinned_repo(self.scan, "Legacy-Name", "build-loop")
        target, actions = mig.plan_migration(self.store, self.scan)
        bl = next(p for p in target["projects"] if p["id"] == "build-loop")
        # dirname 'Legacy-Name' -> 'legacy-name' becomes an alias of the pin.
        self.assertIn("legacy-name", bl["aliases"])
        self.assertTrue(any("Legacy-Name" in a for a in actions))

    def test_pin_equal_to_dirname_adds_no_alias(self) -> None:
        # dirname derives to exactly the pinned slug → path only, no alias.
        _make_pinned_repo(self.scan, "rosslabs-ai-assistant", "rosslabs-ai-assistant")
        target, _ = mig.plan_migration(self.store, self.scan)
        canon = next(p for p in target["projects"] if p["id"] == "rosslabs-ai-assistant")
        # Only the known-rename alias, no dirname alias.
        self.assertEqual(canon["aliases"], ["ai-assistant"])

    def test_idempotent(self) -> None:
        # Apply once, then a second plan yields "no changes".
        target1, _ = mig.plan_migration(self.store, self.scan)
        pr.write_registry(target1, self.store / "config" / "projects.yaml")
        _target2, actions2 = mig.plan_migration(self.store, self.scan)
        self.assertIn("no changes — registry already migrated", actions2)

    def test_dry_run_writes_nothing(self) -> None:
        before = (self.store / "config" / "projects.yaml").read_text(encoding="utf-8")
        rc = mig.main(["--dry-run", "--store-root", str(self.store),
                       "--repo-scan-root", str(self.scan)])
        self.assertEqual(rc, 0)
        after = (self.store / "config" / "projects.yaml").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_apply_writes_v2(self) -> None:
        rc = mig.main(["--apply", "--store-root", str(self.store),
                       "--repo-scan-root", str(self.scan)])
        self.assertEqual(rc, 0)
        reg = pr.load_registry(self.store / "config" / "projects.yaml")
        canon = next(p for p in reg["projects"] if p["id"] == "rosslabs-ai-assistant")
        self.assertIn("ai-assistant", canon["aliases"])
        # Alias resolves after apply.
        self.assertEqual(pr.resolve("ai-assistant", None, reg), "rosslabs-ai-assistant")

    def test_folders_never_move(self) -> None:
        before = sorted(p.name for p in (self.store / "projects").iterdir())
        mig.main(["--apply", "--store-root", str(self.store),
                  "--repo-scan-root", str(self.scan)])
        after = sorted(p.name for p in (self.store / "projects").iterdir())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
