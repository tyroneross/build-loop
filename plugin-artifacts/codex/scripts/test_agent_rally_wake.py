#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Rally standby/wake adapter commands."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_rally  # noqa: E402


def _args(**overrides):
    data = {
        "workdir": ".",
        "session_id": "sess-1",
        "tool": "codex",
        "model": "gpt-5",
        "run_id": "run-1",
        "json": True,
        "reason": "waiting for peer ack",
        "wake_after": "+30m",
        "ref_standby": "standby-1",
        "step": None,
        "parent_step": None,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def _context(tmp_path: Path, *, native: bool) -> SimpleNamespace:
    return SimpleNamespace(
        native=native,
        workdir=tmp_path,
        local_channel_dir=tmp_path,
        envelope=SimpleNamespace(
            app_slug="slug",
            backend="rally" if native else "build-loop-local",
            transport="rally-cli" if native else "fact-v1",
        ),
    )


def test_standby_delegates_to_native_rally(monkeypatch, capsys, tmp_path):
    captured = {}
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _wd: _context(tmp_path, native=True))

    def fake_invoke(_context, argv, **_kwargs):
        captured["cmd"] = argv
        return SimpleNamespace(
            ok=True,
            payload={"ok": True},
            precommit_unavailable=False,
            status="ok",
            reason=None,
            event_id="fact_1",
            remedy=None,
        )

    monkeypatch.setattr(agent_rally, "invoke_native", fake_invoke)

    rc = agent_rally.cmd_standby(
        _args(
            workdir=str(tmp_path),
            step="step-1",
            parent_step="parent-1",
        )
    )

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert captured["cmd"] == [
        "say",
        "standby",
        "--tool",
        "codex:sess-1",
        "--reason",
        "waiting for peer ack",
        "--wake-after",
        "+30m",
        "--json",
        "--run",
        "run-1",
        "--step",
        "step-1",
        "--parent-step",
        "parent-1",
    ]


def test_wake_delegates_to_native_rally(monkeypatch, capsys, tmp_path):
    captured = {}
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _wd: _context(tmp_path, native=True))

    def fake_invoke(_context, argv, **_kwargs):
        captured["cmd"] = argv
        return SimpleNamespace(
            ok=True,
            payload={"ok": True},
            precommit_unavailable=False,
            status="ok",
            reason=None,
            event_id="fact_1",
            remedy=None,
        )

    monkeypatch.setattr(agent_rally, "invoke_native", fake_invoke)

    rc = agent_rally.cmd_wake(_args(workdir=str(tmp_path), step="step-2"))

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert captured["cmd"] == [
        "say",
        "wake",
        "--tool",
        "codex:sess-1",
        "--ref-standby",
        "standby-1",
        "--json",
        "--run",
        "run-1",
        "--step",
        "step-2",
    ]


def test_wake_due_delegates_to_native_rally(monkeypatch, tmp_path):
    native_payload = {
        "command": "wake-due",
        "data": {"wake-due": {"due": [{"standby_event_id": "s1"}]}},
        "ok": True,
    }
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _wd: _context(tmp_path, native=True))

    def fake_invoke(_context, argv, **_kwargs):
        assert argv == ["wake-due", "--tool", "codex:sess-1", "--json"]
        assert _kwargs["tool"] == "codex:sess-1"
        assert _kwargs["session_id"] == "sess-1"
        return SimpleNamespace(ok=True, payload=native_payload, status="ok", reason=None)

    monkeypatch.setattr(agent_rally, "invoke_native", fake_invoke)

    assert agent_rally.build_wake_due_envelope(
        tmp_path, "codex", "sess-1"
    ) == native_payload


@pytest.mark.parametrize(
    ("handler", "kind"),
    (
        (agent_rally.cmd_standby, "standby"),
        (agent_rally.cmd_wake, "wake"),
    ),
)
def test_native_pre_spawn_unavailable_authorizes_one_local_fallback(
    monkeypatch,
    capsys,
    tmp_path,
    handler,
    kind,
):
    context = _context(tmp_path, native=True)
    captured = {}
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _wd: context)
    monkeypatch.setattr(
        agent_rally,
        "invoke_native",
        lambda *_a, **_kw: SimpleNamespace(
            ok=False,
            payload=None,
            precommit_unavailable=True,
            status="unavailable",
            reason="rally spawn failed",
            event_id=None,
            remedy=None,
        ),
    )

    def local_post(**kwargs):
        captured.update(kwargs)
        return 7

    monkeypatch.setattr(agent_rally, "post", local_post)

    assert handler(_args(workdir=str(tmp_path))) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is True
    assert payload["backend"] == "build-loop-local"
    assert payload["fallback_reason"] == "native-precommit-unavailable"
    assert captured["kind"] == kind
    assert captured["channel_dir"] == context.local_channel_dir
    assert captured["workdir"] is None


@pytest.mark.parametrize(
    ("handler", "action"),
    (
        (agent_rally.cmd_standby, "standby-rejected"),
        (agent_rally.cmd_wake, "wake-rejected"),
    ),
)
def test_native_timeout_never_authorizes_local_fallback(
    monkeypatch,
    capsys,
    tmp_path,
    handler,
    action,
):
    context = _context(tmp_path, native=True)
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _wd: context)
    monkeypatch.setattr(
        agent_rally,
        "invoke_native",
        lambda *_a, **_kw: SimpleNamespace(
            ok=False,
            payload=None,
            precommit_unavailable=False,
            status="outcome_unknown",
            reason="rally timed out",
            event_id=None,
            remedy="locate before retry",
        ),
    )
    monkeypatch.setattr(
        agent_rally,
        "post",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("timeout attempted a local fallback write")
        ),
    )

    assert handler(_args(workdir=str(tmp_path))) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is False
    assert payload["action"] == action
    assert payload["status"] == "outcome_unknown"


def test_legacy_wake_due_reads_due_unwoken_standbys(monkeypatch, tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _wd: _context(tmp_path, native=False))
    monkeypatch.setattr(agent_rally, "_resolve_channel", lambda _wd: ("slug", tmp_path))
    monkeypatch.setattr(
        agent_rally.changes,
        "read_changes_since",
        lambda _channel, _offset: (
            [
                {
                    "kind": "standby",
                    "tool": "codex",
                    "payload": {
                        "reason": "peer ack",
                        "wake_after": past,
                    },
                    "revision": 7,
                }
            ],
            7,
        ),
    )

    due = agent_rally.build_wake_due_envelope(tmp_path, "codex")["data"]["wake-due"]["due"]

    assert due == [
        {
            "owner": "codex",
            "reason": "peer ack",
            "standby_event_id": "revision:7",
            "suggested_command": "python3 scripts/agent_rally.py wake --tool codex --ref-standby revision:7 --json",
            "wake_after": past,
        }
    ]


def test_legacy_wake_due_ignores_woken_standbys(monkeypatch, tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(agent_rally, "resolve_context", lambda _wd: _context(tmp_path, native=False))
    monkeypatch.setattr(agent_rally, "_resolve_channel", lambda _wd: ("slug", tmp_path))
    monkeypatch.setattr(
        agent_rally.changes,
        "read_changes_since",
        lambda _channel, _offset: (
            [
                {
                    "kind": "standby",
                    "tool": "codex",
                    "payload": {"wake_after": past},
                    "revision": 7,
                },
                {
                    "kind": "wake",
                    "tool": "codex",
                    "payload": {"ref_standby": "revision:7"},
                    "revision": 8,
                },
            ],
            8,
        ),
    )

    due = agent_rally.build_wake_due_envelope(tmp_path, "codex")["data"]["wake-due"]["due"]

    assert due == []
