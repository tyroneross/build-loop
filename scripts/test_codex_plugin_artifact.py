#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the generated Codex marketplace artifact."""
from __future__ import annotations

import importlib.util
import json
import re
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
PLUGIN_ROOT_RE = re.compile(
    r"\$\{CLAUDE_PLUGIN_ROOT(?::-\$CLAUDE_PROJECT_DIR)?\}/"
    r"([A-Za-z0-9_./-]+)"
)


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

    def test_builder_outputs_one_public_entry_and_full_runtime(self) -> None:
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
            skill_paths = sorted(
                str(path.relative_to(target))
                for path in (target / "codex-skills").rglob("SKILL.md")
            )
            self.assertEqual(
                skill_paths,
                [
                    "codex-skills/build-loop/SKILL.md",
                ],
            )
            manifest = json.loads(
                (target / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest.get("skills"), "./codex-skills")
            self.assertTrue((target / ICON_REL).is_file())
            for rel_path in (
                ".agents/plugins/marketplace.json",
                ".claude-plugin/plugin.json",
                "agents/build-orchestrator.md",
                "commands/run.md",
                "hooks/hooks.json",
                "scripts/self_mod_verify.py",
                "scripts/groundwork_exchange.py",
                "scripts/test_self_mod_verify.py",
                "skills/build-loop/SKILL.md",
                "skills/debug-loop/SKILL.md",
                "src/build_loop/__init__.py",
                "skills/build-loop/templates/codex-worker-prompt.md",
            ):
                self.assertTrue((target / rel_path).is_file(), rel_path)

            wrapper = target / "codex-skills" / "build-loop" / "SKILL.md"
            canonical = wrapper.parent / ".." / ".." / "skills" / "build-loop" / "SKILL.md"
            self.assertTrue(canonical.resolve().is_file())
            self.assertIn("user-invocable: true", wrapper.read_text(encoding="utf-8"))
            attributes = (target / ".gitattributes").read_text(encoding="utf-8")
            self.assertIn("* -whitespace", attributes)
            self.assertIn("* conflict-marker-size=64", attributes)

    def test_builder_copies_every_runtime_directory_file(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as tmp_raw:
            target = Path(tmp_raw) / "codex"
            builder.build_artifact(REPO_ROOT, target)
            for directory in builder.RUNTIME_DIRS:
                source_root = REPO_ROOT / directory
                target_root = target / directory
                source_files = {
                    path.relative_to(source_root)
                    for path in builder.iter_files(source_root)
                    if not builder.is_ignored_source_path(path, source_root)
                }
                target_files = {
                    path.relative_to(target_root)
                    for path in builder.iter_files(target_root)
                }
                if directory == "references":
                    self.assertTrue(
                        source_files <= target_files,
                        "references must include every source file; mirrored "
                        "skill references may add compatibility copies",
                    )
                else:
                    self.assertEqual(source_files, target_files, directory)

    @staticmethod
    def _make_swift_build_output_fixture(root: Path) -> Path:
        """A source tree shaped like ``skills/native-ax-driver/swift/``.

        Reproduces the real layout without needing anything compiled on this
        machine: a package with one real source file plus a SwiftPM ``.build/``
        tree whose ``release`` entry is a SYMLINK to the arch-specific release
        dir. Following that symlink is what let the builder copy paths the
        source scan never saw.
        """
        source = root / "source"
        package = source / "swift" / "bl-ax-driver"
        (package / "Sources" / "bl-ax-driver").mkdir(parents=True)
        (package / "Sources" / "bl-ax-driver" / "main.swift").write_text(
            "// real source\n", encoding="utf-8"
        )
        (package / "Package.swift").write_text("// manifest\n", encoding="utf-8")
        module_cache = package / ".build" / "arm64-apple-macosx" / "release" / "ModuleCache"
        module_cache.mkdir(parents=True)
        (module_cache / "Darwin-1FXX23EKWOBA9.pcm").write_bytes(b"\x00" * 64)
        (package / ".build" / "release").symlink_to(
            Path("arm64-apple-macosx") / "release", target_is_directory=True
        )
        return source

    def test_builder_excludes_swift_build_output_behind_a_symlink(self) -> None:
        """Regression: ``.build/`` output must never reach the artifact.

        Fails on the pre-fix builder two ways — ``.build`` was absent from the
        ignore set, and ``copy_tree`` followed the ``release`` symlink, copying
        paths the ignore-filtered source scan never enumerated.
        """
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            source = self._make_swift_build_output_fixture(tmp)
            target = tmp / "artifact"
            builder.copy_tree(source, target)

            copied = sorted(str(path.relative_to(target)) for path in builder.iter_files(target))
            self.assertEqual(
                copied,
                [
                    "swift/bl-ax-driver/Package.swift",
                    "swift/bl-ax-driver/Sources/bl-ax-driver/main.swift",
                ],
            )

            # The invariant the shipped suite asserts per runtime dir: the
            # ignore-filtered source set and the copied set must agree.
            scanned = {
                path.relative_to(source)
                for path in builder.iter_files(source)
                if not builder.is_ignored_source_path(path, source)
            }
            self.assertEqual(scanned, {path.relative_to(target) for path in builder.iter_files(target)})

    def test_builder_does_not_absorb_symlink_targets_outside_the_source_tree(self) -> None:
        """A symlink must not pull unreviewed, un-ignore-filtered content into the bundle."""
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            outside = tmp / "outside"
            outside.mkdir()
            (outside / "not-shippable.txt").write_text("outside the source tree\n", encoding="utf-8")

            source = tmp / "source"
            (source / "docs").mkdir(parents=True)
            (source / "docs" / "real.md").write_text("# real\n", encoding="utf-8")
            (source / "linked-dir").symlink_to(outside, target_is_directory=True)
            (source / "linked-file.txt").symlink_to(outside / "not-shippable.txt")

            target = tmp / "artifact"
            builder.copy_tree(source, target)

            copied = sorted(str(path.relative_to(target)) for path in builder.iter_files(target))
            self.assertEqual(copied, ["docs/real.md"])
            self.assertFalse((target / "linked-dir").exists())
            self.assertFalse((target / "linked-file.txt").exists())

    def test_packaged_hook_plugin_root_paths_resolve(self) -> None:
        hooks_text = (ARTIFACT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        refs = sorted(set(PLUGIN_ROOT_RE.findall(hooks_text)))
        self.assertGreater(len(refs), 0)
        missing = [ref for ref in refs if not (ARTIFACT / ref).exists()]
        self.assertEqual(missing, [])

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
        surface (AGENTS.md / README.md / the public wrapper and canonical
        build-loop skill tree) must resolve
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
