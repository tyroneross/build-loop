#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Rally standby/wake adapter commands."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def test_standby_delegates_to_native_rally(monkeypatch, capsys, tmp_path):
    captured = {}
    monkeypatch.setattr(agent_rally, "repo_local_rally_binary", lambda _wd: "/bin/rally")

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, '{"ok":true}\n', "")

    monkeypatch.setattr(agent_rally.subprocess, "run", fake_run)

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
        "/bin/rally",
        "say",
        "standby",
        "--tool",
        "codex",
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
    monkeypatch.setattr(agent_rally, "repo_local_rally_binary", lambda _wd: "/bin/rally")

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, '{"ok":true}\n', "")

    monkeypatch.setattr(agent_rally.subprocess, "run", fake_run)

    rc = agent_rally.cmd_wake(_args(workdir=str(tmp_path), step="step-2"))

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert captured["cmd"] == [
        "/bin/rally",
        "say",
        "wake",
        "--tool",
        "codex",
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
    monkeypatch.setattr(agent_rally, "repo_local_rally_binary", lambda _wd: "/bin/rally")

    def fake_run(cmd, **_kwargs):
        assert cmd == ["/bin/rally", "wake-due", "--tool", "codex", "--json"]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(native_payload), "")

    monkeypatch.setattr(agent_rally.subprocess, "run", fake_run)

    assert agent_rally.build_wake_due_envelope(tmp_path, "codex") == native_payload


def test_legacy_wake_due_reads_due_unwoken_standbys(monkeypatch, tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(agent_rally, "repo_local_rally_binary", lambda _wd: None)
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
    monkeypatch.setattr(agent_rally, "repo_local_rally_binary", lambda _wd: None)
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
