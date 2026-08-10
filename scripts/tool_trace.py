#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""OTel-shaped, redacted tool-call traces for supervision and learning.

The hook path is fail-open and dependency-free. It records bounded metadata,
hashes, and redacted previews; it never stores unbounded tool inputs/results.
Transcript reconciliation supplies a durable completed span when host hooks do
not expose a stable pre/post correlation id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import LockedFile  # noqa: E402

TRACE_PATH = Path(".build-loop/telemetry/tool-traces.jsonl")
MAX_PREVIEW_CHARS = 512
MAX_FILE_BYTES = 10 * 1024 * 1024
ROTATED_FILES = 2
SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|api[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)
SENSITIVE_VALUE = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._~+/=-]+|\b(?:sk(?:-[a-z0-9]+)*|ghp|github_pat)[_-][a-z0-9_-]{8,}\b"
)
SENSITIVE_INLINE = re.compile(
    r"(?i)\b((?:[a-z0-9]+[_-])*(?:authorization|cookie|password|passwd|secret|token|"
    r"api[_ -]?key|private[_ -]?key|credential))\b(\s*[:=]\s*)(?:bearer\s+)?"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s'\",;}\]]+)"
)
RATE_LIMIT = re.compile(r"(?:\b429\b|rate.?limit)", re.IGNORECASE)
FAILED_PROCESS = re.compile(
    r"(?i)(?:process|script|command)\s+(?:exited|failed).*?(?:code|status)?\s*[=:]?\s*([1-9]\d*)|"
    r"(?:exited|exit status)\s+(?:with\s+)?(?:code\s+)?([1-9]\d*)"
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _trace_id(session_id: str) -> str:
    return _hash(session_id or "unknown-session")[:32]


def _span_id(session_id: str, tool_use_id: str, tool_name: str, ordinal: int = 0) -> str:
    return _hash(f"{session_id}|{tool_use_id}|{tool_name}|{ordinal}")[:16]


def _redact(value: Any, *, key: str = "") -> Any:
    if SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value[:50]]
    if isinstance(value, str):
        sanitized = SENSITIVE_INLINE.sub(r"\1\2[REDACTED]", value)
        return SENSITIVE_VALUE.sub(
            lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]",
            sanitized,
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(type(value).__name__)


def _content_attributes(prefix: str, value: Any) -> dict[str, Any]:
    redacted = _redact(value)
    encoded = json.dumps(redacted, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    preview = encoded[:MAX_PREVIEW_CHARS]
    attrs: dict[str, Any] = {
        f"{prefix}.bytes": len(encoded.encode("utf-8")),
        f"{prefix}.sha256": _hash(encoded),
        f"{prefix}.preview": preview,
        f"{prefix}.truncated": len(encoded) > MAX_PREVIEW_CHARS,
    }
    if isinstance(redacted, dict):
        attrs[f"{prefix}.keys"] = sorted(redacted)[:50]
    return attrs


def _run_id(workdir: Path) -> str | None:
    try:
        state = json.loads((workdir / ".build-loop/state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    execution = state.get("execution") if isinstance(state, dict) else {}
    return (execution or {}).get("run_id") or state.get("run_id") or state.get("build_loop_id")


def _iso_to_unix_nano(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1_000_000_000)
    except ValueError:
        return None


def build_span(
    *,
    workdir: Path,
    session_id: str,
    tool_name: str,
    tool_use_id: str,
    phase: str,
    tool_input: Any = None,
    tool_result: Any = None,
    is_error: bool = False,
    timestamp: Any = None,
    end_timestamp: Any = None,
    ordinal: int = 0,
) -> dict[str, Any]:
    now_ns = time.time_ns()
    start_ns = _iso_to_unix_nano(timestamp) or now_ns
    end_ns = _iso_to_unix_nano(end_timestamp)
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": tool_name or "unknown",
        "session.id": session_id or "unknown",
        "build_loop.run_id": _run_id(workdir),
        "build_loop.tool.phase": phase,
    }
    if tool_input is not None:
        attributes.update(_content_attributes("gen_ai.tool.call.arguments", tool_input))
    if tool_result is not None:
        attributes.update(_content_attributes("gen_ai.tool.call.result", tool_result))
    return {
        "schema": "build-loop.otel-tool-span.v1",
        "trace_id": _trace_id(session_id),
        "span_id": _span_id(session_id, tool_use_id, tool_name, ordinal),
        "parent_span_id": None,
        "name": f"execute_tool {tool_name or 'unknown'}",
        "kind": "INTERNAL",
        "event": phase,
        "start_time_unix_nano": start_ns,
        "end_time_unix_nano": end_ns,
        "duration_ms": round((end_ns - start_ns) / 1_000_000, 3) if end_ns and end_ns >= start_ns else None,
        "status": {"code": "ERROR" if is_error else "OK" if phase in {"end", "reconciled"} else "UNSET"},
        "attributes": attributes,
    }


def _rotate(path: Path) -> None:
    if not path.exists() or path.stat().st_size < MAX_FILE_BYTES:
        return
    oldest = path.with_suffix(path.suffix + f".{ROTATED_FILES}")
    if oldest.exists():
        oldest.unlink()
    for index in range(ROTATED_FILES - 1, 0, -1):
        source = path.with_suffix(path.suffix + f".{index}")
        target = path.with_suffix(path.suffix + f".{index + 1}")
        if source.exists():
            source.replace(target)
    path.replace(path.with_suffix(path.suffix + ".1"))


def append_span(workdir: Path, span: dict[str, Any]) -> None:
    path = workdir / TRACE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(span, sort_keys=True, separators=(",", ":")) + "\n"
    with LockedFile(path):
        _rotate(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


def _payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        value = json.loads(raw) if raw.strip() else {}
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def record_hook(workdir: Path, phase: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    if not tool_name:
        return None
    session_id = str(payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or "unknown")
    tool_use_id = str(payload.get("tool_use_id") or payload.get("tool_call_id") or "")
    result = payload.get("tool_response", payload.get("tool_result"))
    is_error = phase == "error" or bool(payload.get("is_error"))
    span = build_span(
        workdir=workdir,
        session_id=session_id,
        tool_name=tool_name,
        tool_use_id=tool_use_id,
        phase=phase,
        tool_input=payload.get("tool_input"),
        tool_result=result,
        is_error=is_error,
        timestamp=payload.get("timestamp"),
    )
    append_span(workdir, span)
    return span


def _result_text(block: dict[str, Any]) -> Any:
    return block.get("content") if "content" in block else block.get("result")


def _decode_maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def _contains_error(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("is_error") is True or value.get("error") or value.get("type") == "error":
            return True
        return any(_contains_error(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_error(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        compact = lowered.replace(" ", "")
        return (
            '"is_error":true' in compact
            or '"type":"error"' in compact
            or bool(FAILED_PROCESS.search(value))
        )
    return False


def ingest_transcript(workdir: Path, transcript: Path, session_id: str = "") -> dict[str, int]:
    uses: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    session = session_id or transcript.stem
    try:
        lines = transcript.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"seen": 0, "written": 0}
    for ordinal, line in enumerate(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        payload = record.get("payload") if record.get("type") == "response_item" else None
        if isinstance(payload, dict):
            block_type = payload.get("type")
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            if block_type in {"function_call", "custom_tool_call"} and call_id:
                uses[call_id] = {
                    "name": str(payload.get("name") or "unknown"),
                    "input": _decode_maybe_json(payload.get("arguments", payload.get("input"))),
                    "timestamp": record.get("timestamp"),
                    "ordinal": ordinal,
                }
            elif block_type in {"function_call_output", "custom_tool_call_output"} and call_id:
                output = payload.get("output")
                results[call_id] = {
                    "result": output,
                    "is_error": _contains_error(output),
                    "timestamp": record.get("timestamp"),
                }
            continue
        content = (record.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id"):
                uses[str(block["id"])] = {
                    "name": str(block.get("name") or "unknown"),
                    "input": block.get("input"),
                    "timestamp": record.get("timestamp"),
                    "ordinal": ordinal,
                }
            elif block.get("type") == "tool_result" and block.get("tool_use_id"):
                results[str(block["tool_use_id"])] = {
                    "result": _result_text(block),
                    "is_error": bool(block.get("is_error")),
                    "timestamp": record.get("timestamp"),
                }
    path = workdir / TRACE_PATH
    existing: set[tuple[str, str]] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            existing.add((str(row.get("span_id")), str(row.get("event"))))
    written = 0
    for tool_use_id, use in uses.items():
        result = results.get(tool_use_id, {})
        span = build_span(
            workdir=workdir,
            session_id=session,
            tool_name=use["name"],
            tool_use_id=tool_use_id,
            phase="reconciled",
            tool_input=use.get("input"),
            tool_result=result.get("result"),
            is_error=bool(result.get("is_error")),
            timestamp=use.get("timestamp"),
            end_timestamp=result.get("timestamp"),
            ordinal=int(use.get("ordinal", 0)),
        )
        if (span["span_id"], span["event"]) in existing:
            continue
        append_span(workdir, span)
        existing.add((span["span_id"], span["event"]))
        written += 1
    return {"seen": len(uses), "written": written}


def summarize_path(
    path: Path,
    run_id: str | None = None,
    *,
    recent_seconds: int | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            attrs = row.get("attributes") if isinstance(row, dict) else {}
            if row.get("event") != "reconciled":
                continue
            if run_id and (attrs or {}).get("build_loop.run_id") != run_id:
                continue
            if recent_seconds is not None:
                end_ns = row.get("end_time_unix_nano") or row.get("start_time_unix_nano")
                if not isinstance(end_ns, int) or end_ns < time.time_ns() - recent_seconds * 1_000_000_000:
                    continue
            rows.append(row)
    tools = Counter((row.get("attributes") or {}).get("gen_ai.tool.name", "unknown") for row in rows)
    errors = sum((row.get("status") or {}).get("code") == "ERROR" for row in rows)
    durations = sorted(float(row["duration_ms"]) for row in rows if isinstance(row.get("duration_ms"), (int, float)))
    rate_limits = sum(
        bool(RATE_LIMIT.search(str((row.get("attributes") or {}).get("gen_ai.tool.call.result.preview", ""))))
        for row in rows
    )
    hashes = Counter(
        (
            (row.get("attributes") or {}).get("gen_ai.tool.name"),
            (row.get("attributes") or {}).get("gen_ai.tool.call.arguments.sha256"),
        )
        for row in rows
    )
    retries = sum(max(0, count - 1) for count in hashes.values())
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None
    return {
        "tool_calls": len(rows),
        "tool_errors": errors,
        "error_rate": round(errors / len(rows), 4) if rows else 0.0,
        "repeated_calls": retries,
        "provider_429s": rate_limits,
        "p95_duration_ms": p95,
        "tools": dict(tools.most_common(12)),
    }


def summarize(
    workdir: Path,
    run_id: str | None = None,
    *,
    recent_seconds: int | None = None,
) -> dict[str, Any]:
    return summarize_path(workdir / TRACE_PATH, run_id, recent_seconds=recent_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser("hook")
    hook.add_argument("--phase", choices=("start", "end", "error"), required=True)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--transcript")
    ingest.add_argument("--session-id", default="")
    summary = sub.add_parser("summarize")
    summary.add_argument("--run-id")
    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve()
    try:
        if args.command == "hook":
            record_hook(workdir, args.phase, _payload())
            return 0
        if args.command == "ingest":
            payload = _payload()
            transcript_value = args.transcript or payload.get("transcript_path") or os.environ.get("CLAUDE_TRANSCRIPT_PATH") or os.environ.get("CODEX_TRANSCRIPT_PATH")
            if transcript_value:
                ingest_transcript(workdir, Path(transcript_value), args.session_id or str(payload.get("session_id") or ""))
            return 0
        print(json.dumps({"ok": True, **summarize(workdir, args.run_id)}, sort_keys=True))
        return 0
    except Exception as exc:
        if args.command == "summarize":
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 2
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
