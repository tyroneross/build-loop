# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the deterministic findings detector."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scan_findings.detect import (  # noqa: E402
    SEVERITY_PRIORITY,
    detect_findings,
    normalize_severity,
)


def _by_route(cands):
    return {"backlog": [c for c in cands if c.route == "backlog"],
            "review": [c for c in cands if c.route == "review"]}


# --- the acceptance case -----------------------------------------------------

def test_acceptance_prose_high_finding_routes_to_backlog():
    cands = detect_findings(text_blocks=[
        "HIGH: verify-install.yml interpolates dispatch input into shell — command injection"
    ])
    assert len(cands) == 1
    c = cands[0]
    assert c.route == "backlog"
    assert c.severity == "high"
    assert c.priority == "P1"
    assert "command injection" in c.title
    # severity label is stripped from the title
    assert not c.title.upper().startswith("HIGH")


# --- severity normalization + priority map ----------------------------------

def test_severity_normalization_aliases():
    assert normalize_severity("CRITICAL") == "critical"
    assert normalize_severity("blocker") == "high"   # alias -> high
    assert normalize_severity("minor") == "medium"   # alias -> medium
    assert normalize_severity("info") == "low"        # alias -> low
    assert normalize_severity("banana") is None       # unrecognized -> None
    assert normalize_severity(None) is None


def test_priority_map_covers_all_severities():
    assert SEVERITY_PRIORITY == {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}


def test_each_severity_maps_to_expected_priority():
    cases = [("CRITICAL", "P0"), ("HIGH", "P1"), ("MEDIUM", "P2"), ("LOW", "P3")]
    for label, prio in cases:
        cands = detect_findings(text_blocks=[f"{label}: a concrete defect statement here"])
        assert len(cands) == 1, label
        assert cands[0].priority == prio, label


# --- routing: no severity -> review queue -----------------------------------

def test_finding_keyword_without_severity_routes_to_review():
    cands = detect_findings(text_blocks=[
        "I suspect a race condition in the worker pool under load"
    ])
    assert len(cands) == 1
    assert cands[0].route == "review"
    assert cands[0].severity is None


def test_bug_prefix_without_severity_routes_to_review():
    cands = detect_findings(text_blocks=["Bug: the export drops the last row on pagination"])
    assert len(cands) == 1
    assert cands[0].route == "review"


# --- precision: things that must NOT be captured ----------------------------

def test_question_is_not_a_finding():
    assert detect_findings(text_blocks=["Is this a high risk?"]) == []


def test_ordinary_severity_word_in_prose_is_not_a_label():
    # lowercase 'low' inside a sentence is not a label position
    assert detect_findings(text_blocks=[
        "There is some low latency on the homepage but it loads fine."
    ]) == []


def test_empty_and_blank_blocks_yield_nothing():
    assert detect_findings(text_blocks=["", "   ", "\n"]) == []


def test_severity_labeled_planning_line_downgrades_to_review():
    # A severity label with NO defect/technical signal is a planning line, not a
    # finding — it must NOT auto-land as a P0 backlog item (precision gate, f3).
    cands = detect_findings(text_blocks=["CRITICAL: ship the redesign by Friday for the launch"])
    assert len(cands) == 1
    assert cands[0].route == "review"
    assert cands[0].severity is None


def test_severity_labeled_with_defect_signal_stays_backlog():
    # The acceptance-class clause carries a filename + "command injection" — keeps
    # its severity and routes to backlog.
    cands = detect_findings(text_blocks=[
        "HIGH: verify-install.yml interpolates dispatch input into shell — command injection"
    ])
    assert cands[0].route == "backlog" and cands[0].severity == "high"


# --- structured JSON findings ------------------------------------------------

def test_structured_json_findings_split_by_severity():
    payload = json.dumps({"findings": [
        {"severity": "high", "title": "Token logged in plaintext", "evidence": "auth.py:42"},
        {"severity": "banana", "title": "Unknown-severity item"},
    ]})
    cands = detect_findings(text_blocks=[f"Audit returned: {payload}"])
    routes = _by_route(cands)
    assert len(routes["backlog"]) == 1
    assert routes["backlog"][0].severity == "high"
    assert routes["backlog"][0].source_kind == "structured_json"
    assert len(routes["review"]) == 1          # unknown severity -> review
    assert routes["review"][0].severity is None


def test_review_finding_gate_shaped_list_is_parsed():
    # A bare top-level list of severity-bearing dicts (gate output shape).
    payload = json.dumps([
        {"id": "f1", "severity": "critical", "evidence": "SQLi in /search"},
    ])
    cands = detect_findings(text_blocks=[payload])
    assert len(cands) == 1
    assert cands[0].route == "backlog"
    assert cands[0].priority == "P0"


# --- dedup -------------------------------------------------------------------

def test_same_finding_deduped_within_a_sweep():
    cands = detect_findings(text_blocks=[
        "HIGH: command injection in the verify-install.yml shell step",
        "HIGH: command injection in the verify-install.yml shell step",
    ])
    assert len(cands) == 1


def test_same_finding_different_severity_dedups_and_keeps_more_urgent():
    cands = detect_findings(text_blocks=[
        "MEDIUM: the cache key collides across tenants here",
        "CRITICAL: the cache key collides across tenants here",
    ])
    assert len(cands) == 1
    # most-urgent kept
    assert cands[0].priority == "P0"


def test_finding_hash_is_stable_across_calls():
    a = detect_findings(text_blocks=["HIGH: SQL injection in the search endpoint allows reads"])
    b = detect_findings(text_blocks=["HIGH: SQL injection in the search endpoint allows reads"])
    assert a[0].finding_hash == b[0].finding_hash


# --- transcript reading (assistant text + tool_result attribution) ----------

def test_transcript_reads_tool_result_and_attributes_agent(tmp_path: Path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join([
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Dispatching auditor."},
            {"type": "tool_use", "name": "Task", "input": {"subagent_type": "security-reviewer"}},
        ]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": [
                {"type": "text", "text": "HIGH: hardcoded credential in config loader"}
            ]},
        ]}}),
        # isMeta record must be ignored
        json.dumps({"isMeta": True, "type": "user",
                    "message": {"role": "user", "content": "git diff injected by a hook"}}),
    ]), encoding="utf-8")
    cands = detect_findings(transcript_path=transcript)
    assert len(cands) == 1
    assert cands[0].route == "backlog"
    assert cands[0].agent == "security-reviewer"
    assert cands[0].source_kind == "tool_result"


def test_missing_transcript_is_empty():
    assert detect_findings(transcript_path=Path("/no/such/transcript.jsonl")) == []
