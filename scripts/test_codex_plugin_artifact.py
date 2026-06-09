#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the generated Codex marketplace artifact."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SCRIPT = HERE / "build_codex_plugin_artifact.py"
ARTIFACT = REPO_ROOT / "plugin-artifacts" / "codex"


class CodexPluginArtifactTests(unittest.TestCase):
    def test_checked_in_artifact_is_current(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source",
                str(REPO_ROOT),
                "--target",
                str(ARTIFACT),
                "--check",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

    def test_builder_outputs_one_visible_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            target = Path(tmp_raw) / "codex"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--source",
                    str(REPO_ROOT),
                    "--target",
                    str(target),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            skill_paths = sorted(str(path.relative_to(target)) for path in target.rglob("SKILL.md"))
            self.assertEqual(skill_paths, ["skills/build-loop/SKILL.md"])
            manifest = target / ".codex-plugin" / "plugin.json"
            self.assertIn('"skills": "./skills"', manifest.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
