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


# ---------------------------------------------------------------------------
# Original tests (preserved)
# ---------------------------------------------------------------------------

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
        for key in (
            "scope", "ran", "passed", "failed", "failed_tests", "reverted",
            "verdict", "meta_modification", "meta_files", "timed_out", "errors",
            "effective_scope",
        ):
            self.assertIn(key, payload, f"missing key {key!r}")
        self.assertIsInstance(payload["ran"], list)
        self.assertIsInstance(payload["failed_tests"], list)
        self.assertIsInstance(payload["meta_modification"], bool)
        self.assertIsInstance(payload["meta_files"], list)
        self.assertIsInstance(payload["timed_out"], bool)


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


# ---------------------------------------------------------------------------
# New tests: meta-modification detection
# ---------------------------------------------------------------------------

class TestClassifySelfMod(unittest.TestCase):
    """Unit tests for classify_self_mod()."""

    def setUp(self) -> None:
        # Import directly to unit-test the function
        import importlib.util
        spec = importlib.util.spec_from_file_location("self_mod_verify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_test_file_is_meta(self) -> None:
        result = self.mod.classify_self_mod(
            ["scripts/test_foo.py"], self.workdir
        )
        self.assertTrue(result["meta_modification"])
        self.assertIn("scripts/test_foo.py", result["meta_files"])

    def test_self_mod_verify_is_meta(self) -> None:
        result = self.mod.classify_self_mod(
            ["scripts/self_mod_verify.py"], self.workdir
        )
        self.assertTrue(result["meta_modification"])

    def test_autonomy_gate_is_meta(self) -> None:
        result = self.mod.classify_self_mod(
            ["scripts/autonomy_gate.py"], self.workdir
        )
        self.assertTrue(result["meta_modification"])

    def test_classify_action_is_meta(self) -> None:
        result = self.mod.classify_self_mod(
            ["scripts/classify_action.py"], self.workdir
        )
        self.assertTrue(result["meta_modification"])

    def test_install_self_review_is_meta(self) -> None:
        result = self.mod.classify_self_mod(
            ["scripts/install_self_review.py"], self.workdir
        )
        self.assertTrue(result["meta_modification"])

    def test_normal_source_file_not_meta(self) -> None:
        result = self.mod.classify_self_mod(
            ["scripts/memory_writer.py"], self.workdir
        )
        self.assertFalse(result["meta_modification"])
        self.assertEqual(result["meta_files"], [])

    def test_empty_list_not_meta(self) -> None:
        result = self.mod.classify_self_mod([], self.workdir)
        self.assertFalse(result["meta_modification"])

    def test_mixed_list_detects_meta(self) -> None:
        result = self.mod.classify_self_mod(
            ["scripts/memory_writer.py", "scripts/test_memory_writer.py"],
            self.workdir,
        )
        self.assertTrue(result["meta_modification"])
        self.assertIn("scripts/test_memory_writer.py", result["meta_files"])
        self.assertNotIn("scripts/memory_writer.py", result["meta_files"])


class TestMetaModCLI(unittest.TestCase):
    """CLI-level tests: meta files → needs_human verdict + exit 1."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        _write_passing_test(scripts_dir)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_changed_test_file_gives_needs_human(self) -> None:
        """Changing a test_*.py file triggers needs_human, even if tests pass."""
        scripts_dir = self.workdir / "scripts"
        test_file = scripts_dir / "test_foo.py"
        test_file.write_text("def test_ok(): assert True\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(test_file),
            "--json",
        ])
        self.assertEqual(r.returncode, 1, msg=f"stderr: {r.stderr}\nstdout: {r.stdout}")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "needs_human")
        self.assertTrue(payload["meta_modification"])
        self.assertTrue(len(payload["meta_files"]) > 0)

    def test_changed_self_mod_verify_gives_needs_human(self) -> None:
        """Editing the gate script itself triggers needs_human."""
        # Use temp workdir so we don't accidentally trigger the real full suite
        scripts_dir = self.workdir / "scripts"
        # Write a minimal passing test so there is something to run
        _write_passing_test(scripts_dir, "test_self_mod_verify.py")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", "scripts/self_mod_verify.py",
            "--json",
        ])
        self.assertEqual(r.returncode, 1, msg=f"stderr: {r.stderr}\nstdout: {r.stdout}")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "needs_human")
        self.assertTrue(payload["meta_modification"])

    def test_meta_verdict_needs_human_even_when_tests_pass(self) -> None:
        """Tests run and pass, but verdict is still needs_human for meta files."""
        # Use a temp workdir with passing tests, changed file = a test file
        scripts_dir = self.workdir / "scripts"
        passing_test = scripts_dir / "test_passing.py"
        passing_test.write_text("def test_pass(): assert 1 == 1\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(passing_test),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "needs_human",
                         msg="Even when all tests pass, meta file edit must yield needs_human")
        self.assertEqual(r.returncode, 1)

    def test_normal_changed_file_not_needs_human(self) -> None:
        """A regular source file change does not trigger needs_human."""
        scripts_dir = self.workdir / "scripts"
        normal_file = self.workdir / "scripts" / "normal_module.py"
        normal_file.write_text("X = 1\n")
        # Also write its test so scope=auto has something to run
        (scripts_dir / "test_normal_module.py").write_text(
            "def test_ok(): assert 1 == 1\n"
        )

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(normal_file),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertNotEqual(payload["verdict"], "needs_human",
                            msg=f"Normal file should not trigger needs_human: {payload}")
        self.assertFalse(payload["meta_modification"])


# ---------------------------------------------------------------------------
# New tests: --scope auto
# ---------------------------------------------------------------------------

class TestScopeAuto(unittest.TestCase):
    """Tests for --scope auto blast-radius selection."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_one_source_file_uses_changed_scope(self) -> None:
        """1 non-meta source file → effective_scope = changed."""
        impl = self.scripts_dir / "impl.py"
        impl.write_text("pass\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(impl),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["scope"], "auto")
        self.assertEqual(payload["effective_scope"], "changed")
        self.assertFalse(payload["meta_modification"])

    def test_meta_file_gives_needs_human_regardless(self) -> None:
        """Meta file → needs_human even with auto scope."""
        test_file = self.scripts_dir / "test_meta_check.py"
        test_file.write_text("def test_ok(): pass\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(test_file),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "needs_human")
        self.assertEqual(r.returncode, 1)

    def test_five_files_uses_broad_scope(self) -> None:
        """5+ source files → effective_scope = broad."""
        changed = []
        for i in range(5):
            f = self.scripts_dir / f"module_{i}.py"
            f.write_text("pass\n")
            changed.append(str(f))
        _write_passing_test(self.scripts_dir, "test_module_0.py")

        r = _run(
            ["--workdir", str(self.workdir), "--scope", "auto", "--json"]
            + ["--changed-files"] + changed
        )
        payload = json.loads(r.stdout)
        self.assertEqual(payload["scope"], "auto")
        self.assertEqual(payload["effective_scope"], "broad")


# ---------------------------------------------------------------------------
# New tests: JSON stdout purity
# ---------------------------------------------------------------------------

class TestJsonStdoutPurity(unittest.TestCase):
    """--json stdout must parse as pure JSON with no leading human text."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        _write_passing_test(scripts_dir)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stdout_is_valid_json(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout is not valid JSON: {exc}\nstdout={r.stdout!r}")
        self.assertIsInstance(payload, dict)

    def test_stderr_contains_human_summary(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        self.assertIn("verdict=", r.stderr,
                      msg="Human summary should appear on stderr, not stdout")

    def test_stdout_does_not_contain_human_prefix(self) -> None:
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        # Ensure stdout starts with '{' (JSON object), not human text
        stripped = r.stdout.strip()
        self.assertTrue(
            stripped.startswith("{"),
            msg=f"stdout should start with '{{', got: {stripped[:80]!r}",
        )

    def test_json_parses_even_on_needs_human_verdict(self) -> None:
        """needs_human path also emits pure JSON to stdout."""
        scripts_dir = self.workdir / "scripts"
        test_file = scripts_dir / "test_something.py"
        test_file.write_text("def test_ok(): pass\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(test_file),
            "--json",
        ])
        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"needs_human stdout not valid JSON: {exc}\nstdout={r.stdout!r}")
        self.assertEqual(payload["verdict"], "needs_human")


# ---------------------------------------------------------------------------
# New tests: timeout flag plumbing
# ---------------------------------------------------------------------------

class TestTimeoutFlagPlumbing(unittest.TestCase):
    """Verify timed_out flag is set and verdict is not falsely pass on timeout."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("self_mod_verify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        # Write a test that will time out when timeout=0 is passed to subprocess
        # We can't reliably force a 0s timeout in subprocess, so we unit-test
        # the flag plumbing by monkey-patching subprocess.run
        _write_passing_test(scripts_dir)
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_timeout_result_has_timed_out_false_on_fast_run(self) -> None:
        """Normal fast run sets timed_out=False."""
        result, exit_code = self.mod.verify(
            workdir=self.workdir,
            scope="full",
            changed_files=[],
            auto_revert=False,
            timeout=300,
        )
        self.assertFalse(result["timed_out"])

    def test_timeout_flag_plumbing_unit(self) -> None:
        """Simulate TimeoutExpired and verify timed_out=True, verdict=no_tests."""
        import unittest.mock as mock

        original_run = subprocess.run

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            # Allow first few calls (runner detection) to succeed
            if call_count[0] <= 3:
                return original_run(cmd, **kwargs)
            # Simulate timeout on the actual pytest invocation
            raise subprocess.TimeoutExpired(cmd, 1)

        with mock.patch("subprocess.run", side_effect=mock_run):
            result, exit_code = self.mod.verify(
                workdir=self.workdir,
                scope="full",
                changed_files=[],
                auto_revert=False,
                timeout=1,
            )

        self.assertTrue(result["timed_out"], msg=f"timed_out should be True; result={result}")
        # verdict must not be pass on timeout
        self.assertNotEqual(result["verdict"], "pass",
                            msg="A timed-out run must never report verdict=pass")
        self.assertEqual(result["verdict"], "no_tests")
        # exit_code depends on meta; for non-meta it's 0 (fail-soft)
        self.assertEqual(exit_code, 0)

    def test_cli_timeout_argument_accepted(self) -> None:
        """--timeout flag is parsed without error."""
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "full",
            "--timeout", "600",
            "--json",
        ])
        # Should not error due to unrecognised argument
        payload = json.loads(r.stdout)
        self.assertIn("verdict", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
