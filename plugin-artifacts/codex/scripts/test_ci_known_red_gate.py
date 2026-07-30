#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Regression artifact for scripts/ci_known_red_gate.py.

WHY THIS EXISTS
---------------
The known-red baseline was enforced only by the LOCAL pre-push gate, which is
bypassable (`--no-verify`, `BL_SKIP_PREPUSH_TESTS=1`, a fresh clone with no
hooks). `.github/workflows/pytest.yml` ran the same suite and knew nothing about
the baseline, so a red test could still reach main through CI — the exact path
the 2026-07-26 "pre-existing failures untouched" drift took. This suite pins the
CI-side classification so that hole cannot silently reopen.

Coverage map (one class per required CI behavior):
  - newly-red (a failing test NOT in the baseline)      -> non-zero exit (CI fails)
  - baselined + unexpired failures only                 -> exit 0, and REPORTED
  - ANY entry past expiry                               -> non-zero, even on green,
                                                           and BEFORE pytest runs
  - baseline missing / malformed / schema-violating     -> non-zero (fail-SAFE)
  - pytest exit 1 with unparseable output               -> non-zero (unclassifiable
                                                           is not "no failures")
  - pytest exit 2/3/4/5                                 -> non-zero (CI's deliberate
                                                           fail-CLOSED divergence
                                                           from the pre-push gate)
  - a baselined test that now passes                    -> reported stale, non-blocking
  - REUSE: the classification helpers are the ones prepush_test_gate defines, not
    a fork. If that module's rules change, this module inherits them.
  - the workflow actually invokes this gate (wiring), so the gate cannot ship
    correct-but-unreferenced.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent            # .../build-loop/scripts
REPO = HERE.parent                                # .../build-loop
sys.path.insert(0, str(HERE))

import ci_known_red_gate as G                      # noqa: E402
import prepush_test_gate as P                      # noqa: E402

WORKFLOW = REPO / ".github" / "workflows" / "pytest.yml"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _baseline(*entries: dict) -> list[dict]:
    """Build a validated entry list the way load_baseline would return it."""
    out = []
    for e in entries:
        out.append({
            "test": e["test"],
            "reason": e.get("reason", "r"),
            "owner": e.get("owner", "tyroneross"),
            "expires": e["expires"],
            "expires_date": date.fromisoformat(e["expires"]),
        })
    return out


TODAY = date(2026, 7, 30)

SUMMARY_ONE_FAIL = (
    "=== short test summary info ===\n"
    "FAILED scripts/test_alpha.py::test_one - AssertionError: nope\n"
    "1 failed, 3 passed in 1.02s\n"
)
SUMMARY_TWO_FAILS = (
    "FAILED scripts/test_alpha.py::test_one - AssertionError: nope\n"
    "FAILED scripts/test_beta.py::test_two - AssertionError: also nope\n"
    "2 failed, 3 passed in 1.02s\n"
)


def _write_baseline(tmp_path: Path, payload) -> Path:
    d = tmp_path / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "known_red_baseline.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return p


def _run_cli(tmp_path: Path, output: str, rc: int, *extra: str) -> tuple[int, str]:
    """Drive main() end-to-end against a captured pytest run."""
    out_file = tmp_path / "pytest-output.txt"
    out_file.write_text(output, encoding="utf-8")
    buf = io.StringIO()
    real = sys.stdout
    sys.stdout = buf
    try:
        code = G.main([
            "--workdir", str(tmp_path),
            "--pytest-output", str(out_file),
            "--pytest-exit-code", str(rc),
            "--today", TODAY.isoformat(),
            *extra,
        ])
    finally:
        sys.stdout = real
    return code, buf.getvalue()


# ---------------------------------------------------------------------------
# REUSE — not a fork
# ---------------------------------------------------------------------------

class TestReusesPrepushLogic:
    def test_classification_helpers_are_the_prepush_ones(self):
        """The CI gate must not carry its own copy of the classification rules."""
        assert G.load_baseline is P.load_baseline
        assert G.classify_failures is P.classify_failures
        assert G.expired_entries is P.expired_entries
        assert G.stale_entries is P.stale_entries
        assert G.BASELINE_RELPATH is P.BASELINE_RELPATH

    def test_no_local_reimplementation_of_parsing_or_schema(self):
        """Guard against a future edit that inlines a second parser/schema here."""
        src = (HERE / "ci_known_red_gate.py").read_text(encoding="utf-8")
        assert "def parse_failed_ids" not in src
        assert "def classify_failures" not in src
        assert "def load_baseline" not in src
        assert "def stale_entries" not in src


# ---------------------------------------------------------------------------
# The four required CI behaviors
# ---------------------------------------------------------------------------

class TestNewlyRedBlocks:
    def test_unlisted_failure_blocks(self):
        entries = _baseline({"test": "scripts/test_alpha.py::test_one", "expires": "2026-12-01"})
        v = G.evaluate(rc=1, output=SUMMARY_TWO_FAILS, entries=entries,
                       today=TODAY, targets=["scripts/"])
        assert v["exit_code"] != G.EXIT_PASS
        assert v["newly_red"] == ["scripts/test_beta.py::test_two"]

    def test_empty_baseline_makes_every_failure_newly_red(self):
        v = G.evaluate(rc=1, output=SUMMARY_ONE_FAIL, entries=[],
                       today=TODAY, targets=["scripts/"])
        assert v["exit_code"] == G.EXIT_BLOCK_TESTS
        assert v["newly_red"] == ["scripts/test_alpha.py::test_one"]

    def test_cli_exit_is_nonzero_and_annotates_error(self, tmp_path):
        _write_baseline(tmp_path, {"version": 1, "entries": []})
        code, out = _run_cli(tmp_path, SUMMARY_ONE_FAIL, 1)
        assert code != 0
        assert "::error::" in out
        assert "NEWLY-RED" in out


class TestBaselinedFailurePassesButIsReported:
    def test_all_failures_baselined_and_unexpired_passes(self):
        entries = _baseline(
            {"test": "scripts/test_alpha.py::test_one", "expires": "2026-08-13"},
            {"test": "scripts/test_beta.py::test_two", "expires": "2026-08-13"},
        )
        v = G.evaluate(rc=1, output=SUMMARY_TWO_FAILS, entries=entries,
                       today=TODAY, targets=["scripts/"])
        assert v["exit_code"] == G.EXIT_PASS
        assert v["newly_red"] == []
        assert len(v["baseline_red"]) == 2

    def test_file_scoped_entry_covers_the_whole_file(self):
        entries = _baseline({"test": "scripts/test_alpha.py", "expires": "2026-08-13"})
        v = G.evaluate(rc=1, output=SUMMARY_ONE_FAIL, entries=entries,
                       today=TODAY, targets=["scripts/"])
        assert v["exit_code"] == G.EXIT_PASS

    def test_cli_passes_but_reports_owner_and_days_left(self, tmp_path):
        _write_baseline(tmp_path, {"version": 1, "entries": [{
            "test": "scripts/test_alpha.py::test_one",
            "reason": "known flake under investigation",
            "owner": "tyroneross",
            "expires": "2026-08-13",
        }]})
        code, out = _run_cli(tmp_path, SUMMARY_ONE_FAIL, 1)
        assert code == 0, "a baselined, unexpired failure must not fail CI"
        assert "::warning::" in out, "…but it MUST be reported"
        assert "owner=tyroneross" in out
        assert "14d left" in out


class TestExpiredEntryBlocks:
    def test_expired_blocks_even_on_a_green_tree(self, tmp_path):
        _write_baseline(tmp_path, {"version": 1, "entries": [{
            "test": "scripts/test_alpha.py::test_one",
            "reason": "r", "owner": "tyroneross", "expires": "2026-07-01",
        }]})
        code, out = _run_cli(tmp_path, "3 passed in 0.5s\n", 0)
        assert code == G.EXIT_BLOCK_BASELINE
        assert "EXPIRED" in out

    def test_expiry_is_checked_before_pytest_runs(self, tmp_path, monkeypatch):
        """A stale baseline must fail in seconds, not after a full suite."""
        _write_baseline(tmp_path, {"version": 1, "entries": [{
            "test": "scripts/test_alpha.py::test_one",
            "reason": "r", "owner": "tyroneross", "expires": "2026-07-01",
        }]})

        def _boom(*a, **k):  # pragma: no cover — must never be reached
            raise AssertionError("pytest was run despite an expired baseline entry")

        monkeypatch.setattr(G, "run_pytest", _boom)
        buf, real = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            code = G.main(["--workdir", str(tmp_path), "--today", TODAY.isoformat(),
                           "--", "-q"])
        finally:
            sys.stdout = real
        assert code == G.EXIT_BLOCK_BASELINE

    def test_expiry_boundary_is_inclusive_of_the_expiry_day(self, tmp_path):
        """Expiring 'today' is still valid; blocking starts the day after."""
        _write_baseline(tmp_path, {"version": 1, "entries": [{
            "test": "scripts/test_alpha.py::test_one",
            "reason": "r", "owner": "tyroneross", "expires": TODAY.isoformat(),
        }]})
        code, _ = _run_cli(tmp_path, SUMMARY_ONE_FAIL, 1)
        assert code == 0


class TestUnusableBaselineBlocks:
    def test_missing_baseline_blocks(self, tmp_path):
        code, out = _run_cli(tmp_path, "3 passed in 0.5s\n", 0)
        assert code == G.EXIT_BLOCK_BASELINE
        assert "baseline" in out.lower()

    @pytest.mark.parametrize("payload", [
        "{not json at all",
        json.dumps([{"test": "a", "reason": "r", "owner": "o", "expires": "2026-12-01"}]),
        json.dumps({"version": 1}),
        json.dumps({"version": 1, "entries": [{"test": "a", "reason": "r", "owner": "o"}]}),
        json.dumps({"version": 1, "entries": [{"test": "a", "reason": "r", "owner": "o",
                                              "expires": "not-a-date"}]}),
        json.dumps({"version": 1, "entries": [
            {"test": "a", "reason": "r", "owner": "o", "expires": "2026-12-01"},
            {"test": "a", "reason": "r2", "owner": "o", "expires": "2026-12-02"},
        ]}),
    ], ids=["not-json", "root-not-object", "no-entries", "missing-expires",
            "bad-date", "duplicate-test"])
    def test_malformed_baseline_blocks(self, tmp_path, payload):
        _write_baseline(tmp_path, payload)
        code, _ = _run_cli(tmp_path, "3 passed in 0.5s\n", 0)
        assert code == G.EXIT_BLOCK_BASELINE

    def test_malformed_baseline_blocks_before_pytest_runs(self, tmp_path, monkeypatch):
        _write_baseline(tmp_path, "{broken")

        def _boom(*a, **k):  # pragma: no cover
            raise AssertionError("pytest ran despite an unreadable baseline")

        monkeypatch.setattr(G, "run_pytest", _boom)
        buf, real = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            code = G.main(["--workdir", str(tmp_path), "--today", TODAY.isoformat(),
                           "--", "-q"])
        finally:
            sys.stdout = real
        assert code == G.EXIT_BLOCK_BASELINE


# ---------------------------------------------------------------------------
# Exit-code policy (the deliberate CI divergence) + unclassifiable output
# ---------------------------------------------------------------------------

class TestExitCodePolicy:
    def test_green_run_passes(self):
        v = G.evaluate(rc=0, output="42 passed in 3s\n", entries=[],
                       today=TODAY, targets=["scripts/"])
        assert v["exit_code"] == G.EXIT_PASS

    @pytest.mark.parametrize("rc", [2, 3, 4, 5])
    def test_non_failure_nonzero_exits_block_in_ci(self, rc):
        """prepush fails OPEN on 3/4/5; CI must fail CLOSED — the env is provisioned."""
        entries = _baseline({"test": "scripts/test_alpha.py::test_one", "expires": "2026-12-01"})
        v = G.evaluate(rc=rc, output="Interrupted: 1 error during collection\n",
                       entries=entries, today=TODAY, targets=["scripts/"])
        assert v["exit_code"] != G.EXIT_PASS

    def test_ci_policy_diverges_from_prepush_open_codes(self):
        """Pin the divergence itself so a future 'harmonization' is a red test."""
        assert 5 in (3, 4, 5), "sanity"
        v = G.evaluate(rc=5, output="no tests ran\n", entries=[], today=TODAY,
                       targets=["scripts/"])
        assert v["exit_code"] == G.EXIT_BLOCK_TESTS

    def test_unparseable_failure_output_blocks(self):
        """rc=1 with nothing parseable cannot be proven baselined -> block."""
        entries = _baseline({"test": "scripts/test_alpha.py::test_one", "expires": "2026-12-01"})
        v = G.evaluate(rc=1, output="something went wrong, no summary lines\n",
                       entries=entries, today=TODAY, targets=["scripts/"])
        assert v["exit_code"] == G.EXIT_BLOCK_TESTS
        assert "classif" in v["reason"].lower() or "parsed" in v["reason"].lower()


# ---------------------------------------------------------------------------
# Stale suppressions (non-blocking signal)
# ---------------------------------------------------------------------------

class TestStaleSuppression:
    def test_now_passing_entry_is_reported_but_does_not_block(self, tmp_path):
        _write_baseline(tmp_path, {"version": 1, "entries": [{
            "test": "scripts/test_alpha.py::test_one",
            "reason": "r", "owner": "tyroneross", "expires": "2026-08-13",
        }]})
        code, out = _run_cli(tmp_path, "42 passed in 3s\n", 0)
        assert code == 0
        assert "STALE SUPPRESSION" in out

    def test_entry_outside_the_run_targets_is_not_called_stale(self):
        """A test that did not run this time is not evidence of anything."""
        entries = _baseline({"test": "other/test_gamma.py::test_x", "expires": "2026-08-13"})
        v = G.evaluate(rc=0, output="42 passed\n", entries=entries, today=TODAY,
                       targets=["scripts/", "tests/"])
        assert v["stale"] == []


# ---------------------------------------------------------------------------
# Wiring — the gate must actually be invoked by CI
# ---------------------------------------------------------------------------

class TestWorkflowWiring:
    def test_workflow_invokes_the_gate(self):
        # Non-comment lines only: a mention inside the step's explanatory comment
        # block must not be able to satisfy this (verified by mutation).
        text = "\n".join(
            ln for ln in WORKFLOW.read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("#")
        )
        assert "scripts/ci_known_red_gate.py" in text, (
            "pytest.yml must run the suite through the known-red gate; otherwise a "
            "red test reaches main through CI exactly as it did on 2026-07-26."
        )

    def test_workflow_has_no_unguarded_bare_pytest_run(self):
        """A second `uv run pytest ...` step would reopen the hole."""
        text = WORKFLOW.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"\buv run pytest\b", stripped):
                pytest.fail(f"unguarded pytest invocation in pytest.yml: {stripped!r}")

    def test_workflow_still_runs_the_named_suites_and_markers(self):
        """Preserve the pipeline this gate wraps — same paths, marker, deselects."""
        text = WORKFLOW.read_text(encoding="utf-8")
        for token in ("scripts/ tests/", 'not integration', "--timeout=60",
                      "--timeout-method=thread", "-rfE"):
            assert token in text, f"pytest.yml lost {token!r}"
        for deselect in P._FULL_DESELECT:
            assert deselect in text, f"pytest.yml lost deselect {deselect!r}"

    def test_only_pytest_workflow_runs_the_suite(self):
        """No second, unguarded CI path may run the test suite."""
        wf_dir = REPO / ".github" / "workflows"
        offenders = []
        for path in sorted(wf_dir.glob("*.yml")):
            if path.name == "pytest.yml":
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if re.search(r"\b(pytest|python -m unittest)\b", stripped):
                    offenders.append(f"{path.name}: {stripped}")
        assert not offenders, (
            "another workflow runs tests without the known-red gate: " + "; ".join(offenders)
        )


# ---------------------------------------------------------------------------
# End-to-end: the gate really drives pytest
# ---------------------------------------------------------------------------

class TestRunsRealPytest:
    def test_real_failing_pytest_is_classified(self, tmp_path):
        """Full path: spawn pytest, parse ITS output, classify, exit non-zero."""
        _write_baseline(tmp_path, {"version": 1, "entries": []})
        t = tmp_path / "scripts" / "test_sample.py"
        t.write_text("def test_red():\n    assert False\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(HERE / "ci_known_red_gate.py"),
             "--workdir", str(tmp_path), "--today", TODAY.isoformat(),
             "--", "scripts/test_sample.py", "-p", "no:cacheprovider", "-q",
             "--no-header", "-rfE"],
            cwd=str(tmp_path), capture_output=True, text=True, check=False, timeout=180,
        )
        assert proc.returncode == G.EXIT_BLOCK_TESTS, proc.stdout + proc.stderr
        assert "NEWLY-RED" in proc.stdout

    def test_real_baselined_pytest_failure_passes(self, tmp_path):
        _write_baseline(tmp_path, {"version": 1, "entries": [{
            "test": "scripts/test_sample.py::test_red",
            "reason": "known", "owner": "tyroneross", "expires": "2026-12-01",
        }]})
        t = tmp_path / "scripts" / "test_sample.py"
        t.write_text("def test_red():\n    assert False\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(HERE / "ci_known_red_gate.py"),
             "--workdir", str(tmp_path), "--today", TODAY.isoformat(),
             "--target", "scripts/",
             "--", "scripts/test_sample.py", "-p", "no:cacheprovider", "-q",
             "--no-header", "-rfE"],
            cwd=str(tmp_path), capture_output=True, text=True, check=False, timeout=180,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "KNOWN-RED (suppressed)" in proc.stdout


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
