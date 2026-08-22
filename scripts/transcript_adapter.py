#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""transcript_adapter.py — one transcript shape for every host.

WHY THIS EXISTS (the defect it fixes)
-------------------------------------
Every transcript consumer in this repo parses the Claude Code shape:

    {"type": "user"|"assistant", "message": {"role": ..., "content": ...}}

Codex writes a different envelope entirely:

    {"timestamp": ..., "type": "event_msg"|"response_item"|..., "payload": {...}}

So the Stop sweeps (decisions / corrections / findings / cost-ledger), the
pattern miner, and the retrospective synthesizer all read ZERO events from a
Codex session. Registering those hooks for Codex without this adapter would
reproduce the exact silent no-op documented in the header of
``hooks/stop-transcript-sweep.sh``: a guard that was false in every real
session for three months while appearing to be wired.

WHICH CODEX RECORD WINS
-----------------------
Codex writes each turn TWICE — once as a UI-level ``event_msg`` and once as an
API-level ``response_item``. They are not equivalent for our purpose:

  * ``event_msg/user_message.message`` is the user's actual words.
  * ``response_item/message role=user`` is the same turn WRAPPED in injected
    context (``<recommended_plugins>``, thread-coordination preambles).

The consumers mine human intent -- corrections, steering, decisions -- so the
user/assistant text comes from ``event_msg`` (clean) and only the tool traffic
comes from ``response_item`` (which is the only place it exists). Taking
``response_item`` for prose would feed injected boilerplate to the miner and
score it as user intent.

``role=developer`` records are system injections, never human turns, and are
dropped.

Stdlib only. Python 3.11+. Never raises on a malformed line.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

CLAUDE = "claude"
CODEX = "codex"
UNKNOWN = "unknown"

_DETECT_SCAN_LINES = 40


def _loads(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def detect_format(path: Path) -> str:
    """Classify a transcript by structure, never by file location.

    A Codex rollout copied elsewhere is still Codex; a path-based guess would
    be wrong exactly when someone moves a file to inspect it.
    """
    try:
        with Path(path).open(errors="replace") as fh:
            for _, line in zip(range(_DETECT_SCAN_LINES), fh):
                obj = _loads(line)
                if obj is None:
                    continue
                if obj.get("type") in {"event_msg", "response_item", "turn_context", "session_meta"}:
                    return CODEX
                if "sessionId" in obj or ("message" in obj and "type" in obj):
                    return CLAUDE
    except OSError:
        return UNKNOWN
    return UNKNOWN


def session_cwd(path: Path) -> str | None:
    """Repo the session ran in.

    Claude carries it in the hook payload, not the transcript; Codex records it
    in ``session_meta`` and refreshes it per turn in ``turn_context``. Returning
    it here is what lets a Codex retro resolve its project at all.
    """
    fmt = detect_format(path)
    if fmt != CODEX:
        return None
    try:
        with Path(path).open(errors="replace") as fh:
            for line in fh:
                obj = _loads(line)
                if obj is None:
                    continue
                if obj.get("type") in {"session_meta", "turn_context"}:
                    payload = obj.get("payload") or {}
                    cwd = payload.get("cwd") if isinstance(payload, dict) else None
                    if cwd:
                        return str(cwd)
    except OSError:
        return None
    return None


def _text_block(text: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": text}]


def _turn(role: str, content: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": role, "message": {"role": role, "content": content}}


def _joined_output_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts = [
            str(item.get("text", ""))
            for item in output
            if isinstance(item, dict) and item.get("text")
        ]
        return "\n".join(p for p in parts if p)
    return ""


_CMD_KEYS = ("command", "cmd", "script", "shell")


def _tool_input(raw: Any) -> dict[str, Any]:
    """Normalize a tool payload so `input["command"]` always holds the command.

    Codex's exec payload is a JavaScript program string calling
    `tools.exec_command({"cmd": "..."})`, not JSON -- while every consumer here
    reads `input["command"]` (Claude's Bash shape). Without this mapping,
    session_is_trivial() cannot see a `git commit`, and a Codex session that
    committed still scores as trivial.
    """
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
    if isinstance(parsed, dict):
        out = dict(parsed)
        if "command" not in out:
            for key in _CMD_KEYS:
                if isinstance(out.get(key), str):
                    out["command"] = out[key]
                    break
        out.setdefault("command", "")
        return out
    text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    # Unparseable payloads keep their full text under `command` so command-text
    # scans (git commit detection, ritual mining) still see it.
    return {"command": text, "raw": text}


def _codex_event(obj: dict[str, Any]) -> dict[str, Any] | None:
    kind = obj.get("type")
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None
    ptype = payload.get("type")

    if kind == "event_msg":
        if ptype == "user_message":
            text = str(payload.get("message") or "").strip()
            return _turn("user", _text_block(text)) if text else None
        if ptype == "agent_message":
            text = str(payload.get("message") or "").strip()
            return _turn("assistant", _text_block(text)) if text else None
        return None

    if kind == "response_item":
        if ptype in {"custom_tool_call", "function_call"}:
            raw = payload.get("input") or payload.get("arguments") or ""
            return _turn("assistant", [{
                "type": "tool_use",
                "name": payload.get("name") or "exec",
                "input": _tool_input(raw),
            }])
        if ptype in {"custom_tool_call_output", "function_call_output"}:
            text = _joined_output_text(payload.get("output"))
            return _turn("user", [{"type": "tool_result", "content": text}]) if text else None
    return None


def iter_events(path: Path) -> Iterator[dict[str, Any]]:
    """Yield Claude-shaped rows from a transcript of either host.

    Claude transcripts pass through untouched, so wiring a consumer through
    this adapter cannot change existing behavior.
    """
    path = Path(path)
    fmt = detect_format(path)
    if fmt == UNKNOWN:
        return
    try:
        with path.open(errors="replace") as fh:
            for line in fh:
                obj = _loads(line)
                if obj is None:
                    continue
                if fmt == CLAUDE:
                    yield obj
                else:
                    event = _codex_event(obj)
                    if event is not None:
                        yield event
    except OSError:
        return


def normalize_to_file(src: Path, dest: Path) -> int:
    """Write a Claude-shaped copy of ``src``; return the row count.

    Consumers that take a PATH (the Stop sweeps, the miner) get a normalized
    temp file rather than a new API, so no consumer has to change its contract.
    """
    count = 0
    with Path(dest).open("w", encoding="utf-8") as fh:
        for event in iter_events(src):
            fh.write(json.dumps(event) + "\n")
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--out", type=Path, help="write a normalized JSONL copy here")
    parser.add_argument("--json", action="store_true", help="print a detection receipt")
    args = parser.parse_args(argv)

    fmt = detect_format(args.transcript)
    if args.out:
        rows = normalize_to_file(args.transcript, args.out)
    else:
        rows = sum(1 for _ in iter_events(args.transcript))

    if args.json:
        print(json.dumps({
            "format": fmt,
            "rows": rows,
            "cwd": session_cwd(args.transcript),
            "out": str(args.out) if args.out else None,
        }, indent=2))
    else:
        print(f"{fmt}: {rows} normalized rows")
    return 0 if fmt != UNKNOWN else 1


if __name__ == "__main__":
    raise SystemExit(main())
