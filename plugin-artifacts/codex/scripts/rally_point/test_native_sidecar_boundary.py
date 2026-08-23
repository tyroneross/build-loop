# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Keep Build Loop sidecar readers and writers out of native ``.rally``."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import agent_rally
from rally_point import (
    agent_autoreg,
    backend_adapter as adapter,
    changes,
    discovery_bridge,
    hooks,
    inbox,
    post as post_module,
    presence,
    session_probe,
    task_heartbeat,
)
from rally_point.backend_adapter import BackendContext, NativeResult
from rally_point.discovery_bridge import DiscoveryEnvelope


FORBIDDEN_NATIVE_SIDECARS = (
    "sessions",
    "cursors",
    "inbox",
    "task-heartbeats",
    "watchers",
    "rally",
    "changes.jsonl",
    "revision",
    "rejections.jsonl",
    "liveness-sha-cache.json",
    "current.json",
)


def _native_context(workdir: Path) -> BackendContext:
    return BackendContext(
        workdir=workdir,
        envelope=DiscoveryEnvelope(
            channel_dir=str(workdir / ".rally"),
            app_slug="native-sidecar-boundary",
            repo_id="native-sidecar-boundary",
            channel_layout="repo-local-rally",
            policy="canonical",
            protocol_version="1.0",
            last_resolved_at="2026-08-14T00:00:00Z",
            resolved_via="repo-local-rally-cli",
            raw={"rally_binary": "/fake/rally"},
        ),
        local_channel_dir=workdir / ".build-loop" / "local-rally",
    )


def _assert_no_native_sidecars(workdir: Path) -> None:
    rally_dir = workdir / ".rally"
    for relative in FORBIDDEN_NATIVE_SIDECARS:
        assert not (rally_dir / relative).exists(), (
            f"healthy native Rally created embedded Build Loop sidecar {relative!r}"
        )


def _force_native_spawn_failure(
    monkeypatch: pytest.MonkeyPatch,
    context: BackendContext,
) -> None:
    monkeypatch.setattr(
        discovery_bridge,
        "resolve",
        lambda _workdir: context.envelope,
    )
    monkeypatch.setattr(discovery_bridge, "maybe_auto_migrate", lambda *_a: None)
    monkeypatch.setattr(
        post_module,
        "_build_loop_fallback_channel",
        lambda _workdir: context.local_channel_dir,
    )

    def spawn_error(*_args, **_kwargs):
        raise OSError("rally vanished before process spawn")

    monkeypatch.setattr(adapter.subprocess, "run", spawn_error)


def _forbidden_embedded_call(*_args, **_kwargs):
    raise AssertionError("healthy native Rally invoked an embedded sidecar helper")


def test_native_pre_edit_join_uses_repo_local_throttle_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    native_presence = Mock(return_value=NativeResult("ok", backend="rally"))
    monkeypatch.setattr(hooks, "resolve_operative_repo", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(hooks, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(hooks, "write_backend_presence", native_presence)
    monkeypatch.setattr(hooks.presence, "write_presence", _forbidden_embedded_call)
    monkeypatch.setattr(hooks.presence, "get_cursor", _forbidden_embedded_call)
    monkeypatch.setattr(hooks.checkpoint, "checkpoint_read", _forbidden_embedded_call)
    monkeypatch.setattr(hooks.revision, "read_revision", _forbidden_embedded_call)
    monkeypatch.setenv("BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS", "60")
    monkeypatch.setenv("BUILD_LOOP_RALLY_QUIET", "1")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "native-hook")

    assert hooks.pre_edit_join(tmp_path, file_path="src/app.py", now=1000.0) == 0
    assert hooks.pre_edit_join(tmp_path, file_path="src/app.py", now=1020.0) == 0

    native_presence.assert_called_once()
    assert native_presence.call_args.kwargs["session_id"] == "native-hook"
    assert native_presence.call_args.kwargs["tool"] == "claude_code:native-hook"
    assert (
        tmp_path / ".build-loop" / "rally-hook-throttle" / "native-hook.stamp"
    ).is_file()
    _assert_no_native_sidecars(tmp_path)


def test_native_pre_edit_keeps_two_same_host_sessions_distinct(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    native_presence = Mock(return_value=NativeResult("ok", backend="rally"))
    monkeypatch.setattr(hooks, "resolve_operative_repo", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(hooks, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(hooks, "write_backend_presence", native_presence)
    monkeypatch.setenv("BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS", "60")
    monkeypatch.setenv("BUILD_LOOP_RALLY_QUIET", "1")

    monkeypatch.setenv("CLAUDE_SESSION_ID", "session-a")
    assert hooks.pre_edit_join(tmp_path, file_path="src/app.py", now=1000.0) == 0
    monkeypatch.setenv("CLAUDE_SESSION_ID", "session-b")
    assert hooks.pre_edit_join(tmp_path, file_path="src/app.py", now=1001.0) == 0

    assert [call.kwargs["tool"] for call in native_presence.call_args_list] == [
        "claude_code:session-a",
        "claude_code:session-b",
    ]
    assert [call.kwargs["session_id"] for call in native_presence.call_args_list] == [
        "session-a",
        "session-b",
    ]


def test_native_session_restore_excludes_only_exact_actor_and_advance_acks_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    room = Mock(return_value=NativeResult("ok", backend="rally"))
    ack = Mock(return_value=NativeResult("ok", backend="rally"))
    monkeypatch.setattr(hooks, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(hooks, "room_snapshot", room)
    monkeypatch.setattr(hooks, "acknowledge", ack)
    monkeypatch.setattr(
        hooks,
        "native_room_summary",
        lambda _result: {
            "squads": [
                {"tool": "claude_code:session-a", "status": "active"},
                {"tool": "claude_code:session-b", "status": "active"},
            ]
        },
    )

    assert hooks.session_start_restore(
        tmp_path,
        tool="claude_code",
        session_id="session-a",
    ) == 0
    assert hooks.session_start_advance(
        tmp_path,
        tool="claude_code",
        session_id="session-a",
    ) == 0

    assert "1 live peer(s)" in capsys.readouterr().out
    room.assert_called_once_with(
        context,
        actor="claude_code:session-a",
        readers=True,
    )
    ack.assert_called_once_with(
        context,
        tool="claude_code:session-a",
        session_id="session-a",
    )


def test_native_hook_throttle_prunes_stale_overflow_but_keeps_live_stamps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 10_000.0
    monkeypatch.setenv("BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS", "60")
    directory = hooks._native_throttle_path(tmp_path, "current").parent
    directory.mkdir(parents=True)
    for index in range(hooks._NATIVE_THROTTLE_MAX_ENTRIES + 12):
        stamp = hooks._native_throttle_path(tmp_path, f"stale-{index:03d}")
        stamp.touch()
        os.utime(stamp, (now - 1000 - index, now - 1000 - index))
    fresh = hooks._native_throttle_path(tmp_path, "fresh-peer")
    fresh.touch()
    os.utime(fresh, (now - 10, now - 10))

    hooks._touch_native_throttle(tmp_path, "current", now=now)

    remaining = list(directory.glob("*.stamp"))
    assert len(remaining) <= hooks._NATIVE_THROTTLE_MAX_ENTRIES
    assert hooks._native_throttle_path(tmp_path, "current") in remaining
    assert fresh in remaining


def test_native_agent_autoreg_handles_explicit_session_and_normalized_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    session_id = "explicit-child-session"
    native_tool = agent_autoreg.native_agent_tool_id("Code_Review", session_id)
    protocol_session_id = "sess:managed:explicit-child-session#live"
    native_presence = Mock(return_value=NativeResult("ok", backend="rally"))
    native_stop = Mock(
        return_value=NativeResult(
            "invalid",
            payload={
                "ok": True,
                "product": "rally",
                "schema": "agent-rally.command.status_post.v1",
                "data": {
                    "status_post": {
                        "fact": {
                            "event_id": "done-explicit-child-session",
                            "seq": 2,
                            "kind": "presence",
                            "tool": native_tool,
                            "from_session_id": protocol_session_id,
                            "subject": "state=done",
                        }
                    }
                },
            },
            returncode=0,
            reason="Rally mutation success omitted a positive fact sequence",
        )
    )
    monkeypatch.setattr(agent_autoreg, "_resolve_channel", lambda _workdir: context)
    monkeypatch.setattr(agent_autoreg, "write_backend_presence", native_presence)
    monkeypatch.setattr(
        agent_autoreg,
        "invoke_native",
        lambda *_a, **_kw: NativeResult(
            "ok",
            payload={
                "data": {
                    "whoami": {
                        "whoami": {
                            "session_identity": {"session_id": protocol_session_id},
                        }
                    }
                }
            },
        ),
    )
    monkeypatch.setattr(
        agent_autoreg,
        "recent",
        lambda *_a, **_kw: NativeResult(
            "ok",
            payload={
                "data": {
                    "recent": {
                        "rows": [
                            {
                                "fact": {
                                    "kind": "presence",
                                    "tool": native_tool,
                                    "from_session_id": protocol_session_id,
                                    "seq": 1,
                                    "subject": f"agent presence: {native_tool}",
                                }
                            }
                        ]
                    }
                }
            },
        ),
    )
    monkeypatch.setattr(agent_autoreg, "status_post", native_stop)
    monkeypatch.setattr(
        agent_autoreg.lifecycle, "reap_my_sessions", _forbidden_embedded_call
    )

    assert agent_autoreg.register(
        agent_type="Code_Review",
        task="verify native boundary",
        workdir=tmp_path,
        session_id=session_id,
    ) == session_id
    assert agent_autoreg.deregister(session_id, workdir=tmp_path) is True

    assert native_presence.call_args.kwargs["session_id"] == session_id
    assert native_presence.call_args.kwargs["tool"] == native_tool
    native_stop.assert_called_once_with(
        context,
        tool=native_tool,
        session_id=session_id,
        state="done",
    )
    _assert_no_native_sidecars(tmp_path)


def test_two_native_cursor_children_register_as_distinct_actors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    native_presence = Mock(return_value=NativeResult("ok", backend="rally"))
    monkeypatch.setattr(agent_autoreg, "_resolve_channel", lambda _workdir: context)
    monkeypatch.setattr(agent_autoreg, "write_backend_presence", native_presence)

    first = agent_autoreg.register(
        agent_type="cursor",
        workdir=tmp_path,
        session_id="cursor-child-a",
    )
    second = agent_autoreg.register(
        agent_type="cursor",
        workdir=tmp_path,
        session_id="cursor-child-b",
    )

    assert (first, second) == ("cursor-child-a", "cursor-child-b")
    tools = [call.kwargs["tool"] for call in native_presence.call_args_list]
    assert tools == [
        agent_autoreg.native_agent_tool_id("cursor", first),
        agent_autoreg.native_agent_tool_id("cursor", second),
    ]
    assert tools[0] != tools[1]
    assert all(tool.startswith("agent:cursor-") for tool in tools)
    assert all(len(tool.encode("ascii")) <= 64 for tool in tools)


def test_local_cursor_children_keep_type_label_and_session_scoping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = BackendContext(
        workdir=tmp_path,
        envelope=DiscoveryEnvelope(
            channel_dir=str(tmp_path / ".build-loop" / "local-rally"),
            app_slug="local-agent-autoreg",
            repo_id="local-agent-autoreg",
            channel_layout="build-loop-local",
            policy="private-fallback",
            protocol_version="1.0",
            last_resolved_at="2026-08-14T00:00:00Z",
            resolved_via="build-loop-internal",
            raw={"backend": "build-loop-local"},
        ),
        local_channel_dir=tmp_path / ".build-loop" / "local-rally",
    )
    local_presence = Mock(return_value=NativeResult("ok", backend="build-loop-local"))
    monkeypatch.setattr(agent_autoreg, "_resolve_channel", lambda _workdir: context)
    monkeypatch.setattr(agent_autoreg, "write_backend_presence", local_presence)

    assert agent_autoreg.register(
        agent_type="cursor",
        workdir=tmp_path,
        session_id="cursor-child-a",
    ) == "cursor-child-a"
    assert local_presence.call_args.kwargs["tool"] == "agent:cursor"


def test_native_inbox_refuses_no_broadcast_ack_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    native_ack = Mock(side_effect=_forbidden_embedded_call)
    monkeypatch.setattr(inbox, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(inbox, "native_acknowledge", native_ack)
    monkeypatch.setattr(inbox, "mark_read", _forbidden_embedded_call)

    rc = inbox.main(
        [
            "ack",
            "--workdir",
            str(tmp_path),
            "--tool",
            "cursor:01",
            "--session-id",
            "cursor-session",
            "--no-broadcast",
            "--json",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert output["accepted"] is False
    assert output["backend"] == "rally"
    assert output["action"] == "inbox-ack-refused"
    native_ack.assert_not_called()
    _assert_no_native_sidecars(tmp_path)


def test_agent_rally_native_presence_keeps_cursor_sessions_distinct(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    native_presence = Mock(return_value=NativeResult("ok", backend="rally"))
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(agent_rally, "write_backend_presence", native_presence)

    for session_id in ("cursor-a", "cursor-b"):
        assert agent_rally.main([
            "presence",
            "--workdir", str(tmp_path),
            "--tool", "cursor",
            "--session-id", session_id,
            "--run-id", f"run-{session_id}",
        ]) == 0
    capsys.readouterr()

    assert [call.kwargs["tool"] for call in native_presence.call_args_list] == [
        "cursor:cursor-a",
        "cursor:cursor-b",
    ]
    assert [
        call.kwargs["session_id"] for call in native_presence.call_args_list
    ] == ["cursor-a", "cursor-b"]
    _assert_no_native_sidecars(tmp_path)


def test_agent_rally_native_inbox_ack_uses_exact_cursor_actor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    native_ack = Mock(
        return_value=NativeResult("ok", backend="rally", revision=7)
    )
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(agent_rally, "native_acknowledge", native_ack)

    assert agent_rally.main([
        "ack-inbox",
        "--workdir", str(tmp_path),
        "--tool", "cursor",
        "--session-id", "cursor-a",
    ]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["accepted"] is True
    native_ack.assert_called_once_with(
        context,
        tool="cursor:cursor-a",
        session_id="cursor-a",
    )


def test_native_inbox_read_and_ack_use_exact_cursor_actor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    room = Mock(return_value=NativeResult("ok", backend="rally"))
    recent = Mock(return_value=NativeResult("ok", backend="rally"))
    native_ack = Mock(
        return_value=NativeResult("ok", backend="rally", revision=9)
    )
    monkeypatch.setattr(inbox, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(inbox, "room_snapshot", room)
    monkeypatch.setattr(inbox, "native_recent", recent)
    monkeypatch.setattr(
        inbox,
        "native_inbox_snapshot",
        lambda *_a, **_kw: {
            "counts": {"direct": 1, "broadcast": 0, "total": 1},
            "latest": [{"subject": "exact actor"}],
            "coverage_incomplete": False,
            "coverage": {"repo_recent_available": True, "reasons": []},
        },
    )
    monkeypatch.setattr(inbox, "native_acknowledge", native_ack)

    assert inbox.main([
        "read",
        "--workdir", str(tmp_path),
        "--tool", "cursor",
        "--session-id", "cursor-a",
        "--json",
    ]) == 0
    read_output = json.loads(capsys.readouterr().out)
    assert read_output["rally_tool"] == "cursor:cursor-a"
    room.assert_called_once_with(
        context,
        tool="cursor:cursor-a",
        actor="cursor:cursor-a",
        readers=True,
    )

    assert inbox.main([
        "ack",
        "--workdir", str(tmp_path),
        "--tool", "cursor",
        "--session-id", "cursor-a",
        "--json",
    ]) == 0
    ack_output = json.loads(capsys.readouterr().out)
    assert ack_output["rally_tool"] == "cursor:cursor-a"
    native_ack.assert_called_once_with(
        context,
        tool="cursor:cursor-a",
        session_id="cursor-a",
    )


def test_native_inbox_send_uses_session_actor_and_keeps_host_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    posted: list[dict] = []

    def native_post(**kwargs):
        posted.append(dict(kwargs))
        kwargs["outcome"].update(
            status="posted", backend="rally", transport="rally-cli"
        )
        return 12

    monkeypatch.setattr(inbox, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(post_module, "post", native_post)

    result = inbox.send_to_tool(
        tmp_path / ".rally",
        sender="cursor",
        recipient="codex:reviewer",
        payload={"session_id": "cursor-a", "subject": "review"},
        model="cursor-agent",
        run_id="run-cursor-a",
        app_slug="native-sidecar-boundary",
        workdir=tmp_path,
    )

    assert result["written"] is True
    assert posted[0]["tool"] == "cursor:cursor-a"
    assert posted[0]["payload"]["from"] == "cursor"
    assert posted[0]["payload"]["host_tool"] == "cursor"
    assert posted[0]["payload"]["session_id"] == "cursor-a"
    _assert_no_native_sidecars(tmp_path)


def test_native_heartbeat_spawn_failure_writes_only_base_local_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    _force_native_spawn_failure(monkeypatch, context)
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)

    assert agent_rally.main([
        "heartbeat",
        "--workdir", str(tmp_path),
        "--tool", "cursor",
        "--session-id", "cursor-a",
        "--run-id", "run-cursor-a",
        "--task-ref", "task-a",
        "--progress", "local fallback progress",
    ]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["backend"] == "build-loop-local"
    heartbeat_records = task_heartbeat.read_heartbeats(
        context.local_channel_dir,
        tool="cursor",
        session_id="cursor-a",
    )
    assert len(heartbeat_records) == 1
    assert heartbeat_records[0]["tool"] == "cursor"
    health = task_heartbeat.summarize_task_health_records(
        heartbeat_records,
        tool="cursor",
        session_id="cursor-a",
        expected_ref="task-a",
        now=float(heartbeat_records[0]["next_check_in_at"]) - 1,
    )
    assert health["health"] == "current"

    facts, _offset = changes.read_changes_since(context.local_channel_dir, 0)
    assert len(facts) == 1
    assert facts[0]["tool"] == "cursor"
    assert facts[0]["payload"]["session_id"] == "cursor-a"
    assert facts[0]["payload"]["task_heartbeat"]["tool"] == "cursor"
    assert (
        facts[0]["payload"]["task_heartbeat"]["session_id"]
        == "cursor-a"
    )
    _assert_no_native_sidecars(tmp_path)


def test_native_post_timeout_and_outcome_unknown_never_write_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    monkeypatch.setattr(
        discovery_bridge,
        "resolve",
        lambda _workdir: context.envelope,
    )
    monkeypatch.setattr(discovery_bridge, "maybe_auto_migrate", lambda *_a: None)
    monkeypatch.setattr(
        post_module,
        "_build_loop_fallback_channel",
        lambda _workdir: context.local_channel_dir,
    )

    def time_out(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, timeout=5)

    monkeypatch.setattr(adapter.subprocess, "run", time_out)
    timeout_outcome: dict = {}
    assert post_module.post(
        channel_dir=Path(context.envelope.channel_dir),
        kind="artifact",
        tool="cursor:cursor-a",
        model="cursor-agent",
        run_id="timeout-run",
        app_slug=context.envelope.app_slug,
        payload={"session_id": "cursor-a", "subject": "ambiguous timeout"},
        workdir=tmp_path,
        outcome=timeout_outcome,
        local_tool="cursor",
        local_session_id="cursor-a",
    ) is None
    assert timeout_outcome["status"] == "outcome_unknown"
    assert not (context.local_channel_dir / "changes.jsonl").exists()

    unknown_payload = {
        "ok": False,
        "product": "rally",
        "command": "mutation_outcome_unknown",
        "data": {
            "outcome_unknown": {
                "event_id": "fact-ambiguous",
                "query_remedy": "rally locate fact-ambiguous --json",
            }
        },
    }
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(
            returncode=2,
            stdout=json.dumps(unknown_payload),
            stderr="",
        ),
    )
    unknown_outcome: dict = {}
    assert post_module.post(
        channel_dir=Path(context.envelope.channel_dir),
        kind="artifact",
        tool="cursor:cursor-a",
        model="cursor-agent",
        run_id="unknown-run",
        app_slug=context.envelope.app_slug,
        payload={"session_id": "cursor-a", "subject": "ambiguous result"},
        workdir=tmp_path,
        outcome=unknown_outcome,
        local_tool="cursor",
        local_session_id="cursor-a",
    ) is None
    assert unknown_outcome["status"] == "outcome_unknown"
    assert unknown_outcome["event_id"] == "fact-ambiguous"
    assert not (context.local_channel_dir / "changes.jsonl").exists()


def test_native_status_read_never_falls_through_to_sibling_actor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    facts = [
        {
            "seq": 8,
            "event_id": "status-a",
            "kind": "artifact",
            "tool": "cursor:cursor-a",
            "subject": "status",
            "summary": "actor a [file=/tmp/A.md sha=aaa]",
            "created_at": "2026-08-14T00:00:08Z",
        },
        {
            "seq": 9,
            "event_id": "status-b",
            "kind": "artifact",
            "tool": "cursor:cursor-b",
            "subject": "status",
            "summary": "actor b [file=/tmp/B.md sha=bbb]",
            "created_at": "2026-08-14T00:00:09Z",
        },
    ]
    recent_result = NativeResult(
        "ok",
        payload={"data": {"recent": {"limit": 200, "rows": [{}, {}]}}},
    )
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "_native_recent_facts",
        lambda *_a, **_kw: (facts, recent_result),
    )

    assert agent_rally.main([
        "status-read",
        "--workdir", str(tmp_path),
        "--tool", "cursor",
        "--session-id", "cursor-a",
        "--json",
    ]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["found"] is True
    assert output["coverage_incomplete"] is False
    assert output["pointer"]["tool"] == "cursor:cursor-a"
    assert output["pointer"]["file"] == "/tmp/A.md"


def test_native_status_read_saturated_absence_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    sibling_status = {
        "seq": 200,
        "event_id": "status-b",
        "kind": "artifact",
        "tool": "cursor:cursor-b",
        "subject": "status",
        "summary": "actor b [file=/tmp/B.md sha=bbb]",
        "created_at": "2026-08-14T00:00:09Z",
    }
    recent_result = NativeResult(
        "ok",
        payload={
            "data": {
                "recent": {
                    "limit": 200,
                    "rows": [{"seq": seq} for seq in range(1, 201)],
                }
            }
        },
    )
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "_native_recent_facts",
        lambda *_a, **_kw: ([sibling_status], recent_result),
    )

    assert agent_rally.main([
        "status-read",
        "--workdir", str(tmp_path),
        "--tool", "cursor",
        "--session-id", "cursor-a",
        "--json",
    ]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["found"] is False
    assert output["status"] == "unknown"
    assert output["coverage_incomplete"] is True
    assert output["reason"] == "native_recent_limit_saturated"
    assert output["pointer"] is None


def test_agent_autoreg_spawn_failure_falls_back_to_local_agent_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    _force_native_spawn_failure(monkeypatch, context)
    monkeypatch.setattr(agent_autoreg, "_resolve_channel", lambda _workdir: context)

    session_id = agent_autoreg.register(
        agent_type="cursor",
        workdir=tmp_path,
        session_id="cursor-child-a",
    )

    assert session_id == "cursor-child-a"
    active = presence.read_active_presence(
        context.local_channel_dir, exclude_session=None
    )
    assert len(active) == 1
    assert active[0]["session_id"] == "cursor-child-a"
    assert active[0]["tool"] == "agent:cursor"
    assert active[0]["tool"] != agent_autoreg.native_agent_tool_id(
        "cursor", "cursor-child-a"
    )
    _assert_no_native_sidecars(tmp_path)


def test_native_inbox_spawn_failure_routes_to_local_base_recipient(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    _force_native_spawn_failure(monkeypatch, context)
    monkeypatch.setattr(inbox, "resolve_context", lambda _workdir: context)

    result = inbox.send_to_tool(
        Path(context.envelope.channel_dir),
        sender="cursor",
        recipient="cursor:cursor-b",
        payload={"session_id": "cursor-a", "subject": "base fallback route"},
        model="cursor-agent",
        run_id="run-cursor-a",
        app_slug=context.envelope.app_slug,
        workdir=tmp_path,
    )

    assert result["written"] is True
    assert result["backend"] == "build-loop-local"
    messages = inbox.read_messages(
        context.local_channel_dir,
        tool="cursor",
        include_broadcast=False,
    )
    assert len(messages) == 1
    assert messages[0]["from"] == "cursor"
    assert messages[0]["to"] == "cursor"
    assert messages[0]["payload"]["rally_from_tool"] == "cursor:cursor-a"
    assert messages[0]["payload"]["rally_to_tool"] == "cursor:cursor-b"


def test_native_inbox_spawned_target_uses_explicit_local_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    _force_native_spawn_failure(monkeypatch, context)
    monkeypatch.setattr(inbox, "resolve_context", lambda _workdir: context)

    result = inbox.send_to_tool(
        Path(context.envelope.channel_dir),
        sender="agent:reviewer-native",
        recipient="agent:cursor-child-native",
        local_sender="agent:reviewer",
        local_recipient="agent:cursor",
        payload={"session_id": "reviewer-session", "subject": "child route"},
        workdir=tmp_path,
    )

    assert result["written"] is True
    messages = inbox.read_messages(
        context.local_channel_dir,
        tool="agent:cursor",
        include_broadcast=False,
    )
    assert len(messages) == 1
    assert messages[0]["from"] == "agent:reviewer"
    assert messages[0]["to"] == "agent:cursor"
    assert messages[0]["payload"]["rally_to_tool"] == "agent:cursor-child-native"


def test_healthy_native_session_probe_never_uses_embedded_sidecars(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _native_context(tmp_path)
    native_presence = Mock(
        return_value=NativeResult("ok", backend="rally", transport="rally-cli")
    )
    status_calls: list[dict] = []
    monkeypatch.setattr(session_probe, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        session_probe,
        "_probe_inject_readiness",
        lambda _errors: {
            "tmux": False,
            "ptyd_socket_live": False,
            "ptyd_bin": False,
            "inject_available": False,
            "recommended_backend": "handoff",
        },
    )
    monkeypatch.setattr(
        session_probe,
        "room_snapshot",
        lambda *_a, **_kw: NativeResult(
            "ok", payload={"data": {"room": {}, "readers": []}}
        ),
    )
    monkeypatch.setattr(session_probe, "write_backend_presence", native_presence)

    def native_post(*_args, **kwargs):
        kwargs["outcome"].update(
            backend="rally",
            transport="rally-cli",
            status="posted",
        )
        return 1

    monkeypatch.setattr(session_probe._post_mod, "post", native_post)
    def status_read(**kwargs):
        status_calls.append(kwargs)
        return {"status": "clear", "active_peers": []}

    monkeypatch.setattr(session_probe, "_run_status_subprocess", status_read)
    monkeypatch.setattr(
        session_probe, "_read_coordination_file", _forbidden_embedded_call
    )
    monkeypatch.setattr(session_probe.rally, "read_current", _forbidden_embedded_call)
    monkeypatch.setattr(session_probe.inbox, "unread_counts", _forbidden_embedded_call)
    monkeypatch.setattr(
        session_probe.inbox, "latest_message_summaries", _forbidden_embedded_call
    )
    monkeypatch.setattr(
        session_probe.presence, "write_presence", _forbidden_embedded_call
    )
    monkeypatch.setattr(session_probe, "_reap_stale_watchers", _forbidden_embedded_call)

    result = session_probe.probe(
        workdir=tmp_path,
        tool="cursor",
        session_id="cursor-session-01",
        start_watch=False,
        model="cursor-agent",
        run_id="native-sidecar-test",
        clock=lambda: 1000.0,
    )

    assert result["backend"] == "rally"
    assert result["transport"] == "rally-cli"
    assert result["tool"] == "cursor"
    assert result["rally_tool"] == "cursor:cursor-session-01"
    assert native_presence.call_args.kwargs["tool"] == "cursor:cursor-session-01"
    assert status_calls[0]["tool"] == "cursor:cursor-session-01"
    assert result["errors"] == []
    _assert_no_native_sidecars(tmp_path)
