#!/usr/bin/env python3
"""Tests for the read-only repository closeout inventory."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("audit_repo_closeout.py")
SPEC = importlib.util.spec_from_file_location("audit_repo_closeout", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


class AuditRepoCloseoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo = Path(self.tempdir.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "closeout@example.test")
        git(self.repo, "config", "user.name", "Closeout Test")
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", "tracked.txt")
        git(self.repo, "commit", "-m", "base")

    def test_inventory_finds_unique_branch_worktree_and_stash(self) -> None:
        git(self.repo, "checkout", "-b", "feature")
        (self.repo / "feature.txt").write_text("feature\n", encoding="utf-8")
        git(self.repo, "add", "feature.txt")
        git(self.repo, "commit", "-m", "feature")
        git(self.repo, "checkout", "main")
        worktree = Path(self.tempdir.name) / "feature-worktree"
        git(self.repo, "worktree", "add", str(worktree), "feature")
        (self.repo / "untracked.txt").write_text("stash me\n", encoding="utf-8")
        git(self.repo, "stash", "push", "--include-untracked", "-m", "test stash")

        report = MODULE.audit(self.repo)

        self.assertEqual(report["base"], "main")
        self.assertEqual(len(report["worktrees"]), 2)
        self.assertEqual(len(report["stashes"]), 1)
        self.assertTrue(report["stashes"][0]["has_untracked_parent"])
        self.assertIn("feature", report["unmerged_candidates"])
        feature = next(item for item in report["branches"] if item["name"] == "feature")
        self.assertEqual(feature["ahead_base"], 1)
        self.assertFalse(feature["merged_into_base"])

    def test_merged_branch_becomes_removal_candidate(self) -> None:
        git(self.repo, "checkout", "-b", "feature")
        (self.repo / "feature.txt").write_text("feature\n", encoding="utf-8")
        git(self.repo, "add", "feature.txt")
        git(self.repo, "commit", "-m", "feature")
        git(self.repo, "checkout", "main")
        git(self.repo, "merge", "--ff-only", "feature")

        report = MODULE.audit(self.repo)

        self.assertIn("feature", report["merged_branch_candidates"])
        self.assertNotIn("feature", report["unmerged_candidates"])

    def test_artifact_inventory_protects_canonical_and_marks_stale_ignored_cache(self) -> None:
        (self.repo / ".gitignore").write_text("build*/\n", encoding="utf-8")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-m", "ignore builds")
        canonical = self.repo / "build"
        stale = self.repo / "build-old-lane"
        canonical.mkdir()
        stale.mkdir()
        (canonical / "current.bin").write_bytes(b"current")
        old_file = stale / "old.bin"
        old_file.write_bytes(b"old-cache")
        old = time.time() - (10 * 86_400)
        os.utime(old_file, (old, old))
        os.utime(stale, (old, old))

        report = MODULE.audit(
            self.repo,
            include_artifacts=True,
            protected_artifacts={"build"},
            stale_days=7,
        )

        artifacts = {item["path"]: item for item in report["artifacts"]["artifacts"]}
        self.assertEqual(artifacts["build"]["disposition"], "protected")
        self.assertFalse(artifacts["build"]["cleanup_candidate"])
        self.assertEqual(artifacts["build-old-lane"]["disposition"], "cleanup-candidate")
        self.assertTrue(artifacts["build-old-lane"]["ignored_by_git"])
        self.assertGreater(report["artifacts"]["cleanup_candidate_bytes"], 0)

    def test_source_comparison_matches_sibling_tree_to_target_prefix(self) -> None:
        sibling = Path(self.tempdir.name) / "source"
        sibling.mkdir()
        git(sibling, "init", "-b", "main")
        git(sibling, "config", "user.email", "source@example.test")
        git(sibling, "config", "user.name", "Source Test")
        (sibling / "lib.txt").write_text("shared\n", encoding="utf-8")
        git(sibling, "add", "lib.txt")
        git(sibling, "commit", "-m", "source")

        target = self.repo / "daemon" / "source"
        target.mkdir(parents=True)
        (target / "lib.txt").write_text("shared\n", encoding="utf-8")
        git(self.repo, "add", "daemon/source/lib.txt")
        git(self.repo, "commit", "-m", "import source")

        report = MODULE.audit(
            self.repo,
            compare_repo=sibling,
            compare_prefix="daemon/source",
        )

        comparison = report["source_comparison"]
        self.assertTrue(comparison["exact_tree_match"])
        self.assertEqual(comparison["matching_paths"], 1)
        self.assertEqual(comparison["changed_paths"], [])
        self.assertFalse(comparison["source_head_known_to_target_repo"])

    def test_profile_signals_detect_mixed_apple_rust_workspace(self) -> None:
        (self.repo / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["daemon/ptyd"]\n', encoding="utf-8"
        )
        daemon = self.repo / "daemon" / "ptyd"
        (daemon / "src").mkdir(parents=True)
        (daemon / "Cargo.toml").write_text(
            '[package]\nname = "ptyd"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (daemon / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        swift = self.repo / "Sources" / "App"
        swift.mkdir(parents=True)
        (swift / "App.swift").write_text("import SwiftUI\n", encoding="utf-8")
        (self.repo / "project.yml").write_text("name: TestApp\n", encoding="utf-8")
        (self.repo / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "add mixed stack")

        report = MODULE.audit(self.repo)
        profile = report["profile_signals"]

        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(profile["source_ref"], "main")
        self.assertTrue(
            {"Swift", "Rust"}.issubset({item["name"] for item in profile["languages"]})
        )
        self.assertTrue(
            {"cargo", "xcodegen", "xcodebuild", "shell-build"}.issubset(
                {item["name"] for item in profile["build_systems"]}
            )
        )
        self.assertTrue(
            {"apple-native", "service-or-daemon", "mixed-native-product"}.issubset(
                {item["name"] for item in profile["application_signals"]}
            )
        )
        self.assertTrue(
            {"workspace", "runtime-layout", "mixed-language"}.issubset(
                {item["name"] for item in profile["composition_signals"]}
            )
        )

    def test_profile_signals_detect_node_web_workspace(self) -> None:
        (self.repo / "app").mkdir()
        (self.repo / "app" / "page.tsx").write_text(
            "export default function Page() { return null }\n", encoding="utf-8"
        )
        (self.repo / "package.json").write_text(
            """{
  "private": true,
  "workspaces": ["packages/*"],
  "scripts": {"build": "next build"},
  "dependencies": {"next": "15.0.0", "react": "19.0.0"}
}
""",
            encoding="utf-8",
        )
        (self.repo / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "add web stack")

        profile = MODULE.audit(self.repo)["profile_signals"]

        self.assertIn("TypeScript", {item["name"] for item in profile["languages"]})
        self.assertTrue(
            {"nextjs", "node-scripts", "pnpm"}.issubset(
                {item["name"] for item in profile["build_systems"]}
            )
        )
        self.assertIn(
            "web-application", {item["name"] for item in profile["application_signals"]}
        )
        self.assertIn(
            "workspace", {item["name"] for item in profile["composition_signals"]}
        )
        self.assertEqual(profile["scan_summary"]["package_manifests_scanned"], 1)
        self.assertFalse(profile["scan_summary"]["package_manifest_scan_truncated"])

    def test_profile_signals_read_canonical_ref_not_uncommitted_files(self) -> None:
        (self.repo / "package.json").write_text(
            '{"dependencies": {"next": "15.0.0"}}\n', encoding="utf-8"
        )

        profile = MODULE.audit(self.repo)["profile_signals"]

        self.assertNotIn(
            "web-application", {item["name"] for item in profile["application_signals"]}
        )
        self.assertNotIn("nextjs", {item["name"] for item in profile["build_systems"]})

    def test_profile_signals_support_repository_without_commits(self) -> None:
        empty = Path(self.tempdir.name) / "empty"
        empty.mkdir()
        git(empty, "init", "-b", "main")

        report = MODULE.audit(empty)

        self.assertFalse(report["base_exists"])
        self.assertIsNone(report["profile_signals"]["source_ref"])
        self.assertEqual(report["profile_signals"]["languages"], [])
        self.assertEqual(report["profile_signals"]["scan_summary"]["tracked_paths"], 0)

    def test_profile_signals_cover_common_build_system_roles(self) -> None:
        files = {
            "AndroidManifest.xml": "<manifest />\n",
            "settings.gradle.kts": 'rootProject.name = "sample"\n',
            "pyproject.toml": "[build-system]\nrequires = []\n",
            "go.mod": "module example.test/sample\n",
            "pom.xml": "<project />\n",
            "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\n",
            "Dockerfile": "FROM scratch\n",
            "main.tf": "terraform {}\n",
            "Sample.csproj": "<Project />\n",
            ".github/workflows/ci.yml": "name: CI\n",
            "src/main.kt": "fun main() {}\n",
            "src/main.py": "print('ok')\n",
            "src/main.go": "package main\n",
            "src/main.cs": "class Program {}\n",
            "src/main.cpp": "int main() { return 0; }\n",
        }
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "add polyglot build fixtures")

        profile = MODULE.audit(self.repo)["profile_signals"]
        build_names = {item["name"] for item in profile["build_systems"]}

        self.assertTrue(
            {
                "gradle",
                "python-build",
                "go",
                "maven",
                "cmake",
                "docker",
                "terraform",
                "msbuild",
                "github-actions",
            }.issubset(build_names)
        )
        self.assertIn(
            "android-native", {item["name"] for item in profile["application_signals"]}
        )
        self.assertIn(
            "infrastructure", {item["name"] for item in profile["application_signals"]}
        )


if __name__ == "__main__":
    unittest.main()
