#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for self_mod_verify.py.  Run: uv run pytest scripts/test_self_mod_verify.py -q"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "self_mod_verify.py"


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=cwd,
    )


def _init_git_repo(d: Path) -> None:
    """Initialise a minimal git repo in d with an initial commit."""
    subprocess.run(["git", "-C", str(d), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "config", "user.email", "test@test.local"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    # Need at least one commit so HEAD exists
    dummy = d / "README.txt"
    dummy.write_text("test repo\n")
    subprocess.run(["git", "-C", str(d), "add", "README.txt"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "commit", "-m", "init"],
                   check=True, capture_output=True)


def _write_passing_test(scripts_dir: Path, name: str = "test_sample.py") -> Path:
    p = scripts_dir / name
    p.write_text(
        "def test_always_passes():\n"
        "    assert 1 + 1 == 2\n"
    )
    return p


def _write_failing_test(scripts_dir: Path, name: str = "test_fail.py") -> Path:
    p = scripts_dir / name
    p.write_text(
        "def test_always_fails():\n"
        "    assert False, 'intentional failure'\n"
    )
    return p


class TestVerdictPass(unittest.TestCase):
    """A repo with only a passing test → verdict pass, exit 0."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        _write_passing_test(scripts_dir)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_passing_suite_verdict_pass(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "pass")
        self.assertGreater(payload["passed"], 0)
        self.assertEqual(payload["failed"], 0)
        self.assertFalse(payload["reverted"])

    def test_json_shape_complete(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        payload = json.loads(r.stdout)
        for key in ("scope", "ran", "passed", "failed", "failed_tests", "reverted", "verdict"):
            self.assertIn(key, payload, f"missing key {key!r}")
        self.assertIsInstance(payload["ran"], list)
        self.assertIsInstance(payload["failed_tests"], list)


class TestVerdictFail(unittest.TestCase):
    """A repo with a failing test → verdict fail, exit 1."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _write_failing_test(self.scripts_dir)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_failing_suite_verdict_fail(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        self.assertEqual(r.returncode, 1, msg=f"Expected exit 1; stderr: {r.stderr}")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "fail")
        self.assertGreater(payload["failed"], 0)

    def test_failing_suite_populates_failed_tests(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        payload = json.loads(r.stdout)
        # failed_tests should name the failing test
        self.assertGreater(len(payload["failed_tests"]), 0,
                           msg="failed_tests should be non-empty on failure")


class TestAutoRevert(unittest.TestCase):
    """--auto-revert with a failing test restores the changed file."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_auto_revert_restores_file(self) -> None:
        # Write a good implementation file and commit it
        impl = self.scripts_dir / "mymod.py"
        impl.write_text("ORIGINAL = True\n")
        subprocess.run(
            ["git", "-C", str(self.workdir), "add", str(impl)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.workdir), "commit", "-m", "add impl"],
            check=True, capture_output=True,
        )

        # Now create a failing test that will trigger revert
        _write_failing_test(self.scripts_dir, "test_mymod.py")
        subprocess.run(
            ["git", "-C", str(self.workdir), "add", str(self.scripts_dir / "test_mymod.py")],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.workdir), "commit", "-m", "add failing test"],
            check=True, capture_output=True,
        )

        # Modify the implementation file (this is the "self-modification" we want reverted)
        impl.write_text("ORIGINAL = False  # broken\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(impl),
            "--auto-revert",
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(r.returncode, 1, msg=f"Expected exit 1: {r.stderr}")
        self.assertEqual(payload["verdict"], "fail")
        self.assertTrue(payload["reverted"],
                        msg="reverted should be True after auto-revert on failure")
        # The file should be back to its committed state
        content = impl.read_text()
        self.assertIn("ORIGINAL = True", content,
                      msg="File should be restored to original content after revert")

    def test_auto_revert_no_changed_files_is_noop(self) -> None:
        """--auto-revert with no --changed-files is a warning, not a crash."""
        _write_failing_test(self.scripts_dir, "test_noop.py")
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "full",
            "--auto-revert",
            "--json",
        ])
        # Should exit 1 (fail) but NOT crash; reverted stays False (no files to revert)
        self.assertEqual(r.returncode, 1)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "fail")
        self.assertFalse(payload["reverted"],
                         msg="reverted must be False when no --changed-files given")


class TestScopeChanged(unittest.TestCase):
    """--scope changed only runs test_foo.py for changed scripts/foo.py."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scope_changed_maps_impl_to_test(self) -> None:
        # Write a passing test for impl.py and a failing test for other.py
        impl = self.scripts_dir / "impl.py"
        impl.write_text("pass\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")
        _write_failing_test(self.scripts_dir, "test_other.py")
        # Commit all new files so git HEAD is valid
        subprocess.run(
            ["git", "-C", str(self.workdir), "add", str(self.scripts_dir)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.workdir), "commit", "-m", "add test files"],
            check=True, capture_output=True,
        )

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(impl),
            "--json",
        ])
        payload = json.loads(r.stdout)
        # Only test_impl.py runs → passes; test_other.py is NOT run
        self.assertEqual(payload["verdict"], "pass",
                         msg="Only test_impl.py should run; test_other.py must be excluded")
        # Confirm the right test was in ran[]
        ran_names = [Path(f).name for f in payload["ran"]]
        self.assertIn("test_impl.py", ran_names)
        self.assertNotIn("test_other.py", ran_names)

    def test_scope_changed_no_mapped_test_gives_no_tests(self) -> None:
        """An impl file with no matching test file → verdict no_tests."""
        impl = self.scripts_dir / "orphan.py"
        impl.write_text("pass\n")
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(impl),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(r.returncode, 0)
        self.assertIn(payload["verdict"], ("no_tests", "pass"))


class TestNoPytest(unittest.TestCase):
    """When pytest is not available, verdict = no_tests, exit 0."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        # Scripts dir with a test file, but we'll point to an empty workdir
        # We can't remove pytest from the system — instead test the logic via
        # a workdir with no scripts/ dir (no test files found → no_tests)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_scripts_dir_gives_no_tests(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "no_tests")
        self.assertEqual(payload["ran"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
