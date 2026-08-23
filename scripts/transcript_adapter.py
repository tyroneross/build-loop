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
import os
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


_CWD_EVENT_TYPES = {"session_meta", "turn_context"}


def _event_cwd(obj: dict | None) -> str | None:
    """``cwd`` carried by one cwd-bearing event, or None for every other event."""
    if not obj or obj.get("type") not in _CWD_EVENT_TYPES:
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None
    cwd = payload.get("cwd")
    return str(cwd) if cwd else None


def session_cwd(path: Path) -> str | None:
    """Repo the session ran in.

    Claude carries it in the hook payload, not the transcript; Codex records it
    in ``session_meta`` and refreshes it per turn in ``turn_context``. Returning
    it here is what lets a Codex retro resolve its project at all.
    """
    if detect_format(path) != CODEX:
        return None
    try:
        with Path(path).open(errors="replace") as fh:
            for line in fh:
                cwd = _event_cwd(_loads(line))
                if cwd:
                    return cwd
    except OSError:
        return None
    return None


CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
# Newest-first, so the first cwd match is the answer. Bounded because the
# rollout store grows without limit (2,557 files at time of writing).
_MAX_SESSION_SCAN = 400


def find_codex_transcript(
    cwd: str | Path, sessions_root: Path | None = None, max_scan: int = _MAX_SESSION_SCAN
) -> Path | None:
    """Newest Codex rollout recorded for ``cwd``, or None.

    WHY DISCOVERY RATHER THAN A PAYLOAD FIELD
    -----------------------------------------
    Claude delivers `transcript_path` in the hook payload. Codex's Stop-payload
    contract is not documented here and was not observable without running a
    live session, so depending on it would mean registering hooks that MIGHT
    silently no-op -- the exact failure this whole line of work exists to stop.
    Resolving from cwd removes the dependency instead of guessing at it: it is
    verifiable now, against real files. Callers still prefer an explicit path
    when the host supplies one.

    LIMIT: with two concurrent Codex sessions in one repo this returns the most
    recently written, which is the one that just ended in the Stop case but is
    not guaranteed to be the caller's own session.
    """
    root = sessions_root or CODEX_SESSIONS_ROOT
    if not root.is_dir():
        return None
    target = str(Path(cwd)).rstrip("/")
    try:
        files = sorted(
            root.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:max_scan]
    except OSError:
        return None
    for path in files:
        try:
            with path.open(errors="replace") as fh:
                for _, line in zip(range(3), fh):
                    obj = _loads(line)
                    if obj is None or obj.get("type") != "session_meta":
                        continue
                    payload = obj.get("payload") or {}
                    found = payload.get("cwd") if isinstance(payload, dict) else None
                    if found and str(found).rstrip("/") == target:
                        return path
                    break
        except OSError:
            continue
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


# A normalized rollout runs to multiple MB, four Stop sweeps fire per session,
# and nothing evicts them -- an unbounded accumulator fed by every session. The
# cache is keyed by SOURCE FILE so the four sweeps share one normalization
# instead of writing four copies, and stale entries are reaped on every call.
CACHE_TTL_DAYS = 2


def _reap(cache_dir: Path, ttl_days: int = CACHE_TTL_DAYS) -> None:
    import time

    cutoff = time.time() - ttl_days * 86400
    try:
        for old in cache_dir.glob("normalized-*.jsonl"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                continue
    except OSError:
        return


def normalized_cache_path(source: Path, cache_dir: Path) -> Path:
    import hashlib

    digest = hashlib.sha256(str(Path(source).resolve()).encode()).hexdigest()[:16]
    return Path(cache_dir) / f"normalized-{digest}.jsonl"


def normalize_cached(source: Path, cache_dir: Path) -> Path | None:
    """Normalize ``source`` into ``cache_dir``, reusing a fresh result.

    Four sweeps race here, so the write is atomic (temp + rename): a reader can
    only ever see a complete file, never a half-written one.
    """
    source = Path(source)
    cache_dir = Path(cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    _reap(cache_dir)
    dest = normalized_cache_path(source, cache_dir)
    try:
        if dest.is_file() and dest.stat().st_mtime >= source.stat().st_mtime:
            return dest
    except OSError:
        pass
    tmp = dest.with_suffix(f".{os.getpid()}.part")
    try:
        if normalize_to_file(source, tmp) == 0:
            tmp.unlink(missing_ok=True)
            return None
        os.replace(tmp, dest)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return dest


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path, nargs="?")
    parser.add_argument("--find-codex-session", metavar="CWD",
                        help="print the newest Codex rollout recorded for CWD, then exit")
    parser.add_argument("--out", type=Path, help="write a normalized JSONL copy here")
    parser.add_argument("--cache-dir", type=Path,
                        help="normalize into a shared, self-reaping cache and print its path")
    parser.add_argument("--json", action="store_true", help="print a detection receipt")
    args = parser.parse_args(argv)

    if args.find_codex_session:
        found = find_codex_transcript(args.find_codex_session)
        if found is None:
            return 1
        # With --cache-dir/--out, resolve AND normalize in one call: every
        # consumer parses the Claude shape, so handing back a raw Codex path
        # would just move the silent no-op one step downstream.
        if args.cache_dir:
            cached = normalize_cached(found, args.cache_dir)
            if cached is None:
                return 1
            print(cached)
        elif args.out:
            if normalize_to_file(found, args.out) == 0:
                return 1
            print(args.out)
        else:
            print(found)
        return 0

    if args.transcript is None:
        parser.error("transcript is required unless --find-codex-session is used")

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
