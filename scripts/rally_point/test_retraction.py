# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for rally_point.retraction — append-only withdrawal of a posted fact."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import retraction as rt  # noqa: E402


def fact(event_id: str, **over) -> dict:
    record = {
        "ts": "2026-08-07T00:00:00Z",
        "kind": "decision",
        "tool": "claude_code",
        "model": "m",
        "run_id": "r",
        "app_slug": "build-loop",
        "payload": {"subject": f"subject-{event_id}"},
        "revision": 1,
        "event_id": event_id,
    }
    record.update(over)
    return record


def retraction_record(
    target: str, *, reason="wrong", superseded_by=None, event_id="retract-1", **over
) -> dict:
    payload = rt.build_payload(
        target=target, reason=reason, superseded_by=superseded_by, session_id="s1"
    )
    return fact(event_id, kind=rt.RETRACT_KIND, payload=payload,
                subject=payload["subject"], **over)


# --- payload construction -------------------------------------------------

def test_build_payload_carries_target_in_every_carrier():
    payload = rt.build_payload(target="fact_abc", reason="posted by mistake")
    assert payload["retracts"] == "fact_abc"
    assert payload["subject"] == "retract: fact_abc"
    assert "retracts=fact_abc" in payload["summary"]
    assert "posted by mistake" in payload["summary"]


def test_build_payload_encodes_superseded_by():
    payload = rt.build_payload(
        target="fact_abc", reason="corrected", superseded_by="fact_def"
    )
    assert payload["superseded_by"] == "fact_def"
    assert "superseded_by=fact_def" in payload["summary"]


def test_build_payload_omits_superseded_by_when_absent():
    payload = rt.build_payload(target="fact_abc", reason="oops")
    assert "superseded_by" not in payload
    assert "superseded_by" not in payload["summary"]


# --- cross-store detection ------------------------------------------------

def test_target_of_reads_structured_key():
    assert rt.target_of(retraction_record("fact_abc")) == "fact_abc"


def test_target_of_reads_native_subject_only():
    """The native store drops unknown payload keys and remaps the kind."""
    native = fact("x", kind="artifact", subject="retract: fact_abc",
                  payload={"subject": "retract: fact_abc"})
    assert rt.target_of(native) == "fact_abc"
    assert rt.is_retraction(native)


def test_target_of_reads_summary_token_when_subject_lost():
    degraded = fact("x", kind="artifact", subject="artifact",
                    payload={"summary": "wrong number [retracts=fact_abc]"})
    assert rt.target_of(degraded) == "fact_abc"


def test_ordinary_fact_is_not_a_retraction():
    assert rt.target_of(fact("fact_abc")) is None
    assert not rt.is_retraction(fact("fact_abc"))


def test_subject_discussing_retraction_is_not_a_retraction():
    """Anchored match — a prose subject must not neutralize a random token."""
    prose = fact("x", payload={"subject": "retract: we should support this someday"})
    assert rt.target_of(prose) is None


def test_superseded_by_recovered_from_summary():
    native = fact("x", kind="artifact", subject="retract: fact_abc",
                  payload={"summary": "fixed [retracts=fact_abc superseded_by=fact_def]"})
    assert rt.superseded_by_of(native) == "fact_def"


def test_superseded_by_none_when_absent():
    assert rt.superseded_by_of(retraction_record("fact_abc")) is None


# --- index ----------------------------------------------------------------

def test_index_maps_target_to_reason_and_author():
    idx = rt.index([fact("fact_abc"), retraction_record("fact_abc", reason="bad data")])
    assert set(idx) == {"fact_abc"}
    assert idx["fact_abc"]["reason"] == "bad data"
    assert idx["fact_abc"]["retracted_by"] == "claude_code"


def test_index_last_retraction_wins():
    rows = [
        retraction_record("fact_abc", reason="first"),
        retraction_record("fact_abc", reason="second", superseded_by="fact_def"),
    ]
    idx = rt.index(rows)
    assert idx["fact_abc"]["reason"] == "second"
    assert idx["fact_abc"]["superseded_by"] == "fact_def"


def test_index_empty_for_plain_records():
    assert rt.index([fact("a"), fact("b")]) == {}


# --- apply ----------------------------------------------------------------

def test_apply_drops_the_retracted_fact_and_keeps_the_retraction():
    rows = [fact("fact_abc"), fact("fact_keep"), retraction_record("fact_abc")]
    out = rt.apply(rows)
    ids = [r.get("event_id") for r in out]
    assert "fact_abc" not in ids
    assert "fact_keep" in ids
    assert any(rt.is_retraction(r) for r in out)


def test_apply_is_a_noop_without_retractions():
    rows = [fact("a"), fact("b")]
    assert rt.apply(rows) == rows


def test_apply_never_suppresses_a_retraction_record():
    """A retraction of a retraction must not erase the correction trail."""
    inner = retraction_record("fact_abc", event_id="retract-inner")
    outer = retraction_record(
        "retract-inner", reason="retract the retraction", event_id="retract-outer"
    )
    out = rt.apply([fact("fact_abc"), inner, outer])
    assert [r["event_id"] for r in out] == ["retract-inner", "retract-outer"]


def test_apply_audit_view_annotates_instead_of_dropping():
    rows = [fact("fact_abc"), retraction_record("fact_abc", reason="typo")]
    out = rt.apply(rows, include_retracted=True)
    assert len(out) == 2
    retracted = [r for r in out if r.get("event_id") == "fact_abc"][0]
    assert retracted["_retracted"]["reason"] == "typo"


def test_apply_does_not_mutate_input_records():
    original = fact("fact_abc")
    rows = [original, retraction_record("fact_abc")]
    rt.apply(rows, include_retracted=True)
    assert "_retracted" not in original


def test_apply_suppresses_across_a_native_round_trip():
    """A fact posted natively, retracted natively, still resolves."""
    rows = [
        fact("fact_abc", kind="artifact"),
        fact("r", kind="artifact", subject="retract: fact_abc",
             payload={"subject": "retract: fact_abc", "summary": "wrong [retracts=fact_abc]"}),
    ]
    out = rt.apply(rows)
    assert [r["event_id"] for r in out] == ["r"]


def test_apply_tolerates_empty_and_none():
    assert rt.apply([]) == []
    assert rt.apply(None) == []


def test_index_reason_strips_the_wire_marker_from_a_native_summary():
    """Native round-trip loses payload.reason; the recovered text must read as prose."""
    native = fact("r", kind="artifact", subject="retract: fact_abc",
                  payload={"summary": "bad read [retracts=fact_abc superseded_by=fact_ok]"})
    info = rt.index([native])["fact_abc"]
    assert info["reason"] == "bad read"
    assert info["superseded_by"] == "fact_ok"
