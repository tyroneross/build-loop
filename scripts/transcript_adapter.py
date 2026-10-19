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
user/assistant text prefers ``event_msg`` (clean) when both representations
exist. Response-only rollouts use API prose while marking known context records
as meta. Tool traffic comes from ``response_item``. Consumers must retain the
meta/origin distinction before mining owner intent.

``role=developer`` records are system injections, never human turns, and are
dropped.

Stdlib only. Python 3.11+. Never raises on a malformed line.
"""
from __future__ import annotations

import json
import os
import re
import hashlib
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator

CLAUDE = "claude"
CODEX = "codex"
UNKNOWN = "unknown"

GENERIC_TOOLS = frozenset({
    "Bash", "Edit", "MultiEdit", "Read", "Write", "Grep", "Glob",
    "Task", "Agent", "TodoWrite", "NotebookEdit", "LS",
    "exec", "wait", "exec_command", "write_stdin", "functions.exec", "functions.wait",
    "functions.exec_command", "functions.write_stdin", "spawn_agent", "send_message",
    "followup_task", "wait_agent", "list_agents", "interrupt_agent",
    "collaboration.spawn_agent", "collaboration.send_message", "collaboration.followup_task",
    "collaboration.wait_agent", "collaboration.list_agents", "collaboration.interrupt_agent",
})


def session_key(path: Path) -> str:
    """A safe original session key, independent of a temporary normalized name."""
    identity = path.stem
    try:
        with _open_regular_binary(path) as handle:
            for _, raw in zip(range(40), _bounded_lines(handle)):
                line = raw.decode("utf-8", errors="replace")
                row = _loads(line)
                if not row:
                    continue
                payload = row.get("payload")
                candidate = ((payload.get("id") or payload.get("session_id")) if row.get("type") == "session_meta"
                             and isinstance(payload, dict) else row.get("sessionId") or
                             (row["_source"].get("session_id") if isinstance(row.get("_source"), dict) else None))
                if isinstance(candidate, str) and candidate.strip():
                    identity = candidate.strip()
                    break
    except (OSError, TranscriptLimitError):
        pass
    if re.fullmatch(r"[a-zA-Z0-9_.-]{1,180}", identity) and identity not in {".", ".."}:
        return identity
    return hashlib.sha256(identity.encode()).hexdigest()


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
        with _open_regular_binary(Path(path)) as fh:
            for _, raw in zip(range(_DETECT_SCAN_LINES), _bounded_lines(fh)):
                line = raw.decode("utf-8", errors="replace")
                obj = _loads(line)
                if obj is None:
                    continue
                if obj.get("type") in {"event_msg", "response_item", "turn_context", "session_meta"}:
                    return CODEX
                if ("sessionId" in obj or ("message" in obj and "type" in obj)
                        or obj.get("role") in ("user", "assistant")
                        or obj.get("type") in {"user", "assistant"}):
                    return CLAUDE
    except (OSError, TranscriptLimitError):
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
        with _open_regular_binary(Path(path)) as fh:
            for raw in _bounded_lines(fh):
                cwd = _event_cwd(_loads(raw.decode("utf-8", errors="replace")))
                if cwd:
                    return cwd
    except (OSError, TranscriptLimitError):
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
            with _open_regular_binary(path) as fh:
                for _, raw in zip(range(3), _bounded_lines(fh)):
                    line = raw.decode("utf-8", errors="replace")
                    obj = _loads(line)
                    if obj is None or obj.get("type") != "session_meta":
                        continue
                    payload = obj.get("payload") or {}
                    found = payload.get("cwd") if isinstance(payload, dict) else None
                    if found and str(found).rstrip("/") == target:
                        return path
                    break
        except (OSError, TranscriptLimitError):
            continue
    return None


def human_text(content: Any) -> str:
    """Human prose only; tool results cannot become user directions."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(item if isinstance(item, str) else item["text"]
                     for item in content if isinstance(item, str) or
                     (isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str))).strip()


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


def _codex_text(value: Any) -> str:
    """Flatten Codex response-item text/output blocks without model inference."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"input_text", "output_text", "text"}:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _codex_output_is_error(output: str) -> bool:
    """Return true only for direct, deterministic Codex tool-failure evidence.

    Tool output often contains source code, audits, or prior logs with words
    such as ``ERROR``. Searching the entire payload therefore creates false
    failures. Accept explicit structured failure fields or a failure marker at
    the start of the result instead.
    """
    stripped = output.lstrip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        metadata = parsed.get("metadata")
        exit_code = parsed.get("exit_code", metadata.get("exit_code") if isinstance(metadata, dict) else None)
        if type(exit_code) is int and exit_code != 0:
            return True
        if parsed.get("isError") is True or (parsed.get("ok") is False and parsed.get("error")):
            return True
    # Only a recognized transport header may precede an exit status. Once
    # output starts, an error-looking line is ordinary command output/data.
    lines = stripped.split("\n", 8)[:8]
    if lines and re.match(r"^(?:Chunk ID|Wall time):", lines[0]):
        for line in lines:
            if re.fullmatch(r"(?:Process exited with code|Exit code:)\s*[1-9]\d*", line.strip()):
                return True
            if not re.match(r"^(?:Chunk ID:|Wall time:|Original token count:|Process exited with code 0$|Exit code:\s*0$)", line):
                break
    return bool(re.match(
        r"(?:ERROR:|Error:|Traceback \(most recent call last\)|"
        r"Script failed\b|Process exited with code [1-9]\b|ToolCallError\b)",
        stripped,
    ))


_CODEX_META_PREFIXES = ("<recommended_plugins>", "# AGENTS.md instructions for ",
                        "<environment_context>", "<external_codex_apps_open_page>", "<user_instructions>")
PARSER_VERSION = "6"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 8 * 1024 * 1024


class TranscriptReadError(OSError):
    """Source reading failed; no complete extraction can be published."""


class TranscriptLimitError(ValueError):
    """Input exceeded an explicit ingestion budget; coverage is unavailable."""


@contextmanager
def _open_regular_binary(path: Path) -> Iterator[BinaryIO]:
    """Open without waiting for a FIFO writer, then validate the actual fd.

    A pathname stat before open cannot protect against replacement between the
    check and open. No reader consumes bytes until this descriptor is verified.
    """
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise TranscriptReadError("platform lacks a safe nonblocking source opener")
    descriptor = os.open(path, os.O_RDONLY | nonblocking | getattr(os, "O_BINARY", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TranscriptReadError("source must be a regular file")
        if info.st_size > MAX_SOURCE_BYTES:
            raise TranscriptLimitError("source exceeds source byte budget")
        handle = os.fdopen(descriptor, "rb")
        descriptor = None  # fdopen now owns it, including all exceptional exits.
        with handle:
            yield handle
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _bounded_lines(handle: BinaryIO) -> Iterator[bytes]:
    consumed = 0
    while raw := handle.readline(MAX_RECORD_BYTES + 1):
        consumed += len(raw)
        if len(raw) > MAX_RECORD_BYTES or consumed > MAX_SOURCE_BYTES:
            raise TranscriptLimitError("source exceeds record/source byte budget")
        yield raw


def _codex_event(obj: dict[str, Any]) -> dict[str, Any] | None:
    kind, payload = obj.get("type"), obj.get("payload")
    if not isinstance(payload, dict):
        return None
    ptype = payload.get("type")
    event = None
    if kind == "event_msg" and ptype in {"user_message", "agent_message"}:
        role = "user" if ptype == "user_message" else "assistant"
        text = str(payload.get("message") or "").strip()
        if text:
            event = _turn(role, _text_block(text))
    elif kind == "response_item":
        if ptype == "message" and payload.get("role") in {"user", "assistant"}:
            role = payload["role"]
            text = _codex_text(payload.get("content"))
            if text:
                event = _turn(role, _text_block(text))
                if role == "user" and text.startswith(_CODEX_META_PREFIXES):
                    event["isMeta"] = True
        elif ptype in {"custom_tool_call", "function_call"}:
            event = _turn("assistant", [{"type": "tool_use",
                "id": payload.get("call_id") or payload.get("id"),
                "name": payload.get("name") or "exec",
                "input": _tool_input(payload.get("input", payload.get("arguments", "")))}])
        elif ptype in {"custom_tool_call_output", "function_call_output"}:
            raw_output = payload.get("output")
            output = json.dumps(raw_output) if isinstance(raw_output, dict) else _codex_text(raw_output)
            event = _turn("user", [{"type": "tool_result", "tool_use_id": payload.get("call_id"),
                "content": output, "is_error": any(_codex_output_is_error(text) for text in (
                    [block.get("text", "") for block in payload.get("output", []) if isinstance(block, dict) and block.get("type") in {"text", "input_text", "output_text"}
                     and isinstance(block.get("text"), str)]
                    if isinstance(payload.get("output"), list) else [output]))}])
    if event is not None:
        event["timestamp"] = obj.get("timestamp")
    return event


# These host bookkeeping records carry no extractable turns. Recognize them
# without committing the stream to a host before its first actual turn.
_METADATA_RECORDS = frozenset({"file-history-snapshot", "queue-operation", "summary",
    "progress", "system", "last-prompt", "custom-title", "agent-name", "agent-color",
    "pr-link", "compacted"})
_CODEX_RECORDS = frozenset({"event_msg", "response_item", "session_meta", "turn_context"})
# Only named bookkeeping/reasoning types may disappear without reducing
# coverage. Unknown nested records may carry tools and must remain incomplete.
_CODEX_EVENT_TYPES = frozenset({"user_message", "agent_message"})
_CODEX_IGNORED_EVENTS = frozenset({"agent_reasoning", "token_count", "task_started",
    "task_complete", "turn_aborted", "context_compacted"})
_CODEX_RESPONSE_TYPES = frozenset({"message", "custom_tool_call", "function_call",
    "custom_tool_call_output", "function_call_output"})
_CODEX_IGNORED_RESPONSES = frozenset({"reasoning", "compaction"})


def _supported_codex_record(obj: dict[str, Any]) -> bool:
    kind, payload = obj.get("type"), obj.get("payload")
    if not isinstance(payload, dict):
        return False
    if kind in {"session_meta", "turn_context"}:
        return True
    ptype = payload.get("type")
    if kind == "event_msg":
        return isinstance(ptype, str) and ptype in _CODEX_EVENT_TYPES | _CODEX_IGNORED_EVENTS
    if kind == "response_item":
        return (isinstance(ptype, str) and ptype in _CODEX_RESPONSE_TYPES | _CODEX_IGNORED_RESPONSES
                and (ptype != "message" or payload.get("role") in ("user", "assistant", "system", "developer")))
    return False


def iter_events(path: Path, *, include_provenance: bool = False, require_complete: bool = False) -> Iterator[dict[str, Any]]:
    """One streaming parse; UI prose wins over its adjacent API representation.

    Buffer at most one prose record while choosing between representations.
    Response-only rollouts remain readable; known injected context is excluded.
    Claude pass-through is unchanged unless provenance is explicitly requested.
    """
    path = Path(path)
    consumed = 0
    try:
        fmt = UNKNOWN
        unsupported = 0
        pending = None
        pending_origin = None
        source_session = None
        source_cwd = None
        with _open_regular_binary(path) as handle:
            line_number = 0
            consumed = 0
            for raw in _bounded_lines(handle):
                line_number += 1
                consumed += len(raw)
                obj = _loads(raw.decode("utf-8", errors="replace"))
                if obj is None:
                    if raw.strip():
                        unsupported += 1
                    continue
                kind = obj.get("type")
                if isinstance(kind, str) and kind in _METADATA_RECORDS:
                    continue
                row_format = (CODEX if isinstance(kind, str) and kind in _CODEX_RECORDS
                              else CLAUDE if obj.get("role") in ("user", "assistant")
                              or isinstance(kind, str) and kind in {"user", "assistant"}
                              else UNKNOWN)
                if (row_format == UNKNOWN or fmt not in {UNKNOWN, row_format}
                        or row_format == CODEX and not _supported_codex_record(obj)):
                    unsupported += 1
                    continue
                if fmt == UNKNOWN:
                    fmt = row_format
                if fmt == CLAUDE:
                    if "message" not in obj and "content" in obj:
                        role = obj.get("role") or obj.get("type")
                        event = {k:v for k,v in obj.items() if k not in {"role", "content"}}
                        event.update({"type": role, "message": {"role": role, "content": obj["content"]}})
                    else:
                        event = dict(obj) if include_provenance else obj
                else:
                    payload = obj.get("payload")
                    if obj.get("type") == "session_meta" and isinstance(payload, dict):
                        source_session = payload.get("id") or payload.get("session_id")
                    if obj.get("type") in {"session_meta", "turn_context"} and isinstance(payload, dict):
                        source_cwd = payload.get("cwd") or source_cwd
                    event = _codex_event(obj)
                    if event is None:
                        continue
                    if source_cwd:
                        event["cwd"] = source_cwd
                if include_provenance:
                    original = event.get("_source")
                    valid_origin = (isinstance(original, dict)
                                    and original.get("host") in {CLAUDE, CODEX}
                                    and type(original.get("record_index")) is int
                                    and original["record_index"] > 0
                                    and isinstance(original.get("origin"), str)
                                    and isinstance(original.get("session_id"), (str, type(None))))
                    if not valid_origin:
                        event["_source"] = {"host": fmt, "session_id": source_session or obj.get("sessionId"),
                                             "record_index": line_number, "origin": obj.get("type"),
                                             "path": str(path.resolve())}
                    event["_container"] = {"path": str(path.resolve()), "record_index": line_number, "host": fmt}
                if fmt == CLAUDE:
                    yield event
                    continue
                blocks = event.get("message", {}).get("content", [])
                prose = len(blocks) == 1 and blocks[0].get("type") == "text"
                if event.get("isMeta"):
                    continue
                if prose:
                    origin = obj.get("type")
                    if pending is not None:
                        same_text = pending["message"]["content"][0].get("text") == blocks[0].get("text")
                        if pending["type"] == event["type"] and pending_origin != origin and same_text:
                            if origin == "event_msg":
                                pending, pending_origin = event, origin
                            continue
                        yield pending
                    pending, pending_origin = event, origin
                else:
                    if pending is not None:
                        yield pending
                        pending = pending_origin = None
                    yield event
        if pending is not None:
            yield pending
        if require_complete and unsupported:
            raise TranscriptReadError(f"unsupported transcript records: {unsupported}")
    except OSError as exc:
        if isinstance(exc, TranscriptReadError):
            raise
        if consumed or require_complete:
            raise TranscriptReadError("transcript read incomplete") from exc
        return


def normalize_to_file(src: Path, dest: Path) -> int:
    """Publish a complete reusable normalized artifact, never partial output."""
    dest = Path(dest)
    temporary = dest.with_name(f".{dest.name}.{os.getpid()}.{secrets.token_hex(4)}.part")
    count = published_bytes = 0
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            for event in iter_events(src, include_provenance=True, require_complete=True):
                serialized = json.dumps(event) + "\n"  # ensure_ascii keeps character/byte lengths equal
                published_bytes += len(serialized)
                if len(serialized) > MAX_RECORD_BYTES or published_bytes > MAX_SOURCE_BYTES:
                    raise TranscriptLimitError("normalized artifact exceeds reader byte budget")
                handle.write(serialized)
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, dest)
        return count
    finally:
        temporary.unlink(missing_ok=True)


# A normalized rollout runs to multiple MB, four Stop sweeps fire per session,
# and nothing evicts them -- an unbounded accumulator fed by every session. The
# cache is keyed by SOURCE FILE so the four sweeps share one normalization
# instead of writing four copies, and stale entries are reaped on every call.
CACHE_TTL_DAYS = 2
CACHE_MAX_BYTES = 128 * 1024 * 1024
CACHE_MAX_ENTRIES = 128


CACHE_LOCK_TIMEOUT_S = 0.5
PART_TTL_SECONDS = 300


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _cache_usage(cache_dir: Path) -> tuple[int, int]:
    # Hardlink leases retain an artifact without double-counting its inode.
    inodes = {}
    for pattern in ("normalized-*.jsonl", ".normalized-*.lease-*.jsonl", ".normalized-*.part"):
        for candidate in cache_dir.glob(pattern):
            try:
                info = candidate.stat()
                inodes[(info.st_dev, info.st_ino)] = info.st_size
            except OSError:
                continue
    return sum(inodes.values()), len(inodes)


def _reap(cache_dir: Path, ttl_days: int = CACHE_TTL_DAYS, *, keep: Path | None = None) -> None:
    import time
    now = time.time()
    for candidate in cache_dir.glob(".normalized-*"):
        try:
            if candidate.name.endswith(".part"):
                match = re.search(r"\.(\d+)\.[^.]+\.part$", candidate.name)
                live = bool(match and _pid_alive(int(match[1])))
                if not live and candidate.stat().st_mtime < now - PART_TTL_SECONDS:
                    candidate.unlink()
            elif ".lease-" in candidate.name:
                match = re.search(r"\.lease-(\d+)-", candidate.name)
                if match and not _pid_alive(int(match[1])):
                    candidate.unlink()
        except OSError:
            continue
    files = []
    for candidate in cache_dir.glob("normalized-*.jsonl"):
        try:
            info = candidate.stat()
            if info.st_mtime < now - ttl_days * 86400 and candidate != keep:
                candidate.unlink()
            else:
                files.append((info.st_mtime, candidate))
        except OSError:
            continue
    for _, candidate in sorted(files):
        size, count = _cache_usage(cache_dir)
        if size <= CACHE_MAX_BYTES and count <= CACHE_MAX_ENTRIES:
            break
        if candidate != keep:
            candidate.unlink(missing_ok=True)


def _source_revision(source: Path) -> str:
    digest = hashlib.sha256()
    consumed = 0
    with _open_regular_binary(source) as handle:
        while block := handle.read(64 * 1024):
            consumed += len(block)
            if consumed > MAX_SOURCE_BYTES:
                raise TranscriptLimitError("growing transcript exceeds source byte budget")
            digest.update(block)
    return digest.hexdigest()


def normalized_cache_path(source: Path, cache_dir: Path, *, revision: str | None = None) -> Path:
    identity = f"{Path(source).resolve()}\0{PARSER_VERSION}\0{revision or _source_revision(source)}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
    return Path(cache_dir) / f"normalized-{digest}.jsonl"


def normalize_cached(source: Path, cache_dir: Path, *, lease_owner_pid: int | None = None) -> Path | None:
    """Coalesce normalization under one owner; content and parser revisions key reuse.

    The cache lock also serializes eviction/publication, bounding aggregate bytes.
    A changed-during-read source is not published as a verified revision.
    """
    from atomic_io import LockedFile
    source, cache_dir = Path(source), Path(cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with LockedFile(cache_dir / "normalization", timeout_s=CACHE_LOCK_TIMEOUT_S):
            _reap(cache_dir)
            before = source.stat()
            revision = _source_revision(source)
            dest = normalized_cache_path(source, cache_dir, revision=revision)
            if not dest.is_file():
                if normalize_to_file(source, dest) == 0:
                    dest.unlink(missing_ok=True)
                    return None
            after = source.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                dest.unlink(missing_ok=True)
                return None
            if dest.stat().st_size > CACHE_MAX_BYTES:
                dest.unlink()
                return None
            os.utime(dest, None)
            _reap(cache_dir, keep=dest)
            size, count = _cache_usage(cache_dir)
            if size > CACHE_MAX_BYTES or count > CACHE_MAX_ENTRIES:
                dest.unlink(missing_ok=True)
                return None  # Active readers retain their revision; never borrow another.
            # Legacy path-returning callers also retain their revision until
            # their process exits. Detached workers explicitly release theirs
            # once every consumer finishes, keeping the common path shorter.
            owner = os.getpid() if lease_owner_pid is None else lease_owner_pid
            if type(owner) is not int or owner <= 0 or not _pid_alive(owner):
                return None
            lease = dest.with_name(f".{dest.stem}.lease-{owner}-shared.jsonl")
            if not lease.exists() and sum(1 for _ in cache_dir.glob(".normalized-*.lease-*.jsonl")) >= CACHE_MAX_ENTRIES:
                return None
            try:
                os.link(dest, lease)
            except FileExistsError:
                if not os.path.samefile(dest, lease):
                    return None
            return lease
    except (OSError, TimeoutError, TranscriptLimitError):
        return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path, nargs="?")
    parser.add_argument("--find-codex-session", metavar="CWD",
                        help="print the newest Codex rollout recorded for CWD, then exit")
    parser.add_argument("--out", type=Path, help="write a normalized JSONL copy here")
    parser.add_argument("--cache-dir", type=Path,
                        help="normalize into a shared, self-reaping cache and print its path")
    parser.add_argument("--lease-owner-pid", type=int, help="pin normalized bytes until the detached consumer finishes")
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
            cached = normalize_cached(found, args.cache_dir, lease_owner_pid=args.lease_owner_pid)
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
