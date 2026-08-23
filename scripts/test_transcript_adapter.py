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


def _rollout(root: Path, name: str, cwd: str, mtime: float) -> Path:
    import os

    day = root / "2026" / "08" / "22"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"rollout-{name}.jsonl"
    path.write_text(json.dumps(
        {"timestamp": "2026-08-22T16:14:53Z", "type": "session_meta",
         "payload": {"session_id": name, "cwd": cwd}}) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_finds_the_newest_rollout_for_a_repo(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    _rollout(root, "old", "/repos/demo", 1_000_000)
    newest = _rollout(root, "new", "/repos/demo", 2_000_000)
    _rollout(root, "other", "/repos/elsewhere", 3_000_000)
    assert ta.find_codex_transcript("/repos/demo", sessions_root=root) == newest


def test_repo_names_containing_spaces_resolve(tmp_path: Path) -> None:
    """Space-in-path repos were a real failure cluster for the promotion path."""
    root = tmp_path / "sessions"
    target = _rollout(root, "spaced", "/repos/My Repo Name", 1_000_000)
    assert ta.find_codex_transcript("/repos/My Repo Name", sessions_root=root) == target


def test_trailing_slash_does_not_prevent_a_match(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    target = _rollout(root, "s", "/repos/demo", 1_000_000)
    assert ta.find_codex_transcript("/repos/demo/", sessions_root=root) == target


def test_unknown_repo_and_missing_store_return_none(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    _rollout(root, "s", "/repos/demo", 1_000_000)
    assert ta.find_codex_transcript("/repos/nothing", sessions_root=root) is None
    assert ta.find_codex_transcript("/repos/demo", sessions_root=tmp_path / "gone") is None


def test_scan_is_bounded_so_an_unbounded_store_cannot_stall_a_hook(tmp_path: Path) -> None:
    """The rollout store grows without limit; a Stop hook must still return fast."""
    root = tmp_path / "sessions"
    for i in range(12):
        _rollout(root, f"noise{i}", "/repos/elsewhere", 9_000_000 + i)
    _rollout(root, "target", "/repos/demo", 1_000)
    assert ta.find_codex_transcript("/repos/demo", sessions_root=root, max_scan=5) is None
    assert ta.find_codex_transcript("/repos/demo", sessions_root=root, max_scan=50) is not None


def test_four_sweeps_share_one_normalization(tmp_path: Path) -> None:
    """Four Stop sweeps fire per session; four multi-MB copies is a leak."""
    src = _write(tmp_path / "cx.jsonl", CODEX_ROWS)
    cache = tmp_path / "cache"
    paths = {ta.normalize_cached(src, cache) for _ in range(4)}
    assert len(paths) == 1
    assert len(list(cache.glob("normalized-*.jsonl"))) == 1


def test_cache_refreshes_when_the_session_grows(tmp_path: Path) -> None:
    """A session is still being written at Stop time; a stale copy loses turns."""
    import os

    src = _write(tmp_path / "cx.jsonl", CODEX_ROWS)
    cache = tmp_path / "cache"
    before = ta.normalize_cached(src, cache).read_text().count("\n")
    _write(src, CODEX_ROWS + [
        {"type": "event_msg", "payload": {"type": "user_message", "message": "one more"}}])
    os.utime(src, (9_999_999_999, 9_999_999_999))
    assert ta.normalize_cached(src, cache).read_text().count("\n") == before + 1


def test_stale_cache_entries_are_reaped(tmp_path: Path) -> None:
    import os

    cache = tmp_path / "cache"
    cache.mkdir()
    old = cache / "normalized-deadbeefdeadbeef.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    os.utime(old, (1_000_000, 1_000_000))
    ta.normalize_cached(_write(tmp_path / "cx.jsonl", CODEX_ROWS), cache)
    assert not old.exists()


def test_partial_files_are_never_left_behind(tmp_path: Path) -> None:
    """Readers must see a complete file or none -- four sweeps race on this."""
    cache = tmp_path / "cache"
    ta.normalize_cached(_write(tmp_path / "cx.jsonl", CODEX_ROWS), cache)
    assert list(cache.glob("*.part")) == []


def test_empty_source_produces_no_cache_entry(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert ta.normalize_cached(empty, tmp_path / "cache") is None
