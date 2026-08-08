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

import hashlib
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

# F4 regression fixture: the hostile input passes through a macOS-shaped
# "Application Support" directory segment; the test file below mentions a
# DIFFERENT "Application Support" path entirely -- never the hostile input's
# own distinctive segment ("fake-live-store"). This is the incident's own
# shape: a two-word common-path segment must never act as a distinguishing
# token, and a test that merely brushes past a path of that shape must not
# close the finding.
APP_SUPPORT_HOSTILE_INPUT = "/tmp/fake-live-store/Application Support/RossLabs/store.db"

DIFFERENT_APP_SUPPORT_MENTION_SOURCE = '''"""Mentions a different Application Support path than the hostile input."""
import unittest


class TestMentionsDifferentAppSupportPath(unittest.TestCase):
    def test_mentions_some_other_app_support_path(self):
        other_path = "/tmp/other-fixture/Application Support/OtherVendor/unrelated.db"
        self.assertIn("Application Support", other_path)


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


class TestMutantNeverTouchesTheLiveCheckout(unittest.TestCase):
    """Conviction 7 (F10): when the guard file lives inside a git repo, the
    mutant must never land in the LIVE checkout -- not even transiently
    while the test command is running. The test command itself reads the
    live guard path and records its hash to a side file; that hash must
    match the untouched original at every point during the run."""

    def test_mutant_never_touches_the_live_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), check=True)

            guard_path = repo / "guard.py"
            _write(guard_path, GUARD_SOURCE)
            original_bytes = guard_path.read_bytes()
            original_hash = hashlib.sha256(original_bytes).hexdigest()

            hash_side_file = repo / "live_hash_during_run.txt"

            # This test command reads the LIVE guard path (not whatever copy
            # it's running against) and records its hash while it runs --
            # proof that the process running the test never saw a mutated
            # live file, no matter where the test itself was materialized.
            checker_test = repo / "test_reads_live_guard.py"
            _write(
                checker_test,
                (
                    "import hashlib\n"
                    "import unittest\n"
                    "from pathlib import Path\n\n"
                    f"LIVE_GUARD = {str(guard_path)!r}\n"
                    f"SIDE_FILE = {str(hash_side_file)!r}\n\n"
                    "class TestReadsLiveGuard(unittest.TestCase):\n"
                    "    def test_records_live_guard_hash(self):\n"
                    "        data = Path(LIVE_GUARD).read_bytes()\n"
                    "        digest = hashlib.sha256(data).hexdigest()\n"
                    "        Path(SIDE_FILE).write_text(digest)\n"
                    "        self.assertTrue(True)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                ),
            )

            subprocess.run(
                ["git", "add", "guard.py", "test_reads_live_guard.py"],
                cwd=str(repo), check=True,
            )

            result = hostile_input_gate.mutant_turns_tests_red(
                str(guard_path),
                "resolve_target",
                f"{sys.executable} test_reads_live_guard.py",
                repo=str(repo),
            )

            self.assertTrue(result.get("isolated"), result)
            # The live file was never mutated, at any point during the run.
            self.assertEqual(guard_path.read_bytes(), original_bytes)
            self.assertNotIn("HOSTILE_INPUT_GATE_MUTANT", guard_path.read_text(encoding="utf-8"))

            self.assertTrue(hash_side_file.exists(), "test command never ran against the materialized copy")
            recorded_hash = hash_side_file.read_text().strip()
            self.assertEqual(recorded_hash, original_hash)


class TestMatchedViaDistinctiveToken(unittest.TestCase):
    """Conviction 5: a weak (segment-only) match is labeled distinctive_token,
    never silently reported as literal -- and (updated for F4) a weak-only
    match no longer closes the finding by itself: the verdict is
    `hostile_input_weak_match_only`, not `hostile_input_covered`, and the CLI
    exits 1 for it, unless the caller explicitly opts in."""

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
            # A weak-only match is still `present`, but the top-level verdict
            # must not read as full coverage.
            self.assertEqual(result["verdict"], "hostile_input_weak_match_only")
            self.assertNotEqual(result["verdict"], "hostile_input_covered")

            cli = subprocess.run(
                [
                    sys.executable, str(_SCRIPTS / "hostile_input_gate.py"), "check",
                    "--hostile-input", LIVE_STORE_PATH,
                    "--test-file", str(partial_path),
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
            payload = json.loads(cli.stdout)
            self.assertEqual(payload["verdict"], "hostile_input_weak_match_only")


class TestWeakTokenMatchDoesNotCloseTheFinding(unittest.TestCase):
    """Conviction 6 (F4) -- THE CONVICTION: the gate must not pass the defect
    it was built from. A hostile input that passes through an
    `Application Support`-shaped directory segment, checked against a test
    file that only mentions a DIFFERENT `Application Support` path, must not
    be reported as covered."""

    def test_weak_token_match_does_not_close_the_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            test_path = repo / "test_mentions_other_app_support.py"
            _write(test_path, DIFFERENT_APP_SUPPORT_MENTION_SOURCE)

            result = hostile_input_gate.check_hostile_input_present(
                [APP_SUPPORT_HOSTILE_INPUT], [str(test_path)]
            )
            self.assertNotEqual(result["verdict"], "hostile_input_covered")

            cli = subprocess.run(
                [
                    sys.executable, str(_SCRIPTS / "hostile_input_gate.py"), "check",
                    "--hostile-input", APP_SUPPORT_HOSTILE_INPUT,
                    "--test-file", str(test_path),
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
            payload = json.loads(cli.stdout)
            self.assertNotEqual(payload["verdict"], "hostile_input_covered")


class TestApplicationSupportIsNotDistinctiveToken(unittest.TestCase):
    """F4: `_distinctive_token` must not hand back a common two-word macOS
    path segment as if it were a distinguishing token."""

    def test_application_support_is_not_a_distinctive_token(self) -> None:
        token = hostile_input_gate._distinctive_token(APP_SUPPORT_HOSTILE_INPUT)
        self.assertNotEqual(token, "Application Support")


class TestAcceptWeakMatchFlagOptsIn(unittest.TestCase):
    """--accept-weak-match is the explicit, off-by-default override for a
    genuine weak (segment-only) match."""

    def test_accept_weak_match_flag_opts_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            partial_path = repo / "test_partial_mention.py"
            _write(partial_path, PARTIAL_MENTION_SOURCE)

            without_flag = subprocess.run(
                [
                    sys.executable, str(_SCRIPTS / "hostile_input_gate.py"), "check",
                    "--hostile-input", LIVE_STORE_PATH,
                    "--test-file", str(partial_path),
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(without_flag.returncode, 1, without_flag.stdout + without_flag.stderr)
            self.assertEqual(json.loads(without_flag.stdout)["verdict"], "hostile_input_weak_match_only")

            with_flag = subprocess.run(
                [
                    sys.executable, str(_SCRIPTS / "hostile_input_gate.py"), "check",
                    "--hostile-input", LIVE_STORE_PATH,
                    "--test-file", str(partial_path),
                    "--accept-weak-match",
                    "--json",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(with_flag.returncode, 0, with_flag.stdout + with_flag.stderr)
            self.assertEqual(json.loads(with_flag.stdout)["verdict"], "hostile_input_covered")


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
