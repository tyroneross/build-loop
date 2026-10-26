#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the public build-loop-memory seed installer path."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import install_memory  # noqa: E402


def _write_seed_manifest(seed_dir: Path, *, sources: list[str], patterns: list[str] | None = None) -> None:
    manifest = {
        "schema_version": "1.0.0",
        "kind": "build-loop-memory-public-seed",
        "seed_version": "test",
        "sources": [{"source": source, "target": source.removesuffix(".template")} for source in sources],
        "privacy": {
            "classification": "scaffolding-only",
            "deny_patterns": patterns or ["(?i)tyroneross", "/Users/[^/\\s`]+"],
        },
    }
    (seed_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class InstallMemoryPublicSeedTests(unittest.TestCase):
    def test_package_manifest_includes_public_seed_and_guide(self) -> None:
        package = json.loads(subprocess.check_output(
            ["npm", "pack", "--dry-run", "--ignore-scripts", "--json"], cwd=HERE.parent))[0]
        files = {entry["path"] for entry in package["files"]}

        self.assertIn("scripts/install_memory.py", files)
        self.assertIn("templates/memory/manifest.json", files)
        self.assertIn("docs/memory-setup.md", files)

    def test_packaged_public_seed_validates(self) -> None:
        result = install_memory.validate_public_seed()

        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["kind"], "build-loop-memory-public-seed-validation")
        self.assertEqual(result["privacy_classification"], "scaffolding-only")
        self.assertEqual(
            sorted(result["files"]),
            ["MEMORY.md.template", "README.md", "charter.md.template", "constitution.md.template"],
        )

    def test_seed_validation_rejects_unlisted_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed_dir = Path(tmp)
            _write_seed_manifest(seed_dir, sources=["allowed.md.template"])
            (seed_dir / "allowed.md.template").write_text("# allowed\n", encoding="utf-8")
            (seed_dir / "personal.md").write_text("private note\n", encoding="utf-8")

            result = install_memory.validate_public_seed(seed_dir)

        self.assertFalse(result["ok"])
        self.assertIn("seed file not allowlisted in manifest: personal.md", result["issues"])

    def test_seed_validation_rejects_missing_or_escaping_manifest_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed_dir = Path(tmp)
            _write_seed_manifest(seed_dir, sources=["missing.md.template", "../outside.md.template"])

            result = install_memory.validate_public_seed(seed_dir)

        self.assertFalse(result["ok"])
        self.assertIn("allowlisted seed file missing: missing.md.template", result["issues"])
        self.assertIn("seed manifest source must stay inside template dir: ../outside.md.template", result["issues"])

    def test_seed_validation_rejects_personal_or_local_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed_dir = Path(tmp)
            _write_seed_manifest(seed_dir, sources=["allowed.md.template"])
            (seed_dir / "allowed.md.template").write_text(
                "operator path: /Users/example/private-memory\n",
                encoding="utf-8",
            )

            result = install_memory.validate_public_seed(seed_dir)

        self.assertFalse(result["ok"])
        self.assertTrue(any(issue.startswith("privacy deny pattern matched allowed.md.template") for issue in result["issues"]))

    def test_guided_install_writes_scaffold_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "build-loop-memory"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = install_memory.main(["--dest", str(dest), "--guided"])

            self.assertEqual(code, 0)
            self.assertTrue((dest / "constitution.md").is_file())
            self.assertTrue((dest / "MEMORY.md").is_file())
            self.assertTrue((dest / "indexes").is_dir())
            self.assertTrue((dest / "projects" / "README.md").is_file())
            written = "\n".join(
                path.read_text(encoding="utf-8")
                for path in [dest / "constitution.md", dest / "MEMORY.md", dest / "projects" / "README.md"]
            )
            self.assertNotIn("tyroneross", written.lower())
            self.assertIn("packaged public seed only", stdout.getvalue())

    def test_check_json_includes_public_seed_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "build-loop-memory"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = install_memory.main(["--dest", str(dest), "--check", "--json"])

            self.assertEqual(code, 0)
            status = json.loads(stdout.getvalue())
            self.assertFalse(status["exists"])
            self.assertTrue(status["public_seed"]["ok"])
            self.assertEqual(status["public_seed"]["seed_version"], "2026-06-12")
            self.assertFalse(status["files"]["projects/README.md"]["exists"])

    def test_interrupted_template_copy_is_retried_without_partial_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, dest = root / "seed.md", root / "memory.md"
            src.write_text("complete public seed\n", encoding="utf-8")

            def interrupted_copy(_src: Path, temp: Path) -> None:
                temp.write_text("partial", encoding="utf-8")
                raise OSError("simulated interrupted copy")

            with mock.patch.object(install_memory.shutil, "copy2", side_effect=interrupted_copy):
                with self.assertRaisesRegex(OSError, "interrupted copy"):
                    install_memory.seed_template(src, dest)

            self.assertFalse(dest.exists())
            self.assertEqual(list(root.glob(".memory.md.seed-*")), [])
            self.assertEqual(install_memory.seed_template(src, dest), (True, "seeded"))
            self.assertEqual(dest.read_text(encoding="utf-8"), "complete public seed\n")

    def test_concurrent_user_file_wins_no_overwrite_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, dest = root / "seed.md", root / "memory.md"
            src.write_text("public seed\n", encoding="utf-8")
            real_copy = install_memory.shutil.copy2

            def user_writes_during_copy(source: Path, temp: Path) -> None:
                real_copy(source, temp)
                dest.write_text("private user content\n", encoding="utf-8")

            with mock.patch.object(install_memory.shutil, "copy2", side_effect=user_writes_during_copy):
                self.assertEqual(install_memory.seed_template(src, dest), (False, "already exists"))
            self.assertEqual(dest.read_text(encoding="utf-8"), "private user content\n")
            self.assertEqual(list(root.glob(".memory.md.seed-*")), [])

    def test_existing_target_skips_before_temp_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, dest = root / "seed.md", root / "memory.md"
            src.write_text("public seed\n", encoding="utf-8")
            dest.write_text("private user content\n", encoding="utf-8")
            with mock.patch.object(install_memory.tempfile, "mkstemp") as make_temp:
                with mock.patch.object(install_memory.shutil, "copy2") as copy:
                    self.assertEqual(install_memory.seed_template(src, dest), (False, "already exists"))
            make_temp.assert_not_called()
            copy.assert_not_called()
            self.assertEqual(dest.read_text(encoding="utf-8"), "private user content\n")

    def test_projects_readme_uses_same_atomic_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "README.md"

            def interrupted_write(temp: Path) -> None:
                temp.write_text("partial", encoding="utf-8")
                raise OSError("simulated interrupted write")

            with self.assertRaisesRegex(OSError, "interrupted write"):
                install_memory._publish_seed(dest, interrupted_write)
            self.assertFalse(dest.exists())
            self.assertEqual(list(root.glob(".README.md.seed-*")), [])

            self.assertEqual(
                install_memory._publish_seed(dest, lambda temp: temp.write_text("public README\n", encoding="utf-8")),
                (True, "seeded"),
            )
            self.assertEqual(dest.read_text(encoding="utf-8"), "public README\n")
            self.assertEqual(
                install_memory._publish_seed(dest, lambda temp: temp.write_text("replacement\n", encoding="utf-8")),
                (False, "already exists"),
            )
            self.assertEqual(dest.read_text(encoding="utf-8"), "public README\n")

    def test_rerun_restores_missing_readme_without_overwriting_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "memory-store"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(install_memory.main(["--dest", str(dest)]), 0)
            personal = dest / "MEMORY.md"
            personal.write_text("private user content\n", encoding="utf-8")
            readme = dest / "projects" / "README.md"
            readme.unlink()
            self.assertFalse(install_memory.report(dest)["files"]["projects/README.md"]["exists"])

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(install_memory.main(["--dest", str(dest)]), 0)
            self.assertTrue(readme.is_file())
            self.assertEqual(personal.read_text(encoding="utf-8"), "private user content\n")

            readme.write_text("private README\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(install_memory.main(["--dest", str(dest), "--force"]), 0)
            self.assertEqual(readme.read_text(encoding="utf-8"), "private README\n")


if __name__ == "__main__":
    unittest.main()
