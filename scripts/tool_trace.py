#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""OTel-shaped, redacted tool-call traces for supervision and learning.

The hook path is fail-open and dependency-free. It records bounded metadata,
hashes, and redacted previews; it never stores unbounded tool inputs/results.
Transcript reconciliation supplies a durable completed span when host hooks do
not expose a stable pre/post correlation id. Deduplication covers only the
current ledger and retained rotations; evicted call identity is unavailable.
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
from atomic_io import LockedFile, atomic_write_bytes  # noqa: E402

TRACE_PATH = Path(".build-loop/telemetry/tool-traces.jsonl")
MAX_PREVIEW_CHARS = 512
MAX_FILE_BYTES = 10 * 1024 * 1024
ROTATED_FILES = 2
# Bounds are independent of corpus growth and include every retained rotation.
MAX_INGEST_CALLS = 100_000
MAX_HISTORY_BYTES = 64 * 1024 * 1024
MAX_COVERAGE_BYTES = 128 * 1024
MAX_COVERAGE_RECEIPTS = 64
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


def _run_context(workdir: Path) -> tuple | None:
    """Read one bounded active-run snapshot; its metadata alone proves no call ownership."""
    from transcript_adapter import _open_regular_binary, _bounded_lines, TranscriptLimitError
    from rally_point.build_loop_id import resolve_run_identity
    from temporal_membership import parse_ts
    try:
        with _open_regular_binary(workdir / ".build-loop/state.json") as handle:
            state = json.loads(b"".join(_bounded_lines(handle)))
    except (OSError, ValueError, TranscriptLimitError):
        return None
    identity = resolve_run_identity(state)
    if identity.state != "active" or not identity.build_run_id or not isinstance(state, dict):
        return None
    execution = state.get("execution")
    if not isinstance(execution, dict):
        return None
    start = parse_ts(execution.get("started_at"))
    if start is None:
        return None
    sessions = {value.strip() for key in ("current_session_id", "started_by_session_id")
                if isinstance(value := execution.get(key), str) and value.strip()}
    if not sessions:
        return None
    end_value = execution.get("ended_at")
    end = parse_ts(end_value)
    if end_value is not None and (end is None or end < start):
        return None
    return identity.build_run_id, start, end, sessions


def _attached_run_id(context: tuple | None, session_id: str, timestamp: Any) -> str | None:
    from temporal_membership import parse_ts, is_member
    if context is None:
        return None
    run_id, start, end, sessions = context
    call_time = parse_ts(timestamp)
    if not session_id or session_id not in sessions or call_time is None:
        return None
    member, _ = is_member(call_time, call_time, start, end, bound_hours=0)
    return run_id if member else None


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
    run_context: tuple | None = None,
) -> dict[str, Any]:
    now_ns = time.time_ns()
    start_ns = _iso_to_unix_nano(timestamp) or now_ns
    end_ns = _iso_to_unix_nano(end_timestamp)
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": tool_name or "unknown",
        "build_loop.tool.call_id_sha256": _hash(tool_use_id),
        "session.id": session_id or "unknown",
        "build_loop.run_id": _attached_run_id(run_context, session_id, timestamp),
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
    with LockedFile(path, timeout_s=1):
        _append_span_unlocked(path, line)


def _append_span_unlocked(path: Path, line: str) -> None:
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
        run_context=_run_context(workdir),
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


def _ingest_transcript(workdir: Path, transcript: Path, session_id: str = "", *, history_coverage: dict | None = None) -> dict[str, int]:
    uses: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    session = session_id or transcript.stem
    source_sessions: set[str] = set()
    history_coverage = history_coverage if history_coverage is not None else {}
    from transcript_adapter import iter_events, TranscriptLimitError
    for ordinal, record in enumerate(iter_events(transcript, include_provenance=True, require_complete=True)):
        provenance = record.get("_source") or {}
        if isinstance(provenance.get("path"), str):
            history_coverage.setdefault("source_locator", provenance["path"])
        original_session = provenance.get("session_id")
        if isinstance(original_session, str) and original_session.strip():
            source_sessions.add(original_session.strip())
        content = (record.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id"):
                if len(uses) >= MAX_INGEST_CALLS and str(block["id"]) not in uses:
                    raise TranscriptLimitError("tool call count exceeds ingestion budget")
                origin = record.get("_source") or {}
                container = record.get("_container") or {}
                uses[str(block["id"])] = {
                    "legacy_ordinals": {ordinal, 0, *(value - 1 for value in
                        (origin.get("record_index"), container.get("record_index"))
                        if type(value) is int and value > 0)},
                    "name": str(block.get("name") or "unknown"),
                    "input": block.get("input"),
                    "timestamp": record.get("timestamp"),
                    "ordinal": ordinal,
                }
            elif block.get("type") == "tool_result" and block.get("tool_use_id"):
                if len(results) >= MAX_INGEST_CALLS and str(block["tool_use_id"]) not in results:
                    raise TranscriptLimitError("tool result count exceeds ingestion budget")
                results[str(block["tool_use_id"])] = {
                    "result": _result_text(block),
                    "is_error": bool(block.get("is_error")),
                    "timestamp": record.get("timestamp"),
                }
    if len(source_sessions) > 1 or (session_id and source_sessions and source_sessions != {session_id}):
        raise ValueError("conflicting transcript source session identity")
    if source_sessions:
        session = next(iter(source_sessions))
    path = workdir / TRACE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    history_coverage = history_coverage if history_coverage is not None else {}
    history_coverage.update({"deduplication_window": "current_plus_retained_rotations",
                             "retained_rotations": ROTATED_FILES,
                             "older_call_identity": "unavailable",
                             "aggregate_read_budget_bytes": MAX_HISTORY_BYTES,
                             "invalid_records": 0, "legacy_unmatched_records": 0})
    context = _run_context(workdir)
    history_coverage["run_id"] = context[0] if context else None
    # Retain identities only for this bounded source, never an entire history set.
    incoming = {}
    legacy = {}
    for tool_use_id, use in uses.items():
        phase = "reconciled" if tool_use_id in results else "observed"
        key = (session, _hash(tool_use_id), phase)
        incoming[key] = {**use, "id": tool_use_id}
        for ordinal in use["legacy_ordinals"]:
            legacy[_span_id(session, tool_use_id, use["name"], ordinal)] = key
    existing = set()
    written = 0
    # The existing writer lock covers snapshot+dedup+publication, so concurrent
    # reconciliations cannot both decide that the same semantic event is new.
    with LockedFile(path, timeout_s=1):
        remaining = [MAX_HISTORY_BYTES]
        for history in [path, *(path.with_suffix(path.suffix + f".{i}") for i in range(1, ROTATED_FILES + 1))]:
            for row in _iter_trace_rows(history, remaining=remaining, diagnostics=history_coverage):
                attrs = row.get("attributes") or {}
                if (not isinstance(attrs, dict) or not isinstance(attrs.get("session.id"), str)
                        or not isinstance(attrs.get("gen_ai.tool.name"), str)
                        or not isinstance(row.get("event"), str)):
                    history_coverage["invalid_records"] += 1
                    continue
                if not _valid_span_status(row):
                    history_coverage["invalid_records"] += 1
                    continue
                call_hash = attrs.get("build_loop.tool.call_id_sha256")
                if not isinstance(call_hash, str):
                    old_call_id = attrs.get("gen_ai.tool.call.id")
                    call_hash = _hash(old_call_id) if isinstance(old_call_id, str) else None
                span_id = row.get("span_id")
                if not isinstance(span_id, str):
                    history_coverage["invalid_records"] += 1
                    continue
                key = (attrs.get("session.id"), call_hash, row.get("event")) if call_hash else legacy.get(span_id)
                if key is None:
                    history_coverage["legacy_unmatched_records"] += 1
                if key in incoming and attrs.get("session.id") == session and row.get("event") == key[2]:
                    existing.add(key)
        for key, use in incoming.items():
            if key in existing:
                continue
            phase = key[2]
            tool_use_id = use["id"]
            result = results.get(tool_use_id, {})
            span = build_span(
                workdir=workdir, run_context=context, session_id=session,
                tool_name=use["name"], tool_use_id=tool_use_id, phase=phase,
                tool_input=use.get("input"), tool_result=result.get("result"),
                is_error=bool(result.get("is_error")), timestamp=use.get("timestamp"),
                end_timestamp=result.get("timestamp"), ordinal=0,
            )
            _append_span_unlocked(path, json.dumps(span, sort_keys=True, separators=(",", ":")) + "\n")
            existing.add(key)
            written += 1
    return {"seen": len(uses), "written": written}


def _coverage_path(path: Path) -> Path:
    return path.with_suffix(".coverage.json")


def _read_receipts(path: Path) -> dict[str, Any]:
    from transcript_adapter import _open_regular_binary
    try:
        with _open_regular_binary(path) as handle:
            data = handle.read(MAX_COVERAGE_BYTES + 1)
        if len(data) > MAX_COVERAGE_BYTES:
            raise ValueError("coverage receipt byte budget exceeded")
        value = json.loads(data)
        if not isinstance(value, dict) or not isinstance(value.get("receipts"), list):
            raise ValueError("malformed coverage receipt")
        return value
    except FileNotFoundError:
        return {"receipts": [], "status": "absent"}
    except (OSError, ValueError, RecursionError):
        return {"receipts": [], "status": "unavailable"}


def _ingestion_receipt(workdir: Path, transcript: Path, session_id: str, *,
                       result: dict | None = None, error: Exception | None = None,
                       history_coverage: dict | None = None, source_locator: str | None = None) -> None:
    from transcript_adapter import TranscriptLimitError
    target = _coverage_path(workdir / TRACE_PATH)
    locator = str(Path(source_locator or (history_coverage or {}).get("source_locator") or transcript).absolute())
    reason = ("input_limit" if isinstance(error, TranscriptLimitError) else
              "identity_or_input_rejected" if isinstance(error, ValueError) else
              "source_or_history_unavailable" if error else None)
    row = {"source_key": _hash(locator), "source_locator": locator[:512],
           "declared_session_id": session_id[:180],
           "run_id": (history_coverage or {}).get("run_id"),
           "status": "skipped" if error else "ingested", "reason": reason,
           "error_type": type(error).__name__ if error else None,
           "updated_at_unix": int(time.time()), "counts": result, "history": history_coverage}
    try:
        with LockedFile(target, timeout_s=0.25):
            previous = _read_receipts(target)
            rows = [value for value in previous["receipts"] if isinstance(value, dict)
                    and value.get("source_key") != row["source_key"]]
            rows.append(row)
            rows = rows[-MAX_COVERAGE_RECEIPTS:]
            payload = {"schema": "build-loop.tool-ingestion-coverage.v1", "status": "observed_sources_only",
                       "retention_limit": MAX_COVERAGE_RECEIPTS, "receipts": rows}
            encoded = json.dumps(payload, sort_keys=True).encode()
            while len(encoded) > MAX_COVERAGE_BYTES and len(rows) > 1:
                rows.pop(0)
                encoded = json.dumps(payload, sort_keys=True).encode()
            if len(encoded) > MAX_COVERAGE_BYTES:
                raise ValueError("coverage receipt byte budget exceeded")
            atomic_write_bytes(target, encoded)
    except (OSError, ValueError, TimeoutError):
        print("tool_trace: ingestion receipt unavailable", file=sys.stderr)


def ingest_transcript(workdir: Path, transcript: Path, session_id: str = "", *, source_locator: str | None = None) -> dict[str, int]:
    history_coverage = {}
    try:
        result = _ingest_transcript(workdir, transcript, session_id, history_coverage=history_coverage)
    except Exception as error:
        _ingestion_receipt(workdir, transcript, session_id, error=error, history_coverage=history_coverage, source_locator=source_locator)
        raise
    _ingestion_receipt(workdir, transcript, session_id, result=result, history_coverage=history_coverage, source_locator=source_locator)
    return result


def _valid_span_status(row: dict[str, Any]) -> bool:
    """Only explicit terminal outcomes authorize completed-call accounting."""
    status = row.get("status")
    allowed = {"OK", "ERROR"} if row.get("event") == "reconciled" else {"OK", "ERROR", "UNSET"}
    code = status.get("code") if isinstance(status, dict) else None
    return isinstance(code, str) and code in allowed


def _iter_trace_rows(path: Path, *, remaining: list[int] | None = None, diagnostics: dict | None = None):
    """Stream bounded records; resource/IO failures remain explicit, not empty coverage."""
    from transcript_adapter import _open_regular_binary, _bounded_lines
    try:
        with _open_regular_binary(path) as handle:
            for line in _bounded_lines(handle):
                if remaining is not None:
                    remaining[0] -= len(line)
                    if remaining[0] < 0:
                        from transcript_adapter import TranscriptLimitError
                        raise TranscriptLimitError("retained trace history exceeds aggregate read budget")
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    if diagnostics is not None:
                        diagnostics["invalid_records"] += 1
                    continue
                if isinstance(row, dict):
                    yield row
                elif diagnostics is not None:
                    diagnostics["invalid_records"] += 1
    except FileNotFoundError:
        return


def ingestion_incomplete(summary: dict) -> bool:
    """Known ingestion failures invalidate a zero-error inference; absence is unknown."""
    coverage = summary.get("coverage") or {}
    ingestion = coverage.get("ingestion") or {}
    if ingestion.get("status") == "unavailable":
        return True
    run_id = coverage.get("run_filter")
    cutoff = coverage.get("recent_cutoff_unix")
    for receipt in ingestion.get("receipts") or []:
        if not isinstance(receipt, dict):
            return True
        if run_id and receipt.get("run_id") not in (None, run_id):
            continue
        if cutoff and isinstance(receipt.get("updated_at_unix"), int) and receipt["updated_at_unix"] < cutoff:
            continue
        if receipt.get("status") != "ingested":
            return True
    return bool(coverage.get("invalid_trace_records_in_window", coverage.get("invalid_trace_records")))


def summarize_path(
    path: Path,
    run_id: str | None = None,
    *,
    recent_seconds: int | None = None,
) -> dict[str, Any]:
    count = errors = rate_limits = 0
    tools: Counter = Counter()
    hashes: Counter = Counter()
    durations: list[float] = []
    diagnostics = {"invalid_records": 0, "invalid_records_in_window": 0}
    cutoff_ns = time.time_ns() - recent_seconds * 1_000_000_000 if recent_seconds is not None else None
    remaining = [MAX_HISTORY_BYTES]
    retained = [path, *(path.with_suffix(path.suffix + f".{i}") for i in range(1, ROTATED_FILES + 1))]
    def retained_rows():
        for history in retained:
            try:
                source_recent = cutoff_ns is None or history.stat().st_mtime_ns >= cutoff_ns
            except FileNotFoundError:
                continue
            invalid = {"invalid_records": 0}
            for row in _iter_trace_rows(history, remaining=remaining, diagnostics=invalid):
                yield row, source_recent
            diagnostics["invalid_records"] += invalid["invalid_records"]
            if source_recent:
                # Broken JSON cannot establish a run/time. File mtime bounds
                # its possible recency conservatively, including rotations.
                diagnostics["invalid_records_in_window"] += invalid["invalid_records"]
    for row, source_recent in retained_rows():
        attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        status = row.get("status")
        if (not isinstance(attrs.get("gen_ai.tool.name"), str) or not _valid_span_status(row)
                or not isinstance(attrs.get("gen_ai.tool.call.arguments.sha256"), (str, type(None)))):
            diagnostics["invalid_records"] += 1
            timestamp = row.get("end_time_unix_nano") or row.get("start_time_unix_nano")
            relevant_run = not run_id or attrs.get("build_loop.run_id") in (None, run_id)
            relevant_time = cutoff_ns is None or (timestamp >= cutoff_ns if type(timestamp) is int else source_recent)
            if relevant_run and relevant_time:
                diagnostics["invalid_records_in_window"] += 1
            continue
        if row.get("event") != "reconciled":
            continue
        if run_id and attrs.get("build_loop.run_id") != run_id:
            continue
        if recent_seconds is not None:
            end_ns = row.get("end_time_unix_nano") or row.get("start_time_unix_nano")
            if not isinstance(end_ns, int) or end_ns < time.time_ns() - recent_seconds * 1_000_000_000:
                continue
        count += 1
        tools[attrs.get("gen_ai.tool.name", "unknown")] += 1
        errors += status.get("code") == "ERROR"
        rate_limits += bool(RATE_LIMIT.search(str(attrs.get("gen_ai.tool.call.result.preview", ""))))
        hashes[(attrs.get("gen_ai.tool.name"), attrs.get("gen_ai.tool.call.arguments.sha256"))] += 1
        if isinstance(row.get("duration_ms"), (int, float)):
            durations.append(float(row["duration_ms"]))
    durations.sort()
    retries = sum(max(0, count - 1) for count in hashes.values())
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None
    return {
        "tool_calls": count,
        "tool_errors": errors,
        "error_rate": round(errors / count, 4) if count else 0.0,
        "repeated_calls": retries,
        "provider_429s": rate_limits,
        "p95_duration_ms": p95,
        "tools": dict(tools.most_common(12)),
        "coverage": {"source": "observed_reconciled_spans", "complete_tool_coverage": "unavailable",
                     "source_available": any(history.is_file() for history in retained), "run_filter": run_id,
                     "retained_rotations": ROTATED_FILES,
                     "recent_cutoff_unix": int(time.time()) - recent_seconds if recent_seconds is not None else None,
                     "ingestion": _read_receipts(_coverage_path(path)),
                     "invalid_trace_records": diagnostics["invalid_records"],
                     "invalid_trace_records_in_window": diagnostics["invalid_records_in_window"]},
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
    ingest.add_argument("--source-locator", help="Original source path when ingesting a normalized lease")
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
                ingest_transcript(workdir, Path(transcript_value), args.session_id or str(payload.get("session_id") or ""), source_locator=args.source_locator)
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
