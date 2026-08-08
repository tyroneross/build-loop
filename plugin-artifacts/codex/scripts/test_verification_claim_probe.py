#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
Tests for verification_claim_probe.py.

The convicting mutant: a subagent claimed "verified by reproducing the
auditor's exact attack (exit 2, store still 0)" while the shipped code
actually exits 0. test_claimed_exit_2_but_actually_exit_0_is_contradicted
reproduces that exact failure shape and asserts the probe calls it
`contradicted`, not `executed`.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "verification_claim_probe.py"

# Import the module under test
sys.path.insert(0, str(HERE))
from verification_claim_probe import extract_claims, probe  # noqa: E402


def _write_exit_script(tmpdir: Path, name: str, code: int) -> Path:
    path = tmpdir / name
    path.write_text(textwrap.dedent(f"""\
        import sys
        sys.exit({code})
        """))
    return path


class TestContradictedClaimMutant(unittest.TestCase):
    """The acceptance criterion: a false 'exit 2' claim must be contradicted."""

    def test_claimed_exit_2_but_actually_exit_0_is_contradicted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=0)

            claims = [{
                "claim": (
                    "Fixed — guard refuses the live store; verified by "
                    "reproducing the auditor's exact attack (exit 2, store still 0)."
                ),
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]

            results = probe(claims, cwd=str(tmpdir), timeout=10)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "contradicted")
            self.assertEqual(results[0]["actual"]["returncode"], 0)

            counts = {"executed": 0, "contradicted": 0, "cited": 0, "error": 0}
            for r in results:
                counts[r["status"]] += 1
            self.assertEqual(counts["contradicted"], 1)

            verdict = "contradicted_claims_present" if counts["contradicted"] else "clean"
            self.assertEqual(verdict, "contradicted_claims_present")

    def test_cli_exit_code_1_on_contradicted_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=0)

            claims_path = tmpdir / "claims.json"
            claims_path.write_text(json.dumps([{
                "claim": "verified by reproducing the auditor's exact attack (exit 2, store still 0)",
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]))

            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--claims-file", str(claims_path), "--cwd", str(tmpdir)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["verdict"], "contradicted_claims_present")
            self.assertEqual(payload["counts"]["contradicted"], 1)


class TestGenuinelyVerifiedClaim(unittest.TestCase):
    """Mirror case: command genuinely exits as claimed → executed, exit 0."""

    def test_claimed_exit_2_and_actually_exit_2_is_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=2)

            claims = [{
                "claim": "verified by reproducing the auditor's exact attack (exit 2, store still 0)",
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]

            results = probe(claims, cwd=str(tmpdir), timeout=10)

            self.assertEqual(results[0]["status"], "executed")
            self.assertEqual(results[0]["actual"]["returncode"], 2)

    def test_cli_exit_code_0_when_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=2)

            claims_path = tmpdir / "claims.json"
            claims_path.write_text(json.dumps([{
                "claim": "verified by reproducing the auditor's exact attack (exit 2, store still 0)",
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]))

            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--claims-file", str(claims_path), "--cwd", str(tmpdir)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["verdict"], "clean")
            self.assertEqual(payload["counts"]["executed"], 1)


class TestExtraction(unittest.TestCase):
    """extract_claims pulls the command + expectation out of free prose."""

    def test_extracts_command_and_exit_2_from_literal_sentence(self) -> None:
        text = (
            "Fixed — guard refuses the live store; verified by reproducing the "
            "auditor's exact attack (exit 2, store still 0) via "
            "`python3 attack.py --store /tmp/store`."
        )

        claims = extract_claims(text)

        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual(claim["command"], "python3 attack.py --store /tmp/store")
        self.assertEqual(claim["expected"], {"returncode": 2})
        self.assertEqual(claim["source_line"], 1)

    def test_command_without_verification_verb_is_not_a_claim(self) -> None:
        text = "See `python3 attack.py --store /tmp/store` for the harness entrypoint."
        claims = extract_claims(text)
        self.assertEqual(claims, [])

    def test_extracts_from_multiline_report(self) -> None:
        text = textwrap.dedent("""\
            ## Fix summary

            Guard now refuses writes to the live store.

            Verified by running `pytest tests/test_guard.py -k live_store` — 3 passed.
            """)
        claims = extract_claims(text)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["command"], "pytest tests/test_guard.py -k live_store")
        self.assertEqual(claims[0]["source_line"], 5)


class TestDestructiveCommandsAreNeverExecuted(unittest.TestCase):
    """A destructive command is cited, never run — assert the target survives."""

    def test_rm_is_cited_and_file_survives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            target = tmpdir / "do_not_delete.txt"
            target.write_text("precious")

            claims = [{
                "claim": "verified by cleanup: exit 0",
                "command": f"rm -rf {target}",
                "expected": {"returncode": 0},
            }]

            results = probe(claims, cwd=str(tmpdir), timeout=10)

            self.assertEqual(results[0]["status"], "cited")
            self.assertIn("not_safely_re-executable", results[0]["reason"])
            self.assertTrue(target.exists(), "rm must never actually run")

    def test_git_push_is_cited_and_never_executed(self) -> None:
        claims = [{
            "claim": "verified by pushing: exit 0",
            "command": "git push origin main",
            "expected": {"returncode": 0},
        }]

        results = probe(claims, cwd=".", timeout=10)

        self.assertEqual(results[0]["status"], "cited")
        self.assertIn("not_safely_re-executable", results[0]["reason"])
        self.assertIsNone(results[0]["actual"])

    def test_destructive_command_never_appears_as_executed_or_contradicted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            target = tmpdir / "keepsake.txt"
            target.write_text("keep me")

            claims = [
                {"claim": "ran `rm -rf .` and confirmed exit 0", "command": f"rm -rf {target}",
                 "expected": {"returncode": 0}},
                {"claim": "verified deploy: exit 0", "command": "deploy --prod", "expected": {"returncode": 0}},
                {"claim": "verified with sudo: exit 0", "command": "sudo rm -rf /", "expected": {"returncode": 0}},
            ]

            results = probe(claims, cwd=str(tmpdir), timeout=10)

            for r in results:
                self.assertEqual(r["status"], "cited")
            self.assertTrue(target.exists())


class TestTimeoutHandling(unittest.TestCase):
    """A command that exceeds the timeout errors out instead of crashing."""

    def test_timeout_yields_error_status_not_a_crash(self) -> None:
        claims = [{
            "claim": "verified by running the long check: exit 0",
            "command": f"{sys.executable} -c \"import time; time.sleep(5)\"",
            "expected": {"returncode": 0},
        }]

        results = probe(claims, cwd=".", timeout=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("timeout", results[0]["reason"])


class TestNoStatedExpectation(unittest.TestCase):
    def test_no_expected_runs_but_is_cited(self) -> None:
        claims = [{"claim": "ran the smoke check", "command": f"{sys.executable} -c \"print('ok')\""}]
        results = probe(claims, cwd=".", timeout=10)
        self.assertEqual(results[0]["status"], "cited")
        self.assertEqual(results[0]["reason"], "no_stated_expectation")
        self.assertIsNotNone(results[0]["actual"])
        self.assertEqual(results[0]["actual"]["returncode"], 0)


class TestMarkdownRendering(unittest.TestCase):
    def test_markdown_mode_labels_each_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = _write_exit_script(tmpdir, "shipped_guard.py", code=0)
            claims_path = tmpdir / "claims.json"
            claims_path.write_text(json.dumps([{
                "claim": "verified by reproducing the auditor's exact attack (exit 2, store still 0)",
                "command": f"{sys.executable} {script}",
                "expected": {"returncode": 2},
            }]))

            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--claims-file", str(claims_path),
                 "--cwd", str(tmpdir), "--markdown"],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("contradicted:", r.stdout)


if __name__ == "__main__":
    unittest.main()
