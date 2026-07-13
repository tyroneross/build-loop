#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the generated Codex marketplace artifact."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SCRIPT = HERE / "build_codex_plugin_artifact.py"
ARTIFACT = REPO_ROOT / "plugin-artifacts" / "codex"
ICON_REL = Path("assets") / "build-loop-plugin-icon.png"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_codex_plugin_artifact", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_builder_outputs_approved_public_skills(self) -> None:
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
            self.assertEqual(
                skill_paths,
                ["skills/build-loop/SKILL.md", "skills/repo-closeout/SKILL.md"],
            )
            manifest = target / ".codex-plugin" / "plugin.json"
            self.assertIn('"skills": "./skills"', manifest.read_text(encoding="utf-8"))
            self.assertTrue((target / ICON_REL).is_file())
            self.assertTrue((target / "skills" / "repo-closeout" / "scripts" / "audit_repo_closeout.py").is_file())
            self.assertFalse((target / "skills" / "repo-closeout" / "scripts" / "test_audit_repo_closeout.py").exists())

    def test_checked_in_artifact_includes_plugin_icon(self) -> None:
        icon = ARTIFACT / ICON_REL
        self.assertTrue(icon.is_file(), "Codex artifact must ship the plugin icon")
        header = icon.read_bytes()[:24]
        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        self.assertEqual((width, height), (1024, 1024))

    def test_checked_in_artifact_reference_pointers_resolve(self) -> None:
        """Every ``references/X.md`` pointer on the shipped bundle's primary
        surface (AGENTS.md / README.md / the build-loop skill tree) must resolve
        to a file under the bundle's top-level ``references/``. Guards against
        the dangling-reference regression (codex-bundle-missing-references-dir)
        and catches a stale or hand-edited artifact directly, not just the
        builder. Foreign-skill prose refs and known source TBDs are allowlisted
        in the builder module and excluded here too.
        """
        builder = _load_builder()
        # Raises ArtifactError on any dangling primary-surface pointer.
        builder.check_reference_pointers(ARTIFACT)

        # Positive assertion: the issue's named example resolves.
        self.assertTrue(
            (ARTIFACT / "references" / "research-trigger-policy.md").is_file(),
            "research-trigger-policy.md must be mirrored into the bundle references/",
        )

    def test_codex_artifact_documents_cross_repo_apply_patch_guard(self) -> None:
        text = (ARTIFACT / "AGENTS.md").read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for needle in (
            "apply_patch",
            "relative patch paths target the active workspace",
            "absolute `apply_patch` paths",
            "pointer, mirror, or stub at the old path",
        ):
            self.assertIn(needle, normalized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
