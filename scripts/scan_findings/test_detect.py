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
    FindingCandidate,
    detect_findings,
    junk_title_reason,
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
    # CHANGED: P0 now requires a locator (P0-requires-evidence gate), so the
    # CRITICAL case's clause carries a file path (`config.py`) the others don't
    # need — this is the new gate, not a fixture bug.
    cases = [
        ("CRITICAL", "a concrete defect statement in config.py", "P0"),
        ("HIGH", "a concrete defect statement here", "P1"),
        ("MEDIUM", "a concrete defect statement here", "P2"),
        ("LOW", "a concrete defect statement here", "P3"),
    ]
    for label, clause, prio in cases:
        cands = detect_findings(text_blocks=[f"{label}: {clause}"])
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
    # CHANGED: "Unknown-severity item" was 2 words -> now filtered by the
    # too-short junk gate (MIN_TITLE_WORDS=4); reworded to a real-length title.
    payload = json.dumps({"findings": [
        {"severity": "high", "title": "Token logged in plaintext", "evidence": "auth.py:42"},
        {"severity": "banana", "title": "This item has an unknown severity value"},
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
    # CHANGED: evidence needs >=4 words (too-short gate) and a locator
    # (P0-requires-evidence gate) to keep its P0. Avoids "injection" (a bare
    # _FINDING_KEYWORDS term) so the raw JSON line itself isn't ALSO picked up
    # by the prose keyword scanner as a second (review-route) candidate.
    payload = json.dumps([
        {"id": "f1", "severity": "critical", "evidence": "leaked API tokens found in search/query.py"},
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
    # CHANGED: clause carries a file path (cache.py) so the CRITICAL restatement
    # keeps its P0 under the new P0-requires-evidence gate.
    cands = detect_findings(text_blocks=[
        "MEDIUM: the cache key in cache.py collides across tenants here",
        "CRITICAL: the cache key in cache.py collides across tenants here",
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


# --- unattributed tool output (Read/Grep/Bash/MCP) never feeds the review queue --

def _tool_result_transcript(tmp_path: Path, tool_use: dict, result_text: str) -> Path:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join([
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Reading the file."}, tool_use,
        ]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": [{"type": "text", "text": result_text}]},
        ]}}),
    ]), encoding="utf-8")
    return transcript


# Real shapes from the 2026-11-01 queue: a code line read from disk, and an MCP
# result whose JSON names a docs file about "injection".
_TOOL_NOISE = "\n".join([
    "import { safeFetch } from '@/lib/ingestion/prevalidation/ssrf-guard'",
    '{"chunk_id":"af0a72","exit_code":0,"output":"let docs = try source(\\"docs/agents/injection.md\\")"}',
    '[{"severity": "info", "message": "designer/emit/init has no connections"}]',
])


def test_unattributed_tool_output_does_not_create_review_candidates(tmp_path: Path):
    t = _tool_result_transcript(
        tmp_path, {"type": "tool_use", "name": "Read", "input": {"file_path": "x.ts"}}, _TOOL_NOISE)
    assert [c for c in detect_findings(transcript_path=t) if c.route == "review"] == []


def test_unattributed_tool_output_still_routes_severity_findings_to_backlog(tmp_path: Path):
    t = _tool_result_transcript(
        tmp_path, {"type": "tool_use", "name": "Bash", "input": {"command": "audit"}},
        "HIGH: hardcoded credential in `config/loader.py`")
    cands = detect_findings(transcript_path=t)
    assert [c.route for c in cands] == ["backlog"]


def test_dispatched_agent_tool_result_still_feeds_review_queue(tmp_path: Path):
    t = _tool_result_transcript(
        tmp_path, {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "security-reviewer"}},
        _TOOL_NOISE)
    review = [c for c in detect_findings(transcript_path=t) if c.route == "review"]
    assert review and all(c.agent == "security-reviewer" for c in review)


def test_assistant_prose_finding_without_severity_still_routes_to_review():
    cands = detect_findings(text_blocks=[("I suspect a race condition in the worker pool",
                                          "assistant_text", "session")])
    assert [c.route for c in cands] == ["review"]


def test_parallel_agent_results_keep_their_own_attribution(tmp_path: Path):
    """Two Agent calls in one message: each result maps to its own agent by id."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join([
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a1", "name": "Agent", "input": {"subagent_type": "security-reviewer"}},
            {"type": "tool_use", "id": "a2", "name": "Agent", "input": {"subagent_type": "independent-auditor"}},
        ]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a1", "content": [{"type": "text", "text": "I suspect a race condition in the worker pool"}]},
            {"type": "tool_result", "tool_use_id": "a2", "content": [{"type": "text", "text": "I suspect a race condition in the retry loop"}]},
        ]}}),
    ]), encoding="utf-8")
    review = [c for c in detect_findings(transcript_path=transcript) if c.route == "review"]
    assert sorted(c.agent for c in review) == ["independent-auditor", "security-reviewer"]


# --- junk-title gate: seeded rubric noise -----------------------------------
# Real junk observed in the build-loop backlog, all filed P0 from severity
# critical: an agent's severity RUBRIC line gets mis-parsed as a "finding".

def test_seeded_rubric_lines_produce_zero_candidates():
    blocks = [
        "severity: critical|high, medium, or low",
        "severity: critical|high|medium|low). See agents/independent-auditor.md for the full schema",
        "HIGH: / High / Medium / Low",
        json.dumps({"severity": "critical", "title": "coordination:ack"}),
    ]
    assert detect_findings(text_blocks=blocks) == []


def test_real_finding_still_passes_with_new_gates():
    cands = detect_findings(text_blocks=[
        "HIGH: verify-install.yml interpolates dispatch input into shell — command injection"
    ])
    assert len(cands) == 1
    assert cands[0].route == "backlog"
    assert cands[0].priority == "P1"


# --- P0 gating: critical requires a concrete locator -------------------------

def test_p0_without_locator_downgrades_to_p1():
    cand = FindingCandidate(
        severity="critical",
        title="CRITICAL: session tokens are written to the shared log in plaintext",
        evidence="",
        route="backlog",
        source_kind="assistant_text",
        agent="session",
    )
    assert cand.priority == "P1"


def test_p0_with_inline_code_locator_stays_p0():
    cand = FindingCandidate(
        severity="critical",
        title="CRITICAL: `auth.py` writes session tokens to the shared log",
        evidence="",
        route="backlog",
        source_kind="assistant_text",
        agent="session",
    )
    assert cand.priority == "P0"


def test_p0_locator_may_come_from_evidence_instead_of_title():
    cand = FindingCandidate(
        severity="critical",
        title="session tokens are written to the shared log in plaintext",
        evidence="see auth.py:42 for the write site",
        route="backlog",
        source_kind="assistant_text",
        agent="session",
    )
    assert cand.priority == "P0"


# --- junk_title_reason unit cases --------------------------------------------

def test_junk_title_reason_severity_enum_comma_separated():
    assert junk_title_reason("high, medium, or low") == "severity-enum-template"


def test_junk_title_reason_severity_enum_pipe_separated():
    assert junk_title_reason(
        "high|medium|low). See agents/independent-auditor.md for the full schema"
    ) == "severity-enum-template"


def test_junk_title_reason_severity_enum_slash_separated_anywhere():
    assert junk_title_reason("/ High / Medium / Low") == "severity-enum-template"


def test_junk_title_reason_to_severity_prefix():
    assert junk_title_reason("to Critical") == "severity-enum-template"


def test_junk_title_reason_too_short():
    assert junk_title_reason("coordination:ack") == "too-short"
    assert junk_title_reason("role:lead") == "too-short"


def test_junk_title_reason_none_for_empty():
    assert junk_title_reason("") == "too-short"


def test_junk_title_reason_legitimate_severity_word_used_normally():
    # Only enum SEPARATORS trigger the template rule — an ordinary sentence
    # that happens to start with a severity-like word is a real finding title.
    assert junk_title_reason("High CPU usage in worker pool loop") is None


def test_junk_title_reason_real_finding_is_none():
    assert junk_title_reason(
        "verify-install.yml interpolates dispatch input into shell — command injection"
    ) is None
