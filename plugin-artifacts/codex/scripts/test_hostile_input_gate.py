#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/hostile_input_gate.py.

Reproduces the observed failure shape exactly: a defective guard checks its
`allow_live` flag only on the path-omitted branch, and the three-assertion
test written by reading that guard's branches (in-repo target refused,
default-with-flag-absent refused, outside path returns absolute) is green
without ever calling the guard with the live-store path itself -- the
literal hostile input the finding named. Every fixture path here is
synthetic (`/tmp/fake-live-store/...`), never a real user path.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

# Make scripts/ importable
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import hostile_input_gate  # noqa: E402
import hostile_input_gate as hig  # noqa: E402  (short alias used by the timeout tests)


# Synthetic hostile input: the literal live-store path the finding named.
LIVE_STORE_PATH = "/tmp/fake-live-store/entries.db"

# A defective guard: allow_live is checked ONLY on the path-omitted branch.
# An explicitly named path -- including the live-store path itself -- walks
# straight past the check and comes back unchanged.
GUARD_SOURCE = '''"""Fixture guard reproducing a known defect shape."""
import os

LIVE_STORE = "%s"


def resolve_target(path=None, allow_live=False):
    if path is None:
        if not allow_live:
            return None  # refused: no explicit path and allow_live not set
        return LIVE_STORE
    # BUG: an explicitly named path bypasses the allow_live check entirely,
    # including when that path IS the live store.
    return os.path.abspath(path)
''' % LIVE_STORE_PATH

# The real defective test: three assertions read off the guard's branches,
# never once calling resolve_target with the live-store path as an explicit
# argument. This is the test the finding was written to close, and it
# passes against the defective guard above.
DEFECTIVE_TEST_SOURCE = '''"""Three-branch test that misses the named attack."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import resolve_target


class TestResolveTargetThreeBranches(unittest.TestCase):
    def test_in_repo_target_refused(self):
        self.assertIsNone(resolve_target(allow_live=False))

    def test_default_with_flag_absent_refused(self):
        self.assertIsNone(resolve_target(path=None))

    def test_outside_path_returns_absolute(self):
        outside = "/tmp/some-other-outside-dir/output.db"
        result = resolve_target(outside)
        self.assertEqual(result, os.path.abspath(outside))


if __name__ == "__main__":
    unittest.main()
'''

# A test written from the threat: it calls the guard with the literal
# hostile input the finding named.
THREAT_TEST_SOURCE = '''"""Test written from the threat: names the attack explicitly."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import resolve_target


class TestResolveTargetAgainstNamedAttack(unittest.TestCase):
    def test_explicit_live_store_path_is_refused(self):
        self.assertIsNone(resolve_target("%s"))


if __name__ == "__main__":
    unittest.main()
''' % LIVE_STORE_PATH

# A test file that only mentions a path SEGMENT of the hostile input, never
# the full literal -- exercises the distinctive_token fallback.
PARTIAL_MENTION_SOURCE = '''"""Only mentions a path segment of the hostile input, not the full literal."""
import unittest


class TestPartialMention(unittest.TestCase):
    def test_mentions_fake_live_store_by_name(self):
        note = "the guard must protect the fake-live-store directory contents"
        self.assertIn("fake-live-store", note)


if __name__ == "__main__":
    unittest.main()
'''


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class TestThreeBranchTestMissesTheNamedAttack(unittest.TestCase):
    """Conviction 1: the existing green test does not contain the attack."""

    def test_the_three_branch_test_misses_the_named_attack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write(repo / "guard.py", GUARD_SOURCE)
            test_path = repo / "test_guard.py"
            _write(test_path, DEFECTIVE_TEST_SOURCE)

            # Sanity: the defective three-branch test is green on its own.
            baseline = subprocess.run(
                [sys.executable, "test_guard.py"],
                cwd=str(repo),
                capture_output=True,
                text=True,
            )
            self.assertEqual(baseline.returncode, 0, baseline.stdout + baseline.stderr)

            result = hostile_input_gate.check_hostile_input_present(
                [LIVE_STORE_PATH], [str(test_path)]
            )
            self.assertEqual(result["verdict"], "hostile_input_absent")
            self.assertFalse(result["all_present"])
            self.assertIn(LIVE_STORE_PATH, result["absent"])
            entry = result["hostile_inputs"][0]
            self.assertFalse(entry["present"])
            self.assertIsNone(entry["matched_via"])
            self.assertIsNone(entry["matched_in"])

            cli = subprocess.run(
                [
                    sys.executable, str(_SCRIPTS / "hostile_input_gate.py"), "check",
                    "--hostile-input", LIVE_STORE_PATH,
                    "--test-file", str(test_path),
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
            payload = json.loads(cli.stdout)
            self.assertEqual(payload["verdict"], "hostile_input_absent")


class TestMutantSurvivesTheBranchOnlyTest(unittest.TestCase):
    """Conviction 2: disabling the guard leaves the branch-only test green."""

    def test_mutant_survives_the_branch_only_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            guard_path = repo / "guard.py"
            _write(guard_path, GUARD_SOURCE)
            _write(repo / "test_guard.py", DEFECTIVE_TEST_SOURCE)

            result = hostile_input_gate.mutant_turns_tests_red(
                str(guard_path),
                "resolve_target",
                f"{sys.executable} test_guard.py",
                repo=str(repo),
            )

            self.assertEqual(result["baseline"]["returncode"], 0, result)
            self.assertEqual(result["mutant"]["returncode"], 0, result)
            self.assertEqual(result["verdict"], "mutant_survived")
            self.assertTrue(result["restored"])

            cli = subprocess.run(
                [
                    sys.executable, str(_SCRIPTS / "hostile_input_gate.py"), "mutate",
                    "--guard-file", str(guard_path),
                    "--guard-symbol", "resolve_target",
                    "--test-cmd", f"{sys.executable} test_guard.py",
                    "--repo", str(repo),
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
            payload = json.loads(cli.stdout)
            self.assertEqual(payload["verdict"], "mutant_survived")


class TestThreatWrittenTestPassesTheGate(unittest.TestCase):
    """Conviction 3: a threat-aware test satisfies the gate and would have
    caught the real bug."""

    def test_threat_written_test_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            guard_path = repo / "guard.py"
            _write(guard_path, GUARD_SOURCE)
            threat_test_path = repo / "test_guard_threat.py"
            _write(threat_test_path, THREAT_TEST_SOURCE)

            # Prove the threat-written test FAILS against the defective guard
            # -- it would have caught the real bug.
            defective_run = subprocess.run(
                [sys.executable, "test_guard_threat.py"],
                cwd=str(repo),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(
                defective_run.returncode, 0,
                defective_run.stdout + defective_run.stderr,
            )

            result = hostile_input_gate.check_hostile_input_present(
                [LIVE_STORE_PATH], [str(threat_test_path)]
            )
            self.assertEqual(result["verdict"], "hostile_input_covered")
            self.assertTrue(result["all_present"])
            entry = result["hostile_inputs"][0]
            self.assertTrue(entry["present"])
            self.assertEqual(entry["matched_via"], "literal")

            cli = subprocess.run(
                [
                    sys.executable, str(_SCRIPTS / "hostile_input_gate.py"), "check",
                    "--hostile-input", LIVE_STORE_PATH,
                    "--test-file", str(threat_test_path),
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)


class TestMutantIsAlwaysRestored(unittest.TestCase):
    """Conviction 4: the guard file is byte-identical after mutate, even on
    the mutant_survived path."""

    def test_mutant_is_always_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            guard_path = repo / "guard.py"
            _write(guard_path, GUARD_SOURCE)
            _write(repo / "test_guard.py", DEFECTIVE_TEST_SOURCE)
            original_bytes = guard_path.read_bytes()

            result = hostile_input_gate.mutant_turns_tests_red(
                str(guard_path),
                "resolve_target",
                f"{sys.executable} test_guard.py",
                repo=str(repo),
            )

            self.assertEqual(result["verdict"], "mutant_survived")
            self.assertTrue(result["restored"])
            self.assertNotIn("restore_failed", result)
            self.assertEqual(guard_path.read_bytes(), original_bytes)


class TestMatchedViaDistinctiveToken(unittest.TestCase):
    """Conviction 5: a weak (segment-only) match is labeled distinctive_token,
    never silently reported as literal."""

    def test_matched_via_distinctive_token_when_only_segment_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            partial_path = repo / "test_partial_mention.py"
            _write(partial_path, PARTIAL_MENTION_SOURCE)

            result = hostile_input_gate.check_hostile_input_present(
                [LIVE_STORE_PATH], [str(partial_path)]
            )
            entry = result["hostile_inputs"][0]
            self.assertTrue(entry["present"])
            self.assertEqual(entry["matched_via"], "distinctive_token")
            self.assertNotEqual(entry["matched_via"], "literal")
            self.assertEqual(result["verdict"], "hostile_input_covered")


class TestMutantRunTimeoutIsNotAPass(unittest.TestCase):
    """A run we could not observe must never read as 'the guard is tested'.

    Without this, a hung test command would return exit 0 from `mutate` — the
    same fail-open shape the gate exists to catch, one level up.
    """

    def test_timeout_reports_timeout_and_restores_and_does_not_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            guard = root / "guard.py"
            _write(guard, "def resolve_target(path=None, allow_live=False):\n    return path\n")
            original = guard.read_bytes()

            # A command that outlives the timeout the gate passes to subprocess.
            hang = f"{sys.executable} -c \"import time; time.sleep(30)\""

            with unittest.mock.patch.object(hig, "MUTANT_TEST_TIMEOUT_SEC", 1):
                result = hig.mutant_turns_tests_red(
                    str(guard), "resolve_target", hang, repo=str(root)
                )

            self.assertEqual(result["verdict"], "mutant_run_timeout")
            self.assertTrue(result["restored"], "guard file must be restored after a timeout")
            self.assertEqual(guard.read_bytes(), original)
            self.assertNotIn("HOSTILE_INPUT_GATE_MUTANT", guard.read_text(encoding="utf-8"))

    def test_cli_exits_two_not_zero_on_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            guard = root / "guard.py"
            _write(guard, "def resolve_target(path=None, allow_live=False):\n    return path\n")
            hang = f"{sys.executable} -c \"import time; time.sleep(30)\""

            with unittest.mock.patch.object(hig, "MUTANT_TEST_TIMEOUT_SEC", 1):
                code = hig.main([
                    "mutate", "--guard-file", str(guard), "--guard-symbol", "resolve_target",
                    "--test-cmd", hang, "--repo", str(root), "--json",
                ])
            self.assertEqual(code, 2, "an unobserved run must not exit 0")


if __name__ == "__main__":
    unittest.main()
