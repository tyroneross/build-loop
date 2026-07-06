#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for verify_release_surface.py.

Stdlib only. Run: python3 scripts/test_verify_release_surface.py

Most checks operate on a temp-dir mini-repo + a stub manifest layout. The
remote_refs check requires network — we use a unittest.mock to stub the
subprocess for it. fresh_session_load uses an in-temp-dir cache layout
to avoid touching ~/.claude/plugins/.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verify_release_surface as vrs  # noqa: E402


def _init_repo(workdir: Path, version: str = "0.12.8") -> None:
    """Create a minimal repo with manifests, manifest test, commits, branch, tag."""
    # Manifests.
    (workdir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (workdir / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (workdir / ".agents" / "plugins").mkdir(parents=True, exist_ok=True)
    (workdir / "plugin-artifacts" / "codex" / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (workdir / "plugin-artifacts" / "codex").mkdir(parents=True, exist_ok=True)
    (workdir / "package.json").write_text(json.dumps({
        "name": "@test/plugin",
        "version": version,
    }), encoding="utf-8")
    (workdir / "package-lock.json").write_text(json.dumps({
        "name": "@test/plugin",
        "version": version,
        "lockfileVersion": 3,
        "packages": {"": {"name": "@test/plugin", "version": version}},
    }), encoding="utf-8")
    (workdir / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": "test-plugin",
        "version": version,
        "description": "x" * 50,
        "author": {"name": "Test Author"},
    }), encoding="utf-8")
    (workdir / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": "test-plugin",
        "version": version,
    }), encoding="utf-8")
    (workdir / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "metadata": {"version": version},
        "plugins": [{"name": "test-plugin", "version": version}],
    }), encoding="utf-8")
    (workdir / ".agents" / "plugins" / "marketplace.json").write_text(json.dumps({
        "name": "test-plugin",
        "version": version,
        "plugins": [{"name": "test-plugin", "source": "./plugin-artifacts/codex"}],
    }), encoding="utf-8")
    (workdir / "plugin-artifacts" / "codex" / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": "test-plugin",
        "version": version,
    }), encoding="utf-8")
    readme_text = (
        f"npm install -g @tyroneross/build-loop@{version}\n"
        f"python3 scripts/verify_release_surface.py --version v{version} --branch main --remote origin --json\n"
    )
    (workdir / "README.md").write_text(readme_text, encoding="utf-8")
    (workdir / "plugin-artifacts" / "codex" / "README.md").write_text(readme_text, encoding="utf-8")
    # Manifest test that exits 0.
    (workdir / "scripts").mkdir(exist_ok=True)
    (workdir / "scripts" / "test_plugin_manifest.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8",
    )
    (workdir / "scripts" / "build_codex_plugin_artifact.py").write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8",
    )
    # Init git repo.
    env = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t"}
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", f"v{version} initial"],
    ):
        subprocess.run(cmd, cwd=workdir, check=True, env={**env}, capture_output=True)


class CheckManifestVersionsTests(unittest.TestCase):
    def test_all_match(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            r = vrs.check_manifest_versions(wd, "0.12.8")
            self.assertTrue(r["pass"], r)

    def test_marketplace_drift_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            # Inject drift: marketplace.json still shows 0.12.7
            mp = wd / ".claude-plugin" / "marketplace.json"
            mp.write_text(json.dumps({
                "metadata": {"version": "0.12.7"},
                "plugins": [{"name": "test-plugin", "version": "0.12.7"}],
            }), encoding="utf-8")
            r = vrs.check_manifest_versions(wd, "0.12.8")
            self.assertFalse(r["pass"])
            # Both marketplace fields surface as fail findings.
            fails = [f for f in r["findings"] if f.get("status") == "fail"]
            self.assertEqual(len(fails), 2, f"expected 2 fails, got: {r['findings']}")

    def test_package_lock_drift_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            lock = json.loads((wd / "package-lock.json").read_text(encoding="utf-8"))
            lock["version"] = "0.12.7"
            lock["packages"][""]["version"] = "0.12.7"
            (wd / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
            r = vrs.check_manifest_versions(wd, "0.12.8")
            self.assertFalse(r["pass"])
            fails = [f for f in r["findings"] if f.get("file") == "package-lock.json" and f.get("status") == "fail"]
            self.assertEqual(len(fails), 2, f"expected package-lock root + package drift, got: {r['findings']}")

    def test_agents_marketplace_drift_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            market = json.loads((wd / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
            market["version"] = "0.12.7"
            (wd / ".agents" / "plugins" / "marketplace.json").write_text(json.dumps(market), encoding="utf-8")
            r = vrs.check_manifest_versions(wd, "0.12.8")
            self.assertFalse(r["pass"])
            fails = [f for f in r["findings"] if f.get("file") == ".agents/plugins/marketplace.json" and f.get("status") == "fail"]
            self.assertEqual(len(fails), 1, f"expected agents marketplace version drift, got: {r['findings']}")

    def test_codex_artifact_manifest_drift_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            artifact_manifest = wd / "plugin-artifacts" / "codex" / ".codex-plugin" / "plugin.json"
            data = json.loads(artifact_manifest.read_text(encoding="utf-8"))
            data["version"] = "0.12.7"
            artifact_manifest.write_text(json.dumps(data), encoding="utf-8")
            r = vrs.check_manifest_versions(wd, "0.12.8")
            self.assertFalse(r["pass"])
            fails = [f for f in r["findings"] if f.get("file") == "plugin-artifacts/codex/.codex-plugin/plugin.json" and f.get("status") == "fail"]
            self.assertEqual(len(fails), 1, f"expected Codex artifact manifest drift, got: {r['findings']}")

    def test_v_prefix_normalized(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            r = vrs.check_manifest_versions(wd, "v0.12.8")
            self.assertTrue(r["pass"])


class CheckReadmeVersionsTests(unittest.TestCase):
    def test_all_match(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            r = vrs.check_readme_versions(wd, "0.12.8")
            self.assertTrue(r["pass"], r)

    def test_stale_install_command_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            (wd / "README.md").write_text(
                "npm install -g @tyroneross/build-loop@0.12.7\n",
                encoding="utf-8",
            )
            r = vrs.check_readme_versions(wd, "0.12.8")
            self.assertFalse(r["pass"])
            fails = [f for f in r["findings"] if f.get("status") == "fail"]
            self.assertEqual(len(fails), 1, r)
            self.assertEqual(fails[0]["pattern"], "npm_build_loop_install")

    def test_stale_release_surface_example_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            artifact_readme = wd / "plugin-artifacts" / "codex" / "README.md"
            artifact_readme.write_text(
                "python3 scripts/verify_release_surface.py --version v0.12.7 --branch main --json\n",
                encoding="utf-8",
            )
            r = vrs.check_readme_versions(wd, "0.12.8")
            self.assertFalse(r["pass"])
            fails = [f for f in r["findings"] if f.get("status") == "fail"]
            self.assertEqual(len(fails), 1, r)
            self.assertEqual(fails[0]["pattern"], "release_surface_version_arg")


class CheckManifestTestTests(unittest.TestCase):
    def test_exit_zero_passes(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            r = vrs.check_manifest_test(wd)
            self.assertTrue(r["pass"])

    def test_exit_nonzero_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            (wd / "scripts" / "test_plugin_manifest.py").write_text(
                "import sys; sys.exit(1)\n", encoding="utf-8",
            )
            r = vrs.check_manifest_test(wd)
            self.assertFalse(r["pass"])


class CheckCodexArtifactCurrentTests(unittest.TestCase):
    def test_no_builder_or_artifact_skips(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            r = vrs.check_codex_artifact_current(wd)
            self.assertTrue(r["pass"], r)
            self.assertEqual(r["findings"][0]["status"], "skipped")

    def test_builder_failure_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            script = wd / "scripts" / "build_codex_plugin_artifact.py"
            artifact = wd / "plugin-artifacts" / "codex"
            script.parent.mkdir(parents=True)
            artifact.mkdir(parents=True)
            script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
            r = vrs.check_codex_artifact_current(wd)
            self.assertFalse(r["pass"], r)
            self.assertEqual(r["findings"][0]["exit_code"], 1)


class CheckLocalCommitLogTests(unittest.TestCase):
    def test_finds_versioned_commit(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            r = vrs.check_local_commit_log(wd, "main", "0.12.8")
            self.assertTrue(r["pass"])

    def test_misses_unversioned_commit(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            r = vrs.check_local_commit_log(wd, "main", "0.13.0")
            self.assertFalse(r["pass"])


class CheckLocalTagTests(unittest.TestCase):
    def test_tag_present(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            subprocess.run(["git", "tag", "v0.12.8"], cwd=wd, check=True, capture_output=True)
            r = vrs.check_local_tag(wd, "v0.12.8")
            self.assertTrue(r["pass"])

    def test_tag_absent(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            r = vrs.check_local_tag(wd, "v0.99.0")
            self.assertFalse(r["pass"])


class CheckBranchHeadShaTests(unittest.TestCase):
    def test_branch_head_matches_tag(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            subprocess.run(["git", "tag", "v0.12.8"], cwd=wd, check=True, capture_output=True)
            r = vrs.check_branch_head_sha(wd, "main", "v0.12.8")
            self.assertTrue(r["pass"], r)


class CheckRemoteRefsTests(unittest.TestCase):
    def test_same_sha_passes(self):
        sha = "a" * 40
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = f"{sha}\trefs/heads/main\n{sha}\trefs/tags/v0.12.8\n"
        fake.stderr = ""
        with mock.patch.object(vrs.subprocess, "run", return_value=fake):
            r = vrs.check_remote_refs(Path("."), "origin", "main", "v0.12.8")
        self.assertTrue(r["pass"], r)

    def test_missing_tag_fails(self):
        sha = "b" * 40
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = f"{sha}\trefs/heads/main\n"  # no tag
        fake.stderr = ""
        with mock.patch.object(vrs.subprocess, "run", return_value=fake):
            r = vrs.check_remote_refs(Path("."), "origin", "main", "v0.12.8")
        self.assertFalse(r["pass"])

    def test_mismatched_sha_fails(self):
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = f"{'a'*40}\trefs/heads/main\n{'b'*40}\trefs/tags/v0.12.8\n"
        fake.stderr = ""
        with mock.patch.object(vrs.subprocess, "run", return_value=fake):
            r = vrs.check_remote_refs(Path("."), "origin", "main", "v0.12.8")
        self.assertFalse(r["pass"])


class CheckFreshSessionLoadTests(unittest.TestCase):
    def test_cache_matches_canonical(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d) / "repo"
            wd.mkdir()
            (wd / "agents").mkdir()
            (wd / "skills" / "x").mkdir(parents=True)
            (wd / "agents" / "a.md").write_text("content-a", encoding="utf-8")
            (wd / "skills" / "x" / "SKILL.md").write_text("content-s", encoding="utf-8")
            cache_root = Path(d) / "cache"
            cache_dir = cache_root / "test-plugin" / "0.12.8"
            (cache_dir / "agents").mkdir(parents=True)
            (cache_dir / "skills" / "x").mkdir(parents=True)
            (cache_dir / "agents" / "a.md").write_text("content-a", encoding="utf-8")
            (cache_dir / "skills" / "x" / "SKILL.md").write_text("content-s", encoding="utf-8")
            r = vrs.check_fresh_session_load(wd, "0.12.8", cache_root, "test-plugin")
            self.assertTrue(r["pass"], r)

    def test_cache_drift_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d) / "repo"
            wd.mkdir()
            (wd / "agents").mkdir()
            (wd / "agents" / "a.md").write_text("CANONICAL", encoding="utf-8")
            cache_root = Path(d) / "cache"
            cache_dir = cache_root / "test-plugin" / "0.12.8"
            (cache_dir / "agents").mkdir(parents=True)
            (cache_dir / "agents" / "a.md").write_text("STALE-CACHED", encoding="utf-8")
            r = vrs.check_fresh_session_load(wd, "0.12.8", cache_root, "test-plugin")
            self.assertFalse(r["pass"])

    def test_missing_cache_fails(self):
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            r = vrs.check_fresh_session_load(wd, "0.12.8", Path(d) / "nope", "test-plugin")
            self.assertFalse(r["pass"])


class CliTests(unittest.TestCase):
    def test_fatal_on_bad_version(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = [
                sys.executable,
                str(HERE / "verify_release_surface.py"),
                "--version", "not-a-version",
                "--branch", "main",
                "--workdir", d,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            payload = json.loads(r.stdout)
            self.assertIn("fatal_error", payload)

    def test_fatal_on_invalid_skip(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = [
                sys.executable,
                str(HERE / "verify_release_surface.py"),
                "--version", "v0.12.8",
                "--branch", "main",
                "--workdir", d,
                "--skip-check", "not_a_real_check",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            payload = json.loads(r.stdout)
            self.assertIn("fatal_error", payload)

    def test_end_to_end_with_skips(self):
        """End-to-end against the mini-repo, skipping network + cache checks."""
        with tempfile.TemporaryDirectory() as d:
            wd = Path(d)
            _init_repo(wd, "0.12.8")
            subprocess.run(["git", "tag", "v0.12.8"], cwd=wd, check=True, capture_output=True)
            cmd = [
                sys.executable,
                str(HERE / "verify_release_surface.py"),
                "--version", "v0.12.8",
                "--branch", "main",
                "--workdir", str(wd),
                "--skip-check", "remote_refs",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            payload = json.loads(r.stdout)
            self.assertTrue(payload["overall_pass"],
                            f"expected pass; got envelope: {json.dumps(payload, indent=2)}")
            self.assertEqual(r.returncode, 0)
            self.assertIn("remote_refs", payload["checks_skipped"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
