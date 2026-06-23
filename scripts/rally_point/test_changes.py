# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/changes.py — append-only immutable log.

  - record schema build + validate (warns-not-drops unknown kind, D7)
  - NON-GOAL guard: no frequency/invocation/count keys anywhere
  - O_APPEND atomic under concurrency: N writers -> N intact JSON lines
  - read_changes_since(offset) returns only new records + new offset
  - immutable: no rewrite/delete/truncate API exists
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import changes as ch  # noqa: E402

_FREQ_KEYS = {
    "count", "counts", "frequency", "freq", "invocations", "invocation_count",
    "calls", "num_calls", "call_count", "hits", "usage", "usage_count",
}


def _assert_no_freq(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k.lower() not in _FREQ_KEYS, f"non-goal key leaked: {k}"
            _assert_no_freq(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_freq(v)


@pytest.fixture()
def chan(tmp_path: Path) -> Path:
    d = tmp_path / "chan"
    d.mkdir()
    return d


def test_make_record_schema():
    r = ch.make_record(
        kind="commit", tool="claude", model="opus", run_id="r1",
        app_slug="app", payload={"sha": "abc"}, revision=3,
    )
    assert set(r) == {
        "ts", "kind", "tool", "model", "run_id", "app_slug", "payload",
        "revision",
    }
    assert r["kind"] == "commit" and r["revision"] == 3
    _assert_no_freq(r)


def test_validate_warns_not_drops_unknown_kind(capsys):
    r = ch.make_record(
        kind="totally-new-kind", tool="t", model="m", run_id="r",
        app_slug="a", payload={}, revision=1,
    )
    out = ch.validate_record(r)  # must RETURN the record, not drop it
    assert out == r
    assert "totally-new-kind" in capsys.readouterr().err


def test_append_and_read_since(chan: Path):
    r1 = ch.make_record(kind="commit", tool="t", model="m", run_id="r",
                         app_slug="a", payload={}, revision=1)
    ch.append_change(chan, r1)
    recs, off = ch.read_changes_since(chan, 0)
    assert [x["kind"] for x in recs] == ["commit"]
    r2 = ch.make_record(kind="phase", tool="t", model="m", run_id="r",
                        app_slug="a", payload={"phase": "execute"}, revision=2)
    ch.append_change(chan, r2)
    recs2, off2 = ch.read_changes_since(chan, off)
    assert [x["kind"] for x in recs2] == ["phase"]
    assert off2 > off
    # absent log -> empty, offset 0
    assert ch.read_changes_since(chan / "nope", 0) == ([], 0)


def test_read_normalizes_hash_chain_record(chan: Path):
    row = {
        "event": {
            "id": "evt_hash_1",
            "kind": "handoff",
            "tool": "claude_code",
            "model": "inherit",
            "run_id": "run-1",
            "app_slug": "agent-rally-point",
            "subject": "review this",
            "time": "2026-05-29T00:00:00Z",
            "payload": {
                "from_tool": "claude_code",
                "to_tool": "codex",
                "subject": "review this",
                "requires_ack": True,
            },
        },
        "local_seq": 7,
        "received_at": "2026-05-29T00:00:01Z",
    }
    (chan / "changes.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    recs, off = ch.read_changes_since(chan, 0)

    assert off > 0
    assert len(recs) == 1
    assert recs[0]["kind"] == "handoff"
    assert recs[0]["tool"] == "claude_code"
    assert recs[0]["revision"] == 7
    assert recs[0]["payload"]["to_tool"] == "codex"


def test_read_normalizes_repo_local_rally_log(chan: Path):
    log_dir = chan / "log"
    log_dir.mkdir()
    row = {
        "seq": 42,
        "occurred_at": "2026-06-01T20:41:07Z",
        "event_type": "handoff",
        "engagement": "easy-terminal",
        "payload": {
            "kind": "handoff",
            "tool": "claude_code:redesign-coord",
            "target": "codex",
            "subject": "Workbench glass redesign LANDED",
            "summary": "ready for review",
            "status": "done",
            "scope": ["file:Sources/App.swift"],
            "event_id": "fact_123",
        },
    }
    (log_dir / "easy-terminal.jsonl").write_text(
        json.dumps(row) + "\n",
        encoding="utf-8",
    )

    recs, off = ch.read_changes_since(chan, 0)

    assert off == 42
    assert len(recs) == 1
    assert recs[0]["kind"] == "handoff"
    assert recs[0]["tool"] == "claude_code:redesign-coord"
    assert recs[0]["revision"] == 42
    assert recs[0]["app_slug"] == "easy-terminal"
    assert recs[0]["payload"]["to_tool"] == "codex"
    assert recs[0]["payload"]["subject"] == "Workbench glass redesign LANDED"


def test_read_normalizes_fact_v1_record(chan: Path):
    # A fact.v1 line (build-loop's ARP-ingestible fallback shape) must read back
    # to the legacy reader shape, with revision sourced from bl_revision (NOT seq)
    # so coordination_rally.py's `revision == channel_rev` equality survives.
    row = {
        "schema": "agent-rally.fact.v1",
        "event_id": "blf_abc123",
        "seq": 0,
        "thread_id": "run-9",
        "kind": "handoff",
        "subject": "review this",
        "scope": ["file.py"],
        "created_at": "2026-06-17T00:00:00Z",
        "evidence": [],
        "tool": "claude_code",
        "target": "codex",
        "bl_revision": 5,
        "bl_kind": "handoff",
        "bl_model": "opus",
        "bl_app_slug": "build-loop",
        "bl_payload": {"subject": "review this", "to_tool": "codex", "run_id": "run-9"},
    }
    (chan / "changes.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    recs, off = ch.read_changes_since(chan, 0)

    assert off > 0
    assert len(recs) == 1
    r = recs[0]
    assert r["_source_format"] == "fact-v1"
    assert r["kind"] == "handoff"
    assert r["tool"] == "claude_code"
    assert r["model"] == "opus"
    assert r["run_id"] == "run-9"
    assert r["app_slug"] == "build-loop"
    assert r["revision"] == 5  # from bl_revision, NOT seq=0 — the regression guard
    assert r["payload"]["to_tool"] == "codex"


def test_fact_v1_revision_match_regression_guard(chan: Path):
    # The exact pattern coordination_rally.py uses: filter records by revision == channel_rev.
    # With bl_revision preserved, the match holds after the fact.v1 write-flip.
    import fact_v1 as fv  # noqa: PLC0415
    channel_rev = 11
    f = fv.to_fact_v1(kind="handoff", tool="claude_code", model="m", run_id="run-x",
                      app_slug="a", payload={"subject": "h", "to": "codex"}, revision=channel_rev)
    fv.write_fact_v1_line(chan, f)
    recs, _ = ch.read_changes_since(chan, 0)
    matching = [r for r in recs if r.get("revision") == channel_rev
                and r.get("kind") == "handoff" and r.get("tool") == "claude_code"]
    assert len(matching) == 1, "revision-match handoff verify must still find the record"


def test_repo_local_rally_log_offset_is_sequence(chan: Path):
    log_dir = chan / "log"
    log_dir.mkdir()
    rows = [
        {"seq": 1, "event_type": "presence", "payload": {"tool": "a"}},
        {"seq": 3, "event_type": "risk", "payload": {"tool": "b"}},
    ]
    (log_dir / "room.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    recs, off = ch.read_changes_since(chan, 1)

    assert off == 3
    assert [r["revision"] for r in recs] == [3]


def _writer(d: str, tag: int):
    for i in range(20):
        ch.append_change(
            Path(d),
            ch.make_record(kind="commit", tool="t", model="m",
                           run_id=f"w{tag}", app_slug="a",
                           payload={"i": i}, revision=1),
        )


def test_concurrent_append_no_torn_lines(chan: Path):
    procs = [mp.Process(target=_writer, args=(str(chan), t)) for t in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    recs, _ = ch.read_changes_since(chan, 0)
    assert len(recs) == 6 * 20
    for r in recs:  # every line parsed cleanly == no torn writes
        assert set(r) >= {"kind", "run_id", "payload"}
        _assert_no_freq(r)


def test_no_mutation_api():
    for forbidden in ("rewrite", "delete_change", "truncate", "overwrite",
                       "update_change", "remove_change"):
        assert not hasattr(ch, forbidden), f"immutability violated: {forbidden}"


def test_read_archived_changes_reads_rotated_files(chan: Path):
    # A rotated (archived) log is a `changes.jsonl.<DATE>` sibling that
    # read_changes_since never reads — read_archived_changes is the read-back.
    r1 = ch.make_record(kind="decision", tool="t", model="m", run_id="r",
                        app_slug="a", payload={"old": True}, revision=1)
    rotated = chan / "changes.jsonl.2026-06-01"
    rotated.write_text(json.dumps(r1) + "\n", encoding="utf-8")
    # also a same-day numeric-suffix rotation
    r2 = ch.make_record(kind="decision", tool="t", model="m", run_id="r",
                        app_slug="a", payload={"old2": True}, revision=2)
    (chan / "changes.jsonl.2026-06-01.2").write_text(
        json.dumps(r2) + "\n", encoding="utf-8"
    )
    archived = ch.read_archived_changes(chan)
    kinds = [a["kind"] for a in archived]
    assert kinds == ["decision", "decision"]
    # live changes.jsonl is NOT included by the archive reader.
    ch.append_change(chan, ch.make_record(kind="commit", tool="t", model="m",
                     run_id="r", app_slug="a", payload={}, revision=3))
    archived2 = ch.read_archived_changes(chan)
    assert all(a["kind"] == "decision" for a in archived2), "live log excluded"


def test_read_archived_changes_absent_is_empty(chan: Path):
    assert ch.read_archived_changes(chan) == []
    assert ch.read_archived_changes(chan / "nope") == []
