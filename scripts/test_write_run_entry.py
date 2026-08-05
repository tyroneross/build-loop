#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for write_run_entry.py. Zero deps. Run: python3 test_write_run_entry.py"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "write_run_entry" / "__main__.py"
RUN_ID_RE = re.compile(r"^run_\d{8}T\d{6}Z_[0-9a-f]{8}$")


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True,
        text=True,
    )


class WriteRunEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.state = self.workdir / ".build-loop" / "state.json"
        self.experiments = self.workdir / ".build-loop" / "experiments"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _base_args(self, **overrides: str) -> list[str]:
        args = {
            "--workdir": str(self.workdir),
            "--goal": "ship write_run_entry",
            "--outcome": "pass",
            "--phases-json": '{"assess":{"status":"pass","duration_s":2}}',
        }
        args.update(overrides)
        flat: list[str] = []
        for k, v in args.items():
            flat.extend([k, v])
        return flat

    def test_first_run_creates_runs_array(self) -> None:
        result = run(self._base_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        run_id = result.stdout.strip()
        self.assertRegex(run_id, RUN_ID_RE)
        state = json.loads(self.state.read_text())
        self.assertIn("runs", state)
        self.assertEqual(len(state["runs"]), 1)
        self.assertEqual(state["runs"][0]["run_id"], run_id)
        self.assertEqual(state["runs"][0]["outcome"], "pass")

    def test_second_run_appends(self) -> None:
        self.assertEqual(run(self._base_args()).returncode, 0)
        r2 = run(self._base_args(**{"--goal": "second build"}))
        self.assertEqual(r2.returncode, 0, msg=r2.stderr)
        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["runs"]), 2)
        self.assertNotEqual(state["runs"][0]["run_id"], state["runs"][1]["run_id"])

    def test_legacy_state_additive_migration(self) -> None:
        # Simulate Example-iOS-App v0.2.0 shape: no runs[], rich existing data
        self.state.parent.mkdir(parents=True)
        legacy = {
            "goal": "old goal",
            "active": False,
            "currentPhase": "report",
            "phases": {"assess": {"status": "pass"}, "execute": {"status": "pass"}},
            "iterations": 2,
        }
        self.state.write_text(json.dumps(legacy, indent=2))
        result = run(self._base_args())
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        state = json.loads(self.state.read_text())
        # All legacy keys preserved
        for k, v in legacy.items():
            self.assertEqual(state[k], v, f"legacy key {k!r} mutated")
        self.assertEqual(len(state["runs"]), 1)

    def test_confound_across_two_artifacts(self) -> None:
        # Create baseline jsonl files for two experimental artifacts
        self.experiments.mkdir(parents=True)
        for name in ("skill-a", "skill-b"):
            (self.experiments / f"{name}.jsonl").write_text(
                json.dumps({"event": "created", "artifact": name, "baseline_metric": "x", "baseline_value": 1, "target_value": 2, "sample_size_target": 8}) + "\n"
            )
        result = run(self._base_args(**{"--active-experimental-artifacts": "skill-a,skill-b"}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        for name, other in (("skill-a", "skill-b"), ("skill-b", "skill-a")):
            lines = (self.experiments / f"{name}.jsonl").read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)  # created + applied
            applied = json.loads(lines[-1])
            self.assertEqual(applied["event"], "applied")
            self.assertEqual(applied["co_applied_experimental_artifacts"], [other])
            self.assertTrue(applied["confounded"])

    def test_malformed_phases_exits_1(self) -> None:
        result = run(self._base_args(**{"--phases-json": "not-json"}))
        self.assertEqual(result.returncode, 1)
        self.assertIn("validation error", result.stderr)

    def test_invalid_outcome_exits_1(self) -> None:
        result = run(self._base_args(**{"--outcome": "bogus"}))
        self.assertEqual(result.returncode, 1)

    def test_single_artifact_not_confounded(self) -> None:
        self.experiments.mkdir(parents=True)
        (self.experiments / "solo.jsonl").write_text(
            json.dumps({"event": "created", "artifact": "solo", "baseline_metric": "x", "baseline_value": 1, "target_value": 2, "sample_size_target": 8}) + "\n"
        )
        result = run(self._base_args(**{"--active-experimental-artifacts": "solo"}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = (self.experiments / "solo.jsonl").read_text().strip().splitlines()
        applied = json.loads(lines[-1])
        self.assertEqual(applied["co_applied_experimental_artifacts"], [])
        self.assertFalse(applied["confounded"])

    def test_corrupt_state_json_exits_1(self) -> None:
        self.state.parent.mkdir(parents=True)
        self.state.write_text("{corrupted-not-json")
        result = run(self._base_args())
        self.assertEqual(result.returncode, 1)
        self.assertIn("validation error", result.stderr)

    def test_missing_baseline_skips_applied(self) -> None:
        # No baseline file for 'ghost' — script should warn and not create one
        result = run(self._base_args(**{"--active-experimental-artifacts": "ghost"}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("no baseline for experiment 'ghost'", result.stderr)
        self.assertFalse((self.experiments / "ghost.jsonl").exists())


class ReviewCompletenessGateTests(unittest.TestCase):
    """scope=build review-completeness gate (bl-enforce-independent-auditor-dispatch)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.state = self.workdir / ".build-loop" / "state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _judge_file(self, decisions: list) -> str:
        p = self.workdir / "judges.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(decisions))
        return str(p)

    def _args(self, **overrides: str) -> list[str]:
        args = {
            "--workdir": str(self.workdir),
            "--goal": "ship a code change",
            "--outcome": "pass",
            "--phases-json": "{}",
        }
        args.update(overrides)
        flat: list[str] = []
        for k, v in args.items():
            flat.extend([k, v])
        return flat

    def test_build_scope_code_pass_without_auditor_exits_3(self) -> None:
        result = run(self._args(**{"--scope": "build", "--files-touched": "src/x.py"}))
        self.assertEqual(result.returncode, 3, msg=result.stderr)
        self.assertIn("independent-auditor", result.stderr)
        self.assertFalse(self.state.exists(), "no entry should be written when the gate fails")

    def test_build_scope_with_auditor_verdict_passes(self) -> None:
        jf = self._judge_file([{"judge_id": "independent-auditor", "verdict": "yay"}])
        result = run(self._args(**{
            "--scope": "build", "--files-touched": "src/x.py", "--judge-decisions-json": jf,
        }))
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_auditor_hook_verdict_also_satisfies(self) -> None:
        jf = self._judge_file([{"judge_id": "independent-auditor-hook", "verdict": "suggest"}])
        result = run(self._args(**{
            "--scope": "build", "--files-touched": "src/x.py", "--judge-decisions-json": jf,
        }))
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_other_judge_only_does_not_satisfy(self) -> None:
        jf = self._judge_file([{"judge_id": "plan-critic", "verdict": "approve"}])
        result = run(self._args(**{
            "--scope": "build", "--files-touched": "src/x.py", "--judge-decisions-json": jf,
        }))
        self.assertEqual(result.returncode, 3, msg=result.stderr)

    def test_build_scope_no_files_no_gate(self) -> None:
        result = run(self._args(**{"--scope": "build"}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_build_scope_partial_outcome_no_gate(self) -> None:
        result = run(self._args(**{
            "--scope": "build", "--files-touched": "src/x.py", "--outcome": "partial",
        }))
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_scope_none_no_gate_even_with_code(self) -> None:
        result = run(self._args(**{"--files-touched": "src/x.py"}))  # default scope=none
        self.assertEqual(result.returncode, 0, msg=result.stderr)


class AdditiveOptionalFieldsTests(unittest.TestCase):
    """oracle_completeness (B1) + models/harness (C) — additive + optional."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.state = self.workdir / ".build-loop" / "state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_json(self, name: str, obj: object) -> str:
        p = self.workdir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj))
        return str(p)

    def _args(self, **overrides: str) -> list[str]:
        args = {
            "--workdir": str(self.workdir),
            "--goal": "additive-fields",
            "--outcome": "pass",
            "--phases-json": "{}",
        }
        args.update(overrides)
        flat: list[str] = []
        for k, v in args.items():
            flat.extend([k, v])
        return flat

    def _last_run(self) -> dict:
        return json.loads(self.state.read_text())["runs"][-1]

    def test_baseline_has_no_new_keys(self) -> None:
        # A run without the new flags must not carry models/harness keys (purely additive).
        self.assertEqual(run(self._args()).returncode, 0)
        r = self._last_run()
        self.assertNotIn("models", r)
        self.assertNotIn("harness", r)

    def test_judge_oracle_completeness_accepted(self) -> None:
        jf = self._write_json("judges.json", [{
            "judge_id": "review-b-validate",
            "verdict": "yay",
            "oracle_completeness": {"covered": "auth+schema", "uncovered": "rate-limit path", "coverage": "partial"},
        }])
        result = run(self._args(**{"--judge-decisions-json": jf}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        oc = self._last_run()["judge_decisions"][0]["oracle_completeness"]
        self.assertEqual(oc["coverage"], "partial")
        self.assertEqual(oc["uncovered"], "rate-limit path")

    def test_judge_oracle_completeness_invalid_coverage_rejected(self) -> None:
        jf = self._write_json("judges.json", [{
            "judge_id": "review-b-validate", "verdict": "yay",
            "oracle_completeness": {"coverage": "mostly"},  # not in full|partial|thin
        }])
        result = run(self._args(**{"--judge-decisions-json": jf}))
        self.assertEqual(result.returncode, 1)
        self.assertIn("oracle_completeness", result.stderr)

    def test_judge_without_oracle_completeness_still_valid(self) -> None:
        jf = self._write_json("judges.json", [{"judge_id": "review-b-validate", "verdict": "yay"}])
        self.assertEqual(run(self._args(**{"--judge-decisions-json": jf})).returncode, 0)

    def test_models_and_harness_written_when_supplied(self) -> None:
        mf = self._write_json("models.json", {"orchestrator": "opus", "implementer": "sonnet"})
        hf = self._write_json("harness.json", {"scaffold": "build-loop-mode-A", "context_budget": 200000})
        result = run(self._args(**{"--models-json": mf, "--harness-json": hf}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        r = self._last_run()
        self.assertEqual(r["models"]["orchestrator"], "opus")
        self.assertEqual(r["harness"]["scaffold"], "build-loop-mode-A")

    def test_models_non_object_rejected(self) -> None:
        mf = self._write_json("models.json", ["opus", "sonnet"])  # list, not object
        result = run(self._args(**{"--models-json": mf}))
        self.assertEqual(result.returncode, 1)
        self.assertIn("--models-json", result.stderr)

    def test_empty_harness_file_skipped(self) -> None:
        hf = self.workdir / "empty.json"
        hf.write_text("")
        result = run(self._args(**{"--harness-json": str(hf)}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("harness", self._last_run())



class OwedVerificationEnforcementTests(unittest.TestCase):
    """GAP-1: a run cannot close with neither a verdict nor a manifest."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)
        self.state = self.workdir / ".build-loop" / "state.json"
        self.manifest = self.workdir / ".build-loop" / "owed-verification.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _judge_file(self, decisions: list) -> str:
        p = self.workdir / "judges.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(decisions))
        return str(p)

    def _args(self, **overrides: str) -> list[str]:
        args = {
            "--workdir": str(self.workdir),
            "--goal": "ship a code change",
            "--outcome": "pass",
            "--phases-json": "{}",
        }
        args.update(overrides)
        flat: list[str] = []
        for k, v in args.items():
            flat.extend([k, v])
        return flat

    def test_pending_hook_packet_does_not_satisfy_the_auditor_requirement(self) -> None:
        """The verdict-blindness defect, tested where it actually lives.

        `auditor_present` matched on the judge_id substring alone, so six
        `verdict: pending, status: packet_emitted` rows -- an audit packet
        EMITTED and never answered -- certified two real runs as reviewed.
        A packet is a request for a verdict, not a verdict.

        Asserted against the function rather than the CLI on purpose: the CLI
        rejects `pending` earlier, on the judge-decision verdict ENUM, but
        `scripts/audit_before_commit.py` writes these rows straight into
        state.json without passing that enum. So the enum is not the control
        here, and testing through the CLI would prove a protection this defect
        routes around.
        """
        sys.path.insert(0, str(HERE))
        from write_run_entry.validators import auditor_present

        packet = [{"judge_id": "independent-auditor-hook",
                   "verdict": "pending", "status": "packet_emitted"}]
        self.assertFalse(auditor_present(packet),
                         "an emitted-but-unanswered packet is not a verdict")
        # ...and the exact six-row shape the two real runs carried.
        self.assertFalse(auditor_present(packet * 6))

    def test_a_rendered_verdict_still_satisfies(self) -> None:
        """Acquittal half: the tightening must not reject real verdicts, and a
        NEGATIVE verdict is still a verdict -- `nay` means the auditor ran."""
        sys.path.insert(0, str(HERE))
        from write_run_entry.validators import auditor_present

        for verdict in ("yay", "suggest", "nay", "rethink", "new_approach"):
            with self.subTest(verdict=verdict):
                self.assertTrue(auditor_present(
                    [{"judge_id": "independent-auditor", "verdict": verdict}]))

    def test_entry_past_the_gate_without_a_verdict_leaves_a_manifest(self) -> None:
        """The exit-3 gate only fires on scope=build + pass + filesTouched.
        Everything outside that intersection reached the writer owing nothing
        on disk -- which is where all five real runs landed."""
        result = run(self._args(**{
            "--scope": "none", "--files-touched": "src/x.py", "--outcome": "partial",
        }))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(self.manifest.exists(),
                        "a code-touching run with no verdict must owe one")
        owed = json.loads(self.manifest.read_text())["owed"]
        self.assertIn("independent-auditor", owed)
        self.assertIs(json.loads(self.state.read_text()).get("review_incomplete"), True)

    def test_a_quiet_entry_owes_nothing(self) -> None:
        """Precision: a run that touched nothing and engaged no auditor is not
        an escaped review. A flag that is always on is a flag nobody reads."""
        result = run(self._args(**{"--outcome": "partial"}))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse(self.manifest.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
