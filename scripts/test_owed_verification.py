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

# The real repository, for the range-resolution tests: `_normalise_range`
# calls git, and a temp dir has no commits to resolve.
ROOT_REPO = Path(__file__).resolve().parent.parent


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
        res = ov.clear_verifiers(
            self.workdir, verifiers=["independent-auditor"], reason="test waiver")
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
        res = ov.clear_verifiers(self.workdir, clear_all=True, reason="test waiver")
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
        ov.clear_verifiers(
            self.workdir, verifiers=["independent-auditor"], reason="test waiver")
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

        ov.clear_verifiers(self.workdir, clear_all=True, reason="test waiver")
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
        res = ov.clear_verifiers(
            self.workdir, verifiers=["independent-auditor"], reason="test waiver")
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

        # clear → exit 0. `--reason` is now required because no verdict backs
        # this waiver; without one the CLI refuses and says which verifier.
        rc, _ = _run_cli(self.workdir, "clear", "--all", "--reason", "test")
        self.assertEqual(rc, 0)
        rc, _ = _run_cli(self.workdir, "clear", "--all")
        self.assertEqual(rc, 0, "an absent manifest stays a no-op, not an error")

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
# A real second-vendor entry also names the RANGE it reviewed: the file is
# append-only, so an unstamped verdict would discharge a debt armed by later
# commits. The default range matches the `diff_range="A..B"` these tests arm.
# And it POINTS AT the round's own output: the `vendor` string is metadata the
# recorder asserts about itself, so nothing stops a same-vendor process from
# typing another provider's name. `codex_session_id` is the opaque form --
# unresolvable from this process, but a specific claim a later audit can chase.
CROSS_VENDOR_VERDICT = {
    "judge_id": "cross-vendor-audit", "verdict": "yay",
    "vendor": "openai/codex-cli 0.154.0", "diff_range": "A..B",
    "codex_session_id": "01JQ8Z3K4M5N6P7Q8R9STVWXYZ",
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
        # The host is load-bearing: a cross-vendor verdict discharges the debt
        # only when its `vendor` names a provider outside the run's own host
        # family, and an unmapped host cannot answer "different vendor" at all.
        "host": "claude_code",
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
        # An ARMED debt is discharged only by a verdict stamped with its own
        # range. Arming stays lenient about an unstamped verdict (139 of 143
        # real auditor rows carry no range), but once the debt exists its
        # dispatch command spells run_id and diff_range -- so an unstamped entry
        # is evidence the instruction was not followed, and accepting it let an
        # older verdict discharge a debt armed over code written after it.
        record["judge_decisions"] = [dict(AUDITOR_VERDICT)]
        record["auditor_status"] = "ran:dispatched-agent"
        manifest = self._enforce(record)
        self.assertEqual(
            sorted(manifest["owed"]),
            sorted([ov.AUTO_OWED_VERIFIER, ov.CROSS_VENDOR_VERIFIER]),
            "an unstamped verdict must not discharge an already-armed debt",
        )

        record["judge_decisions"] = [{**AUDITOR_VERDICT, "diff_range": "A..B"}]
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
                "host": "claude_code",
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

        ov.clear_verifiers(self.workdir, clear_all=True, reason="test waiver")
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
            "host": "claude_code",
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
            "host": "claude_code",
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
            "host": "claude_code",
            "filesTouched": list(HIGH_RISK_FILES),
            "judge_decisions": [dict(AUDITOR_VERDICT)],
            "auditor_status": "ran:dispatched-agent",
        }
        self._state([run_a])
        self._enforce(run_a)

        run_b = {
            "run_id": "run_b",
            "host": "claude_code",
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
            "outcome": "pass", "host": "claude_code",
            "filesTouched": list(HIGH_RISK_FILES),
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

        # The entry must CLAIM this run. The file is append-only and outlives
        # every run in the repo, so an unscoped read let an older verdict
        # discharge a new debt.
        (self.workdir / ".build-loop" / "judge-decisions.json").write_text(
            json.dumps([{**CROSS_VENDOR_VERDICT, "run_id": "run_cv"}]), encoding="utf-8"
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


class TestSecondCrossVendorRoundRegressions(_Base):
    """Findings from the second cross-vendor round, run against the fix-up itself.

    Five of them shared one root cause: the manifest keyed debts by verifier
    NAME, and a debt is per (run, verifier). The record is now one row per pair.
    """

    def _state(self, runs: list[dict]) -> None:
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

    def _record(self, run_id: str, **over) -> dict:
        record = {
            "run_id": run_id,
            "host": "claude_code",
            "filesTouched": list(HIGH_RISK_FILES),
            "judge_decisions": [dict(AUDITOR_VERDICT)],
            "auditor_status": "ran:dispatched-agent",
        }
        record.update(over)
        return record

    def test_two_runs_owing_the_same_verifier_both_keep_their_debt(self) -> None:
        """One slot per verifier could not represent two runs, so one was dropped."""
        ov.write_manifest(self.workdir, run_id="RUN_A", diff_range="a0..a1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        ov.write_manifest(self.workdir, run_id="RUN_B", diff_range="b0..b1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        debts = ov.check_manifest(self.workdir)["debts"]
        self.assertEqual(
            sorted(d["run_id"] for d in debts), ["RUN_A", "RUN_B"]
        )

    def test_each_debt_keeps_its_own_diff_range(self) -> None:
        """A later run's write re-pointed an earlier run's dispatch command."""
        ov.write_manifest(self.workdir, run_id="RUN_A", diff_range="a0..a1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        ov.write_manifest(self.workdir, run_id="RUN_B", diff_range="b0..b1",
                          owed=[ov.AUTO_OWED_VERIFIER])
        by_run = {d["run_id"]: d for d in ov.check_manifest(self.workdir)["debts"]}
        self.assertEqual(by_run["RUN_A"]["diff_range"], "a0..a1")
        self.assertIn("a0..a1", by_run["RUN_A"]["dispatch_command"])
        self.assertEqual(by_run["RUN_B"]["diff_range"], "b0..b1")

    def test_a_waiver_for_one_run_does_not_suppress_another_runs_debt(self) -> None:
        """Driven through the CLI, the path an operator actually reaches.

        The property was previously asserted only through the `run_id=` kwarg,
        which no production caller and no CLI flag reached -- a guard written
        against the implementation rather than the reachable path, and the
        unscoped CLI clear discharged BOTH runs.
        """
        ov.write_manifest(self.workdir, run_id="RUN_A", diff_range="a0..a1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        ov.write_manifest(self.workdir, run_id="RUN_B", diff_range="b0..b1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])

        # Unscoped, with two owners: refused rather than waiving both.
        rc, _ = _run_cli(self.workdir, "clear", "--verifier", ov.CROSS_VENDOR_VERIFIER)
        self.assertEqual(rc, 2)
        self.assertEqual(len(ov.check_manifest(self.workdir)["debts"]), 2)

        rc, _ = _run_cli(self.workdir, "clear", "--verifier", ov.CROSS_VENDOR_VERIFIER,
                         "--run-id", "RUN_A", "--reason", "no second vendor here")
        self.assertEqual(rc, 0)
        debts = ov.check_manifest(self.workdir)["debts"]
        self.assertEqual([(d["verifier"], d["run_id"]) for d in debts],
                         [(ov.CROSS_VENDOR_VERIFIER, "RUN_B")])

    def test_the_close_gate_reads_the_debt_owner_not_the_last_writer(self) -> None:
        import run_close_lint

        self._state([
            {"run_id": rid, "date": "2026-09-12T12:00:00Z", "goal": "g",
             "outcome": "pass", "host": "claude_code",
             "filesTouched": list(HIGH_RISK_FILES)}
            for rid in ("RUN_A", "RUN_B")
        ])
        # SAME verifier for both runs -- the motivating shape. Giving them
        # different verifiers hid the defect: `owed_runs` is name-keyed and the
        # last row wins, so RUN_A was told the debt was someone else's.
        ov.write_manifest(self.workdir, run_id="RUN_A", diff_range="a0..a1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        ov.write_manifest(self.workdir, run_id="RUN_B", diff_range="b0..b1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        envelope = run_close_lint.check(self.workdir, run_id="RUN_A")
        self.assertEqual(envelope["status"], "review_owed")
        self.assertIn("RUN_A", envelope["owed_run_ids"])
        # One dispatch line per debt, each naming its own range.
        self.assertIn("a0..a1", envelope["remediation"])
        self.assertIn("b0..b1", envelope["remediation"])

    def test_a_verdict_naming_an_older_run_does_not_discharge_this_one(self) -> None:
        record = self._record("run_new")
        ov.enforce_for_run_record(self.workdir, record, written_by="t", diff_range="A..B")
        (self.workdir / ".build-loop" / "judge-decisions.json").write_text(
            json.dumps([{**CROSS_VENDOR_VERDICT, "run_id": "run_OLD"}]), encoding="utf-8"
        )
        ov.enforce_for_run_record(self.workdir, record, written_by="t", diff_range="A..B")
        self.assertIn(
            ov.CROSS_VENDOR_VERIFIER, ov.check_manifest(self.workdir)["owed"]
        )

    def test_a_not_run_marker_is_not_a_verdict(self) -> None:
        """A record of the round NOT happening read as the round happening."""
        record = self._record("run_cv", judge_decisions=[
            dict(AUDITOR_VERDICT),
            {"judge_id": "cross-vendor-audit", "verdict": "not-run",
             "status": "failed", "vendor": "openai"},
        ])
        manifest = ov.enforce_for_run_record(
            self.workdir, record, written_by="t", diff_range="A..B")
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["owed"])

    def test_an_unrecognised_vendor_string_is_not_evidence(self) -> None:
        record = self._record("run_cv", judge_decisions=[
            dict(AUDITOR_VERDICT),
            {"judge_id": "cross-vendor-audit", "verdict": "yay", "vendor": "unknown"},
        ])
        manifest = ov.enforce_for_run_record(
            self.workdir, record, written_by="t", diff_range="A..B")
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["owed"])

    def test_an_unmappable_host_cannot_answer_different_vendor(self) -> None:
        record = self._record("run_cv", host="other", judge_decisions=[
            dict(AUDITOR_VERDICT), dict(CROSS_VENDOR_VERDICT),
        ])
        manifest = ov.enforce_for_run_record(
            self.workdir, record, written_by="t", diff_range="A..B")
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["owed"])

    def test_harness_detail_after_the_provider_does_not_reject_the_round(self) -> None:
        """"openai via claude-code peer" is an OpenAI review, not an Anthropic one."""
        record = self._record("run_cv", judge_decisions=[
            dict(AUDITOR_VERDICT),
            {"judge_id": "cross-vendor-audit", "verdict": "yay",
             "vendor": "openai via claude-code peer", "diff_range": "A..B",
             "codex_session_id": "01JQ8Z3K4M5N6P7Q8R9STVWXYZ"},
        ])
        self.assertIsNone(
            ov.enforce_for_run_record(self.workdir, record, written_by="t", diff_range="A..B")
        )

    def test_an_error_inside_the_manifest_lock_surfaces_as_itself(self) -> None:
        """The lock's own handler yielded twice and replaced the caller's error."""
        with self.assertRaises(ValueError):
            with ov._manifest_lock(self.workdir):
                raise ValueError("the caller's real error")


class TestThirdRoundRegressions(_Base):
    """Findings from the third pass (same-vendor auditor + second-vendor round).

    Both reviewers converged: the RECORD was re-keyed per (verifier, run_id) but
    the LIFECYCLE still ran through the name-keyed derived views.
    """

    def _armed_pair(self) -> None:
        """Two runs owing the SAME verifier, armed through the production path."""
        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": []}), encoding="utf-8"
        )
        for run_id, rng in (("RUN_A", "a0..a1"), ("RUN_B", "b0..b1")):
            ov.enforce_for_run_record(
                self.workdir,
                {
                    "run_id": run_id, "host": "claude_code",
                    "filesTouched": list(HIGH_RISK_FILES),
                    "judge_decisions": [dict(AUDITOR_VERDICT)],
                    "auditor_status": "ran:dispatched-agent",
                },
                written_by="test", diff_range=rng,
            )

    def test_one_runs_verdict_does_not_discharge_another_runs_debt(self) -> None:
        """The discharge cleared by verifier NAME across every owner.

        Reproduced end to end by the auditor: run B recording its own verdict
        deleted run A's debt and the manifest with it, so A's diff shipped with
        no second-vendor review and no record that one was ever owed.
        """
        self._armed_pair()
        self.assertEqual(len(ov.check_manifest(self.workdir)["debts"]), 2)

        ov.enforce_for_run_record(
            self.workdir,
            {
                "run_id": "RUN_B", "host": "claude_code",
                "filesTouched": list(HIGH_RISK_FILES),
                "judge_decisions": [
                    dict(AUDITOR_VERDICT),
                    {**CROSS_VENDOR_VERDICT, "diff_range": "b0..b1"},
                ],
                "auditor_status": "ran:dispatched-agent",
            },
            written_by="test", diff_range="b0..b1",
        )
        chk = ov.check_manifest(self.workdir)
        self.assertEqual(chk["status"], "incomplete")
        self.assertEqual(
            [(d["verifier"], d["run_id"]) for d in chk["debts"]],
            [(ov.CROSS_VENDOR_VERIFIER, "RUN_A")],
        )

    def test_a_failed_status_does_not_discharge_on_an_allowed_verdict(self) -> None:
        """`{"verdict": "yay", "status": "failed"}` is a record of not reviewing."""
        for status in ("failed", "error", "not-run", "timeout"):
            with self.subTest(status=status):
                from write_run_entry.validators import cross_vendor_present

                self.assertFalse(cross_vendor_present([{
                    "judge_id": "cross-vendor-audit", "verdict": "yay",
                    "status": status, "vendor": "openai",
                }], "claude_code"))

    def test_vendor_separators_other_than_slash_are_recognised(self) -> None:
        from write_run_entry.validators import vendor_provider

        for value, expected in (
            ("openai:gpt-5", "openai"),
            ("codex_cli", "openai"),
            ("ollama/qwen2.5-coder:32b", "ollama"),
            ("anthropic.claude-opus-5", "anthropic"),
            ("unknown", None),
        ):
            with self.subTest(vendor=value):
                self.assertEqual(vendor_provider(value), expected)

    def test_a_half_upgraded_manifest_reads_as_incomplete(self) -> None:
        """Every malformed shape must resolve toward MORE debt, never less."""
        for payload in (
            {"run_id": "A", "owed": ["independent-auditor"], "debts": []},
            {"run_id": "A", "owed": ["independent-auditor"], "debts": ["not-a-dict"]},
            {"run_id": "A", "owed": ["independent-auditor"]},
        ):
            with self.subTest(payload=sorted(payload)):
                (self.workdir / ".build-loop" / "owed-verification.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                chk = ov.check_manifest(self.workdir)
                self.assertEqual(chk["status"], "incomplete")
                self.assertIn("independent-auditor", chk["owed"])

    def test_a_scalar_owed_field_does_not_wedge_the_check(self) -> None:
        (self.workdir / ".build-loop" / "owed-verification.json").write_text(
            json.dumps({"run_id": "A", "debts": {}, "owed": 7}), encoding="utf-8"
        )
        self.assertIn(ov.check_manifest(self.workdir)["status"], {"complete", "incomplete"})

    def test_a_debt_row_without_an_owner_inherits_the_manifest_run(self) -> None:
        (self.workdir / ".build-loop" / "owed-verification.json").write_text(
            json.dumps({
                "run_id": "A", "diff_range": "x..y",
                "debts": [{"verifier": ov.CROSS_VENDOR_VERIFIER}],
            }),
            encoding="utf-8",
        )
        debt = ov.check_manifest(self.workdir)["debts"][0]
        self.assertEqual(debt["run_id"], "A")
        self.assertEqual(debt["diff_range"], "x..y")

    def test_the_dispatch_command_spells_the_entry_that_discharges_it(self) -> None:
        """Following the emitted command produced an entry that could not discharge."""
        manifest = ov.write_manifest(
            self.workdir, run_id="RUN_A", diff_range="a0..a1",
            owed=[ov.CROSS_VENDOR_VERIFIER],
        )
        command = manifest["dispatch_commands"][ov.CROSS_VENDOR_VERIFIER]
        for required in ("judge_id", "verdict", "vendor", "run_id", "RUN_A", "a0..a1"):
            self.assertIn(required, command)

    def test_a_failed_arm_is_logged_rather_than_read_as_nothing_owed(self) -> None:
        """A swallowed lock timeout looked exactly like a clean run."""
        import unittest.mock as mock

        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": []}), encoding="utf-8"
        )
        with mock.patch.object(ov, "write_manifest", side_effect=TimeoutError("lock")):
            result = ov.enforce_for_run_record(
                self.workdir,
                {
                    "run_id": "RUN_A", "host": "claude_code",
                    "filesTouched": list(HIGH_RISK_FILES),
                    "judge_decisions": [dict(AUDITOR_VERDICT)],
                    "auditor_status": "ran:dispatched-agent",
                },
                written_by="test", diff_range="a0..a1",
            )
        self.assertIsNone(result)
        log = (self.workdir / ".build-loop" / "audit-log.md").read_text()
        self.assertIn("enforce FAILED", log)
        self.assertIn("RUN_A", log)


class TestFourthRoundRegressions(_Base):
    """Findings from the fourth pass. Two reviewers converged on all three Highs."""

    def test_a_codex_host_command_spells_the_entry_that_discharges_it(self) -> None:
        """The f5 fix landed on the default template and not on the override.

        The override is the ONLY command a codex-hosted run ever sees, which is
        exactly the run class where a cross-vendor debt matters most, and its
        text named no vendor and no run_id -- rejected by both checks.
        """
        manifest = ov.enforce_for_run_record(
            self.workdir,
            {
                "run_id": "RUN_CODEX", "host": "codex",
                "filesTouched": list(HIGH_RISK_FILES),
                "judge_decisions": [dict(AUDITOR_VERDICT)],
                "auditor_status": "ran:dispatched-agent",
            },
            written_by="test", diff_range="a0..a1",
        )
        command = manifest["dispatch_commands"][ov.CROSS_VENDOR_VERIFIER]
        self.assertNotIn("codex exec", command)
        for required in ("vendor", "run_id", "RUN_CODEX", "a0..a1"):
            self.assertIn(required, command)

    def test_a_clear_refuses_a_manifest_it_cannot_parse(self) -> None:
        """The escape hatch was deleting itself.

        A malformed manifest yields no debt rows, so the "nothing left" branch
        unlinked the file and reported the review complete -- and `check`
        reports owed=['unknown'] on that shape, making `clear --verifier
        unknown` the operator's natural next keystroke.
        """
        (self.workdir / ".build-loop" / "owed-verification.json").write_text(
            "{ not json at all", encoding="utf-8"
        )
        self.assertTrue(ov.check_manifest(self.workdir)["review_incomplete"])
        result = ov.clear_verifiers(self.workdir, verifiers=["unknown"])
        self.assertEqual(result["action"], "refused_malformed")
        self.assertTrue((self.workdir / ".build-loop" / "owed-verification.json").exists())
        self.assertTrue(ov.check_manifest(self.workdir)["review_incomplete"])

    def test_clear_all_is_not_refused_by_the_ambiguity_guard(self) -> None:
        """`--all` is an unambiguous intent; refusing it left no command at all.

        Still true, and still the property under test. What changed on
        2026-09-13 is WHICH runs one `--all` may sweep: it used to delete every
        run's obligation and unlink the sole manifest with no owner boundary at
        all. A single-owner `--all` is unchanged. A multi-owner sweep is a
        decision about OTHER runs' obligations, so it must be stated -- and the
        refusal names the flag that states it, rather than leaving the operator
        with no command, which is the failure this test was written for.
        """
        ov.write_manifest(self.workdir, run_id="RUN_A", diff_range="a0..a1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        rc, _ = _run_cli(self.workdir, "clear", "--all", "--reason", "no peer host")
        self.assertEqual(rc, 0, "a single-owner --all must still run")
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "absent")

        ov.write_manifest(self.workdir, run_id="RUN_A", diff_range="a0..a1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        ov.write_manifest(self.workdir, run_id="RUN_B", diff_range="b0..b1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        rc, payload = _run_cli(self.workdir, "clear", "--all", "--reason", "no peer host")
        self.assertEqual(rc, 2, "a multi-owner sweep must be stated, not inferred")
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "incomplete")

        rc, _ = _run_cli(
            self.workdir, "clear", "--all", "--i-mean-every-run",
            "--reason", "no peer host reachable for either run",
        )
        self.assertEqual(rc, 0, "the stated sweep must run; otherwise no command exists")
        self.assertEqual(ov.check_manifest(self.workdir)["status"], "absent")

    def test_a_prior_runs_waiver_does_not_suppress_this_runs_debt(self) -> None:
        """The cleared NAME list carries no owner; backfilling it invented one."""
        (self.workdir / ".build-loop" / "owed-verification.json").write_text(
            json.dumps({
                "run_id": "RUN_C",
                "diff_range": "c0..c1",
                "debts": [{"verifier": ov.AUTO_OWED_VERIFIER, "run_id": "RUN_C"}],
                "cleared_debts": [
                    {"verifier": ov.CROSS_VENDOR_VERIFIER, "run_id": "RUN_A"}
                ],
                "cleared": [ov.CROSS_VENDOR_VERIFIER],
            }),
            encoding="utf-8",
        )
        manifest = ov.write_manifest(
            self.workdir, run_id="RUN_C", diff_range="c0..c1",
            owed=[ov.CROSS_VENDOR_VERIFIER],
        )
        self.assertIn(
            (ov.CROSS_VENDOR_VERIFIER, "RUN_C"),
            [(d["verifier"], d["run_id"]) for d in manifest["debts"]],
        )

    def test_an_unreadable_manifest_shape_fails_closed(self) -> None:
        """{"debts": {}, "owed": 7} read as a complete review."""
        (self.workdir / ".build-loop" / "owed-verification.json").write_text(
            json.dumps({"run_id": "A", "status": "incomplete", "debts": {}, "owed": 7}),
            encoding="utf-8",
        )
        chk = ov.check_manifest(self.workdir)
        self.assertEqual(chk["status"], "incomplete")
        self.assertTrue(chk["review_incomplete"])

    def test_a_debts_row_and_a_legacy_owner_are_both_kept(self) -> None:
        """Name-keyed reconciliation dropped the legacy view's owner."""
        (self.workdir / ".build-loop" / "owed-verification.json").write_text(
            json.dumps({
                "run_id": "RUN_A", "diff_range": "a0..a1",
                "debts": [{"verifier": ov.CROSS_VENDOR_VERIFIER, "run_id": "RUN_A"}],
                "owed": [ov.CROSS_VENDOR_VERIFIER],
                "owed_runs": {ov.CROSS_VENDOR_VERIFIER: "RUN_B"},
            }),
            encoding="utf-8",
        )
        owners = {d["run_id"] for d in ov.check_manifest(self.workdir)["debts"]}
        self.assertEqual(owners, {"RUN_A", "RUN_B"})

    def test_a_failed_auditor_row_does_not_discharge_the_auditor_debt(self) -> None:
        """The verdict checks were only in the cross-vendor caller, not the base."""
        from write_run_entry.validators import auditor_present

        for entry in (
            {"judge_id": "independent-auditor", "verdict": "yay", "status": "failed"},
            {"judge_id": "independent-auditor", "verdict": "not-run"},
            {"judge_id": "independent-auditor", "verdict": "yay", "status": "timeout"},
        ):
            with self.subTest(entry=entry):
                self.assertFalse(auditor_present([entry]))

    def test_a_verdict_for_an_obsolete_range_does_not_discharge(self) -> None:
        from write_run_entry.validators import cross_vendor_present

        entry = {
            "judge_id": "cross-vendor-audit", "verdict": "yay",
            "vendor": "openai/codex-cli", "diff_range": "old0..old1",
            "codex_session_id": "01JQ8Z3K4M5N6P7Q8R9STVWXYZ",
        }
        self.assertFalse(cross_vendor_present([entry], "claude_code", "new0..new1"))
        self.assertTrue(cross_vendor_present([entry], "claude_code", "old0..old1"))

    def test_the_emitted_waiver_command_is_runnable(self) -> None:
        """The printed last-resort command exited 2 in the multi-run case."""
        import run_close_lint
        import shlex

        (self.workdir / ".build-loop" / "state.json").write_text(
            json.dumps({"runs": [{
                "run_id": "RUN_A", "date": "2026-09-13T12:00:00Z", "goal": "g",
                "outcome": "pass", "filesTouched": list(HIGH_RISK_FILES),
            }]}),
            encoding="utf-8",
        )
        ov.write_manifest(self.workdir, run_id="RUN_A", diff_range="a0..a1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        ov.write_manifest(self.workdir, run_id="RUN_B", diff_range="b0..b1",
                          owed=[ov.CROSS_VENDOR_VERIFIER])
        remediation = run_close_lint.check(self.workdir, run_id="RUN_A")["remediation"]
        import re

        waiver = re.search(
            r"python3 scripts/owed_verification\.py clear [^;]+", remediation
        ).group(0).strip()
        argv = shlex.split(waiver)[1:]  # drop "python3"
        proc = subprocess.run([sys.executable, *argv], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Exit 0 alone is not evidence: `clear --verifier <name>` on a verifier
        # nobody owes also exits 0 while doing nothing. The command must have
        # cleared THIS run's debt and left the other run's standing.
        self.assertEqual(
            [(d["verifier"], d["run_id"]) for d in ov.check_manifest(self.workdir)["debts"]],
            [(ov.CROSS_VENDOR_VERIFIER, "RUN_B")],
        )


class TestFifthRoundRegressions(_Base):
    """The final pass: two Highs, both 'the fix exists but nothing calls it'."""

    def setUp(self) -> None:
        super().setUp()
        # A REAL git repo in the run's own workdir. `_normalise_range` resolves
        # against the workdir, so a bare temp dir cannot exercise the mechanism
        # at all -- the comparison would silently fall back to a string compare
        # and the test would certify the fallback instead of the fix.
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "test")
        self._shas = []
        for index in range(3):
            (self.workdir / f"f{index}.txt").write_text(str(index), encoding="utf-8")
            self._git("add", f"f{index}.txt")
            self._git("commit", "-q", "-m", f"c{index}", "--no-verify")
            self._shas.append(self._git("rev-parse", "HEAD").strip())

    def _git(self, *args: str) -> str:
        import subprocess

        return subprocess.run(
            ["git", "-C", str(self.workdir), *args],
            capture_output=True, text=True, check=False,
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(self.workdir),
                 "GIT_CONFIG_NOSYSTEM": "1"},
        ).stdout

    def _repo_range(self) -> tuple[str, str]:
        """Two real, resolvable ranges in this run's own repository."""
        return f"{self._shas[0]}..{self._shas[1]}", f"{self._shas[1]}..{self._shas[2]}"

    def test_an_obsolete_range_verdict_is_rejected_on_the_production_path(self) -> None:
        """The guard existed with a signature no production caller passed.

        `cross_vendor_present` grew a diff_range parameter and the only real
        caller passed two arguments, so the check was dormant on every run and
        only a direct-call test saw it -- the exact anti-pattern this module's
        own comment names.
        """
        old_range, new_range = self._repo_range()
        (self.workdir / ".build-loop" / "judge-decisions.json").write_text(
            json.dumps([{**CROSS_VENDOR_VERDICT, "run_id": "run_cv",
                         "diff_range": old_range}]),
            encoding="utf-8",
        )
        record = {
            "run_id": "run_cv", "host": "claude_code",
            "filesTouched": list(HIGH_RISK_FILES),
            "judge_decisions": [dict(AUDITOR_VERDICT)],
            "auditor_status": "ran:dispatched-agent",
        }
        manifest = ov.enforce_for_run_record(
            self.workdir, record, written_by="test", diff_range=new_range
        )
        self.assertIsNotNone(manifest, "an obsolete-range verdict must not discharge")
        self.assertIn(ov.CROSS_VENDOR_VERIFIER, manifest["owed"])

    def test_a_current_range_verdict_still_discharges_on_the_production_path(self) -> None:
        """The guard must not refuse a legitimate round and wedge the run.

        The real-world shape: the run side resolves `<sha>..HEAD` while the
        reviewer recorded two concrete shas. A literal string compare calls
        those different and leaves the debt armed with only the waiver as an
        exit, which is why the comparison resolves both sides first.
        """
        base, head = self._shas[1], self._shas[2]
        # Run side: symbolic. Recorded verdict: concrete. Same two commits.
        new_range = f"{base}..HEAD"
        (self.workdir / ".build-loop" / "judge-decisions.json").write_text(
            json.dumps([{**CROSS_VENDOR_VERDICT, "run_id": "run_cv",
                         "diff_range": f"{base}..{head}"}]),
            encoding="utf-8",
        )
        record = {
            "run_id": "run_cv", "host": "claude_code",
            "filesTouched": list(HIGH_RISK_FILES),
            "judge_decisions": [dict(AUDITOR_VERDICT)],
            "auditor_status": "ran:dispatched-agent",
        }
        self.assertIsNone(
            ov.enforce_for_run_record(
                self.workdir, record, written_by="test", diff_range=new_range
            )
        )

    def test_a_symbolic_range_matches_the_same_resolved_commits(self) -> None:
        """The run side resolves `<sha>..HEAD`; a reviewer records concrete shas.

        Comparing the literal strings would refuse a real round and leave the
        debt permanently armed with only the waiver as an exit.
        """
        base, head = self._shas[1], self._shas[2]
        self.assertEqual(
            ov._normalise_range(self.workdir, f"{base}..HEAD"),
            ov._normalise_range(self.workdir, f"{base}..{head}"),
        )

    def test_an_unresolvable_range_compares_as_itself(self) -> None:
        """Fail OPEN on resolution: a false reject wedges, a false accept ships."""
        self.assertEqual(ov._normalise_range(self.workdir, "nope..alsonope"),
                         "nope..alsonope")
        self.assertEqual(ov._normalise_range(self.workdir, "unknown"), "unknown")


class TestVerdictAndVendorEvidence(_Base):
    """The regression the fifth cross-vendor round caught in the fix itself."""

    def test_a_recorded_hook_verdict_is_not_rejected_by_its_stale_status(self) -> None:
        """The status check re-armed 103 real rows in this repo's own ledger.

        `audit_record_verdict.py` fills the verdict IN PLACE and used to leave
        `status: packet_emitted`, so treating that status as disqualifying
        rejected every verdict the hook path ever recorded -- including three
        passing code-touching runs whose auditor debt would have re-armed.
        """
        from write_run_entry.validators import auditor_present

        self.assertTrue(auditor_present([{
            "judge_id": "independent-auditor-hook", "verdict": "yay",
            "status": "packet_emitted", "verdict_ts": "2026-09-13T00:00:00Z",
        }]))
        # An un-answered packet still fails, on the verdict rather than the status.
        self.assertFalse(auditor_present([{
            "judge_id": "independent-auditor-hook", "verdict": "pending",
            "status": "packet_emitted",
        }]))

    def test_the_live_ledger_keeps_every_verdict_it_already_recorded(self) -> None:
        """Run the real predicate over the repo's REAL rows, not a fixture.

        A synthetic table cannot see a vocabulary the ledger actually contains;
        this is the check that caught `verdict: "pass"` and the stale status.
        """
        from write_run_entry.validators import rendered_verdict

        state = Path(__file__).resolve().parent.parent / ".build-loop" / "state.json"
        if not state.exists():
            self.skipTest("no local ledger to check")
        data = json.loads(state.read_text(encoding="utf-8"))
        rejected = [
            entry
            for run in data.get("runs", [])
            for entry in (run.get("judge_decisions") or [])
            if isinstance(entry, dict)
            and str(entry.get("verdict") or "").strip().lower() not in ("", "pending")
            and not rendered_verdict(entry)
        ]
        self.assertEqual(
            rejected, [],
            "the verdict predicate rejects rows the ledger already holds; a run "
            "that was genuinely audited would re-arm its debt",
        )

    def test_a_verdict_naming_no_range_is_not_evidence_about_this_one(self) -> None:
        from write_run_entry.validators import cross_vendor_present

        unstamped = {
            "judge_id": "cross-vendor-audit", "verdict": "nay",
            "vendor": "openai/codex", "run_id": "r",
            "codex_session_id": "01JQ8Z3K4M5N6P7Q8R9STVWXYZ",
        }
        self.assertFalse(cross_vendor_present([unstamped], "claude_code", "aaa..bbb"))
        self.assertTrue(cross_vendor_present([unstamped], "claude_code", None))

    def test_the_first_provider_in_position_order_wins(self) -> None:
        """A later local-runtime phrase overrode the reviewing provider."""
        from write_run_entry.validators import vendor_provider

        for value, expected in (
            ("openai via llama.cpp fallback", "openai"),
            ("openai via lm-studio fallback", "openai"),
            ("llama.cpp", "local"),
            ("anthropic via ollama proxy", "anthropic"),
        ):
            with self.subTest(vendor=value):
                self.assertEqual(vendor_provider(value), expected)

    def test_a_debts_list_holding_junk_is_unreadable_not_empty(self) -> None:
        for payload in (
            {"status": "incomplete", "debts": [{}], "owed": []},
            {"debts": [7], "owed": []},
            {"debts": [{"run_id": "r"}], "owed": []},
        ):
            with self.subTest(payload=str(payload)):
                (self.workdir / ".build-loop" / "owed-verification.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                self.assertEqual(ov.check_manifest(self.workdir)["status"], "incomplete")
