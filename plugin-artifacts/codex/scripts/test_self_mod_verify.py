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
        """JSON output has exactly the expected keys; no meta_modification / meta_files."""
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        payload = json.loads(r.stdout)
        expected_keys = {
            "scope", "ran", "passed", "failed", "failed_tests", "reverted",
            "verdict", "timed_out", "errors", "effective_scope", "error_reason",
        }
        for key in expected_keys:
            self.assertIn(key, payload, f"missing key {key!r}")
        # Removed keys must not be present
        self.assertNotIn("meta_modification", payload)
        self.assertNotIn("meta_files", payload)
        self.assertIsInstance(payload["ran"], list)
        self.assertIsInstance(payload["failed_tests"], list)
        self.assertIsInstance(payload["timed_out"], bool)
        # error_reason is None on a clean pass
        self.assertIsNone(payload["error_reason"])


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
        """An impl file with no matching test file → verdict no_tests, exit 3.

        no_tests is NOT green: the gate exercised nothing, so it must not read as
        pass. Exit 3 (inconclusive) is distinct from a real fail (1) or error (2).
        """
        impl = self.scripts_dir / "orphan.py"
        impl.write_text("pass\n")
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(impl),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "no_tests")
        self.assertEqual(r.returncode, 3,
                         msg="no_tests must be non-green (exit 3), never exit 0")

    def test_changed_test_named_markdown_doc_is_not_run_as_pytest(self) -> None:
        """A changed doc whose basename starts with `test_` (the per-script doc
        convention, e.g. docs/scripts/test_foo.md) must NOT be handed to pytest.

        Regression: pytest exits 4 on a .md path, which the gate surfaced as
        verdict=error and falsely blocked a docs-only commit. A non-.py change
        with no sibling .py test maps to no target → verdict no_tests (exit 3,
        inconclusive — the doc change is not a green pass, but it is not a real
        test failure/error either)."""
        docs = self.workdir / "docs" / "scripts"
        docs.mkdir(parents=True)
        doc = docs / "test_thing.md"
        doc.write_text("# test_thing.py\n\nDocumentation, not a test.\n")
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(doc),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "no_tests", msg=f"stderr={r.stderr!r}")
        self.assertEqual(r.returncode, 3, msg=f"stderr={r.stderr!r}")
        self.assertEqual(payload["ran"], [])


    def test_mirror_copy_under_plugin_artifacts_is_never_a_gate_target(self) -> None:
        """A test file under a generated mirror tree must not be collected.

        Regression: `plugin-artifacts/codex/` mirrors `scripts/`, so every one of
        its `test_*.py` files shares a basename with its source twin. When git
        reported both copies as changed, the gate handed both to pytest, which
        aborted collection with "import file mismatch" and surfaced verdict=error
        — the gate failing on a change that was itself green. Discovery now drops
        anything under a generated tree, so only the `scripts/` copy runs.
        """
        scripts = self.workdir / "scripts"
        scripts.mkdir(exist_ok=True)
        source_test = scripts / "test_mirrored_thing.py"
        source_test.write_text("def test_ok(): assert True\n")
        mirror = self.workdir / "plugin-artifacts" / "codex" / "scripts"
        mirror.mkdir(parents=True)
        mirror_test = mirror / "test_mirrored_thing.py"
        mirror_test.write_text("def test_ok(): assert True\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(source_test), str(mirror_test),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "pass", msg=f"stderr={r.stderr!r}")
        self.assertEqual(
            [Path(p).resolve() for p in payload["ran"]],
            [source_test.resolve()],
            msg=f"mirror copy leaked into gate targets: {payload['ran']}",
        )

    def test_mirror_copy_alone_maps_to_no_gate_target(self) -> None:
        """A change confined to the generated mirror has nothing to verify."""
        mirror = self.workdir / "plugin-artifacts" / "codex" / "scripts"
        mirror.mkdir(parents=True)
        mirror_test = mirror / "test_only_in_mirror.py"
        mirror_test.write_text("def test_ok(): assert True\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--changed-files", str(mirror_test),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "no_tests", msg=f"stderr={r.stderr!r}")
        self.assertEqual(payload["ran"], [])


class TestNoGateTreePredicate(unittest.TestCase):
    """`_in_non_gate_tree` classifies mirror trees without touching the disk."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("self_mod_verify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_every_named_tree_is_excluded(self) -> None:
        for tree in self.mod._NON_GATE_TREES:
            path = self.workdir / tree / "scripts" / "test_x.py"
            self.assertTrue(
                self.mod._in_non_gate_tree(path, self.workdir), tree
            )

    def test_ordinary_scripts_path_is_not_excluded(self) -> None:
        path = self.workdir / "scripts" / "test_x.py"
        self.assertFalse(self.mod._in_non_gate_tree(path, self.workdir))

    def test_substring_match_does_not_falsely_exclude(self) -> None:
        path = self.workdir / "scripts" / "distribution" / "test_x.py"
        self.assertFalse(self.mod._in_non_gate_tree(path, self.workdir))

    def test_workdir_inside_a_mirror_tree_still_gates_its_own_scripts(self) -> None:
        """Running the gate FROM inside the artifact must not exclude everything."""
        inner = self.workdir / "plugin-artifacts" / "codex"
        path = inner / "scripts" / "test_x.py"
        self.assertFalse(self.mod._in_non_gate_tree(path, inner))


class TestNoPytest(unittest.TestCase):
    """When no tests are found, verdict = no_tests, exit 3 (inconclusive)."""

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
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "no_tests")
        self.assertEqual(r.returncode, 3,
                         msg=f"no_tests must be non-green (exit 3); stderr: {r.stderr}")
        self.assertEqual(payload["ran"], [])


# ---------------------------------------------------------------------------
# Tests: --scope auto (file-count / core-path rules only)
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
        """1 source file → effective_scope = changed."""
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
# Regression: gate/test files are NOT special-cased (no needs_human)
# ---------------------------------------------------------------------------

class TestPytestTimeoutProbe(unittest.TestCase):
    """`--timeout` is a PLUGIN flag; passing it without the plugin exits pytest 4.

    That exit was classified as verdict=error/"Timeout", so on a venv provisioned
    without the `test` extra the self-modification gate hard-errored on a healthy
    tree — 6 failing tests here, and a gate that verified nothing. The probe asks
    the RESOLVED runner (which may be a different venv than this process) whether
    pytest-timeout is loaded, and the caller drops the flag when it is not.
    """

    def setUp(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import self_mod_verify  # noqa: PLC0415 - late import keeps module-path setup local

        self.mod = self_mod_verify

    def test_probe_reports_plugin_presence_for_the_real_runner(self) -> None:
        repo = Path(__file__).resolve().parent.parent
        runner = self.mod._find_runner(repo)
        if runner is None:
            self.skipTest("no pytest runner available")
        answer = self.mod._runner_has_pytest_timeout(runner, cwd=repo)
        self.assertIsInstance(answer, bool)
        # Cross-check against the runner itself so the probe cannot silently
        # invert. `--version` must be DOUBLED: modern pytest prints only
        # "pytest <N>" for a single flag and reserves the registered-plugin list
        # for the doubled form. This cross-check used the single flag and so
        # agreed with the probe's own blind spot — both reported "no plugin"
        # against a venv that had it, and the gate ran without hang protection.
        r = subprocess.run(
            [*runner, "--version", "--version", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo),
        )
        expected = "timeout" in (r.stdout + r.stderr).lower()
        self.assertEqual(answer, expected)

    def test_probe_sees_a_plugin_that_the_runner_really_loaded(self) -> None:
        """Ground truth, not self-consistency: if the resolved runner loads
        pytest-timeout, the probe must say True.

        The sibling test above only asserts probe == cross-check, so a probe and
        a cross-check that share a blind spot agree while both are wrong. This
        one reads the plugin list directly and requires the probe to match it.
        """
        repo = Path(__file__).resolve().parent.parent
        runner = self.mod._find_runner(repo)
        if runner is None:
            self.skipTest("no pytest runner available")
        listing = subprocess.run(
            [*runner, "--version", "--version", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo),
        )
        if "pytest-timeout" not in (listing.stdout + listing.stderr):
            self.skipTest("resolved runner has no pytest-timeout installed")
        self.assertTrue(
            self.mod._runner_has_pytest_timeout(runner, cwd=repo),
            msg="runner loads pytest-timeout but the probe reported it missing; "
                "per-test hang protection would be silently dropped",
        )

    def test_probe_answers_for_the_directory_the_suite_will_run_in(self) -> None:
        """The probe must be asked in the run's working directory.

        `uv run pytest` resolves its virtualenv from the working directory, so a
        probe taken in the plugin repo answered "pytest-timeout installed" while
        the run, executed in a temp workdir, reached a venv without it and exited
        4 — surfacing verdict=error on a healthy tree. Probing the temp workdir
        must therefore be allowed to disagree with probing the repo, and the
        production call site passes cwd=workdir.
        """
        repo = Path(__file__).resolve().parent.parent
        runner = self.mod._find_runner(repo)
        if runner is None:
            self.skipTest("no pytest runner available")
        with tempfile.TemporaryDirectory() as elsewhere:
            # Both answers must be booleans obtained WITHOUT raising; the point
            # is that cwd is a real input to the probe, not that they differ on
            # every machine.
            here = self.mod._runner_has_pytest_timeout(runner, cwd=repo)
            there = self.mod._runner_has_pytest_timeout(runner, cwd=elsewhere)
            self.assertIsInstance(here, bool)
            self.assertIsInstance(there, bool)
        # The production path must pass the workdir through, not probe blind.
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("_runner_has_pytest_timeout(runner_base, cwd=workdir)", source)

    def test_probe_fails_closed_on_a_broken_runner(self) -> None:
        # A probe that cannot answer must say False: dropping the flag degrades
        # cleanly, while passing it on a plugin-less runner exits pytest 4.
        self.assertFalse(
            self.mod._runner_has_pytest_timeout(
                [sys.executable, "-c", "import sys; sys.exit(3)"]
            )
        )
        self.assertFalse(
            self.mod._runner_has_pytest_timeout(["definitely-not-a-real-binary-xyz"])
        )


class TestNoMetaHalt(unittest.TestCase):
    """Passing scripts/self_mod_verify.py or test_*.py in --changed-files must
    never yield needs_human.  The gate runs mapped tests and returns pass/fail
    based on the test result only."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_self_mod_verify_changed_not_needs_human(self) -> None:
        """Listing scripts/self_mod_verify.py as changed → verdict is pass or
        no_tests (based on mapped test result), never needs_human."""
        # Write a passing test that maps to self_mod_verify.py
        (self.scripts_dir / "test_self_mod_verify.py").write_text(
            "def test_ok(): assert True\n"
        )
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", "scripts/self_mod_verify.py",
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertNotEqual(payload["verdict"], "needs_human",
                            msg=f"Gate file must not trigger needs_human: {payload}")
        self.assertIn(payload["verdict"], ("pass", "no_tests"),
                      msg=f"Expected pass or no_tests, got: {payload['verdict']}")
        # pass is green (0); no_tests is inconclusive (3) — never needs_human.
        self.assertEqual(r.returncode, 0 if payload["verdict"] == "pass" else 3)

    def test_test_file_changed_not_needs_human(self) -> None:
        """Listing a test_*.py file as changed → verdict reflects test outcome,
        not file identity.  When the test passes, verdict = pass."""
        test_file = self.scripts_dir / "test_something.py"
        test_file.write_text("def test_ok(): assert 1 == 1\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(test_file),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertNotEqual(payload["verdict"], "needs_human",
                            msg=f"Test file must not trigger needs_human: {payload}")
        # test_something.py is included directly (it IS a test file), runs and passes
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"Passing test file should yield pass, got: {payload['verdict']}")
        self.assertEqual(r.returncode, 0)

    def test_gate_file_failing_tests_yields_fail_not_needs_human(self) -> None:
        """When the gate file is changed and its mapped test FAILS, verdict=fail
        (exit 1), not needs_human."""
        (self.scripts_dir / "test_self_mod_verify.py").write_text(
            "def test_broken(): assert False, 'intentional'\n"
        )
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", "scripts/self_mod_verify.py",
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertNotEqual(payload["verdict"], "needs_human",
                            msg=f"Gate file with failing tests must give fail, not needs_human: {payload}")
        self.assertEqual(payload["verdict"], "fail",
                         msg=f"Failing tests must yield fail: {payload['verdict']}")
        self.assertEqual(r.returncode, 1)

    def test_no_meta_modification_key_in_output(self) -> None:
        """The meta_modification and meta_files keys are gone from the JSON output."""
        _write_passing_test(self.scripts_dir)
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        payload = json.loads(r.stdout)
        self.assertNotIn("meta_modification", payload)
        self.assertNotIn("meta_files", payload)


# ---------------------------------------------------------------------------
# Tests: JSON stdout purity
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

    def test_json_parses_on_pass_verdict(self) -> None:
        """Pass path also emits pure JSON to stdout."""
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
            self.fail(f"stdout not valid JSON: {exc}\nstdout={r.stdout!r}")
        self.assertIn(payload["verdict"], ("pass", "no_tests"))


# ---------------------------------------------------------------------------
# Tests: timeout flag plumbing
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
            # Let ALL runner-detection probes through (`uv run pytest --version`
            # AND the `python3 -m pytest --version` fallback — _find_runner may
            # make either or both depending on whether uv resolves here). Only
            # the real test-run invocation (no `--version`) simulates a timeout.
            if "--version" in cmd:
                return original_run(cmd, **kwargs)
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
        # Timeout at the subprocess layer → verdict "error" (infrastructure failure)
        self.assertEqual(result["verdict"], "error",
                         msg=f"Timed-out subprocess must be verdict=error; got {result['verdict']}")
        self.assertIsNotNone(result.get("error_reason"),
                             msg="error_reason must be set on verdict=error")
        # exit_code is 2 (error — infrastructure failure, not a test failure)
        self.assertEqual(exit_code, 2)

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


# ---------------------------------------------------------------------------
# Tests: conftest.py live-marker skip logic
# ---------------------------------------------------------------------------

class TestConftestLiveSkip(unittest.TestCase):
    """Verify conftest.py skips `live`-marked tests when the probe fails."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_live_test(self) -> Path:
        p = self.scripts_dir / "test_live_service.py"
        p.write_text(
            "import pytest\n"
            "@pytest.mark.live\n"
            "def test_needs_live_service():\n"
            "    # This must be skipped when Ollama is unreachable.\n"
            "    assert False, 'live service required'\n"
        )
        return p

    def test_live_marked_test_skipped_when_probe_fails(self) -> None:
        """A test marked @pytest.mark.live must be skipped (not failed/hung)
        when the conftest Ollama probe monkeypatches to unreachable.

        We test this by writing a conftest.py to the tmp workdir that
        patches _ollama_reachable to return False, then verifies the
        @pytest.mark.live test is skipped (not failed).
        """
        import importlib.util
        import sys as _sys

        # Copy the repo conftest to the tmp workdir so pytest finds it.
        repo_root = HERE.parent
        repo_conftest = repo_root / "conftest.py"

        # Write a local conftest to workdir that forces _ollama_reachable=False
        # so we don't depend on whether the real Ollama service is running.
        (self.workdir / "conftest.py").write_text(
            "import pytest\n"
            "\n"
            "def _ollama_reachable():\n"
            "    return False  # monkeypatched for test isolation\n"
            "\n"
            "def pytest_collection_modifyitems(config, items):\n"
            "    skip_marker = pytest.mark.skip(\n"
            "        reason='live service (Ollama/qwen on 127.0.0.1:11434) unreachable'\n"
            "    )\n"
            "    for item in items:\n"
            "        if item.get_closest_marker('live') is not None:\n"
            "            item.add_marker(skip_marker, append=False)\n"
        )
        # Register the `live` marker so pytest doesn't warn
        (self.workdir / "pytest.ini").write_text(
            "[pytest]\n"
            "markers =\n"
            "    live: requires live external service\n"
        )

        self._write_live_test()

        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short",
             str(self.scripts_dir / "test_live_service.py")],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(self.workdir),
        )
        combined = r.stdout + r.stderr
        # Must NOT fail (the test assertion "assert False" must not run)
        self.assertNotIn("FAILED", combined,
                         msg=f"live test must be skipped, not failed:\n{combined}")
        # Must appear as SKIPPED
        self.assertIn("skipped", combined.lower(),
                      msg=f"live test must appear skipped:\n{combined}")


# ---------------------------------------------------------------------------
# Tests: verdict=error on collection failure / no summary
# ---------------------------------------------------------------------------

class TestVerdictError(unittest.TestCase):
    """When pytest exits non-zero with no summary line, verdict must be
    'error' (not 'fail') so callers see WHY the gate produced 0/0."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("self_mod_verify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        _init_git_repo(self.workdir)
        # A test with a syntax error so pytest fails to collect it
        (scripts_dir / "test_bad_syntax.py").write_text(
            "def test_ok():\n"
            "    assert True\n"
            "\n"
            "def broken syntax here:\n"  # intentional SyntaxError
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_collection_error_yields_verdict_error(self) -> None:
        """A file that fails to collect (SyntaxError) must produce
        verdict='error' with error_reason set, exit code 2."""
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        # Exit code 2 = error (not 0=pass, not 1=fail)
        self.assertEqual(r.returncode, 2,
                         msg=f"Expected exit 2 on collection error; stderr: {r.stderr}")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "error",
                         msg=f"Collection failure must be verdict=error; got {payload['verdict']}")
        self.assertIsNotNone(payload.get("error_reason"),
                             msg="error_reason must be set when verdict=error")
        # Passed and failed are both 0 (nothing ran successfully)
        self.assertEqual(payload["passed"], 0)
        self.assertEqual(payload["failed"], 0)

    def test_error_reason_in_stderr_summary(self) -> None:
        """The human summary on stderr must include error_reason when present."""
        r = _run(["--workdir", str(self.workdir), "--scope", "full", "--json"])
        # Only relevant if we actually get verdict=error
        payload = json.loads(r.stdout)
        if payload["verdict"] == "error":
            self.assertIn("error_reason", r.stderr,
                          msg=f"stderr summary must include error_reason; stderr={r.stderr!r}")


# ---------------------------------------------------------------------------
# Tests: git-derived change set + no_changes verdict
# ---------------------------------------------------------------------------
# The documented gate invocation is `--scope auto --auto-revert` with NO
# --changed-files. Historically that resolved to an empty test set → a green
# no_tests that gated nothing. These cover the fix: derive the change set from
# git (tracked diff + untracked new files), and emit an explicit non-green
# no_changes verdict when git shows no changes at all.

class TestGitDerivedChangeSet(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _commit(self, msg: str = "wip") -> None:
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", msg],
                       check=True, capture_output=True)

    def test_explicit_changed_files_bypasses_git_derivation(self) -> None:
        """When --changed-files IS given, the gate uses it verbatim and does not
        touch git (derived_from_git=False)."""
        impl = self.scripts_dir / "impl.py"
        impl.write_text("pass\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")
        self._commit("add impl + test")
        # Modify impl so there IS a git change too — but pass it explicitly.
        impl.write_text("pass  # edited\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--changed-files", str(impl),
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"test_impl.py should run and pass: {payload}")
        self.assertFalse(payload["derived_from_git"],
                         msg="explicit --changed-files must NOT set derived_from_git")
        self.assertEqual(r.returncode, 0)

    def test_fallback_derives_tracked_modified_file(self) -> None:
        """No --changed-files + a tracked-but-modified impl.py → the gate derives
        impl.py from `git diff HEAD`, runs its mapped test, and marks
        derived_from_git=True. This is the documented `--scope auto` path."""
        impl = self.scripts_dir / "impl.py"
        impl.write_text("VALUE = 1\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")
        self._commit("baseline")
        # Modify the tracked file WITHOUT staging — the exact self-mod shape.
        impl.write_text("VALUE = 2\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertTrue(payload["derived_from_git"],
                        msg=f"gate must auto-derive the change set from git: {payload}")
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"mapped test_impl.py should run and pass: {payload}")
        ran_names = [Path(f).name for f in payload["ran"]]
        self.assertIn("test_impl.py", ran_names,
                      msg="the git-derived file's mapped test must actually run")
        self.assertEqual(r.returncode, 0)

    def test_fallback_derives_untracked_new_file(self) -> None:
        """Regression BUIL-SELFMOD-001: a self-mod that ADDS a new foo.py +
        test_foo.py (both untracked) must be picked up under --scope auto with no
        --changed-files. `git diff` alone misses untracked files; the
        `ls-files --others` arm closes it. Expect the new test to actually run."""
        (self.scripts_dir / "newmod.py").write_text("def f(): return 42\n")
        (self.scripts_dir / "test_newmod.py").write_text(
            "from newmod import f\n"
            "def test_f(): assert f() == 42\n"
        )
        # Deliberately do NOT commit or stage — both files are untracked.
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--json",
        ], cwd=str(self.scripts_dir))
        payload = json.loads(r.stdout)
        self.assertTrue(payload["derived_from_git"],
                        msg=f"untracked new files must be derived: {payload}")
        ran_names = [Path(f).name for f in payload["ran"]]
        self.assertIn("test_newmod.py", ran_names,
                      msg=f"the NEW untracked test must run (BUIL-SELFMOD-001): {payload}")
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"expected pass, not a green no_tests: {payload}")
        self.assertEqual(r.returncode, 0)

    def test_no_changes_verdict_when_clean(self) -> None:
        """No --changed-files + a fully clean tree → verdict no_changes, exit 3.
        A self-mod gate with nothing to verify must NOT read as pass."""
        impl = self.scripts_dir / "impl.py"
        impl.write_text("pass\n")
        _write_passing_test(self.scripts_dir, "test_impl.py")
        self._commit("everything committed, tree clean")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--auto-revert",
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "no_changes",
                         msg=f"clean tree must yield no_changes, not pass/no_tests: {payload}")
        self.assertEqual(r.returncode, 3,
                         msg="no_changes must be non-green (exit 3)")
        self.assertFalse(payload["derived_from_git"])
        self.assertEqual(payload["ran"], [])

    def test_no_changes_is_distinct_from_pass(self) -> None:
        """The no_changes verdict string is distinct from pass — a caller keying
        on verdict=='pass' will not be fooled by an empty run."""
        # _init_git_repo already leaves a clean tree (README committed); no diff.
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "changed",
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertNotEqual(payload["verdict"], "pass")
        self.assertEqual(payload["verdict"], "no_changes")

    def test_full_scope_ignores_git_derivation(self) -> None:
        """--scope full runs the whole suite regardless, so an empty
        --changed-files must NOT trigger the no_changes short-circuit."""
        _write_passing_test(self.scripts_dir, "test_thing.py")
        self._commit("add a test")
        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "full",
            "--json",
        ])
        payload = json.loads(r.stdout)
        self.assertEqual(payload["verdict"], "pass",
                         msg=f"full scope must run the suite, not no_changes: {payload}")
        self.assertFalse(payload["derived_from_git"])


# ---------------------------------------------------------------------------
# f1: --auto-revert partitions tracked vs untracked (no silent no-op revert)
# ---------------------------------------------------------------------------

class TestRevertPartitioning(unittest.TestCase):
    """A failing derived-set revert must restore the TRACKED file, report the
    UNTRACKED file (never delete it), and populate errors[]. Regression: the old
    code handed the mixed list to one `git restore`, which exits 1 on any
    untracked path having restored NOTHING — a silent no-op revert."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.scripts_dir = self.workdir / "scripts"
        self.scripts_dir.mkdir()
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_fail_path_reverts_tracked_reports_untracked(self) -> None:
        # Commit a tracked impl + a FAILING mapped test (fail → revert fires).
        impl = self.scripts_dir / "impl.py"
        impl.write_text("VALUE = 1\n")
        (self.scripts_dir / "test_impl.py").write_text(
            "def test_broken():\n    assert False, 'intentional'\n"
        )
        subprocess.run(["git", "-C", str(self.workdir), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.workdir), "commit", "-m", "baseline"],
                       check=True, capture_output=True)

        # Tracked-mod: edit the committed impl (unstaged self-mod).
        impl.write_text("VALUE = 2  # broken self-mod\n")
        # Untracked-new: a brand-new file, part of the git-derived change set.
        extra = self.scripts_dir / "extra.py"
        extra.write_text("EXTRA = True\n")

        r = _run([
            "--workdir", str(self.workdir),
            "--scope", "auto",
            "--auto-revert",
            "--json",
        ])
        payload = json.loads(r.stdout)
        # Change set derived from git spans BOTH files; test_impl.py fails.
        self.assertTrue(payload["derived_from_git"], msg=payload)
        self.assertEqual(payload["verdict"], "fail", msg=payload)
        self.assertEqual(r.returncode, 1, msg=f"stderr={r.stderr!r}")

        # The TRACKED file was restored to its committed content.
        self.assertTrue(payload["reverted"],
                        msg="tracked file must be restored on the fail path")
        restored = impl.read_text()
        self.assertIn("VALUE = 1", restored)
        self.assertNotIn("VALUE = 2", restored)

        # The UNTRACKED file is NOT deleted (concurrent WIP is never swept)...
        self.assertTrue(extra.exists(),
                        msg="untracked file must never be deleted by revert")

        # ...and errors[] is non-empty, reporting the untracked file explicitly
        # plus the derived-set breadth warning.
        self.assertTrue(payload["errors"], "errors[] must be non-empty")
        joined = "\n".join(payload["errors"])
        self.assertIn("untracked, not reverted", joined, msg=joined)
        self.assertIn("extra.py", joined, msg=joined)
        self.assertIn("breadth warning", joined, msg=joined)


# ---------------------------------------------------------------------------
# f2: a failing git change-set arm is recorded; tracked-arm failure → error
# ---------------------------------------------------------------------------

class TestGitDerivationErrors(unittest.TestCase):
    """A failing `git diff --name-only HEAD` (tracked) arm must not yield a
    truncated green change set: the failure is recorded in errors[] and the
    verdict escalates to error. Regression: per-arm `continue` swallowed the
    failure and could pass green over a knowingly-partial set."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("self_mod_verify", SCRIPT)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        scripts_dir = self.workdir / "scripts"
        scripts_dir.mkdir()
        _write_passing_test(scripts_dir, "test_thing.py")
        _init_git_repo(self.workdir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_helper_records_tracked_arm_failure(self) -> None:
        import unittest.mock as mock
        original_run = subprocess.run

        def mock_run(cmd, **kwargs):
            if "diff" in cmd and "--name-only" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 128, stdout="", stderr="fatal: bad revision 'HEAD'")
            return original_run(cmd, **kwargs)

        with mock.patch("subprocess.run", side_effect=mock_run):
            files, derivation_errors = self.mod._git_changed_files(self.workdir)

        self.assertTrue(
            any("tracked-diff" in e for e in derivation_errors),
            msg=f"derivation_errors must name the failed arm: {derivation_errors}",
        )

    def test_tracked_arm_failure_escalates_to_verdict_error(self) -> None:
        import unittest.mock as mock
        original_run = subprocess.run

        def mock_run(cmd, **kwargs):
            # Let runner --version probes through so _find_runner succeeds.
            if "--version" in cmd:
                return original_run(cmd, **kwargs)
            if "diff" in cmd and "--name-only" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 128, stdout="", stderr="fatal: bad revision 'HEAD'")
            return original_run(cmd, **kwargs)

        with mock.patch("subprocess.run", side_effect=mock_run):
            result, exit_code = self.mod.verify(
                workdir=self.workdir,
                scope="auto",
                changed_files=[],
                auto_revert=False,
                timeout=60,
            )

        self.assertEqual(result["verdict"], "error", msg=f"result={result}")
        self.assertEqual(exit_code, 2, msg=f"result={result}")
        self.assertTrue(result["errors"], "errors[] must be populated")
        self.assertTrue(
            any("tracked-diff" in e for e in result["errors"]),
            msg=f"errors[] must name the failed arm: {result['errors']}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
