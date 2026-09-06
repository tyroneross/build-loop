#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/reference_pointer_lint.py."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SCRIPT = HERE / "reference_pointer_lint.py"

sys.path.insert(0, str(HERE))
import reference_pointer_lint as lint  # noqa: E402


def _make_repo(root: Path) -> None:
    """Minimal tree with one resolving pointer on the primary surface."""
    (root / "skills" / "build-loop" / "references").mkdir(parents=True)
    (root / "codex-skills" / "build-loop").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "skills" / "build-loop" / "references" / "phase-2-plan.md").write_text("plan\n")
    (root / "skills" / "build-loop" / "SKILL.md").write_text(
        "See `references/phase-2-plan.md` for detail.\n"
    )
    (root / "codex-skills" / "build-loop" / "SKILL.md").write_text("wrapper\n")
    (root / "AGENTS.md").write_text("Read `references/phase-2-plan.md`.\n")
    (root / "README.md").write_text("no pointers here\n")


class ReferencePointerLintTest(unittest.TestCase):
    def test_repo_tree_has_no_dangling_primary_surface_pointers(self) -> None:
        self.assertEqual([], lint.dangling_reference_pointers(REPO_ROOT))

    def test_cli_exits_zero_on_the_repo_tree(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(REPO_ROOT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_resolves_across_every_reference_source_dir(self) -> None:
        """A pointer resolves no matter which of the four search dirs holds it."""
        for rel_dir in lint.REFERENCE_SOURCE_DIRS:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_repo(root)
                (root / rel_dir).mkdir(parents=True, exist_ok=True)
                (root / rel_dir / "only-here.md").write_text("body\n")
                (root / "AGENTS.md").write_text("Read `references/only-here.md`.\n")
                self.assertEqual(
                    [],
                    lint.dangling_reference_pointers(root),
                    f"pointer should resolve via {rel_dir}",
                )

    def test_planted_dangling_pointer_fails(self) -> None:
        """The lint's own conviction test: plant a dead pointer, expect exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            (root / "skills" / "build-loop" / "SKILL.md").write_text(
                "See `references/this-file-does-not-exist.md` for detail.\n"
            )
            self.assertEqual(
                ["this-file-does-not-exist.md"],
                lint.dangling_reference_pointers(root),
            )
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, proc.returncode)
            self.assertIn("this-file-does-not-exist.md", proc.stderr)

    def test_allowlists_suppress_a_dangling_pointer(self) -> None:
        for basename in ("anti-patterns.md", "brief-filters.md"):
            with self.subTest(basename=basename):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _make_repo(root)
                    (root / "AGENTS.md").write_text(f"The skill's `references/{basename}`.\n")
                    self.assertEqual([], lint.dangling_reference_pointers(root))

    def test_pointer_outside_the_primary_surface_is_not_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            (root / "docs").mkdir()
            (root / "docs" / "note.md").write_text("`references/nowhere.md`\n")
            self.assertEqual([], lint.dangling_reference_pointers(root))


if __name__ == "__main__":
    unittest.main()
