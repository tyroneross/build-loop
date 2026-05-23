# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Contract test for orchestrator Rally Point presence/phase surfacing (T7).

Two halves:
  1. Behavioural: exercise the REAL presence + changes + checkpoint
     modules the way the orchestrator is documented to — preamble writes
     presence, each phase-start appends a `phase` record and calls
     checkpoint_read; peers/dep/arch reactions show in the envelope;
     soft-claim is a WARNING, never a block.
  2. Doc contract: agents/build-orchestrator.md contains the documented
     Rally Point surfacing block (grep — existence only, not behaviour).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import changes as ch  # noqa: E402
import checkpoint as cp  # noqa: E402
import presence as pr  # noqa: E402
import revision as rev  # noqa: E402

_ORCH_DOC = _HERE.parent.parent / "agents" / "build-orchestrator.md"


@pytest.fixture()
def chan(tmp_path: Path) -> Path:
    d = tmp_path / "chan"
    d.mkdir()
    return d


def test_orchestrator_preamble_and_phase_flow(chan: Path):
    # Preamble: orchestrator writes its own presence.
    pr.write_presence(chan, session_id="orch", tool="claude", model="opus",
                      run_id="run-self", app_slug="a", phase="assess",
                      files_in_flight=[])
    assert (chan / "sessions" / "orch.json").exists()

    # A peer (other tool) is mid-execute owning a file we will touch.
    pr.write_presence(chan, session_id="peer", tool="codex", model="m",
                      run_id="run-peer", app_slug="a", phase="execute",
                      files_in_flight=["src/api.py"])
    ch.append_change(chan, ch.make_record(
        kind="dep-change", tool="codex", model="m", run_id="run-peer",
        app_slug="a", payload={"manifests": ["package.json"]}, revision=1))
    rev.bump_revision(chan)

    # Phase-start: append a `phase` record, then checkpoint_read.
    ch.append_change(chan, ch.make_record(
        kind="phase", tool="claude", model="opus", run_id="run-self",
        app_slug="a", payload={"phase": "execute"}, revision=2))
    rev.bump_revision(chan)
    env = cp.checkpoint_read(chan, session_id="orch",
                             my_files=["src/api.py"])

    assert env["changed"] is True
    assert any(c["kind"] == "phase" for c in env["new_changes"])
    assert any(p["session_id"] == "peer" for p in env["active_peers"])
    types = {r["type"] for r in env["reactions"]}
    assert "reinstall" in types
    soft = [r for r in env["reactions"] if r["type"] == "soft-claim"]
    assert soft
    # 2026-05-19: severity is now one of {warning, informational} keyed
    # off the peer's merge-status; D4 still holds — never a "block".
    assert soft[0]["severity"] in {"warning", "informational"}
    assert soft[0].get("reason") in {"merged_residue", "squash_landed",
                                     "active_conflict"}
    # D4: soft-claim never carries a blocking directive.
    assert "block" not in soft[0] and soft[0].get("severity") != "block"


def test_orchestrator_doc_documents_surfacing_block():
    text = _ORCH_DOC.read_text()
    assert "Rally Point" in text
    # the documented surfacing contract must name the entry point,
    # presence write, phase record, and the soft-claim-is-warning rule.
    for token in ("checkpoint_read", "presence", "phase record",
                  "soft-claim"):
        assert token in text, f"orchestrator doc missing: {token!r}"
