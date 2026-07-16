# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for prepush_judgment_gate.py — proves the BLOCKING self-mod judgment gate.

Rigs the failing case (stakes-gated self-mod run, Frontier layer skipped) → BLOCK,
then the passing cases (auditor dispatched → allow; non-self-mod push → allow;
out-of-window stale run → allow; bypass env → bypass).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import prepush_judgment_gate as g  # noqa: E402


def _write_state(workdir: Path, run: dict) -> None:
    (workdir / ".build-loop").mkdir(parents=True, exist_ok=True)
    (workdir / ".build-loop" / "state.json").write_text(json.dumps({"runs": [run]}))


def _now_iso(offset_days: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).isoformat()


def _self_mod_run(auditor_status: str, *, stakes="medium") -> dict:
    return {
        "run_id": "bl-test-selfmod",
        "date": _now_iso(),
        "started_at": _now_iso(-0.01),
        "ended_at": _now_iso(),
        "stakes": stakes,
        "auditor_status": auditor_status,
        "scope": "build",
    }


def test_bypass_env(tmp_path):
    v = g.evaluate(tmp_path, [], env={"BUILDLOOP_JUDGMENT_GATE_SKIP": "1"})
    assert v["action"] == "bypass"


def test_non_selfmod_push_allows(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "_pushed_files", lambda repo, lines: {"docs/readme.md", "src/x.ts"})
    _write_state(tmp_path, _self_mod_run("not-run:parent-must-dispatch"))
    v = g.evaluate(tmp_path, ["r a r b"])
    assert v["action"] == "allow" and "not a self-modifying" in v["reason"]


def test_selfmod_skipped_frontier_blocks(tmp_path, monkeypatch):
    # RIG FAILURE: self-mod push + stakes-gated run whose auditor was NOT dispatched.
    monkeypatch.setattr(g, "_pushed_files", lambda repo, lines: {"scripts/foo.py"})
    _write_state(tmp_path, _self_mod_run("not-run:parent-must-dispatch"))
    v = g.evaluate(tmp_path, ["refs/heads/main abc refs/heads/main def"])
    assert v["action"] == "block", v
    assert v["exit_code"] == 1
    msg = g.format_block_message(v)
    assert "JUDGMENT GATE" in msg


def test_selfmod_auditor_dispatched_allows(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "_pushed_files", lambda repo, lines: {"scripts/foo.py"})
    _write_state(tmp_path, _self_mod_run("ran:dispatched-agent"))
    v = g.evaluate(tmp_path, ["refs/heads/main abc refs/heads/main def"])
    assert v["action"] == "allow", v


def test_out_of_window_run_allows(tmp_path, monkeypatch):
    # A self-mod push but the only run record is weeks old → not attributed → allow.
    monkeypatch.setattr(g, "_pushed_files", lambda repo, lines: {"agents/x.md"})
    stale = _self_mod_run("not-run:parent-must-dispatch")
    stale["date"] = _now_iso(-30)
    stale["started_at"] = _now_iso(-30.02)
    stale["ended_at"] = _now_iso(-30)
    _write_state(tmp_path, stale)
    v = g.evaluate(tmp_path, ["refs/heads/main abc refs/heads/main def"])
    assert v["action"] == "allow", v


def test_no_run_record_allows(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "_pushed_files", lambda repo, lines: {"scripts/foo.py"})
    (tmp_path / ".build-loop").mkdir(parents=True)
    (tmp_path / ".build-loop" / "state.json").write_text(json.dumps({"runs": []}))
    v = g.evaluate(tmp_path, ["refs/heads/main abc refs/heads/main def"])
    assert v["action"] == "allow" and "no run record" in v["reason"]
