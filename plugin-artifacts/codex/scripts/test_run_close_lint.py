#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for run_close_lint.py + the run-close contract it enforces in the owned docs.

Two classes, one file (fewer nodes, same coverage):

* :class:`RunCloseLintTest` — the behavior of the assertion, including the exact shape
  of the 2026-07-16 failure it exists to catch (a dispatched orchestrator whose workdir
  has no ``.build-loop/`` at all, and a workdir whose ``runs[]`` holds only hook-written
  floor records).
* :class:`RunCloseContractTest` — the clauses that wire the lint into Review-G and into
  the dispatching parent's completion check. Locks them against silent removal, the same
  way ``test_auditor_dispatch_contract.py`` locks the GAP-1 contract.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_close_lint  # noqa: E402

# scripts/ -> repo root
REPO = Path(__file__).resolve().parent.parent

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _orchestrator_run(run_id: str, when: datetime = NOW) -> dict:
    """A Review-G record: write_run_entry stamps no ``source``."""
    return {"run_id": run_id, "date": _iso(when), "outcome": "pass", "goal": "g"}


def _write_learn_receipt(workdir: Path, run_id: str, status: str = "complete") -> None:
    path = workdir / ".build-loop" / "learn" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "build-loop.learn-receipt.v1", "run_id": run_id, "status": status}),
        encoding="utf-8",
    )


def _floor_run(run_id: str, when: datetime = NOW) -> dict:
    """A Stop-hook record: append_run stamps ``source: append_run``."""
    return {"run_id": run_id, "date": _iso(when), "outcome": "pass", "source": "append_run"}


def _commit_hook_run(when: datetime = NOW) -> dict:
    """A commit-hook record, verbatim shape from scripts/audit_before_commit.py.

    Carries NO ``source`` and a compact non-ISO ``date``. Sampled from a live
    21-entry state.json where 16 rows looked like this — the exact shape the
    2026-07-16 vault was full of, and the one a source-only predicate mis-grades.
    """
    return {
        "run_id": f"hook_{when.strftime('%Y%m%dT%H%M%SZ')}",
        "date": when.strftime("%Y%m%dT%H%M%SZ"),
        "goal": "(hook-only commit; no orchestrator run)",
        "outcome": "partial",
        "phases": {},
        "filesTouched": [],
    }


class RunCloseLintTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write_state(self, state: dict) -> Path:
        path = self.workdir / ".build-loop" / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
        return path

    # ---- the observed failure shapes ------------------------------------------

    def test_no_build_loop_dir_at_all_is_no_state(self) -> None:
        """The 2026-07-16 vault shape: the run's workdir has no .build-loop/."""
        result = run_close_lint.check(self.workdir, run_id="bl-1", now=NOW)
        self.assertEqual(result["status"], "no_state")
        self.assertIn("no durable footprint", result["reason"])
        self.assertIn("write_run_entry", result["remediation"])

    def test_run_id_absent_from_populated_runs_is_missing(self) -> None:
        """Other runs recorded, this one did not — Learn cannot see it."""
        self._write_state({"runs": [_orchestrator_run("bl-other")]})
        result = run_close_lint.check(self.workdir, run_id="bl-mine", now=NOW)
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["runs_count"], 1)
        self.assertIn("Phase 6 Learn cannot see this run", result["reason"])
        self.assertIn("bl-mine", result["remediation"])

    def test_floor_record_only_is_floor_only_when_orchestrator_required(self) -> None:
        """Hook-written entries are exactly what the vault had; they are not Review-G."""
        self._write_state({"runs": [_floor_run("bl-1")]})
        result = run_close_lint.check(
            self.workdir, run_id="bl-1", require_orchestrator=True, now=NOW
        )
        self.assertEqual(result["status"], "floor_only")
        self.assertIn("append_run", result["reason"])

    def test_commit_hook_record_is_floor_only_despite_absent_source(self) -> None:
        """The live-data regression: hook_* rows carry no ``source`` at all.

        A source-only predicate grades these orchestrator-written and hands back a
        false pass on precisely the hook-only rows that mark a starved Learn.
        """
        entry = _commit_hook_run()
        self._write_state({"runs": [entry]})
        result = run_close_lint.check(
            self.workdir, run_id=entry["run_id"], require_orchestrator=True, now=NOW
        )
        self.assertEqual(result["status"], "floor_only")
        self.assertFalse(run_close_lint.is_orchestrator_grade(entry))

    def test_commit_hook_compact_date_is_parsed_for_the_recency_window(self) -> None:
        """A date the parser cannot read would drop a recorded run out of the window."""
        self._write_state({"runs": [_commit_hook_run(NOW - timedelta(minutes=10))]})
        result = run_close_lint.check(self.workdir, recent_minutes=240, now=NOW)
        self.assertEqual(result["status"], "recorded")
        self.assertFalse(result["orchestrator_grade"])

    def test_floor_record_counts_when_orchestrator_not_required(self) -> None:
        self._write_state({"runs": [_floor_run("bl-1")]})
        result = run_close_lint.check(self.workdir, run_id="bl-1", now=NOW)
        self.assertEqual(result["status"], "recorded")
        self.assertFalse(result["orchestrator_grade"])

    # ---- the happy path ------------------------------------------------------

    def test_orchestrator_record_is_recorded(self) -> None:
        self._write_state({"runs": [_orchestrator_run("bl-1")]})
        result = run_close_lint.check(
            self.workdir, run_id="bl-1", require_orchestrator=True, now=NOW
        )
        self.assertEqual(result["status"], "recorded")
        self.assertTrue(result["orchestrator_grade"])
        self.assertEqual(result["mode"], "run-id")

    def test_require_learn_rejects_prose_only_run_close(self) -> None:
        run = _orchestrator_run("bl-1")
        self._write_state({"runs": [run]})
        result = run_close_lint.check(self.workdir, run_id="bl-1", require_learn=True, now=NOW)
        self.assertEqual(result["status"], "learn_missing")
        self.assertIn("scripts/learn/__main__.py run", result["remediation"])

    def test_require_learn_accepts_matching_complete_receipt_and_state_summary(self) -> None:
        run = _orchestrator_run("bl-1")
        run["learn"] = {
            "schema": "build-loop.learn-receipt.v1",
            "status": "complete",
            "receipt": ".build-loop/learn/bl-1.json",
        }
        self._write_state({"runs": [run]})
        _write_learn_receipt(self.workdir, "bl-1")
        result = run_close_lint.check(self.workdir, run_id="bl-1", require_learn=True, now=NOW)
        self.assertEqual(result["status"], "recorded")
        self.assertTrue(result["learn_complete"])

    # ---- evidence-mode precedence -------------------------------------------

    def test_execution_run_id_is_the_fallback_identity(self) -> None:
        """No --run-id: the run that actually ran is state.json.execution.run_id."""
        self._write_state({"execution": {"run_id": "bl-exec"}, "runs": []})
        result = run_close_lint.check(self.workdir, now=NOW)
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["mode"], "execution-run-id")
        self.assertEqual(result["run_id"], "bl-exec")

    def test_no_identity_anywhere_is_skipped_not_a_failure(self) -> None:
        """An unrelated repo must not be reported as a missing run record."""
        self._write_state({"runs": []})
        result = run_close_lint.check(self.workdir, now=NOW)
        self.assertEqual(result["status"], "skipped")
        self.assertIsNone(result.get("remediation"))

    def test_recent_window_finds_a_fresh_entry(self) -> None:
        self._write_state({"runs": [_orchestrator_run("bl-1", NOW - timedelta(minutes=30))]})
        result = run_close_lint.check(self.workdir, recent_minutes=240, now=NOW)
        self.assertEqual(result["status"], "recorded")
        self.assertEqual(result["mode"], "recent-window")
        self.assertEqual(result["run_id"], "bl-1")

    def test_recent_window_rejects_a_stale_entry(self) -> None:
        """The dispatched run closed with nothing; only yesterday's record exists."""
        self._write_state({"runs": [_orchestrator_run("bl-old", NOW - timedelta(days=1))]})
        result = run_close_lint.check(self.workdir, recent_minutes=240, now=NOW)
        self.assertEqual(result["status"], "missing")
        self.assertIn("240 minutes", result["reason"])

    def test_recent_window_ignores_execution_identity(self) -> None:
        """--expect-recent-minutes is the parent's mode; it must not silently switch."""
        self._write_state(
            {"execution": {"run_id": "bl-exec"}, "runs": [_orchestrator_run("bl-1")]}
        )
        result = run_close_lint.check(self.workdir, recent_minutes=240, now=NOW)
        self.assertEqual(result["mode"], "recent-window")
        self.assertEqual(result["status"], "recorded")

    def test_recent_window_with_require_orchestrator_filters_floor_records(self) -> None:
        self._write_state({"runs": [_floor_run("bl-hook", NOW - timedelta(minutes=5))]})
        result = run_close_lint.check(
            self.workdir, recent_minutes=240, require_orchestrator=True, now=NOW
        )
        self.assertEqual(result["status"], "missing")
        self.assertIn("orchestrator-written", result["reason"])

    # ---- robustness: a hook caller must never eat a traceback ----------------

    def test_corrupt_state_is_a_status_not_an_exception(self) -> None:
        path = self.workdir / ".build-loop" / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        result = run_close_lint.check(self.workdir, run_id="bl-1", now=NOW)
        self.assertEqual(result["status"], "no_state")
        self.assertIn("unusable", result["reason"])

    def test_runs_not_a_list_is_tolerated(self) -> None:
        self._write_state({"runs": "nonsense"})
        result = run_close_lint.check(self.workdir, run_id="bl-1", now=NOW)
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["runs_count"], 0)

    def test_unparseable_date_does_not_satisfy_the_window(self) -> None:
        self._write_state({"runs": [{"run_id": "bl-1", "date": "not-a-date"}]})
        result = run_close_lint.check(self.workdir, recent_minutes=240, now=NOW)
        self.assertEqual(result["status"], "missing")

    # ---- exit codes ---------------------------------------------------------

    def test_main_exits_1_on_missing_and_0_with_advisory(self) -> None:
        self._write_state({"runs": []})
        argv = ["--workdir", str(self.workdir), "--run-id", "bl-1", "--json"]
        self.assertEqual(run_close_lint.main(argv), 1)
        self.assertEqual(run_close_lint.main(argv + ["--advisory"]), 0)

    def test_main_exits_0_when_recorded(self) -> None:
        self._write_state({"runs": [_orchestrator_run("bl-1", datetime.now(timezone.utc))]})
        self.assertEqual(
            run_close_lint.main(["--workdir", str(self.workdir), "--run-id", "bl-1"]), 0
        )

    def test_recent_minutes_flag_defaults_when_given_without_a_value(self) -> None:
        self._write_state({"runs": [_orchestrator_run("bl-1", datetime.now(timezone.utc))]})
        self.assertEqual(
            run_close_lint.main(
                ["--workdir", str(self.workdir), "--expect-recent-minutes", "--json"]
            ),
            0,
        )

    def test_is_orchestrator_grade_predicate(self) -> None:
        self.assertTrue(run_close_lint.is_orchestrator_grade({"run_id": "a"}))
        self.assertFalse(run_close_lint.is_orchestrator_grade({"source": "append_run"}))
        self.assertFalse(run_close_lint.is_orchestrator_grade("not-a-dict"))


class RunCloseContractTest(unittest.TestCase):
    """The lint is wired into Review-G and into the parent's completion check.

    A script nobody calls is the same defect as no script, so these clauses are the
    load-bearing half of the fix. Tokens, not sentences — prose may evolve.
    """

    REQUIRED_CLAUSES: dict[str, list[str]] = {
        # Review-G self-assert.
        "agents/build-orchestrator.md": [
            "run_close_lint.py",
            "--require-orchestrator",
            "--require-learn",
        ],
        "skills/build-loop/references/phase-4-review.md": [
            "run_close_lint.py",
            "--require-orchestrator",
            "--require-learn",
            "non-invocation",
        ],
        "references/phase-gate-checklist.md": [
            "run_close_lint.py",
            "--require-learn",
        ],
        # Parent verifies at the completion boundary.
        "skills/build-loop/references/verify-dispatch.md": [
            "run_close_lint.py",
            "--expect-recent-minutes",
            "--require-learn",
        ],
    }

    def test_owned_docs_carry_the_contract(self) -> None:
        for rel, tokens in self.REQUIRED_CLAUSES.items():
            path = REPO / rel
            self.assertTrue(path.exists(), f"{rel} is missing")
            body = path.read_text(encoding="utf-8").lower()
            for token in tokens:
                self.assertIn(
                    token.lower(),
                    body,
                    f"{rel} lost the run-close contract token {token!r}",
                )

    def test_script_is_executable_and_self_documenting(self) -> None:
        script = REPO / "scripts" / "run_close_lint.py"
        self.assertTrue(script.exists())
        head = script.read_text(encoding="utf-8")[:400]
        self.assertIn("SPDX-License-Identifier", head)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
