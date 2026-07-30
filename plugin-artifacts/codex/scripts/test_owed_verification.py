#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/owed_verification.py (GAP-1 owed-verification manifest).

Mandated by the build-loop SELF-MOD SAFETY GATE for any new script.  Covers:

- ``write`` creates a manifest that ``check`` reads back as INCOMPLETE (exit 1).
- ``clear`` of the last owed verifier flips ``check`` to COMPLETE (exit 0) and
  removes the manifest.
- Partial ``clear`` leaves the rest owed (still INCOMPLETE).
- ``check`` on a fresh repo (no manifest) is COMPLETE/absent (exit 0).
- ``write`` flips ``state.json.review_incomplete`` true; full ``clear`` flips it
  back to false.
- ``write`` merges (accumulates) owed verifiers across calls and never re-adds a
  cleared verifier.
- ``--all`` clears every owed verifier at once.
- A malformed manifest reads as INCOMPLETE (fail-safe).
- Dispatch commands are emitted per owed verifier with the diff range filled in.
- state.json update is best-effort: no state.json → manifest still written,
  ``_state_updated`` false.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = Path(__file__).resolve().parent / "owed_verification.py"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import owed_verification as ov  # noqa: E402


def _run_cli(workdir: Path, *args: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--workdir", str(workdir), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"_stdout": proc.stdout, "_stderr": proc.stderr}
    return proc.returncode, payload


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        (self.workdir / ".build-loop").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_state(self, data: dict) -> None:
        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def _read_state(self) -> dict:
        return json.loads(
            (self.workdir / ".build-loop" / "state.json").read_text(encoding="utf-8")
        )

    @property
    def _manifest(self) -> Path:
        return self.workdir / ov.MANIFEST_RELPATH


# ---------------------------------------------------------------------------
# Importable-surface round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip(_Base):
    def test_write_check_clear_roundtrip(self) -> None:
        # write → INCOMPLETE
        ov.write_manifest(
            self.workdir,
            run_id="run_x",
            diff_range="HEAD~2..HEAD",
            owed=["independent-auditor", "plan-critic"],
        )
        chk = ov.check_manifest(self.workdir)
        self.assertEqual(chk["status"], "incomplete")
        self.assertEqual(set(chk["owed"]), {"independent-auditor", "plan-critic"})
        self.assertTrue(chk["review_incomplete"])
        self.assertTrue(self._manifest.exists())

        # partial clear → still INCOMPLETE
        res = ov.clear_verifiers(self.workdir, verifiers=["plan-critic"])
        self.assertEqual(res["status"], "incomplete")
        self.assertEqual(res["remaining"], ["independent-auditor"])
        self.assertTrue(self._manifest.exists())

        # clear last → COMPLETE, manifest removed
        res = ov.clear_verifiers(self.workdir, verifiers=["independent-auditor"])
        self.assertEqual(res["status"], "complete")
        self.assertTrue(res["manifest_removed"])
        self.assertFalse(self._manifest.exists())

        chk = ov.check_manifest(self.workdir)
        self.assertEqual(chk["status"], "absent")
        self.assertFalse(chk["review_incomplete"])

    def test_clear_all(self) -> None:
        ov.write_manifest(
            self.workdir,
            run_id="run_y",
            diff_range="HEAD~1..HEAD",
            owed=["independent-auditor", "security-reviewer", "plan-critic"],
        )
        res = ov.clear_verifiers(self.workdir, clear_all=True)
        self.assertEqual(res["status"], "complete")
        self.assertEqual(res["remaining"], [])
        self.assertFalse(self._manifest.exists())

    def test_write_merges_and_never_readds_cleared(self) -> None:
        ov.write_manifest(
            self.workdir, run_id="r", diff_range="A..B", owed=["independent-auditor"]
        )
        # second write for a later chunk adds another verifier
        m = ov.write_manifest(
            self.workdir, run_id="r", diff_range="A..B", owed=["security-reviewer"]
        )
        self.assertEqual(set(m["owed"]), {"independent-auditor", "security-reviewer"})

        # clear the auditor, then a later write must NOT re-add it
        ov.clear_verifiers(self.workdir, verifiers=["independent-auditor"])
        m2 = ov.write_manifest(
            self.workdir, run_id="r", diff_range="A..B", owed=["independent-auditor"]
        )
        self.assertNotIn("independent-auditor", m2["owed"])
        self.assertIn("independent-auditor", m2["cleared"])

    def test_dispatch_commands_filled(self) -> None:
        m = ov.write_manifest(
            self.workdir,
            run_id="r",
            diff_range="HEAD~5..HEAD",
            owed=["independent-auditor"],
        )
        cmd = m["dispatch_commands"]["independent-auditor"]
        self.assertIn("HEAD~5..HEAD", cmd)
        self.assertIn("independent-auditor", cmd)


# ---------------------------------------------------------------------------
# state.json mirror flag
# ---------------------------------------------------------------------------


class TestStateFlag(_Base):
    def test_state_flag_flips(self) -> None:
        self._write_state({"execution": {"build_loop_id": "run_z"}})
        ov.write_manifest(
            self.workdir, run_id="run_z", diff_range="A..B", owed=["independent-auditor"]
        )
        self.assertTrue(self._read_state()["review_incomplete"])

        ov.clear_verifiers(self.workdir, clear_all=True)
        self.assertFalse(self._read_state()["review_incomplete"])
        # existing keys preserved
        self.assertEqual(self._read_state()["execution"]["build_loop_id"], "run_z")

    def test_state_update_best_effort_when_absent(self) -> None:
        # no state.json — manifest still written, _state_updated false
        m = ov.write_manifest(
            self.workdir, run_id="r", diff_range="A..B", owed=["plan-critic"]
        )
        self.assertFalse(m["_state_updated"])
        self.assertTrue(self._manifest.exists())


# ---------------------------------------------------------------------------
# Fail-safe / edge cases
# ---------------------------------------------------------------------------


class TestFailSafe(_Base):
    def test_malformed_manifest_reads_incomplete(self) -> None:
        self._manifest.write_text("{ this is not json", encoding="utf-8")
        chk = ov.check_manifest(self.workdir)
        self.assertEqual(chk["status"], "incomplete")
        self.assertTrue(chk["malformed"])
        self.assertTrue(chk["review_incomplete"])

    def test_check_absent_is_complete(self) -> None:
        chk = ov.check_manifest(self.workdir)
        self.assertEqual(chk["status"], "absent")
        self.assertFalse(chk["review_incomplete"])

    def test_clear_absent_is_noop(self) -> None:
        res = ov.clear_verifiers(self.workdir, verifiers=["independent-auditor"])
        self.assertEqual(res["action"], "noop_absent")


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------


class TestCLI(_Base):
    def test_cli_write_check_clear_exit_codes(self) -> None:
        rc, _ = _run_cli(
            self.workdir,
            "write",
            "--run-id",
            "run_cli",
            "--diff-range",
            "HEAD~1..HEAD",
            "--owe",
            "independent-auditor",
        )
        self.assertEqual(rc, 0)

        # check → INCOMPLETE → exit 1
        rc, payload = _run_cli(self.workdir, "check")
        self.assertEqual(rc, 1)
        self.assertEqual(payload["status"], "incomplete")

        # clear → exit 0
        rc, _ = _run_cli(self.workdir, "clear", "--all")
        self.assertEqual(rc, 0)

        # check → COMPLETE → exit 0
        rc, payload = _run_cli(self.workdir, "check")
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "absent")

    def test_cli_check_clean_repo_exit_0(self) -> None:
        rc, payload = _run_cli(self.workdir, "check")
        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "absent")

    def test_cli_write_comma_separated_owed(self) -> None:
        rc, payload = _run_cli(
            self.workdir,
            "write",
            "--run-id",
            "r",
            "--diff-range",
            "A..B",
            "--owed",
            "independent-auditor,plan-critic,security-reviewer",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(payload["owed"]), 3)

    def test_cli_write_requires_a_verifier(self) -> None:
        rc, _ = _run_cli(
            self.workdir, "write", "--run-id", "r", "--diff-range", "A..B"
        )
        self.assertEqual(rc, 2)  # argparse error


if __name__ == "__main__":
    unittest.main()
