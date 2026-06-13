#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/acceptance_probe.py — the acceptance-probe contract (gate #1).

Coverage:
  - parsing: fenced goal.md block, sidecar JSON, absent (opt-in → empty), bad JSON
  - classify: verifiable / unverifiable / invalid(defect-class) / empty-baseline
  - rerun: still_failing / resolved / error / skipped, with REAL subprocess probes
  - gate_verdict mapping
  - decision_command → autonomy_gate routes to confirm (DECISION) via repo config
  - CLI exit codes for classify + rerun

Run: python3 scripts/test_acceptance_probe.py  (or via pytest)
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
SCRIPT = HERE / "acceptance_probe.py"
GATE_SCRIPT = HERE / "autonomy_gate.py"

sys.path.insert(0, str(HERE))
from acceptance_probe import (  # noqa: E402
    ProbeParseError,
    classify_all,
    classify_criterion,
    decision_command,
    gate_verdict,
    parse_goal_probes,
    rerun_all,
    rerun_probe,
    still_at_baseline,
)


def _write_goal(tmp: Path, criteria_json: str | None, sidecar: str | None = None) -> Path:
    bl = tmp / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    goal = bl / "goal.md"
    body = "Some goal prose.\n\n"
    if criteria_json is not None:
        body += "```acceptance_probe\n" + criteria_json + "\n```\n"
    goal.write_text(body)
    if sidecar is not None:
        (bl / "acceptance-probes.json").write_text(sidecar)
    return goal


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParsing(unittest.TestCase):
    def test_fenced_block(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            goal = _write_goal(
                tmp,
                json.dumps(
                    [{"id": "C1", "acceptance_probe": "echo x", "baseline": "x", "boundary": "console"}]
                ),
            )
            crits = parse_goal_probes(goal)
            self.assertEqual(len(crits), 1)
            self.assertEqual(crits[0]["id"], "C1")

    def test_sidecar_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            goal = _write_goal(
                tmp,
                None,
                sidecar=json.dumps([{"id": "S1", "acceptance_probe": "echo y", "baseline": "y", "boundary": "api"}]),
            )
            crits = parse_goal_probes(goal)
            self.assertEqual(len(crits), 1)
            self.assertEqual(crits[0]["id"], "S1")

    def test_fenced_wins_over_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            goal = _write_goal(
                tmp,
                json.dumps([{"id": "FENCED", "acceptance_probe": "echo z", "baseline": "z", "boundary": "data"}]),
                sidecar=json.dumps([{"id": "SIDECAR"}]),
            )
            crits = parse_goal_probes(goal)
            self.assertEqual(crits[0]["id"], "FENCED")

    def test_absent_is_opt_in_empty(self):
        # No fenced block, no sidecar → empty list (additive/opt-in for old runs).
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            goal = _write_goal(tmp, None)
            self.assertEqual(parse_goal_probes(goal), [])

    def test_missing_goal_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(parse_goal_probes(Path(d) / "nope.md"), [])

    def test_bad_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            goal = _write_goal(tmp, "{ not json ]")
            with self.assertRaises(ProbeParseError):
                parse_goal_probes(goal)


# ---------------------------------------------------------------------------
# Classification (Phase 1 Assess)
# ---------------------------------------------------------------------------


class TestClassify(unittest.TestCase):
    def test_verifiable(self):
        c = {"id": "C1", "acceptance_probe": "echo x", "baseline": "x", "boundary": "render"}
        r = classify_criterion(c)
        self.assertEqual(r["status"], "verifiable")
        self.assertEqual(r["missing"], [])

    def test_unverifiable_missing_probe_non_defect(self):
        c = {"id": "C2", "baseline": "x", "boundary": "api"}
        r = classify_criterion(c)
        self.assertEqual(r["status"], "unverifiable")
        self.assertIn("acceptance_probe", r["missing"])

    def test_invalid_defect_class_missing_probe(self):
        # Defect-class WITH no probe is a hard failure — it had a reproducible bug.
        c = {"id": "C3", "boundary": "console", "defect_class": True}
        r = classify_criterion(c)
        self.assertEqual(r["status"], "invalid")
        self.assertIn("acceptance_probe", r["missing"])

    def test_invalid_boundary_value(self):
        c = {"id": "C4", "acceptance_probe": "echo x", "baseline": "x", "boundary": "frobnicate"}
        r = classify_criterion(c)
        self.assertEqual(r["status"], "unverifiable")
        self.assertTrue(any("boundary(invalid" in m for m in r["missing"]))

    def test_empty_baseline_is_valid(self):
        # baseline "" = "empty output is the failing value" — must be accepted.
        c = {"id": "C5", "acceptance_probe": "echo x", "baseline": "", "boundary": "data"}
        r = classify_criterion(c)
        self.assertEqual(r["status"], "verifiable")

    def test_missing_baseline_key_unverifiable(self):
        c = {"id": "C6", "acceptance_probe": "echo x", "boundary": "data"}
        r = classify_criterion(c)
        self.assertEqual(r["status"], "unverifiable")
        self.assertIn("baseline", r["missing"])

    def test_classify_all_verdicts(self):
        self.assertEqual(classify_all([])["verdict"], "no_probes")
        ok = classify_all([{"id": "a", "acceptance_probe": "e", "baseline": "b", "boundary": "api"}])
        self.assertEqual(ok["verdict"], "ok")
        flagged = classify_all([{"id": "a", "baseline": "b", "boundary": "api"}])
        self.assertEqual(flagged["verdict"], "flagged")
        invalid = classify_all([{"id": "a", "boundary": "api", "defect_class": True}])
        self.assertEqual(invalid["verdict"], "invalid")


# ---------------------------------------------------------------------------
# Re-run harness (Phase 4 Review) — real subprocess probes
# ---------------------------------------------------------------------------


class TestRerun(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.workdir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_still_failing(self):
        # Probe still emits the baseline-failure signal → blocked.
        c = {
            "id": "BUG1",
            "acceptance_probe": "echo 'route: keyword'",
            "baseline": "route: keyword",
            "boundary": "api",
        }
        rr = rerun_probe(c, self.workdir)
        self.assertEqual(rr["rerun_state"], "still_failing")
        self.assertEqual(gate_verdict(rr), "blocked")

    def test_resolved(self):
        # Probe no longer emits the baseline signal → passed.
        c = {
            "id": "BUG2",
            "acceptance_probe": "echo 'route: vector'",
            "baseline": "route: keyword",
            "boundary": "api",
        }
        rr = rerun_probe(c, self.workdir)
        self.assertEqual(rr["rerun_state"], "resolved")
        self.assertEqual(gate_verdict(rr), "passed")

    def test_skipped_no_probe(self):
        c = {"id": "NP", "baseline": "x", "boundary": "api"}
        rr = rerun_probe(c, self.workdir)
        self.assertEqual(rr["rerun_state"], "skipped")
        self.assertEqual(gate_verdict(rr), "unverifiable")

    def test_error_timeout(self):
        c = {
            "id": "SLOW",
            "acceptance_probe": "sleep 5",
            "baseline": "anything",
            "boundary": "api",
        }
        rr = rerun_probe(c, self.workdir, timeout=1)
        self.assertEqual(rr["rerun_state"], "error")
        self.assertEqual(gate_verdict(rr), "error")

    def test_empty_baseline_match(self):
        # Empty baseline matches only empty output.
        self.assertTrue(still_at_baseline("", ""))
        self.assertFalse(still_at_baseline("", "something"))

    def test_substring_normalized_match(self):
        # Baseline is the failing SIGNAL; extra surrounding output still counts.
        self.assertTrue(still_at_baseline("Error: boom", "line1\n  Error:   boom \nline3"))
        self.assertFalse(still_at_baseline("Error: boom", "all good"))

    def test_rerun_all_blocks_when_any_still_failing(self):
        crits = [
            {"id": "OK", "acceptance_probe": "echo good", "baseline": "BADSIGNAL", "boundary": "api"},
            {"id": "BAD", "acceptance_probe": "echo BADSIGNAL", "baseline": "BADSIGNAL", "boundary": "api"},
        ]
        summary = rerun_all(crits, self.workdir)
        self.assertEqual(summary["verdict"], "blocked")
        self.assertEqual(summary["counts"]["blocked"], 1)
        bad = [r for r in summary["criteria"] if r["id"] == "BAD"][0]
        self.assertIn("decision_command", bad)


# ---------------------------------------------------------------------------
# DECISION routing: blocked deferral → autonomy_gate confirm
# ---------------------------------------------------------------------------


class TestDecisionRouting(unittest.TestCase):
    def test_decision_command_shape(self):
        cmd = decision_command({"id": "C9"})
        self.assertEqual(cmd, "defer acceptance criterion C9")

    def test_blocked_deferral_routes_to_confirm(self):
        """A blocked criterion's deferral, run through autonomy_gate with the repo
        confirmFor pattern, must verdict `confirm` (DECISION → ## Held), proving the
        gate reuses the existing autonomy surface rather than inventing a new one."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cfg_dir = tmp / ".build-loop"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            # Repo opts the deferral pattern into confirmFor (the documented wiring).
            (cfg_dir / "config.json").write_text(
                json.dumps({"autonomy": {"confirmFor": ["defer acceptance criterion *"]}})
            )
            cmd = decision_command({"id": "BUG"})
            r = subprocess.run(
                [
                    sys.executable, str(GATE_SCRIPT),
                    "--workdir", str(tmp),
                    "--action", f"defer acceptance criterion BUG",
                    "--command", cmd,
                    "--json",
                ],
                capture_output=True, text=True,
            )
            # exit 1 == confirm per autonomy_gate ACTION_EXIT_CODES
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            env = json.loads(r.stdout)
            self.assertEqual(env["action"], "confirm")


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------


def _run_cli(*cli_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *cli_args],
        capture_output=True, text=True,
    )


class TestCLI(unittest.TestCase):
    def test_classify_ok_exit0(self):
        with tempfile.TemporaryDirectory() as d:
            goal = _write_goal(
                Path(d),
                json.dumps([{"id": "C1", "acceptance_probe": "echo x", "baseline": "x", "boundary": "api"}]),
            )
            r = _run_cli("classify", "--goal", str(goal), "--json")
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout)["verdict"], "ok")

    def test_classify_invalid_exit1(self):
        with tempfile.TemporaryDirectory() as d:
            goal = _write_goal(
                Path(d),
                json.dumps([{"id": "D1", "boundary": "api", "defect_class": True}]),
            )
            r = _run_cli("classify", "--goal", str(goal), "--json")
            self.assertEqual(r.returncode, 1)

    def test_classify_unverifiable_exit0_flagged(self):
        # Net-new criterion missing a probe is flagged, not a hard fail.
        with tempfile.TemporaryDirectory() as d:
            goal = _write_goal(Path(d), json.dumps([{"id": "U1", "boundary": "api"}]))
            r = _run_cli("classify", "--goal", str(goal), "--json")
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout)["verdict"], "flagged")

    def test_classify_empty_no_probes_exit0(self):
        with tempfile.TemporaryDirectory() as d:
            goal = _write_goal(Path(d), None)
            r = _run_cli("classify", "--goal", str(goal), "--json")
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout)["verdict"], "no_probes")

    def test_rerun_blocked_exit1(self):
        with tempfile.TemporaryDirectory() as d:
            goal = _write_goal(
                Path(d),
                json.dumps([{"id": "B1", "acceptance_probe": "echo STILLBAD", "baseline": "STILLBAD", "boundary": "api"}]),
            )
            r = _run_cli("rerun", "--goal", str(goal), "--workdir", d, "--json")
            self.assertEqual(r.returncode, 1)
            self.assertEqual(json.loads(r.stdout)["verdict"], "blocked")

    def test_rerun_clear_exit0(self):
        with tempfile.TemporaryDirectory() as d:
            goal = _write_goal(
                Path(d),
                json.dumps([{"id": "G1", "acceptance_probe": "echo FIXED", "baseline": "STILLBAD", "boundary": "api"}]),
            )
            r = _run_cli("rerun", "--goal", str(goal), "--workdir", d, "--json")
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout)["verdict"], "clear")


if __name__ == "__main__":
    unittest.main(verbosity=2)
