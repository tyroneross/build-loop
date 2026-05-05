"""Tests for plan_verify rule `schema-migration-full-chain` (priority 10/11).

Pattern: writer emits keys X, reader expects keys Y, drift undetected until
runtime. Rule fires WARN when a plan touches schema/serializer/migration
files but provides no reader-side or test-fixture co-change.

Three cases:
  - flags writer-only change (no test, no reader hint) → rule fires
  - silent when test fixture is co-changed → rule does not fire
  - explicit override silences the rule

Stdlib only (json, subprocess, sys, pathlib + pytest).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLAN_VERIFY = REPO / "scripts" / "plan_verify.py"


def _run_plan_verify(plan_path: Path) -> dict:
    """Invoke plan_verify.py --json against `plan_path`, return parsed JSON."""
    result = subprocess.run(
        [sys.executable, str(PLAN_VERIFY), str(plan_path), "--json", "--quiet"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    # exit 0 = no blockers, 1 = blockers, 2 = verifier error.
    assert result.returncode in (0, 1), (
        f"plan_verify exited {result.returncode}; stderr=\n{result.stderr}"
    )
    return json.loads(result.stdout)


def _has_schema_finding(payload: dict) -> bool:
    return any(
        f.get("rule_id") == "schema-migration-full-chain"
        for f in payload.get("findings", [])
    )


def test_flags_writer_change_without_test_update(tmp_path: Path) -> None:
    """Plan modifies a schemas.py file but does not co-change tests/ — fires."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Plan\n"
        "\n"
        "## Files touched\n"
        "- `src/build_loop/architecture/schemas.py` — add `confidence` field "
        "to the dataclass and update `to_dict()`.\n"
        "\n"
        "No further changes needed.\n",
        encoding="utf-8",
    )
    out = _run_plan_verify(plan)
    assert _has_schema_finding(out), (
        "expected schema-migration-full-chain to fire on writer-only change; "
        f"got findings: {[f.get('rule_id') for f in out.get('findings', [])]}"
    )
    # Severity should be WARN (not BLOCKER) — false-positive risk on
    # greenfield schemas.
    finding = next(
        f for f in out["findings"] if f["rule_id"] == "schema-migration-full-chain"
    )
    assert finding["severity"] == "WARN"


def test_silent_when_test_co_changed(tmp_path: Path) -> None:
    """Plan modifies schemas.py AND tests/test_schemas.py — does not fire."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Plan\n"
        "\n"
        "## Files touched\n"
        "- `src/build_loop/architecture/schemas.py` — add `confidence` field.\n"
        "- `tests/test_schemas.py` — add fixture asserting the new key round-trips.\n",
        encoding="utf-8",
    )
    out = _run_plan_verify(plan)
    assert not _has_schema_finding(out), (
        "schema-migration-full-chain should NOT fire when a tests/ co-change "
        f"is present; findings: {[f.get('rule_id') for f in out.get('findings', [])]}"
    )


def test_explicit_override_silences(tmp_path: Path) -> None:
    """Plan modifies schemas.py with `override: schema-migration-full-chain` — silenced."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Plan\n"
        "\n"
        "## Files touched\n"
        "- `src/build_loop/architecture/schemas.py` — add `confidence` field.\n"
        "\n"
        "override: schema-migration-full-chain (greenfield schema, no consumers yet)\n",
        encoding="utf-8",
    )
    out = _run_plan_verify(plan)
    assert not _has_schema_finding(out), (
        "schema-migration-full-chain should NOT fire when an explicit override "
        f"is present; findings: {[f.get('rule_id') for f in out.get('findings', [])]}"
    )


def test_serializer_method_change_without_evidence(tmp_path: Path) -> None:
    """Plan mentions `to_dict` / `@dataclass` change without reader hint — fires."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Plan\n"
        "\n"
        "Refactor `Component.to_dict()` to emit a new `confidence` field.\n"
        "All callers will pick it up automatically.\n",
        encoding="utf-8",
    )
    out = _run_plan_verify(plan)
    assert _has_schema_finding(out), (
        "expected rule to fire on bare serializer-method change without "
        f"reader/test evidence; findings: {[f.get('rule_id') for f in out.get('findings', [])]}"
    )


def test_reader_hint_silences(tmp_path: Path) -> None:
    """Plan mentions `to_dict` change AND `json.loads` reader — silenced."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Plan\n"
        "\n"
        "Refactor `Component.to_dict()` to emit a new `confidence` field.\n"
        "Update the reader at `scripts/load_index.py` (calls `json.loads`) to "
        "consume the new key.\n",
        encoding="utf-8",
    )
    out = _run_plan_verify(plan)
    assert not _has_schema_finding(out), (
        "schema-migration-full-chain should NOT fire when a reader-side hint "
        f"is present; findings: {[f.get('rule_id') for f in out.get('findings', [])]}"
    )
