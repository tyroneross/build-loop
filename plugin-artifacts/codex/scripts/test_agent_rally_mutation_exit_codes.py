#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Mutation CLIs must not report success when no fact or sidecar committed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import agent_rally  # noqa: E402
from rally_point.backend_adapter import BackendContext, NativeResult  # noqa: E402
from rally_point.discovery_bridge import DiscoveryEnvelope  # noqa: E402


def _local_context(workdir: Path) -> BackendContext:
    channel = workdir / ".build-loop" / "local-rally"
    return BackendContext(
        workdir=workdir,
        envelope=DiscoveryEnvelope(
            channel_dir=str(channel),
            app_slug="mutation-exit-codes",
            repo_id=None,
            channel_layout="legacy",
            policy="legacy-only",
            protocol_version="1.0",
            last_resolved_at="2026-08-14T00:00:00Z",
            resolved_via="build-loop-internal",
        ),
        local_channel_dir=channel,
    )


@pytest.mark.parametrize(
    ("argv", "expected_action"),
    (
        (
            [
                "handoff", "--session-id", "safe", "--tool", "cursor",
                "--run-id", "run-1",
            ],
            "handoff-rejected",
        ),
        (
            [
                "escalate", "--session-id", "safe", "--tool", "cursor",
                "--run-id", "run-1", "--reason", "needs decision",
            ],
            "escalation-rejected",
        ),
        (
            [
                "retract", "--session-id", "safe", "--tool", "cursor",
                "--run-id", "run-1", "--fact", "missing", "--reason", "bad",
                "--force",
            ],
            "retract-rejected",
        ),
        (
            [
                "status-post", "--session-id", "safe", "--tool", "cursor",
                "--run-id", "run-1", "--file", "CURRENT.md",
            ],
            "status-rejected",
        ),
        (
            [
                "standby", "--session-id", "safe", "--tool", "cursor",
                "--run-id", "run-1", "--reason", "wait", "--wake-after", "+30m",
            ],
            "standby-rejected",
        ),
        (
            [
                "wake", "--session-id", "safe", "--tool", "cursor",
                "--run-id", "run-1", "--ref-standby", "missing",
            ],
            "wake-rejected",
        ),
    ),
)
def test_failed_local_post_mutations_exit_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    argv: list[str],
    expected_action: str,
) -> None:
    context = _local_context(tmp_path)
    argv = [*argv, "--workdir", str(tmp_path)]
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "_resolve_channel",
        lambda _workdir: (context.envelope.app_slug, context.local_channel_dir),
    )
    monkeypatch.setattr(agent_rally, "_read_raw_log", lambda _channel: [])
    monkeypatch.setattr(agent_rally, "post", lambda **_kwargs: None)

    assert agent_rally.main(argv) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == expected_action
    assert payload["accepted"] is False


def test_presence_write_failure_exits_nonzero_and_reports_false_ack(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _local_context(tmp_path)
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally,
        "write_backend_presence",
        lambda *_a, **_kw: NativeResult(
            "failed",
            reason="presence write failed",
            backend="build-loop-local",
            transport="presence-json",
        ),
    )

    rc = agent_rally.main(
        [
            "presence", "--workdir", str(tmp_path), "--session-id", "safe",
            "--tool", "cursor", "--run-id", "run-1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["action"] == "presence-failed"
    assert payload["accepted"] is False
    assert payload["reason"] == "presence write failed"


def test_read_command_preserves_zero_exit_on_empty_local_store(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    context = _local_context(tmp_path)
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(
        agent_rally.changes,
        "read_changes_since",
        lambda *_a, **_kw: ([], 0),
    )

    assert agent_rally.main(
        ["status-read", "--workdir", str(tmp_path), "--json"]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "status-read"
    assert payload["found"] is False
