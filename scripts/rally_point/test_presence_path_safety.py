#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Filesystem boundaries for Build Loop fallback presence sidecars."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import backend_adapter, hooks, presence
from rally_point.backend_adapter import BackendContext
from rally_point.discovery_bridge import DiscoveryEnvelope


def _write(channel: Path, session_id: str) -> bool:
    return presence.write_presence(
        channel,
        session_id=session_id,
        tool="cursor",
        model="test",
        run_id="run-1",
        app_slug="presence-path-safety",
        phase="test",
        cwd=channel,
    )


@pytest.mark.parametrize(
    "session_id",
    (
        "../escape",
        "../../victim",
        "session/name",
        r"session\name",
        "*",
        "session?",
        "session[ab]",
        "x" * 161,
        " space",
    ),
)
def test_write_presence_rejects_traversal_glob_and_unbounded_ids(
    tmp_path: Path,
    session_id: str,
) -> None:
    channel = tmp_path / "local-channel"

    assert _write(channel, session_id) is False

    assert not channel.exists()
    assert not (tmp_path / "escape.json").exists()
    assert not (tmp_path / "victim.json").exists()


def test_write_presence_accepts_generated_colon_hyphen_underscore_id(
    tmp_path: Path,
) -> None:
    channel = tmp_path / "local-channel"
    session_id = "agent:code_review-worker-01"

    assert _write(channel, session_id) is True

    path = presence.presence_path(channel, session_id)
    assert path.is_file()
    assert path.parent == (channel / "sessions").resolve()


def test_write_presence_refuses_channel_beneath_dot_rally(tmp_path: Path) -> None:
    native_channel = tmp_path / ".rally" / "overridden" / "private"

    assert _write(native_channel, "safe-session") is False

    assert not native_channel.exists()
    assert not (tmp_path / ".rally" / "sessions").exists()


def test_write_presence_refuses_sessions_symlink_escape(tmp_path: Path) -> None:
    channel = tmp_path / "local-channel"
    outside = tmp_path / "outside"
    channel.mkdir()
    outside.mkdir()
    (channel / "sessions").symlink_to(outside, target_is_directory=True)

    assert _write(channel, "safe-session") is False

    assert not (outside / "safe-session.json").exists()


def test_adapter_does_not_ack_rejected_presence_path(tmp_path: Path) -> None:
    channel = tmp_path / "local-channel"
    context = BackendContext(
        workdir=tmp_path,
        envelope=DiscoveryEnvelope(
            channel_dir=str(channel),
            app_slug="presence-path-safety",
            repo_id=None,
            channel_layout="legacy",
            policy="legacy-only",
            protocol_version="1.0",
            last_resolved_at="2026-08-14T00:00:00Z",
            resolved_via="build-loop-internal",
        ),
        local_channel_dir=channel,
    )

    result = backend_adapter.write_local_presence(
        context,
        session_id="../escape",
        tool="cursor",
        model="test",
        run_id="run-1",
        app_slug="presence-path-safety",
        phase="test",
    )

    assert result.status == "failed"
    assert not result.ok
    assert "not committed" in str(result.reason)
    assert not channel.exists()


def test_local_pre_edit_does_not_claim_join_when_presence_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    channel = tmp_path / "local-channel"
    context = BackendContext(
        workdir=tmp_path,
        envelope=DiscoveryEnvelope(
            channel_dir=str(channel),
            app_slug="presence-path-safety",
            repo_id=None,
            channel_layout="legacy",
            policy="legacy-only",
            protocol_version="1.0",
            last_resolved_at="2026-08-14T00:00:00Z",
            resolved_via="build-loop-internal",
        ),
        local_channel_dir=channel,
    )
    monkeypatch.setattr(hooks, "resolve_operative_repo", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(hooks, "resolve_context", lambda _workdir: context)
    monkeypatch.setattr(hooks, "_heartbeat_session_id", lambda _slug: "safe-session")
    monkeypatch.setattr(hooks.presence, "write_presence", lambda *_a, **_kw: False)
    monkeypatch.delenv("BUILD_LOOP_RALLY_QUIET", raising=False)

    assert hooks.pre_edit_join(tmp_path, file_path="src/app.py", now=1000.0) == 0

    captured = capsys.readouterr()
    assert "joined" not in captured.out
    assert not (channel / "sessions" / "safe-session.json").exists()


def test_adapter_does_not_ack_atomic_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    channel = tmp_path / "local-channel"
    context = BackendContext(
        workdir=tmp_path,
        envelope=DiscoveryEnvelope(
            channel_dir=str(channel),
            app_slug="presence-path-safety",
            repo_id=None,
            channel_layout="legacy",
            policy="legacy-only",
            protocol_version="1.0",
            last_resolved_at="2026-08-14T00:00:00Z",
            resolved_via="build-loop-internal",
        ),
        local_channel_dir=channel,
    )
    monkeypatch.setattr(
        presence,
        "_atomic_write",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk full")),
    )

    result = backend_adapter.write_local_presence(
        context,
        session_id="safe-session",
        tool="cursor",
        model="test",
        run_id="run-1",
        app_slug="presence-path-safety",
        phase="test",
    )

    assert result.status == "failed"
    assert not result.ok
