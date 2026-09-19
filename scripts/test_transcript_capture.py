#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for segment-first transcript capture."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import transcript_capture as tc  # noqa: E402


def _transcript(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def agent(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def test_only_owner_authored_text_counts_as_owner(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [
        user("use real data, never synthetic fixtures"),
        user("<system-reminder>Codebase instructions follow</system-reminder>"),
        user("Another Claude session sent a message:\n<agent-message from=\"a1\">700</agent-message>"),
        user("Base directory for this skill: /plugins/x"),
        user("<pasted_content id=\"0b6d\">[RALLY] do not edit files during reviews</pasted_content>"),
        user("[Request interrupted by user]"),
        {"type": "queue-operation", "operation": "enqueue", "content": "all tests must run on real work"},
        {"type": "queue-operation", "operation": "enqueue", "content": "<task-notification>done</task-notification>"},
        agent("I merged the branch."),
    ])

    rows = tc.segments(path)
    owner = [t for k, t in rows if k == tc.OWNER_SAID]

    assert owner == ["use real data, never synthetic fixtures", "all tests must run on real work"]
    assert [t for k, t in rows if k == tc.AGENT_REPORTED] == ["I merged the branch."]
    assert sum(1 for k, _ in rows if k == tc.HARNESS) == 5


def test_owner_message_is_not_duplicated_by_its_queue_record(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [
        {"type": "queue-operation", "operation": "enqueue", "content": "ship it"},
        user("ship it"),
    ])
    assert [t for k, t in tc.segments(path) if k == tc.OWNER_SAID] == ["ship it"]


def test_steering_answer_is_captured_with_its_question(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
             "input": {"questions": [{"question": "When may a script run?"}]}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": 'Your questions have been answered: "When may a script run?"="Approve once per version"'}]}},
    ])
    assert [k for k, _ in tc.segments(path)] == [tc.OWNER_ANSWERED]


def test_closing_reports_are_the_last_message_of_each_turn() -> None:
    rows = [(tc.AGENT_REPORTED, "working"), (tc.AGENT_REPORTED, "still working"),
            (tc.OWNER_SAID, "go on"), (tc.AGENT_REPORTED, "done, here is what I found")]
    assert tc.closing_reports(rows) == [1, 3]


def test_capture_routes_each_segment_to_its_own_question(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
             "input": {"questions": [{"question": "When may a script run?"}]}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": 'Your questions have been answered: "When may a script run?"="Approve once per version", "Start now?"="Yes"'}]}},
        user("never push before the installed app has been run"),
        user("<system-reminder>ignore me</system-reminder>"),
        agent("x" * 250),
    ])

    def fake_ask(prompt: str, key: str, _model: str, **_kw: object) -> str | None:
        if key == "standing":
            return "yes" if "When may a script run?" in prompt else "no"
        if key == "rule":
            return "Never push before the installed app has been run." if "never push" in prompt else None
        return "The index does not score file bodies." if key == "lesson" else None

    items = tc.capture(path, "test-model", ask=fake_ask)
    by_class = {i["capture_class"]: i for i in items}

    assert by_class["decision"]["confidence"] in ("explicit", "confirmed")
    assert [i["decision"] for i in items if i["capture_class"] == "activity"] == ["Start now? -> Yes"]
    assert by_class["lesson"]["decision"] == "The index does not score file bodies."
    assert all(i["evidence"] for i in items)


def test_closing_question_only_when_owner_never_replied() -> None:
    asked = [(tc.AGENT_REPORTED, "Work is done. Should I push it now?")]
    assert tc._closing_question(asked) == "Should I push it now?"
    answered = asked + [(tc.OWNER_SAID, "yes push")]
    assert tc._closing_question(answered) is None


def test_lesson_extraction_stops_at_the_deadline(tmp_path: Path) -> None:
    path = _transcript(tmp_path, [user("just do it"), agent("y" * 250), user("next"), agent("z" * 250)])
    calls: list[str] = []

    def slow_ask(prompt: str, key: str, _model: str, **_kw: object) -> str | None:
        calls.append(key)
        return "A fact about the system." if key == "lesson" else None

    items = tc.capture(path, "test-model", ask=slow_ask, deadline_s=0)
    assert calls.count("lesson") == 0
    assert items == []
