# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Focused contracts for the Rally backend adapter and payload codec."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import backend_adapter as adapter
from rally_point import payload_codec
from rally_point import post as post_module
from rally_point.discovery_bridge import DiscoveryEnvelope


def _envelope(tmp_path: Path, *, native: bool = True) -> DiscoveryEnvelope:
    return DiscoveryEnvelope(
        channel_dir=str(tmp_path / (".rally" if native else "fallback")),
        app_slug="adapter-contract",
        repo_id="adapter-contract",
        channel_layout="repo-local-rally" if native else "fact-v1",
        policy="canonical" if native else "legacy-only",
        protocol_version="1.0",
        last_resolved_at="2026-08-14T00:00:00Z",
        resolved_via="repo-local-rally-cli" if native else "build-loop-internal",
        raw={"rally_binary": "/fake/rally"} if native else {},
    )


def _context(tmp_path: Path, *, native: bool = True) -> adapter.BackendContext:
    return adapter.BackendContext(
        workdir=tmp_path,
        envelope=_envelope(tmp_path, native=native),
        local_channel_dir=tmp_path / "fallback",
    )


def _completed(payload: dict, *, returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        returncode=returncode,
        stdout=json.dumps(payload),
        stderr="",
    )


def test_payload_roundtrip_and_tamper_rejection() -> None:
    payload = {
        "subject": "unicode survives: café ☕",
        "nested": {"z": 3, "a": [True, None, {"key": "value"}]},
    }
    encoded = payload_codec.encode_payload(payload)

    assert payload_codec.decode_payload(["caller evidence", *encoded]) == payload

    header, body = encoded[0].rsplit(":", 1)
    replacement = "A" if body[0] != "A" else "B"
    tampered = [f"{header}:{replacement}{body[1:]}", *encoded[1:]]
    assert payload_codec.decode_payload(tampered) is None


def test_payload_oversize_marker_and_rally_evidence_bounds() -> None:
    fixed_json_bytes = len(payload_codec.canonical_payload({"blob": ""}))
    largest_payload = {"blob": "x" * (payload_codec.MAX_PAYLOAD_BYTES - fixed_json_bytes)}

    evidence = payload_codec.encode_payload(largest_payload)

    assert len(payload_codec.canonical_payload(largest_payload)) == payload_codec.MAX_PAYLOAD_BYTES
    assert len(evidence) == payload_codec.MAX_CHUNKS
    assert len(evidence) <= 64
    assert all(len(entry.encode("utf-8")) <= 4096 for entry in evidence)
    assert payload_codec.decode_payload(evidence) == largest_payload

    oversize = payload_codec.encode_payload(
        {"blob": "x" * (payload_codec.MAX_PAYLOAD_BYTES + 1)}
    )
    assert len(oversize) == 1
    assert payload_codec.has_oversize_marker(oversize)
    assert payload_codec.decode_payload(oversize) is None


def test_native_inbox_projects_ack_and_non_ack_messages() -> None:
    artifact_evidence = payload_codec.encode_event(
        kind="message",
        payload={
            "subject": "FYI",
            "from": "claude_code",
            "to": "cursor",
            "requires_ack": False,
            "payload": {"message": "artifact message"},
        },
    )
    broadcast_evidence = payload_codec.encode_event(
        kind="message",
        payload={
            "subject": "Broadcast",
            "from": "codex",
            "to": "all",
            "requires_ack": False,
            "payload": {"message": "room notice"},
        },
    )
    result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "readers": [{"tool": "cursor", "last_read_seq": 10}],
                "room": {
                    "open_handoffs": [
                        {
                            "seq": 11,
                            "event_id": "handoff-11",
                            "kind": "handoff",
                            "tool": "codex",
                            "target": "cursor",
                            "subject": "Please verify",
                        }
                    ],
                    "recent_artifacts": [
                        {
                            "seq": 12,
                            "event_id": "message-12",
                            "kind": "artifact",
                            "tool": "claude_code",
                            "target": "cursor",
                            "subject": "FYI",
                            "evidence": artifact_evidence,
                        },
                        {
                            "seq": 13,
                            "event_id": "message-13",
                            "kind": "artifact",
                            "tool": "codex",
                            "target": "all",
                            "subject": "Broadcast",
                            "evidence": broadcast_evidence,
                        },
                        {
                            "seq": 14,
                            "event_id": "unrelated",
                            "kind": "artifact",
                            "tool": "codex",
                            "subject": "not an inbox message",
                            "evidence": ["ordinary evidence"],
                        },
                    ],
                },
            }
        },
    )

    projected = adapter.native_inbox_snapshot(result, tool="cursor")

    assert projected["counts"] == {"direct": 2, "broadcast": 1, "total": 3}
    assert [row["event_id"] for row in projected["latest"]] == [
        "handoff-11",
        "message-12",
        "message-13",
    ]
    assert projected["latest"][1]["requires_ack"] is False
    assert projected["coverage_incomplete"] is False


def test_native_inbox_does_not_treat_unrouted_codec_events_as_broadcasts() -> None:
    evidence = payload_codec.encode_event(
        kind="message",
        payload={"reason": "ordinary channel event"},
    )
    result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "readers": [],
                "room": {
                    "open_handoffs": [],
                    "recent_artifacts": [
                        {
                            "seq": 1,
                            "event_id": "channel-1",
                            "kind": "artifact",
                            "tool": "codex",
                            "evidence": evidence,
                        }
                    ],
                },
            }
        },
    )

    projected = adapter.native_inbox_snapshot(result, tool="cursor")

    assert projected["counts"] == {"direct": 0, "broadcast": 0, "total": 0}
    assert projected["latest"] == []


def test_native_inbox_recovers_budget_omitted_messages_from_repo_recent() -> None:
    emitted_evidence = payload_codec.encode_event(
        kind="message",
        payload={
            "subject": "Visible",
            "from": "codex",
            "to": "cursor",
            "requires_ack": False,
            "payload": {"message": "visible in room"},
        },
    )
    omitted_evidence = payload_codec.encode_event(
        kind="message",
        payload={
            "subject": "Recovered",
            "from": "claude_code",
            "to": "cursor",
            "requires_ack": False,
            "payload": {"message": "recovered from recent"},
        },
    )
    room_result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "readers": [{"tool": "cursor", "last_read_seq": 20}],
                "room": {
                    "open_handoffs": [],
                    "recent_artifacts": [
                        {
                            "seq": 21,
                            "event_id": "message-21",
                            "kind": "artifact",
                            "tool": "codex",
                            "target": "cursor",
                            "evidence": emitted_evidence,
                        }
                    ],
                    "unconsumed_artifacts": [
                        {
                            "seq": 21,
                            "event_id": "message-21",
                            "kind": "artifact",
                            "tool": "codex",
                            "target": "cursor",
                            "evidence": emitted_evidence,
                        }
                    ],
                    "totals": {
                        "recent_artifacts": 2,
                        "unconsumed_artifacts": 2,
                        "stale_facts": 0,
                    },
                    "composition": {
                        "buckets": {
                            "recent_artifacts": {
                                "total": 2,
                                "emitted": 1,
                                "omitted": 1,
                                "omitted_ids": ["message-22"],
                                "omitted_ids_truncated": False,
                                "reason": "budget",
                            },
                            "unconsumed_artifacts": {
                                "total": 2,
                                "emitted": 1,
                                "omitted": 1,
                                "omitted_ids": ["message-22"],
                                "omitted_ids_truncated": False,
                                "reason": "budget",
                            },
                        }
                    },
                },
            }
        },
    )
    recent_result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "recent": {
                    "all": False,
                    "limit": 500,
                    "rows": [
                        {
                            "source": "room",
                            "seq": 22,
                            "fact": {
                                "seq": 22,
                                "event_id": "message-22",
                                "kind": "artifact",
                                "tool": "claude_code",
                                "target": "cursor",
                                "evidence": omitted_evidence,
                            },
                        }
                    ],
                    "warnings": [],
                }
            }
        },
    )

    projected = adapter.native_inbox_snapshot(
        room_result,
        tool="cursor",
        recent_result=recent_result,
    )

    assert projected["counts"] == {"direct": 2, "broadcast": 0, "total": 2}
    assert [row["event_id"] for row in projected["latest"]] == [
        "message-21",
        "message-22",
    ]
    assert projected["coverage_incomplete"] is False
    assert projected["coverage"]["omitted_artifact_ids_recovered"] == 1
    assert projected["coverage"]["omitted_unconsumed_artifact_ids_recovered"] == 1
    assert projected["coverage"]["room_unconsumed_artifacts_omitted"] == 1


def test_native_inbox_reports_incomplete_coverage_when_omitted_ids_are_truncated() -> None:
    room_result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "readers": [],
                "room": {
                    "open_handoffs": [],
                    "recent_artifacts": [],
                    "totals": {"recent_artifacts": 3},
                    "composition": {
                        "buckets": {
                            "recent_artifacts": {
                                "total": 3,
                                "emitted": 0,
                                "omitted": 3,
                                "omitted_ids": ["artifact-1"],
                                "omitted_ids_truncated": True,
                                "reason": "budget",
                            }
                        }
                    },
                },
            }
        },
    )
    recent_result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "recent": {
                    "all": False,
                    "limit": 500,
                    "rows": [{"fact": {"event_id": "artifact-1", "seq": 1}}],
                    "warnings": [],
                }
            }
        },
    )

    projected = adapter.native_inbox_snapshot(
        room_result,
        tool="cursor",
        recent_result=recent_result,
    )

    assert projected["coverage_incomplete"] is True
    assert "room_omitted_artifact_ids_incomplete" in projected["coverage"]["reasons"]


def test_native_inbox_requires_independent_unconsumed_omission_proof() -> None:
    room_result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "readers": [],
                "room": {
                    "open_handoffs": [],
                    "recent_artifacts": [],
                    "unconsumed_artifacts": [],
                    "totals": {
                        "recent_artifacts": 1,
                        "unconsumed_artifacts": 1,
                    },
                    "composition": {
                        "buckets": {
                            "recent_artifacts": {
                                "total": 1,
                                "emitted": 0,
                                "omitted": 1,
                                "omitted_ids": ["artifact-1"],
                                "omitted_ids_truncated": False,
                                "reason": "budget",
                            },
                            "unconsumed_artifacts": {
                                "total": 1,
                                "emitted": 0,
                                "omitted": 1,
                                "omitted_ids": [],
                                "omitted_ids_truncated": True,
                                "reason": "budget",
                            },
                        }
                    },
                },
            }
        },
    )
    recent_result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "recent": {
                    "all": False,
                    "limit": 500,
                    "rows": [{"fact": {"event_id": "artifact-1", "seq": 1}}],
                    "warnings": [],
                }
            }
        },
    )

    projected = adapter.native_inbox_snapshot(
        room_result,
        tool="cursor",
        recent_result=recent_result,
    )

    assert projected["coverage"]["omitted_artifact_ids_recovered"] == 1
    assert projected["coverage"]["omitted_unconsumed_artifact_ids_recovered"] == 0
    assert projected["coverage_incomplete"] is True
    assert "room_unconsumed_artifact_ids_incomplete" in projected["coverage"]["reasons"]


def test_native_inbox_reports_missing_unconsumed_ids_independently() -> None:
    room_result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "readers": [],
                "room": {
                    "open_handoffs": [],
                    "recent_artifacts": [],
                    "unconsumed_artifacts": [],
                    "totals": {
                        "recent_artifacts": 1,
                        "unconsumed_artifacts": 1,
                    },
                    "composition": {
                        "buckets": {
                            "recent_artifacts": {
                                "total": 1,
                                "emitted": 0,
                                "omitted": 1,
                                "omitted_ids": ["artifact-1"],
                                "omitted_ids_truncated": False,
                                "reason": "budget",
                            },
                            "unconsumed_artifacts": {
                                "total": 1,
                                "emitted": 0,
                                "omitted": 1,
                                "omitted_ids": ["unconsumed-2"],
                                "omitted_ids_truncated": False,
                                "reason": "budget",
                            },
                        }
                    },
                },
            }
        },
    )
    recent_result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "recent": {
                    "all": False,
                    "limit": 500,
                    "rows": [{"fact": {"event_id": "artifact-1", "seq": 1}}],
                    "warnings": [],
                }
            }
        },
    )

    projected = adapter.native_inbox_snapshot(
        room_result,
        tool="cursor",
        recent_result=recent_result,
    )

    assert projected["coverage_incomplete"] is True
    assert "room_unconsumed_artifacts_not_in_repo_recent" in projected["coverage"]["reasons"]


def test_native_inbox_reports_archived_unread_history_as_incomplete() -> None:
    room_result = adapter.NativeResult(
        "ok",
        payload={
            "data": {
                "readers": [{"tool": "cursor", "last_read_seq": 5}],
                "room": {
                    "content_max_seq": 40,
                    "open_handoffs": [],
                    "recent_artifacts": [],
                    "unconsumed_artifacts": [],
                    "totals": {
                        "recent_artifacts": 0,
                        "unconsumed_artifacts": 0,
                        "stale_facts": 8,
                    },
                    "composition": {
                        "buckets": {
                            "stale_facts": {
                                "total": 8,
                                "emitted": 0,
                                "omitted": 8,
                                "reason": "archived",
                            }
                        }
                    },
                },
            }
        },
    )

    projected = adapter.native_inbox_snapshot(room_result, tool="cursor")

    assert projected["counts"] == {"direct": 0, "broadcast": 0, "total": 0}
    assert projected["coverage_incomplete"] is True
    assert "archived_facts_may_contain_unread_messages" in projected["coverage"]["reasons"]


def test_acknowledge_accepts_current_noop_and_documents_checkpoint_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    calls: list[tuple[list[str], dict]] = []
    payload = {
        "ok": True,
        "product": "rally",
        "schema": "agent-rally.command.next.v1",
        "data": {"next": {"action": "none"}},
    }

    def no_op(_context, argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return adapter.NativeResult(
            "invalid",
            payload=payload,
            returncode=0,
            reason="mutation success omitted a fact sequence",
        )

    monkeypatch.setattr(adapter, "invoke_native", no_op)

    result = adapter.acknowledge(context, tool="cursor", session_id="session-1")

    assert result.ok
    assert "already current" in str(result.reason)
    assert "not checkpoint scope" in str(result.reason)
    assert calls == [
        (
            ["next", "--json", "--tool", "cursor", "--limit", "20"],
            {
                "expected_schema": "agent-rally.command.next.v1",
                "tool": "cursor",
                "session_id": "session-1",
                "mutating": True,
            },
        )
    ]


def test_acknowledge_reports_the_content_tip_from_committed_read_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    payload = {
        "ok": True,
        "product": "rally",
        "schema": "agent-rally.command.next.v1",
        "data": {
            "append_outcomes": [
                {
                    "fact": {
                        "kind": "read",
                        "tool": "cursor",
                        "summary": "read_seq:42",
                        "seq": 43,
                        "event_id": "read-43",
                    }
                }
            ]
        },
    }
    monkeypatch.setattr(
        adapter,
        "invoke_native",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "ok",
            payload=payload,
            returncode=0,
            revision=43,
            event_id="read-43",
        ),
    )

    result = adapter.acknowledge(context, tool="cursor")

    assert result.ok
    assert result.revision == 43
    assert result.event_id == "read-43"
    assert "content seq 42" in str(result.reason)


def test_acknowledge_rejects_success_envelope_with_failed_checkpoint_append(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    payload = {
        "ok": True,
        "product": "rally",
        "schema": "agent-rally.command.next.v1",
        "data": {
            "append_issues": [
                {"code": "io", "message": "read checkpoint append failed"}
            ]
        },
    }
    monkeypatch.setattr(
        adapter,
        "invoke_native",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "invalid",
            payload=payload,
            returncode=0,
        ),
    )

    result = adapter.acknowledge(context, tool="cursor")

    assert result.status == "failed"
    assert not result.ok
    assert "did not prove" in str(result.reason)


def test_acknowledge_does_not_trust_read_fact_in_wrong_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    payload = {
        "ok": True,
        "product": "rally",
        "schema": "agent-rally.command.room.v1",
        "data": {
            "append_outcomes": [
                {
                    "fact": {
                        "kind": "read",
                        "tool": "cursor",
                        "summary": "read_seq:42",
                        "seq": 43,
                        "event_id": "read-43",
                    }
                }
            ]
        },
    }
    original = adapter.NativeResult(
        "invalid",
        payload=payload,
        returncode=0,
        reason="invalid Rally success envelope",
    )
    monkeypatch.setattr(
        adapter,
        "invoke_native",
        lambda *_args, **_kwargs: original,
    )

    result = adapter.acknowledge(context, tool="cursor")

    assert result is original
    assert result.status == "invalid"


def test_native_heartbeat_uses_local_presence_only_on_precommit_unavailability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        adapter,
        "room_snapshot",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "ok", payload={"data": {"room": {"active_claims": []}}}
        ),
    )
    monkeypatch.setattr(
        adapter,
        "status_post",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "unavailable", reason="binary disappeared before spawn"
        ),
    )
    local_calls: list[dict] = []

    def fake_local(_context, **kwargs):
        local_calls.append(kwargs)
        return adapter.NativeResult(
            "ok", backend="build-loop-local", transport="presence-json"
        )

    monkeypatch.setattr(adapter, "write_local_presence", fake_local)

    result = adapter.write_heartbeat_presence(
        context,
        session_id="native-session-1",
        tool="cursor:native-session-1",
        local_session_id="session-1",
        local_tool="cursor",
        phase="execute",
        intent="chunk one",
        files_in_flight=["src/app.py"],
        run_id="run-1",
        app_slug="adapter-contract",
    )

    assert result.ok
    assert result.backend == "build-loop-local"
    assert result.transport == "presence-json"
    assert local_calls[0]["run_id"] == "run-1"
    assert local_calls[0]["tool"] == "cursor"
    assert local_calls[0]["session_id"] == "session-1"


def test_native_presence_spawn_error_falls_back_with_explicit_local_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    def spawn_error(*_args, **_kwargs):
        raise OSError("rally vanished before spawn")

    local_calls: list[dict] = []

    def fake_local(_context, **kwargs):
        local_calls.append(kwargs)
        return adapter.NativeResult(
            "ok", backend="build-loop-local", transport="presence-json"
        )

    monkeypatch.setattr(adapter.subprocess, "run", spawn_error)
    monkeypatch.setattr(adapter, "write_local_presence", fake_local)

    result = adapter.write_presence(
        context,
        session_id="native-session-a",
        tool="cursor:native-session-a",
        local_session_id="raw-session-a",
        local_tool="cursor",
        model="cursor-agent",
        run_id="run-a",
        app_slug="adapter-contract",
        phase="execute",
    )

    assert result.ok
    assert result.backend == "build-loop-local"
    assert len(local_calls) == 1
    assert local_calls[0]["tool"] == "cursor"
    assert local_calls[0]["session_id"] == "raw-session-a"


def test_native_presence_timeout_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    def time_out(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, timeout=5)

    monkeypatch.setattr(adapter.subprocess, "run", time_out)
    monkeypatch.setattr(
        adapter,
        "write_local_presence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ambiguous native outcome must not fall back")
        ),
    )

    result = adapter.write_presence(
        context,
        session_id="native-session-a",
        tool="cursor:native-session-a",
        local_session_id="raw-session-a",
        local_tool="cursor",
        model="cursor-agent",
        run_id="run-a",
        app_slug="adapter-contract",
        phase="execute",
    )

    assert not result.ok
    assert not result.precommit_unavailable


def test_native_success_requires_the_exact_expected_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(adapter, "repo_local_rally_binary", lambda _workdir: "/fake/rally")
    responses = iter(
        [
            _completed(
                {
                    "ok": True,
                    "product": "rally",
                    "schema": "agent-rally.command.room.v1",
                    "data": {"room": {}},
                }
            ),
            _completed(
                {
                    "ok": True,
                    "product": "rally",
                    "schema": "agent-rally.command.status_read.v1",
                    "data": {"room": {}},
                }
            ),
        ]
    )
    monkeypatch.setattr(adapter.subprocess, "run", lambda *_args, **_kwargs: next(responses))

    accepted = adapter.invoke_native(
        context,
        ["room", "--json"],
        expected_schema="agent-rally.command.room.v1",
        tool="codex",
    )
    wrong_schema = adapter.invoke_native(
        context,
        ["room", "--json"],
        expected_schema="agent-rally.command.room.v1",
        tool="codex",
    )

    assert accepted.status == "ok"
    assert wrong_schema.status == "invalid"
    assert not wrong_schema.precommit_unavailable


def test_rally_session_id_is_stable_across_native_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    monkeypatch.delenv("BUILD_LOOP_RALLY_SESSION_ID", raising=False)
    monkeypatch.delenv("RALLY_SESSION_ID", raising=False)
    monkeypatch.setattr(adapter, "repo_local_rally_binary", lambda _workdir: "/fake/rally")
    seen_session_ids: list[str] = []

    def fake_run(argv, **kwargs):
        seen_session_ids.append(kwargs["env"]["RALLY_SESSION_ID"])
        schema = (
            "agent-rally.command.room.v1"
            if argv[1] == "room"
            else "agent-rally.command.status_read.v1"
        )
        return _completed({"ok": True, "product": "rally", "schema": schema, "data": {}})

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    assert adapter.room_snapshot(context, tool="cursor").ok
    assert adapter.status_read(context, tool="cursor").ok
    assert seen_session_ids == [
        adapter.stable_session_id(tmp_path, "cursor"),
        adapter.stable_session_id(tmp_path, "cursor"),
    ]


def test_mutating_timeout_and_outcome_unknown_never_authorize_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(adapter, "repo_local_rally_binary", lambda _workdir: "/fake/rally")

    def time_out(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, timeout=5)

    monkeypatch.setattr(adapter.subprocess, "run", time_out)
    timed_out = adapter.invoke_native(
        context,
        ["say", "artifact", "--json"],
        expected_schema="agent-rally.command.say.v1",
        tool="codex",
        mutating=True,
    )
    assert timed_out.status == "outcome_unknown"
    assert not timed_out.precommit_unavailable

    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(
            {
                "ok": False,
                "product": "rally",
                "command": "mutation_outcome_unknown",
                "data": {
                    "outcome_unknown": {
                        "event_id": "fact_123",
                        "query_remedy": "rally locate fact_123 --json",
                    }
                },
            },
            returncode=2,
        ),
    )
    ambiguous = adapter.invoke_native(
        context,
        ["say", "artifact", "--json"],
        expected_schema="agent-rally.command.say.v1",
        tool="codex",
        mutating=True,
    )
    assert ambiguous.status == "outcome_unknown"
    assert ambiguous.event_id == "fact_123"
    assert ambiguous.remedy == "rally locate fact_123 --json"
    assert not ambiguous.precommit_unavailable


def test_partial_commit_is_typed_and_primary_presence_can_be_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(adapter, "repo_local_rally_binary", lambda _workdir: "/fake/rally")
    payload = {
        "ok": False,
        "product": "rally",
        "command": "partial_commit",
        "data": {
            "committed": True,
            "projection_complete": False,
            "message": "presence committed; later projection failed",
            "append_outcomes": [
                {
                    "fact": {
                        "kind": "presence",
                        "tool": "cursor",
                        "from_session_id": "sess:managed:session-1#live",
                        "subject": "presence: cursor",
                        "seq": 7,
                        "event_id": "fact_presence",
                    }
                }
            ],
        },
    }
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(payload, returncode=1),
    )

    typed = adapter.invoke_native(
        context,
        ["enter", "--tool", "cursor", "--session-id", "session-1", "--json"],
        expected_schema="agent-rally.command.enter.v1",
        tool="cursor",
        session_id="session-1",
        mutating=True,
    )
    assert typed.status == "partial_commit"
    assert typed.revision == 7
    assert not typed.precommit_unavailable

    entered = adapter.enter_session(
        context,
        tool="cursor",
        session_id="session-1",
    )
    assert entered.ok
    assert entered.revision == 7
    assert entered.event_id == "fact_presence"


def test_post_partial_commit_cannot_use_same_tool_sibling_fact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    payload = {"subject": "session-bound", "session_id": "session-a"}
    evidence = payload_codec.encode_event(
        kind="artifact",
        payload=payload,
        model="gpt",
        run_id="run-1",
        app_slug="adapter-contract",
    )
    partial = adapter.NativeResult(
        "partial_commit",
        payload={
            "data": {
                "append_outcomes": [
                    {
                        "fact": {
                            "kind": "artifact",
                            "tool": "cursor",
                            "from_session_id": "sess:managed:session-b#live",
                            "subject": "session-bound",
                            "evidence": evidence,
                            "seq": 12,
                            "event_id": "sibling-fact",
                        }
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(adapter, "invoke_native", lambda *_args, **_kwargs: partial)

    result = post_module._post_via_repo_local_rally(
        context=context,
        kind="artifact",
        tool="cursor",
        model="gpt",
        run_id="run-1",
        app_slug="adapter-contract",
        payload=payload,
    )

    assert result.status == "partial_commit"
    assert result.revision is None


def test_normal_enter_uses_exact_presence_not_last_append_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    payload = {
        "ok": True,
        "product": "rally",
        "schema": "agent-rally.command.enter.v1",
        "data": {
            "enter": {"tool": "cursor", "session_id": "session-1"},
            "append_outcomes": [
                {
                    "fact": {
                        "kind": "presence",
                        "tool": "cursor",
                        "from_session_id": "session-1",
                        "seq": 48,
                        "event_id": "presence-48",
                    }
                },
                {
                    "fact": {
                        "kind": "risk",
                        "tool": "rally",
                        "seq": 49,
                        "event_id": "risk-49",
                    }
                },
                {
                    "fact": {
                        "kind": "read",
                        "tool": "cursor",
                        "seq": 50,
                        "event_id": "read-50",
                    }
                },
            ],
        },
    }
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(payload),
    )

    entered = adapter.enter_session(
        context,
        tool="cursor",
        session_id="session-1",
    )

    assert entered.ok
    assert entered.revision == 48
    assert entered.event_id == "presence-48"


def test_canonical_session_generation_is_not_collapsed() -> None:
    assert adapter._session_id_matches(
        "sess:managed:session-1#live", "session-1"
    )
    assert adapter._session_id_matches(
        "sess:managed:session-1#live", "sess:managed:session-1#live"
    )
    assert not adapter._session_id_matches(
        "sess:managed:session-1#old", "sess:managed:session-1#live"
    )


def test_say_success_cannot_be_proved_by_auto_presence_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    payload = {
        "ok": True,
        "product": "rally",
        "schema": "agent-rally.command.say.v1",
        "data": {
            "say": {},
            "append_outcomes": [
                {
                    "fact": {
                        "kind": "presence",
                        "tool": "codex",
                        "seq": 4,
                        "event_id": "presence-only",
                    }
                }
            ],
        },
    }
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(payload),
    )

    result = adapter.invoke_native(
        context,
        ["say", "artifact", "--json", "--tool", "codex", "--subject", "x"],
        expected_schema="agent-rally.command.say.v1",
        tool="codex",
        mutating=True,
    )

    assert result.status == "invalid"
    assert result.revision is None


def test_retract_accepts_exact_artifact_and_idempotent_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    exact_fact = {
        "kind": "artifact",
        "tool": "cursor",
        "from_session_id": "sess:managed:session-1#live",
        "subject": "retract: fact_old",
        "summary": "superseded [retracts=fact_old superseded_by=fact_new]",
        "ref": "fact_old",
        "status": "retraction",
        "seq": 19,
        "event_id": "fact_retraction",
    }
    responses = iter(
        [
            _completed(
                {
                    "ok": True,
                    "product": "rally",
                    "schema": "agent-rally.command.retract.v1",
                    "data": {
                        "retract": {
                            "target": "fact_old",
                            "status": "retracted",
                            "reason": "superseded",
                            "superseded_by": "fact_new",
                            "fact": exact_fact,
                        }
                    },
                }
            ),
            _completed(
                {
                    "ok": True,
                    "product": "rally",
                    "schema": "agent-rally.command.retract.v1",
                    "data": {
                        "retract": {
                            "target": "fact_old",
                            "status": "noop_already_retracted",
                            "reason": "superseded",
                            "superseded_by": "fact_new",
                            "prior_retraction": "fact_retraction",
                            "prior_superseded_by": "fact_new",
                        }
                    },
                }
            ),
        ]
    )
    monkeypatch.setattr(
        adapter.subprocess, "run", lambda *_args, **_kwargs: next(responses)
    )
    argv = [
        "retract", "fact_old", "--tool", "cursor", "--reason", "superseded",
        "--json", "--superseded-by", "fact_new",
    ]

    posted = adapter.invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.retract.v1",
        tool="cursor",
        session_id="session-1",
        mutating=True,
    )
    repeated = adapter.invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.retract.v1",
        tool="cursor",
        session_id="session-1",
        mutating=True,
    )

    assert posted.ok and posted.revision == 19
    assert posted.event_id == "fact_retraction"
    assert repeated.ok and repeated.revision is None
    assert "reason equality is unprovable" in str(repeated.reason)


def test_retract_rejects_old_pseudo_kind_and_lead_requires_exact_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    responses = iter(
        [
            _completed(
                {
                    "ok": True,
                    "product": "rally",
                    "schema": "agent-rally.command.retract.v1",
                    "data": {
                        "retract": {
                            "target": "fact_old",
                            "status": "retracted",
                            "reason": "wrong",
                            "superseded_by": None,
                            "fact": {
                                "kind": "retract",
                                "tool": "cursor",
                                "seq": 3,
                                "event_id": "wrong-shape",
                            },
                        }
                    },
                }
            ),
            _completed(
                {
                    "ok": True,
                    "product": "rally",
                    "schema": "agent-rally.command.lead.v1",
                    "data": {
                        "lead": {
                            "action": "handoff",
                            "current_lead": "claude_code",
                            "assigned": "handoff",
                            "fact": {
                                "kind": "decision",
                                "tool": "cursor",
                                "subject": "role:lead",
                                "target": "claude_code",
                                "evidence": ["assigned:handoff"],
                                "seq": 4,
                                "event_id": "wrong-target",
                            },
                        }
                    },
                }
            ),
        ]
    )
    monkeypatch.setattr(
        adapter.subprocess, "run", lambda *_args, **_kwargs: next(responses)
    )

    retract = adapter.invoke_native(
        context,
        ["retract", "fact_old", "--tool", "cursor", "--reason", "wrong", "--json"],
        expected_schema="agent-rally.command.retract.v1",
        tool="cursor",
        mutating=True,
    )
    lead = adapter.invoke_native(
        context,
        ["lead", "--json", "handoff", "--tool", "cursor", "--to", "codex"],
        expected_schema="agent-rally.command.lead.v1",
        tool="cursor",
        mutating=True,
    )

    assert retract.status == "invalid"
    assert lead.status == "invalid"


def test_retract_noop_requires_exact_prior_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(
            {
                "ok": True,
                "product": "rally",
                "schema": "agent-rally.command.retract.v1",
                "data": {
                    "retract": {
                        "target": "fact_old",
                        "status": "noop_already_retracted",
                        "reason": "newly requested reason",
                        "superseded_by": "fact_new",
                        "prior_retraction": "fact_prior_retraction",
                        "prior_superseded_by": "fact_different_replacement",
                    }
                },
            }
        ),
    )

    result = adapter.invoke_native(
        context,
        [
            "retract",
            "fact_old",
            "--tool",
            "cursor",
            "--reason",
            "newly requested reason",
            "--superseded-by",
            "fact_new",
            "--json",
        ],
        expected_schema="agent-rally.command.retract.v1",
        tool="cursor",
        session_id="session-1",
        mutating=True,
    )

    assert result.status == "invalid"
    assert "different prior_superseded_by" in str(result.reason)


_STATUS_ARGV = [
    "status",
    "--json",
    "post",
    "--tool",
    "cursor",
    "--state",
    "blocked",
    "--file",
    "src/app.py",
    "--intent",
    "verify exact markers",
    "--blocked-ref",
    "fact_blocker",
    "--wake-after",
    "2026-08-14T01:00:00Z",
    "--committed-sha",
    "abc123",
    "--worktree-branch",
    "bl/exact",
]
_STATUS_SUBJECT = (
    "state=blocked | file=src/app.py | intent=verify exact markers | "
    "ref=fact_blocker | wake_after=2026-08-14T01:00:00Z | "
    "committed_sha=abc123 | worktree_branch=bl/exact"
)


def _status_payload(subject: str, *, partial: bool = False) -> dict:
    fact = {
        "kind": "presence",
        "tool": "cursor",
        "from_session_id": "sess:managed:session-1#live",
        "subject": subject,
        "seq": 31,
        "event_id": "status-31",
    }
    if partial:
        return {
            "ok": False,
            "product": "rally",
            "command": "partial_commit",
            "data": {
                "committed": True,
                "append_outcomes": [{"fact": fact}],
            },
        }
    return {
        "ok": True,
        "product": "rally",
        "schema": "agent-rally.command.status_post.v1",
        "data": {"status_post": {"fact": fact}},
    }


@pytest.mark.parametrize(
    "bad_subject",
    [
        _STATUS_SUBJECT.replace("state=blocked", "state=blocked-extra"),
        _STATUS_SUBJECT.replace("file=src/app.py", "file=src/other.py"),
        _STATUS_SUBJECT.replace("intent=verify exact markers", "intent=other"),
        _STATUS_SUBJECT.replace("ref=fact_blocker", "ref=fact_other"),
        _STATUS_SUBJECT.replace(
            "wake_after=2026-08-14T01:00:00Z",
            "wake_after=2026-08-14T02:00:00Z",
        ),
        _STATUS_SUBJECT.replace("committed_sha=abc123", "committed_sha=def456"),
        _STATUS_SUBJECT.replace("worktree_branch=bl/exact", "worktree_branch=bl/other"),
    ],
)
def test_status_receipt_rejects_each_mismatched_supplied_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bad_subject: str,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(_status_payload(bad_subject)),
    )

    result = adapter.invoke_native(
        context,
        list(_STATUS_ARGV),
        expected_schema="agent-rally.command.status_post.v1",
        tool="cursor",
        session_id="session-1",
        mutating=True,
    )

    assert result.status == "invalid"
    assert result.revision is None


def test_status_receipt_accepts_all_exact_supplied_markers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(_status_payload(_STATUS_SUBJECT)),
    )

    result = adapter.invoke_native(
        context,
        list(_STATUS_ARGV),
        expected_schema="agent-rally.command.status_post.v1",
        tool="cursor",
        session_id="session-1",
        mutating=True,
    )

    assert result.ok
    assert result.revision == 31
    assert result.event_id == "status-31"


def test_status_partial_commit_uses_the_same_exact_marker_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    bad = _STATUS_SUBJECT.replace("intent=verify exact markers", "intent=other")
    monkeypatch.setattr(
        adapter,
        "invoke_native",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "partial_commit",
            payload=_status_payload(bad, partial=True),
            returncode=1,
        ),
    )

    result = adapter.status_post(
        context,
        tool="cursor",
        state="blocked",
        session_id="session-1",
        file="src/app.py",
        intent="verify exact markers",
        blocked_ref="fact_blocker",
        wake_after="2026-08-14T01:00:00Z",
        committed_sha="abc123",
        worktree_branch="bl/exact",
    )

    assert result.status == "partial_commit"
    assert not result.ok


def test_claim_receipt_requires_the_exact_requested_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    exact = {
        "kind": "claim",
        "tool": "cursor",
        "from_session_id": "sess:managed:session-1#live",
        "subject": adapter._FILES_IN_FLIGHT_CLAIM_SUBJECT,
        "scope": ["file:src/app.py"],
        "evidence": [
            "lease_expires_at:2026-08-14T02:00:00Z",
            "claimhash:src/app.py=abc123",
        ],
        "seq": 41,
        "event_id": "claim-41",
    }
    responses = iter(
        [
            _completed(
                {
                    "ok": True,
                    "product": "rally",
                    "schema": "agent-rally.command.say.v1",
                    "data": {"say": {"fact": exact}},
                }
            ),
            _completed(
                {
                    "ok": True,
                    "product": "rally",
                    "schema": "agent-rally.command.say.v1",
                    "data": {"say": {"fact": {**exact, "scope": ["file:src/other.py"]}}},
                }
            ),
        ]
    )
    monkeypatch.setattr(
        adapter.subprocess, "run", lambda *_args, **_kwargs: next(responses)
    )
    argv = [
        "say",
        "claim",
        "--json",
        "--tool",
        "cursor",
        "--subject",
        adapter._FILES_IN_FLIGHT_CLAIM_SUBJECT,
        "--path",
        "src/app.py",
    ]

    accepted = adapter.invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.say.v1",
        tool="cursor",
        session_id="session-1",
        mutating=True,
    )
    rejected = adapter.invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.say.v1",
        tool="cursor",
        session_id="session-1",
        mutating=True,
    )

    assert accepted.ok and accepted.event_id == "claim-41"
    assert rejected.status == "invalid"


def test_native_files_in_flight_reconciles_without_growth_or_foreign_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    claims: list[dict] = []
    mutations: list[list[str]] = []
    sequence = 50

    def active_claim(
        *,
        event_id: str,
        path: str,
        subject: str,
        tool: str = "cursor",
        session: str = "sess:managed:session-1#live",
        seq: int = 1,
    ) -> dict:
        return {
            "kind": "claim",
            "tool": tool,
            "from_session_id": session,
            "subject": subject,
            "scope": [f"file:{path}"],
            "seq": seq,
            "event_id": event_id,
        }

    def snapshot(*_args, **_kwargs):
        return adapter.NativeResult(
            "ok",
            payload={"data": {"room": {"active_claims": list(claims)}}},
        )

    def mutate(_context, argv, **_kwargs):
        nonlocal sequence
        mutations.append(list(argv))
        sequence += 1
        kind = argv[1]
        subject = argv[argv.index("--subject") + 1]
        if kind == "claim":
            path = argv[argv.index("--path") + 1]
            event_id = f"claim-{sequence}"
            fact = active_claim(
                event_id=event_id,
                path=path,
                subject=subject,
                seq=sequence,
            )
            claims.append(fact)
        else:
            target = argv[argv.index("--ref") + 1]
            event_id = f"release-{sequence}"
            claims[:] = [fact for fact in claims if fact["event_id"] != target]
            fact = {
                "kind": "release",
                "tool": "cursor",
                "from_session_id": "sess:managed:session-1#live",
                "subject": subject,
                "ref": target,
                "evidence": [],
                "seq": sequence,
                "event_id": event_id,
            }
        return adapter.NativeResult(
            "ok", payload={"fact": fact}, revision=sequence, event_id=event_id
        )

    monkeypatch.setattr(adapter, "room_snapshot", snapshot)
    monkeypatch.setattr(
        adapter,
        "enter_session",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "ok", revision=10, event_id="presence-10"
        ),
    )
    monkeypatch.setattr(adapter, "invoke_native", mutate)

    first = adapter.write_presence(
        context,
        session_id="session-1",
        tool="cursor",
        model="cursor",
        run_id="run-1",
        app_slug="adapter-contract",
        phase="execute",
        files_in_flight=["src/a.py", "src/b.py"],
    )
    repeated = adapter.write_presence(
        context,
        session_id="session-1",
        tool="cursor",
        model="cursor",
        run_id="run-1",
        app_slug="adapter-contract",
        phase="execute",
        files_in_flight=["src/a.py", "src/b.py"],
    )

    assert first.ok and repeated.ok
    assert [call[1] for call in mutations] == ["claim", "claim"]
    managed_a = next(
        fact
        for fact in claims
        if fact["subject"] == adapter._FILES_IN_FLIGHT_CLAIM_SUBJECT
        and fact["scope"] == ["file:src/a.py"]
    )
    claims.extend(
        [
            active_claim(
                event_id="manual-a",
                path="src/a.py",
                subject="manual agent claim",
                seq=60,
            ),
            active_claim(
                event_id="sibling-a",
                path="src/a.py",
                subject=adapter._FILES_IN_FLIGHT_CLAIM_SUBJECT,
                session="sess:managed:session-2#live",
                seq=61,
            ),
            active_claim(
                event_id="other-tool-a",
                path="src/a.py",
                subject=adapter._FILES_IN_FLIGHT_CLAIM_SUBJECT,
                tool="claude_code",
                session="sess:managed:claude-session#live",
                seq=62,
            ),
        ]
    )

    shrunk = adapter.write_presence(
        context,
        session_id="session-1",
        tool="cursor",
        model="cursor",
        run_id="run-1",
        app_slug="adapter-contract",
        phase="execute",
        files_in_flight=["src/b.py"],
    )

    assert shrunk.ok
    releases = [call for call in mutations if call[1] == "release"]
    assert [call[call.index("--ref") + 1] for call in releases] == [
        managed_a["event_id"]
    ]
    assert {fact["event_id"] for fact in claims} >= {
        "manual-a",
        "sibling-a",
        "other-tool-a",
    }


def test_concurrent_identical_presence_converges_to_one_managed_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    writer_count = 8
    initial_reads = 0
    sequence = 80
    claims: list[dict] = []
    results: list[adapter.NativeResult] = []
    errors: list[BaseException] = []
    state_lock = threading.Lock()
    initial_barrier = threading.Barrier(writer_count)
    claim_barrier = threading.Barrier(writer_count)

    def snapshot(*_args, **_kwargs):
        nonlocal initial_reads
        with state_lock:
            is_initial = initial_reads < writer_count
            if is_initial:
                initial_reads += 1
                visible: list[dict] = []
            else:
                visible = [dict(fact) for fact in claims]
        if is_initial:
            initial_barrier.wait(timeout=5)
        return adapter.NativeResult(
            "ok", payload={"data": {"room": {"active_claims": visible}}}
        )

    def mutate(_context, argv, **_kwargs):
        nonlocal sequence
        kind = argv[1]
        if kind == "claim":
            with state_lock:
                sequence += 1
                seq = sequence
                event_id = f"claim-{seq}"
                claims.append(
                    {
                        "kind": "claim",
                        "tool": "cursor",
                        "from_session_id": "sess:managed:session-1#live",
                        "subject": adapter._FILES_IN_FLIGHT_CLAIM_SUBJECT,
                        "scope": ["file:src/racy.py"],
                        "seq": seq,
                        "event_id": event_id,
                    }
                )
            claim_barrier.wait(timeout=5)
            return adapter.NativeResult(
                "ok", revision=seq, event_id=event_id
            )

        target = argv[argv.index("--ref") + 1]
        with state_lock:
            existing = next(
                (fact for fact in claims if fact["event_id"] == target),
                None,
            )
            if existing is None:
                return adapter.NativeResult(
                    "rejected", reason="claim already released by concurrent writer"
                )
            claims.remove(existing)
            sequence += 1
            seq = sequence
        return adapter.NativeResult(
            "ok", revision=seq, event_id=f"release-{seq}"
        )

    monkeypatch.setattr(adapter, "room_snapshot", snapshot)
    monkeypatch.setattr(
        adapter,
        "enter_session",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "ok", revision=10, event_id="presence-10"
        ),
    )
    monkeypatch.setattr(adapter, "invoke_native", mutate)

    def writer() -> None:
        try:
            result = adapter.write_presence(
                context,
                session_id="session-1",
                tool="cursor",
                model="cursor",
                run_id="run-race",
                app_slug="adapter-contract",
                phase="execute",
                files_in_flight=["src/racy.py"],
            )
            with state_lock:
                results.append(result)
        except BaseException as exc:  # noqa: BLE001 - thread assertion capture
            with state_lock:
                errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(writer_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == writer_count
    assert all(result.ok for result in results)
    with state_lock:
        managed = [
            fact
            for fact in claims
            if fact["subject"] == adapter._FILES_IN_FLIGHT_CLAIM_SUBJECT
            and fact["tool"] == "cursor"
            and fact["from_session_id"] == "sess:managed:session-1#live"
        ]
    assert len(managed) == 1
    assert managed[0]["scope"] == ["file:src/racy.py"]


def test_claim_spawn_unavailable_after_presence_is_partial_not_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        adapter,
        "room_snapshot",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "ok", payload={"data": {"room": {"active_claims": []}}}
        ),
    )
    monkeypatch.setattr(
        adapter,
        "enter_session",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "ok", revision=10, event_id="presence-10"
        ),
    )
    monkeypatch.setattr(
        adapter,
        "invoke_native",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "unavailable", reason="claim process could not spawn"
        ),
    )
    monkeypatch.setattr(
        adapter,
        "write_local_presence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not fallback after native presence committed")
        ),
    )

    result = adapter.write_presence(
        context,
        session_id="session-1",
        tool="cursor",
        model="cursor",
        run_id="run-1",
        app_slug="adapter-contract",
        phase="execute",
        files_in_flight=["src/app.py"],
    )

    assert result.status == "partial_commit"
    assert not result.precommit_unavailable
    assert result.backend == "rally"


def test_native_heartbeat_claims_every_file_in_flight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    claim_paths: list[str] = []
    active_claims: list[dict] = []
    monkeypatch.setattr(
        adapter,
        "room_snapshot",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "ok",
            payload={"data": {"room": {"active_claims": list(active_claims)}}},
        ),
    )
    monkeypatch.setattr(
        adapter,
        "status_post",
        lambda *_args, **_kwargs: adapter.NativeResult(
            "ok", revision=70, event_id="status-70"
        ),
    )

    def claim(_context, argv, **_kwargs):
        path = argv[argv.index("--path") + 1]
        claim_paths.append(path)
        seq = 70 + len(claim_paths)
        active_claims.append(
            {
                "kind": "claim",
                "tool": "cursor",
                "from_session_id": "sess:managed:session-1#live",
                "subject": adapter._FILES_IN_FLIGHT_CLAIM_SUBJECT,
                "scope": [f"file:{path}"],
                "seq": seq,
                "event_id": f"claim-{seq}",
            }
        )
        return adapter.NativeResult(
            "ok", revision=seq, event_id=f"claim-{seq}"
        )

    monkeypatch.setattr(adapter, "invoke_native", claim)

    result = adapter.write_heartbeat_presence(
        context,
        session_id="session-1",
        tool="cursor",
        phase="execute",
        intent="two-file change",
        files_in_flight=["src/a.py", "src/b.py"],
        run_id="run-1",
        app_slug="adapter-contract",
    )

    assert result.ok
    assert claim_paths == ["src/a.py", "src/b.py"]
    assert "requested=2" in str(result.reason)


@pytest.mark.parametrize(
    ("argv", "fact"),
    [
        (
            ["say", "wake", "--tool", "cursor", "--ref-standby", "standby-1", "--json"],
            {"kind": "wake", "tool": "cursor", "ref": "other"},
        ),
        (
            [
                "say", "standby", "--tool", "cursor", "--reason", "waiting",
                "--wake-after", "+30m", "--json",
            ],
            {"kind": "standby", "tool": "cursor", "summary": "unrelated"},
        ),
    ],
)
def test_wake_and_standby_require_semantic_receipt_markers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    fact: dict,
) -> None:
    context = _context(tmp_path)
    fact.update({"seq": 8, "event_id": "fact_wrong_markers"})
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(
            {
                "ok": True,
                "product": "rally",
                "schema": "agent-rally.command.say.v1",
                "data": {"say": {"fact": fact}},
            }
        ),
    )

    result = adapter.invoke_native(
        context,
        argv,
        expected_schema="agent-rally.command.say.v1",
        tool="cursor",
        mutating=True,
    )

    assert result.status == "invalid"


def test_enter_room_status_and_retract_use_the_native_cli_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    calls: list[tuple[list[str], dict]] = []

    def capture(_context, argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return adapter.NativeResult("ok")

    monkeypatch.setattr(adapter, "invoke_native", capture)

    adapter.enter_session(
        context,
        tool="cursor",
        session_id="session-1",
        role="implementer",
        tier="code",
        paths=("scripts/a.py", "scripts/b.py"),
    )
    adapter.room_snapshot(context, tool="cursor", since=-4, readers=True)
    adapter.status_read(context, tool="cursor")
    adapter.status_post(
        context,
        tool="cursor",
        state="blocked",
        session_id="session-1",
        file="scripts/a.py",
        intent="adapter tests",
        blocked_ref="fact_blocker",
        wake_after="2026-08-14T01:00:00Z",
        committed_sha="abc123",
        worktree_branch="bl/adapter-tests",
    )
    adapter.retract_fact(
        context,
        fact_id="fact_old",
        tool="cursor",
        reason="superseded",
        superseded_by="fact_new",
        session_id="session-1",
    )

    assert calls == [
        (
            [
                "enter", "--tool", "cursor", "--session-id", "session-1", "--json",
                "--role", "implementer", "--tier", "code",
                "--path", "scripts/a.py", "--path", "scripts/b.py",
            ],
            {
                "expected_schema": "agent-rally.command.enter.v1",
                "tool": "cursor",
                "session_id": "session-1",
                "mutating": True,
            },
        ),
        (
            ["room", "--json", "--tool", "cursor", "--readers", "--since", "0"],
            {"expected_schema": "agent-rally.command.room.v1", "tool": "cursor"},
        ),
        (
            ["status", "--json", "read", "--tool", "cursor"],
            {"expected_schema": "agent-rally.command.status_read.v1", "tool": "cursor"},
        ),
        (
            [
                "status", "--json", "post", "--tool", "cursor", "--state", "blocked",
                "--file", "scripts/a.py", "--intent", "adapter tests",
                "--blocked-ref", "fact_blocker", "--wake-after", "2026-08-14T01:00:00Z",
                "--committed-sha", "abc123", "--worktree-branch", "bl/adapter-tests",
            ],
            {
                "expected_schema": "agent-rally.command.status_post.v1",
                "tool": "cursor",
                "session_id": "session-1",
                "mutating": True,
            },
        ),
        (
            [
                "retract", "fact_old", "--tool", "cursor", "--reason", "superseded",
                "--json", "--superseded-by", "fact_new",
            ],
            {
                "expected_schema": "agent-rally.command.retract.v1",
                "tool": "cursor",
                "session_id": "session-1",
                "mutating": True,
            },
        ),
    ]


def test_local_backend_never_shells_rally(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, native=False)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("local backend must not resolve or shell Rally")

    monkeypatch.setattr(adapter, "repo_local_rally_binary", unexpected)
    monkeypatch.setattr(adapter.subprocess, "run", unexpected)

    result = adapter.enter_session(
        context,
        tool="codex",
        session_id="local-session",
        paths=("scripts/rally_point/backend_adapter.py",),
    )

    assert result.status == "unavailable"
    assert result.precommit_unavailable
    assert result.reason == "native backend not selected"
