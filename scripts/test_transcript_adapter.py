#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the host-neutral transcript adapter.

Fixtures are synthetic: a real rollout carries the user's own prompts, and a
test corpus is the wrong place for them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import transcript_adapter as ta  # type: ignore  # noqa: E402


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


CODEX_ROWS = [
    {"timestamp": "2026-08-22T16:14:53Z", "type": "session_meta",
     "payload": {"session_id": "s1", "cwd": "/repos/demo"}},
    {"timestamp": "2026-08-22T16:15:00Z", "type": "event_msg",
     "payload": {"type": "user_message", "message": "add a retry to the fetch helper"}},
    # Same turn, API-level, wrapped in injected context. Must NOT be mined as intent.
    {"timestamp": "2026-08-22T16:15:00Z", "type": "response_item",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text",
                              "text": "<recommended_plugins>Airtable</recommended_plugins>"}]}},
    {"timestamp": "2026-08-22T16:15:01Z", "type": "response_item",
     "payload": {"type": "message", "role": "developer",
                 "content": [{"type": "input_text", "text": "Thread coordination preamble"}]}},
    {"timestamp": "2026-08-22T16:15:02Z", "type": "event_msg",
     "payload": {"type": "agent_message", "message": "I'll add the retry."}},
    {"timestamp": "2026-08-22T16:15:03Z", "type": "response_item",
     "payload": {"type": "custom_tool_call", "name": "exec",
                 "input": "{\"cmd\": \"pytest -q\"}"}},
    {"timestamp": "2026-08-22T16:15:04Z", "type": "response_item",
     "payload": {"type": "custom_tool_call_output",
                 "output": [{"type": "input_text", "text": "3 passed"}]}},
]

CLAUDE_ROWS = [
    {"type": "user", "sessionId": "abc",
     "message": {"role": "user", "content": "add a retry to the fetch helper"}},
    {"type": "assistant", "sessionId": "abc",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}]}},
]


def test_detects_each_host_by_structure(tmp_path: Path) -> None:
    assert ta.detect_format(_write(tmp_path / "cx.jsonl", CODEX_ROWS)) == ta.CODEX
    assert ta.detect_format(_write(tmp_path / "cl.jsonl", CLAUDE_ROWS)) == ta.CLAUDE


def test_claude_transcripts_pass_through_byte_identical(tmp_path: Path) -> None:
    """Wiring a consumer through the adapter must not change Claude behavior."""
    src = _write(tmp_path / "cl.jsonl", CLAUDE_ROWS)
    assert list(ta.iter_events(src)) == CLAUDE_ROWS


def test_codex_turns_normalize_to_the_claude_shape(tmp_path: Path) -> None:
    events = list(ta.iter_events(_write(tmp_path / "cx.jsonl", CODEX_ROWS)))
    kinds = [(e["type"], e["message"]["content"][0]["type"]) for e in events]
    assert kinds == [
        ("user", "text"),
        ("assistant", "text"),
        ("assistant", "tool_use"),
        ("user", "tool_result"),
    ]
    assert events[0]["message"]["content"][0]["text"] == "add a retry to the fetch helper"
    # `command` is synthesized from Codex's `cmd` so consumers that scan
    # input["command"] (git-commit detection, ritual mining) still see it.
    assert events[2]["message"]["content"][0]["input"] == {
        "cmd": "pytest -q", "command": "pytest -q",
    }
    assert events[3]["message"]["content"][0]["content"] == "3 passed"


def test_injected_context_and_developer_turns_are_not_mined_as_intent(tmp_path: Path) -> None:
    """Codex records each turn twice; the API-level copy carries injected text.

    Mining that copy would score '<recommended_plugins>' as something the user
    said, which is exactly what the miner must never do.
    """
    events = list(ta.iter_events(_write(tmp_path / "cx.jsonl", CODEX_ROWS)))
    blob = json.dumps(events)
    assert "recommended_plugins" not in blob
    assert "Thread coordination preamble" not in blob


def test_session_cwd_resolves_for_codex_and_is_absent_for_claude(tmp_path: Path) -> None:
    assert ta.session_cwd(_write(tmp_path / "cx.jsonl", CODEX_ROWS)) == "/repos/demo"
    assert ta.session_cwd(_write(tmp_path / "cl.jsonl", CLAUDE_ROWS)) is None


def test_malformed_lines_never_raise(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"type": "event_msg", "payload": {"type":"user_message","message":"ok"}}\n'
                    "not json at all\n"
                    "[]\n", encoding="utf-8")
    assert len(list(ta.iter_events(path))) == 1


def test_missing_file_yields_nothing_instead_of_raising(tmp_path: Path) -> None:
    assert list(ta.iter_events(tmp_path / "nope.jsonl")) == []
    assert ta.detect_format(tmp_path / "nope.jsonl") == ta.UNKNOWN


def test_normalize_to_file_round_trips(tmp_path: Path) -> None:
    src = _write(tmp_path / "cx.jsonl", CODEX_ROWS)
    dest = tmp_path / "norm.jsonl"
    assert ta.normalize_to_file(src, dest) == 4
    assert ta.detect_format(dest) == ta.CLAUDE


def test_command_key_is_synthesized_for_every_codex_payload_shape(tmp_path: Path) -> None:
    """session_is_trivial() and the ritual miner both read input["command"].

    Codex emits the command under "cmd" inside a JS program string, so without
    this mapping a Codex session that ran `git commit` scores as trivial.
    """
    rows = [
        {"type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec",
            "input": 'tools.exec_command({"cmd":"git commit -m wip"})'}},
    ]
    events = list(ta.iter_events(_write(tmp_path / "cx.jsonl", rows)))
    assert "git commit" in events[0]["message"]["content"][0]["input"]["command"]
