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


# ---------------------------------------------------------------------------
# The SECOND debt: cross-vendor-audit
#
# `scripts/review_trigger.py` has computed `cross_vendor_required` since QM
# v0.13.0 and nothing owed the round back, so it degraded to an advisory note.
# Measured on bl-20260912T180923Z-claude_code-selfmodrevert: the trigger said
# true for a 1282-line diff, the round was skipped, the run read clean, and
# running the round afterwards returned 11 findings (6 Critical) disjoint from
# the same-vendor auditor's. These tests are the arm / check / refuse / clear
# lifecycle, each one red under its planted defect.
# ---------------------------------------------------------------------------

# Paths whose names carry the profiler's own high-risk signals (auth + network),
# so `review_trigger.build_profile` returns cross_vendor_required true.
HIGH_RISK_FILES = ["src/auth/session.ts", "server/api/route.ts"]
# One doc file: no risk signal, single file, so the profile is `skip`.
LOW_RISK_FILES = ["README.md"]

AUDITOR_VERDICT = {"judge_id": "independent-auditor", "verdict": "yay"}
# A real second-vendor entry NAMES its vendor. Without that field the id is
# just a label an agent typed, which is what the debt exists to prevent.
CROSS_VENDOR_VERDICT = {
    "judge_id": "cross-vendor-audit", "verdict": "yay",
    "vendor": "openai/codex-cli 0.154.0",
}


def _record(**overrides) -> dict:
    """A run record that owes NOTHING by default.

    The auditor verdict is present on purpose: without it every record owes
    `independent-auditor` too, and a test asserting "arms nothing" would pass
    for the wrong reason.
    """
    record = {
        "run_id": "run_cv",
        "outcome": "pass",
        "filesTouched": list(LOW_RISK_FILES),
        "judge_decisions": [dict(AUDITOR_VERDICT)],
        "auditor_status": "ran:dispatched-agent",
    }
    record.update(overrides)
    return record


class TestCrossVendorDebt(_Base):
    def _enforce(self, record: dict) -> dict | None:
        return ov.enforce_for_run_record(
            self.workdir, record, written_by="test", diff_range="A..B"
        )

    # (a) a diff that triggers cross_vendor_required arms the debt
    def test_high_risk_diff_arms_the_cross_vendor_debt(self) -> None:
        manifest = self._enforce(_record(filesTouched=list(HIGH_RISK_FILES)))
        self.assertIsNotNone(manifest)
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["owed"])
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["dispatch_commands"])
        self.assertIn("A..B", manifest["dispatch_commands"][ov.CROSS_VENDOR_VERIFIER])
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["reasons"])

    def test_recorded_profile_arms_the_debt_without_rederiving(self) -> None:
        """A recorded `cross_vendor_required: true` is authoritative.

        The recorded value came from running review_trigger on the REAL diff;
        re-derivation only sees the file names the record kept.
        """
        manifest = self._enforce(
            _record(review_trigger={"cross_vendor_required": True})
        )
        self.assertIsNotNone(manifest)
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["owed"])

    def test_same_vendor_auditor_verdict_does_not_discharge_it(self) -> None:
        """The measured value of the round was findings the auditor did NOT reach."""
        manifest = self._enforce(
            _record(
                filesTouched=list(HIGH_RISK_FILES),
                judge_decisions=[dict(AUDITOR_VERDICT)],
            )
        )
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["owed"], [ov.CROSS_VENDOR_VERIFIER])

    def test_pending_cross_vendor_entry_is_not_a_verdict(self) -> None:
        manifest = self._enforce(
            _record(
                filesTouched=list(HIGH_RISK_FILES),
                judge_decisions=[
                    dict(AUDITOR_VERDICT),
                    {"judge_id": "cross-vendor-audit", "verdict": "pending"},
                ],
            )
        )
        self.assertIsNotNone(manifest)
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["owed"])

    # (b) `check` exits 1 while owed
    def test_check_exits_1_while_the_cross_vendor_debt_is_owed(self) -> None:
        self._enforce(_record(filesTouched=list(HIGH_RISK_FILES)))
        rc, payload = _run_cli(self.workdir, "check")
        self.assertEqual(rc, 1)
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, payload["owed"])

    # (d) recording a verdict clears it
    def test_recording_a_cross_vendor_verdict_clears_the_debt(self) -> None:
        record = _record(filesTouched=list(HIGH_RISK_FILES))
        self._enforce(record)
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "incomplete")

        record["judge_decisions"] = [dict(AUDITOR_VERDICT), dict(CROSS_VENDOR_VERDICT)]
        self._enforce(record)

        chk = ov.check_manifest(self.workdir)
        self.assertEqual(chk["status"], "absent")
        self.assertFalse(chk["review_incomplete"])

    def test_a_manual_clear_is_not_re_armed_on_the_next_write(self) -> None:
        """The escape valve for an unreachable peer host has to actually hold.

        A full clear deletes the manifest and its `cleared` list, so without the
        run-scoped tombstone the next run-record write re-arms the debt and a
        machine with no second vendor could never close a run.
        """
        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": []}), encoding="utf-8"
        )
        record = _record(filesTouched=list(HIGH_RISK_FILES))
        self._enforce(record)
        ov.clear_verifiers(
            self.workdir,
            verifiers=[ov.CROSS_VENDOR_VERIFIER],
            reason="no peer host reachable",
        )
        self.assertIsNone(self._enforce(record))
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "absent")

    # (e) a diff below threshold arms nothing
    def test_low_risk_diff_arms_nothing(self) -> None:
        self.assertIsNone(self._enforce(_record()))
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "absent")

    def test_recorded_profile_false_suppresses_the_debt(self) -> None:
        self.assertIsNone(
            self._enforce(
                _record(
                    filesTouched=list(HIGH_RISK_FILES),
                    review_trigger={"cross_vendor_required": False},
                )
            )
        )

    def test_a_run_that_touched_nothing_owes_nothing(self) -> None:
        self.assertIsNone(self._enforce(_record(filesTouched=[], judge_decisions=[])))

    def test_both_debts_arm_together_and_discharge_independently(self) -> None:
        record = _record(
            filesTouched=list(HIGH_RISK_FILES),
            judge_decisions=[],
            auditor_status="not-run:parent-must-dispatch",
        )
        manifest = self._enforce(record)
        self.assertEqual(
            sorted(manifest["owed"]),
            sorted([ov.AUTO_OWED_VERIFIER, ov.CROSS_VENDOR_VERIFIER]),
        )
        record["judge_decisions"] = [dict(AUDITOR_VERDICT)]
        record["auditor_status"] = "ran:dispatched-agent"
        manifest = self._enforce(record)
        self.assertEqual(manifest["owed"], [ov.CROSS_VENDOR_VERIFIER])

    def test_an_explicit_write_of_another_verifier_is_never_auto_cleared(self) -> None:
        """Auto-discharge covers MANAGED debts only."""
        ov.write_manifest(
            self.workdir,
            run_id="run_cv",
            diff_range="A..B",
            owed=["plan-critic"],
        )
        self._enforce(_record())
        self.assertEqual(ov.check_manifest(self.workdir)["owed"], ["plan-critic"])


# ---------------------------------------------------------------------------
# (c) the close gate refuses a report while the debt is owed
# ---------------------------------------------------------------------------


class TestCloseGateRefusesOwedReview(_Base):
    def _seed_recorded_run(self) -> None:
        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps(
                {
                    "runs": [
                        {
                            "run_id": "run_cv",
                            "date": "2026-09-12T12:00:00Z",
                            "goal": "g",
                            "outcome": "pass",
                            "filesTouched": list(HIGH_RISK_FILES),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def test_close_gate_refuses_while_cross_vendor_is_owed(self) -> None:
        import run_close_lint

        self._seed_recorded_run()
        self.assertEqual(
            run_close_lint.check(self.workdir, run_id="run_cv")["status"], "recorded"
        )

        ov.enforce_for_run_record(
            self.workdir,
            {
                "run_id": "run_cv",
                "filesTouched": list(HIGH_RISK_FILES),
                "judge_decisions": [dict(AUDITOR_VERDICT)],
                "auditor_status": "ran:dispatched-agent",
            },
            written_by="test",
            diff_range="A..B",
        )
        envelope = run_close_lint.check(self.workdir, run_id="run_cv")
        self.assertEqual(envelope["status"], "review_owed")
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, envelope["owed"])
        self.assertIn("codex exec", envelope["remediation"])

    def test_close_gate_cli_exits_1_while_owed_and_0_after_clear(self) -> None:
        self._seed_recorded_run()
        ov.write_manifest(
            self.workdir,
            run_id="run_cv",
            diff_range="A..B",
            owed=[ov.CROSS_VENDOR_VERIFIER],
        )
        lint = Path(__file__).resolve().parent / "run_close_lint.py"
        cmd = [sys.executable, str(lint), "--workdir", str(self.workdir),
               "--run-id", "run_cv", "--json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["status"], "review_owed")

        ov.clear_verifiers(self.workdir, clear_all=True)
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["status"], "recorded")

    def test_advisory_mode_still_exits_0(self) -> None:
        """Hook callers must never be wedged by an owed review."""
        self._seed_recorded_run()
        ov.write_manifest(
            self.workdir,
            run_id="run_cv",
            diff_range="A..B",
            owed=[ov.CROSS_VENDOR_VERIFIER],
        )
        lint = Path(__file__).resolve().parent / "run_close_lint.py"
        proc = subprocess.run(
            [sys.executable, str(lint), "--workdir", str(self.workdir),
             "--run-id", "run_cv", "--advisory", "--json"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["status"], "review_owed")

    def test_a_missing_run_record_is_not_relabelled_as_owed_review(self) -> None:
        """Order matters: the more fundamental failure must keep its own name."""
        import run_close_lint

        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": []}), encoding="utf-8"
        )
        ov.write_manifest(
            self.workdir, run_id="run_cv", diff_range="A..B",
            owed=[ov.CROSS_VENDOR_VERIFIER],
        )
        self.assertEqual(
            run_close_lint.check(self.workdir, run_id="run_cv")["status"], "missing"
        )


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# The meta-defect: this module was CORRECT, COMPLETE, TESTED -- and had zero
# executable call sites. Every reference to it lived in markdown that an agent
# had to remember to obey, so the escape hatch never fired once, including on
# runs that explicitly recorded `auditor_status: not-run:parent-must-dispatch`.
# A module reachable only from prose is documentation wearing a .py extension.
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_the_manifest_writer_has_an_executable_call_site():
    """Guard against this module going back to being prose-only.

    Deliberately does NOT count markdown: prose calling itself a mandate is
    exactly the state that failed. It also does not count this test file, or
    the module itself, for the same reason -- a module imported only by its own
    tests is still unreachable from a real run.
    """
    root = _repo_root()
    module = Path(__file__).resolve().parent / "owed_verification.py"
    callers = []
    for path in sorted((root / "scripts").rglob("*.py")):
        if path.name.startswith("test_") or path == module:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "import owed_verification" in text or "owed_verification." in text:
            callers.append(str(path.relative_to(root)))

    assert callers, (
        "scripts/owed_verification.py has no executable call site. It is "
        "reachable only from markdown, which is the exact state in which the "
        "GAP-1 escape hatch never fired on five consecutive runs. Wire it into "
        "the run-close path, or delete it and stop claiming the guarantee."
    )


def test_both_run_close_writers_enforce_the_manifest():
    """Name the two writers explicitly.

    One wired writer is not the guarantee -- `append_run.py` is the path the
    five real runs took, and `write_run_entry` is the orchestrator's. A run
    closing through either must land a verdict or a manifest, so a future
    refactor that unwires one of them has to fail here rather than silently
    reopen half the hole.
    """
    scripts = Path(__file__).resolve().parent
    for name in ("append_run.py", "write_run_entry/__main__.py"):
        text = (scripts / name).read_text(encoding="utf-8")
        assert "_enforce_owed_verification" in text, (
            f"{name} no longer enforces the owed-verification manifest; a run "
            "closing through it can record no auditor verdict and owe nothing"
        )


# ---------------------------------------------------------------------------
# Regressions from the cross-vendor review round on this very change.
# Each one was reproduced by the second-vendor reviewer before it was fixed;
# each fails without its fix.
# ---------------------------------------------------------------------------


class TestCrossVendorRoundRegressions(_Base):
    def _state(self, runs: list[dict]) -> None:
        """Set runs[] without erasing the rest of state.json.

        A full overwrite would wipe `review_cleared_verifiers`, which is the
        very key the tombstone tests below are checking -- the test would then
        pass for the wrong reason and certify a hole.
        """
        path = self.workdir / ".build-loop" / "state.json"
        data: dict = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except json.JSONDecodeError:
                data = {}
        data["runs"] = runs
        path.write_text(json.dumps(data), encoding="utf-8")

    def _enforce(self, record: dict) -> dict | None:
        return ov.enforce_for_run_record(
            self.workdir, record, written_by="test", diff_range="A..B"
        )

    def test_a_goal_only_correction_cannot_discharge_a_live_debt(self) -> None:
        """Enforcement reads the PERSISTED row, not the caller's thinner view.

        Both writers upsert-merge the incoming entry onto the stored row and
        then hand enforcement the incoming entry. A correction restating only
        --goal therefore looked like a run that touched nothing and owed
        nothing, and cleared audits on a diff still carrying auth files.
        """
        stored = {
            "run_id": "run_cv",
            "filesTouched": list(HIGH_RISK_FILES),
            "judge_decisions": [dict(AUDITOR_VERDICT)],
            "auditor_status": "ran:dispatched-agent",
        }
        self._state([stored])
        self._enforce(stored)
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "incomplete")

        thin = {"run_id": "run_cv", "goal": "restated", "filesTouched": [],
                "judge_decisions": []}
        self._enforce(thin)

        chk = ov.check_manifest(self.workdir)
        self.assertEqual(chk["status"], "incomplete")
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, chk["owed"])

    def test_a_verdict_discharge_leaves_no_tombstone_so_a_wider_scope_re_arms(self) -> None:
        """A verdict suppresses re-arming on its own; a tombstone outlives it.

        When a correction expands the file set, upsert_merge drops the verdict
        that was rendered against the narrower set -- and the debt must come
        back. A tombstone would exempt the run permanently.
        """
        record = {
            "run_id": "run_cv",
            "filesTouched": list(HIGH_RISK_FILES),
            "judge_decisions": [dict(AUDITOR_VERDICT)],
            "auditor_status": "ran:dispatched-agent",
        }
        self._state([record])
        self._enforce(record)

        record["judge_decisions"] = [dict(AUDITOR_VERDICT), dict(CROSS_VENDOR_VERDICT)]
        self._state([record])
        self._enforce(record)
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "absent")

        widened = dict(record)
        widened["filesTouched"] = [*HIGH_RISK_FILES, "server/api/admin.ts"]
        widened["judge_decisions"] = [dict(AUDITOR_VERDICT)]
        self._state([widened])
        self._enforce(widened)

        self.assertIn(ov.CROSS_VENDOR_VERIFIER, ov.check_manifest(self.workdir)["owed"])

    def test_one_run_cannot_discharge_another_runs_inherited_debt(self) -> None:
        """The manifest accumulates debts across runs; its run_id is the last writer's."""
        run_a = {
            "run_id": "run_a",
            "filesTouched": list(HIGH_RISK_FILES),
            "judge_decisions": [dict(AUDITOR_VERDICT)],
            "auditor_status": "ran:dispatched-agent",
        }
        self._state([run_a])
        self._enforce(run_a)

        run_b = {
            "run_id": "run_b",
            "filesTouched": list(LOW_RISK_FILES),
            "judge_decisions": [],
            "auditor_status": "not-run:parent-must-dispatch",
        }
        self._state([run_a, run_b])
        self._enforce(run_b)
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, ov.check_manifest(self.workdir)["owed"])

        run_b["judge_decisions"] = [dict(AUDITOR_VERDICT)]
        run_b["auditor_status"] = "ran:dispatched-agent"
        self._state([run_a, run_b])
        self._enforce(run_b)

        chk = ov.check_manifest(self.workdir)
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, chk["owed"])
        self.assertEqual(chk["status"], "incomplete")

    def test_an_unpersistable_waiver_is_reported_not_claimed(self) -> None:
        """No state.json means the tombstone cannot persist, so the debt re-arms."""
        ov.write_manifest(
            self.workdir, run_id="run_cv", diff_range="A..B",
            owed=[ov.CROSS_VENDOR_VERIFIER],
        )
        res = ov.clear_verifiers(
            self.workdir, clear_all=True, reason="no peer host reachable"
        )
        self.assertEqual(res["status"], "complete")
        self.assertIs(res["tombstone_persisted"], False)

    def test_a_codex_run_is_not_told_to_review_itself_with_codex(self) -> None:
        manifest = self._enforce({
            "run_id": "run_cv",
            "host": "codex",
            "filesTouched": list(HIGH_RISK_FILES),
            "judge_decisions": [dict(AUDITOR_VERDICT)],
            "auditor_status": "ran:dispatched-agent",
        })
        command = manifest["dispatch_commands"][ov.CROSS_VENDOR_VERIFIER]
        self.assertNotIn("codex exec", command)
        self.assertIn("DIFFERENT vendor", command)

    def test_a_malformed_dispatch_commands_block_does_not_crash_the_close_gate(self) -> None:
        import run_close_lint

        self._state([{
            "run_id": "run_cv", "date": "2026-09-12T12:00:00Z", "goal": "g",
            "outcome": "pass", "filesTouched": list(HIGH_RISK_FILES),
        }])
        (self.workdir / ".build-loop" / "owed-verification.json").write_text(
            json.dumps({
                "run_id": "run_cv", "owed": [ov.CROSS_VENDOR_VERIFIER],
                "cleared": [], "status": "incomplete",
                "dispatch_commands": ["bad-shape"],
            }),
            encoding="utf-8",
        )
        envelope = run_close_lint.check(self.workdir, run_id="run_cv")
        self.assertEqual(envelope["status"], "review_owed")
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, envelope["remediation"])


class TestSecondVendorEvidence(_Base):
    """A cross-vendor label is not a cross-vendor round."""

    def _enforce(self, record: dict) -> dict | None:
        return ov.enforce_for_run_record(
            self.workdir, record, written_by="test", diff_range="A..B"
        )

    def _armed_record(self, **over) -> dict:
        record = {
            "run_id": "run_cv",
            "host": "claude_code",
            "filesTouched": list(HIGH_RISK_FILES),
            "judge_decisions": [dict(AUDITOR_VERDICT)],
            "auditor_status": "ran:dispatched-agent",
        }
        record.update(over)
        return record

    def test_a_verdict_with_no_named_vendor_does_not_discharge(self) -> None:
        manifest = self._enforce(self._armed_record(judge_decisions=[
            dict(AUDITOR_VERDICT), {"judge_id": "cross-vendor-audit", "verdict": "yay"},
        ]))
        self.assertIsNotNone(manifest)
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["owed"])

    def test_a_same_vendor_verdict_wearing_the_label_does_not_discharge(self) -> None:
        manifest = self._enforce(self._armed_record(judge_decisions=[
            dict(AUDITOR_VERDICT),
            {"judge_id": "cross-vendor-audit", "verdict": "yay",
             "vendor": "anthropic/claude-opus-5"},
        ]))
        self.assertIsNotNone(manifest)
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["owed"])

    def test_a_verdict_in_the_judge_decisions_file_discharges_the_debt(self) -> None:
        """The round runs AFTER the record is written; that is the only sequence."""
        record = self._armed_record()
        self._enforce(record)
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "incomplete")

        (self.workdir / ".build-loop" / "judge-decisions.json").write_text(
            json.dumps([dict(CROSS_VENDOR_VERDICT)]), encoding="utf-8"
        )
        self._enforce(record)
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "absent")

    def test_the_emitted_dispatch_command_names_a_real_git_range(self) -> None:
        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": [], "preBuildSha": "abc1234"}), encoding="utf-8"
        )
        manifest = ov.enforce_for_run_record(
            self.workdir, self._armed_record(), written_by="test"
        )
        self.assertEqual(manifest["diff_range"], "abc1234..HEAD")
        self.assertNotIn("unknown", json.dumps(manifest["dispatch_commands"]))

    def test_a_waiver_is_recorded_against_the_run_that_owed_the_debt(self) -> None:
        """Ownership decides the waiver exactly as it decides the discharge."""
        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": []}), encoding="utf-8"
        )
        run_a = self._armed_record(run_id="RUN_A")
        ov.enforce_for_run_record(self.workdir, run_a, written_by="test", diff_range="A..B")
        run_b = {
            "run_id": "RUN_B", "host": "claude_code",
            "filesTouched": list(LOW_RISK_FILES), "judge_decisions": [],
            "auditor_status": "not-run:parent-must-dispatch",
        }
        ov.enforce_for_run_record(self.workdir, run_b, written_by="test", diff_range="A..B")

        ov.clear_verifiers(
            self.workdir, verifiers=[ov.CROSS_VENDOR_VERIFIER],
            reason="no second vendor on this machine",
        )
        registry = json.loads(
            (self.workdir / ".build-loop" / "state.json").read_text()
        )[ov.CLEARED_STATE_KEY]
        self.assertIn("RUN_A", registry)
        self.assertNotIn("RUN_B", registry)

        widened = dict(run_b)
        widened["filesTouched"] = list(HIGH_RISK_FILES)
        widened["judge_decisions"] = [dict(AUDITOR_VERDICT)]
        widened["auditor_status"] = "ran:dispatched-agent"
        ov.enforce_for_run_record(self.workdir, widened, written_by="test", diff_range="A..B")
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, ov.check_manifest(self.workdir)["owed"])


class TestCloseGateIsScopedByOwner(_Base):
    """A gate that blocks unrelated runs gets muted, and a muted gate is no gate."""

    def _seed(self, run_ids: list[str]) -> None:
        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": [
                {"run_id": rid, "date": "2026-09-12T12:00:00Z", "goal": "g",
                 "outcome": "pass", "filesTouched": list(HIGH_RISK_FILES)}
                for rid in run_ids
            ]}),
            encoding="utf-8",
        )

    def test_the_owing_run_is_blocked_and_an_unrelated_run_is_not(self) -> None:
        import run_close_lint

        self._seed(["RUN_A", "RUN_B"])
        ov.write_manifest(
            self.workdir, run_id="RUN_A", diff_range="A..B",
            owed=[ov.CROSS_VENDOR_VERIFIER],
        )

        owing = run_close_lint.check(self.workdir, run_id="RUN_A")
        self.assertEqual(owing["status"], "review_owed")

        unrelated = run_close_lint.check(self.workdir, run_id="RUN_B")
        self.assertEqual(unrelated["status"], "review_owed_other_run")
        self.assertIn("RUN_A", unrelated["reason"])

    def test_the_unrelated_run_exits_0_and_the_owing_run_exits_1(self) -> None:
        self._seed(["RUN_A", "RUN_B"])
        ov.write_manifest(
            self.workdir, run_id="RUN_A", diff_range="A..B",
            owed=[ov.CROSS_VENDOR_VERIFIER],
        )
        lint = Path(__file__).resolve().parent / "run_close_lint.py"
        for run_id, expected in (("RUN_A", 1), ("RUN_B", 0)):
            with self.subTest(run_id=run_id):
                proc = subprocess.run(
                    [sys.executable, str(lint), "--workdir", str(self.workdir),
                     "--run-id", run_id, "--json"],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(proc.returncode, expected, proc.stdout)

    def test_the_remediation_leads_with_the_verdict_not_the_waiver(self) -> None:
        import run_close_lint

        self._seed(["RUN_A"])
        ov.write_manifest(
            self.workdir, run_id="RUN_A", diff_range="A..B",
            owed=[ov.CROSS_VENDOR_VERIFIER],
        )
        remediation = run_close_lint.check(self.workdir, run_id="RUN_A")["remediation"]
        self.assertLess(remediation.index("codex exec"), remediation.index("clear"))
        self.assertIn("LAST RESORT", remediation)
