# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/log_decision.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import log_decision


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    return tmp_path


def _read_state(workdir: Path) -> dict:
    return json.loads((workdir / ".build-loop" / "state.json").read_text())


# ---------------------------------------------------------------------------
# autonomous_default — happy path
# ---------------------------------------------------------------------------


def test_autonomous_default_append(workdir: Path) -> None:
    payload = {
        "decision_id": "d1",
        "phase": "execute",
        "chosen": "A",
        "options": [
            {"id": "A", "summary": "use cache", "user_impact": "faster", "performance": "p95 60ms"},
            {"id": "B", "summary": "fetch fresh", "user_impact": "always-current", "performance": "p95 220ms"},
        ],
        "confidence": "high",
        "rationale": "users see stale-cache-on-rate-limit per plan rubric r3",
    }
    entry = log_decision.log_autonomous_default(workdir, payload)
    assert entry["decision_id"] == "d1"
    assert entry["chosen"] == "A"
    assert entry["escalated"] is False
    assert entry["ts"]

    state = _read_state(workdir)
    assert len(state["runs"]) == 1
    assert len(state["runs"][0]["autonomousDefaults"]) == 1
    assert state["runs"][0]["autonomousDefaults"][0]["decision_id"] == "d1"


def test_autonomous_default_idempotent_on_decision_id(workdir: Path) -> None:
    payload = {
        "decision_id": "d1",
        "phase": "execute",
        "chosen": "A",
        "options": [{"id": "A"}, {"id": "B"}],
        "confidence": "high",
    }
    log_decision.log_autonomous_default(workdir, payload)
    log_decision.log_autonomous_default(workdir, payload)
    log_decision.log_autonomous_default(workdir, payload)

    state = _read_state(workdir)
    assert len(state["runs"][0]["autonomousDefaults"]) == 1


def test_autonomous_default_multiple_distinct(workdir: Path) -> None:
    base = {
        "phase": "execute",
        "chosen": "A",
        "options": [{"id": "A"}, {"id": "B"}],
        "confidence": "high",
    }
    log_decision.log_autonomous_default(workdir, {**base, "decision_id": "d1"})
    log_decision.log_autonomous_default(workdir, {**base, "decision_id": "d2"})
    log_decision.log_autonomous_default(workdir, {**base, "decision_id": "d3"})

    state = _read_state(workdir)
    ids = [e["decision_id"] for e in state["runs"][0]["autonomousDefaults"]]
    assert ids == ["d1", "d2", "d3"]


# ---------------------------------------------------------------------------
# autonomous_default — validation
# ---------------------------------------------------------------------------


def test_missing_required_field_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="missing required fields"):
        log_decision.log_autonomous_default(
            workdir,
            {"decision_id": "d1", "phase": "execute"},  # missing chosen/options/confidence
        )


def test_invalid_confidence_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="confidence must be one of"):
        log_decision.log_autonomous_default(
            workdir,
            {
                "decision_id": "d1",
                "phase": "execute",
                "chosen": "A",
                "options": [{"id": "A"}],
                "confidence": "ultra",
            },
        )


def test_chosen_not_in_options_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="not in options ids"):
        log_decision.log_autonomous_default(
            workdir,
            {
                "decision_id": "d1",
                "phase": "execute",
                "chosen": "Z",  # not in options
                "options": [{"id": "A"}, {"id": "B"}],
                "confidence": "high",
            },
        )


def test_empty_options_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="options must be a non-empty list"):
        log_decision.log_autonomous_default(
            workdir,
            {
                "decision_id": "d1",
                "phase": "execute",
                "chosen": "A",
                "options": [],
                "confidence": "high",
            },
        )


# ---------------------------------------------------------------------------
# risky_branch — happy path
# ---------------------------------------------------------------------------


def test_risky_branch_append(workdir: Path) -> None:
    payload = {
        "branch": "buildloop-risky-c3-a1b2c3d4",
        "hash": "a1b2c3d4e5f6",
        "files": ["migrations/0042_add_index.sql"],
        "summary": "migration touched — isolated to branch for review",
        "trade_offs": "reversible; user merges branch in morning",
        "matched_rule": "migrations/0042_add_index.sql matches migrations/**",
    }
    entry = log_decision.log_risky_branch(workdir, payload)
    assert entry["branch"] == "buildloop-risky-c3-a1b2c3d4"
    assert entry["files"] == ["migrations/0042_add_index.sql"]

    state = _read_state(workdir)
    assert len(state["runs"][0]["riskyBranches"]) == 1


def test_risky_branch_idempotent_on_hash(workdir: Path) -> None:
    payload = {
        "branch": "buildloop-risky-c3-a1",
        "hash": "a1b2c3",
        "files": [],
    }
    log_decision.log_risky_branch(workdir, payload)
    log_decision.log_risky_branch(workdir, payload)

    state = _read_state(workdir)
    assert len(state["runs"][0]["riskyBranches"]) == 1


def test_risky_branch_missing_hash_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="missing required fields"):
        log_decision.log_risky_branch(workdir, {"branch": "x"})


# ---------------------------------------------------------------------------
# state.json bootstrapping
# ---------------------------------------------------------------------------


def test_creates_state_json_skeleton(workdir: Path) -> None:
    """When state.json doesn't exist, the helper creates a minimal skeleton."""
    payload = {
        "decision_id": "d1",
        "phase": "execute",
        "chosen": "A",
        "options": [{"id": "A"}],
        "confidence": "high",
    }
    log_decision.log_autonomous_default(workdir, payload)
    state_path = workdir / ".build-loop" / "state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert "runs" in data
    assert data["schema_version"] == "1.0.0"


def test_unparseable_state_exits(workdir: Path) -> None:
    bl = workdir / ".build-loop"
    bl.mkdir()
    (bl / "state.json").write_text("not json")
    with pytest.raises(SystemExit, match="unparseable"):
        log_decision.log_autonomous_default(
            workdir,
            {
                "decision_id": "d1",
                "phase": "execute",
                "chosen": "A",
                "options": [{"id": "A"}],
                "confidence": "high",
            },
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_autonomous_default(
    workdir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload_file = tmp_path / "p.json"
    payload_file.write_text(json.dumps({
        "decision_id": "d1",
        "phase": "execute",
        "chosen": "A",
        "options": [{"id": "A"}, {"id": "B"}],
        "confidence": "high",
    }))
    rc = log_decision.main([
        "--workdir", str(workdir),
        "--kind", "autonomous_default",
        "--payload-json", str(payload_file),
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision_id"] == "d1"


def test_cli_missing_payload_file(
    workdir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = log_decision.main([
        "--workdir", str(workdir),
        "--kind", "risky_branch",
        "--payload-json", str(tmp_path / "nope.json"),
    ])
    assert rc == 1


def test_cli_unparseable_payload(
    workdir: Path, tmp_path: Path
) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json")
    rc = log_decision.main([
        "--workdir", str(workdir),
        "--kind", "autonomous_default",
        "--payload-json", str(p),
    ])
    assert rc == 1
