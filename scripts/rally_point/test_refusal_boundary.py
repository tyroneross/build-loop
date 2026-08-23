#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Refused discovery must never authorize Build Loop private fallback state."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import agent_rally  # noqa: E402
import coordination_status  # noqa: E402
from rally_point import agent_autoreg, hooks, session_probe  # noqa: E402
from rally_point.backend_adapter import BackendContext  # noqa: E402
from rally_point.discovery_bridge import DiscoveryEnvelope  # noqa: E402


def _refusal_context(workdir: Path, reason: str = "ambiguous_host") -> BackendContext:
    envelope = DiscoveryEnvelope(
        channel_dir=str(workdir / ".rally"),
        app_slug="refusal-boundary",
        repo_id="refusal-boundary",
        channel_layout="repo-local-rally",
        policy="canonical",
        protocol_version="1.0",
        last_resolved_at="2026-08-14T00:00:00Z",
        resolved_via="repo-local-rally-cli",
        coordination_unavailable=reason,
        raw={"detail": "Rally could not identify one host runtime"},
    )
    return BackendContext(
        workdir=workdir,
        envelope=envelope,
        local_channel_dir=workdir / ".build-loop" / "private-rally",
    )


def _forbidden_private_call(*_args, **_kwargs):
    raise AssertionError("refused discovery accessed Build Loop private state")


def test_refusal_backend_overrides_native_source_label(tmp_path: Path) -> None:
    envelope = _refusal_context(tmp_path).envelope

    assert envelope.backend == "unavailable"
    assert envelope.transport == "none"
    assert envelope.refusal_reason == "Rally could not identify one host runtime"
    assert "rally whoami" in str(envelope.refusal_remedy)
    assert envelope.to_dict()["backend"] == "unavailable"


def test_hooks_surface_refusal_without_private_checkpoint_or_presence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _refusal_context(tmp_path)
    monkeypatch.setattr(hooks, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(hooks, "resolve_operative_repo", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(hooks.checkpoint, "checkpoint_read", _forbidden_private_call)
    monkeypatch.setattr(hooks.presence, "get_cursor", _forbidden_private_call)
    monkeypatch.setattr(hooks.presence, "write_presence", _forbidden_private_call)
    monkeypatch.setattr(hooks.revision, "read_revision", _forbidden_private_call)

    assert hooks.session_start_restore(tmp_path) == 0
    assert hooks.session_start_advance(tmp_path) == 0
    assert hooks.pre_edit_hint(tmp_path) == 0
    assert hooks.pre_edit_join(tmp_path, file_path="src/app.py") == 0

    stderr = capsys.readouterr().err
    assert stderr.count("refused") == 4
    assert "Rally could not identify one host runtime" in stderr
    assert "remedy:" in stderr
    assert not context.local_channel_dir.exists()


def test_agent_rally_commands_refuse_before_private_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _refusal_context(tmp_path)
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(agent_rally.presence, "presence_path", _forbidden_private_call)
    monkeypatch.setattr(agent_rally.inbox, "mark_read", _forbidden_private_call)
    monkeypatch.setattr(
        agent_rally.task_heartbeat,
        "_line_append",
        _forbidden_private_call,
    )
    monkeypatch.setattr(agent_rally.leadership, "read_lead", _forbidden_private_call)
    monkeypatch.setattr(agent_rally.leadership, "is_lease_valid", _forbidden_private_call)

    commands = (
        ["stop", "--workdir", str(tmp_path), "--session-id", "safe", "--tool", "cursor"],
        ["ack-inbox", "--workdir", str(tmp_path), "--session-id", "safe", "--tool", "cursor"],
        [
            "heartbeat", "--workdir", str(tmp_path), "--session-id", "safe",
            "--tool", "cursor", "--run-id", "run-1", "--task-ref", "task-1",
        ],
        [
            "lead", "status", "--workdir", str(tmp_path),
            "--session-id", "safe", "--tool", "cursor",
        ],
    )
    for argv in commands:
        assert agent_rally.main(argv) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["accepted"] is False
        assert payload["status"] == "refused"
        assert payload["backend"] == "unavailable"
        assert payload["reason"]
        assert payload["remedy"]
    assert not context.local_channel_dir.exists()


def test_autoreg_deregister_refuses_before_private_reap(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _refusal_context(tmp_path)
    monkeypatch.setattr(agent_autoreg, "_resolve_channel", lambda _workdir: context)
    monkeypatch.setattr(
        agent_autoreg.lifecycle,
        "reap_my_sessions",
        _forbidden_private_call,
    )

    assert agent_autoreg.deregister("safe-session", workdir=tmp_path) is False
    stderr = capsys.readouterr().err
    assert "subagent deregistration refused" in stderr
    assert "remedy:" in stderr
    assert not context.local_channel_dir.exists()


def test_coordination_status_returns_warning_without_private_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _refusal_context(tmp_path)
    monkeypatch.setattr(
        coordination_status,
        "resolve_context",
        lambda _workdir: context,
    )
    monkeypatch.setattr(
        coordination_status,
        "_default_coordination_file",
        _forbidden_private_call,
    )
    monkeypatch.setattr(
        coordination_status.presence,
        "read_active_presence",
        _forbidden_private_call,
    )
    args = coordination_status.parse_args(
        ["--workdir", str(tmp_path), "--session-id", "safe-session", "--json"]
    )

    result = coordination_status.build_status(args)

    assert result["status"] == "warn"
    assert result["coordination_refused"] is True
    assert result["backend"] == "unavailable"
    assert result["reason"]
    assert result["remedy"]
    assert result["coordination_file"] is None
    assert not context.local_channel_dir.exists()


def test_session_probe_refuses_without_reads_writes_or_watcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _refusal_context(tmp_path)
    monkeypatch.setattr(session_probe, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        session_probe,
        "_probe_inject_readiness",
        lambda _errors: {"inject_available": False},
    )
    monkeypatch.setattr(session_probe, "_read_coordination_file", _forbidden_private_call)
    monkeypatch.setattr(session_probe.inbox, "unread_counts", _forbidden_private_call)
    monkeypatch.setattr(session_probe, "write_backend_presence", _forbidden_private_call)
    monkeypatch.setattr(session_probe._post_mod, "post", _forbidden_private_call)
    monkeypatch.setattr(session_probe, "_run_status_subprocess", _forbidden_private_call)
    monkeypatch.setattr(session_probe, "_reap_stale_watchers", _forbidden_private_call)
    monkeypatch.setattr(session_probe, "_launch_watcher", _forbidden_private_call)

    result = session_probe.probe(
        workdir=tmp_path,
        tool="cursor",
        start_watch=True,
        run_id="refusal-test",
    )

    assert result["status"] == "warn"
    assert result["coordination_refused"] is True
    assert result["coordination_write_failed"] is True
    assert result["watcher_started"] is False
    assert result["backend"] == "unavailable"
    assert result["reason"]
    assert result["remedy"]
    assert not context.local_channel_dir.exists()
