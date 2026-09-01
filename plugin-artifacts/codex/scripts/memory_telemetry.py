# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Memory-read / memory-write telemetry writer.

Writes append-only rows to the canonical build-loop-memory telemetry file recording
when a memory fact was read or written and what effect (if any) it had on
the build-loop's downstream behavior. Distinct from
the lane `INDEX.jsonl` files (M5 discovery indexes, owned by
``scripts/memory_index.py``, schema ``action: write|update|delete``) which
this module does NOT touch.

Why a separate file:
    Audit §5 + Codex VARIANCE at 13:47 PDT 2026-05-20 flagged that today
    we record memory WAS READ but not whether the read CHANGED ANYTHING.
    INDEX.jsonl's existing schema (action enum: write/update/delete) is
    preserved untouched; usefulness telemetry lives in a separate file
    with its own schema_version so we can evolve the effect vocabulary
    without breaking M5 discovery readers.

Effect enum (read-side):
    changed_plan       — read caused the orchestrator/agent to revise its plan
    changed_routing    — read caused a different agent/tier/dispatch decision
    added_check        — read caused a new gate/criterion/check to fire
    informed_decision  — read informed a synthesis or design decision without
                         changing routing
    ignored            — read returned a result but the consumer did not act
                         on it
    stale              — read returned a fact that turned out to be obsolete
                         (file moved, code refactored, etc.)

The writer wraps each row with provenance (phase, reader_or_writer agent
identity, query that surfaced the fact). Effect is reported AFTER the
consumer acts on the fact — callers may emit a `memory-read` row first
with ``effect: null`` and a follow-up `memory-effect` row once outcome is
known. The follow-up row's `correlation_id` joins back to the original.

Contract:
    - Fire-and-forget per the M5 + Rally Point pattern; never raise into the
      caller.
    - Append-only; never rewrites rows.
    - Uses fcntl.flock for cross-process safety on macOS/Linux.

Zero dependencies. Python 3.11+.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from _paths import memory_indexes_dir  # type: ignore  # noqa: E402
from atomic_io import LockedFile  # type: ignore  # noqa: E402

LOCK_TIMEOUT_S = 5


VALID_SOURCES = {"runtime", "test", "hook", "interactive", "background"}


def telemetry_source(explicit: str | None = None) -> str:
    """Classify the event stream without making every caller test-aware."""
    source = explicit or os.environ.get("BUILD_LOOP_TELEMETRY_SOURCE")
    if source in VALID_SOURCES:
        return source
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "test"
    return "runtime"


def default_telemetry_path(source: str | None = None) -> Path:
    """Return a runtime or isolated test stream path.

    Pytest sets ``PYTEST_CURRENT_TEST`` after module import, so callers must use
    this function at emit time instead of relying only on the compatibility
    constant below.
    """
    resolved_source = telemetry_source(source)
    if resolved_source == "test":
        override = os.environ.get("BUILD_LOOP_TEST_TELEMETRY_PATH")
        if override:
            return Path(override).expanduser()
        return Path(tempfile.gettempdir()) / "build-loop-memory-telemetry-tests" / f"{os.getpid()}.jsonl"
    return memory_indexes_dir() / "TELEMETRY.jsonl"


DEFAULT_TELEMETRY_PATH = memory_indexes_dir() / "TELEMETRY.jsonl"
SCHEMA_VERSION = "1.1"

KIND_READ = "memory-read"
KIND_WRITE = "memory-write"
KIND_EFFECT = "memory-effect"
KIND_USE = "memory-use"
VALID_KINDS = {KIND_READ, KIND_WRITE, KIND_EFFECT, KIND_USE}

VALID_EFFECTS = {
    "changed_plan",
    "changed_routing",
    "added_check",
    "informed_decision",
    "ignored",
    "stale",
}


def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _correlation_id() -> str:
    return f"mt-{secrets.token_hex(4)}"


def _write_all(fd: int, payload: bytes) -> None:
    """Write every byte of ``payload`` to ``fd``.

    os.write may write short. Under O_APPEND a partial write leaves a truncated
    JSONL row that no reader can parse, so the retry loop is load-bearing rather
    than defensive.
    """
    remaining = memoryview(payload)
    while remaining:
        written = os.write(fd, remaining)
        if written == 0:
            raise OSError("telemetry append wrote zero bytes")
        remaining = remaining[written:]


def _append_row(path: Path, row: dict[str, Any]) -> None:
    """Append one row under a sidecar lock. Fire-and-forget — swallows errors.

    Routes through atomic_io.LockedFile (timeout_s=LOCK_TIMEOUT_S). LockedFile
    raises TimeoutError on lock-acquisition timeout; the outer except swallows
    it + logs, preserving the best-effort give-up-rather-than-block contract.
    A single ``O_APPEND`` write avoids the previous O(file-size) read + rewrite.
    """
    try:
        line = (json.dumps(row, separators=(",", ":"), default=str) + "\n").encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        with LockedFile(path, timeout_s=LOCK_TIMEOUT_S):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                _write_all(fd, line)
            finally:
                os.close(fd)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget by contract
        print(f"WARN: memory_telemetry append failed: {exc}", file=sys.stderr)


def emit_read(
    *,
    phase: str,
    reader: str,
    query: str,
    memory_ids_seen: list[str],
    memory_ids_used: list[str] | None = None,
    effect: str | None = None,
    reason: str = "",
    telemetry_path: Path | None = None,
    source: str | None = None,
    engine: str | None = None,
    returned_paths: list[str] | None = None,
    latency_ms: float | None = None,
    zero_result: bool | None = None,
    ranks: list[int] | None = None,
    scores: list[float | None] | None = None,
    shown_count: int | None = None,
    session_id: str | None = None,
) -> str:
    """Emit a `memory-read` row. Returns a correlation_id for follow-up effect rows.

    `effect` may be left ``None`` at read time; the consumer can emit a follow-up
    ``memory-effect`` row once the outcome is known, joining via correlation_id.
    """
    if effect is not None and effect not in VALID_EFFECTS:
        # Coerce-and-log rather than raise — fire-and-forget per contract.
        print(
            f"WARN: memory_telemetry invalid effect {effect!r}; coercing to 'informed_decision'",
            file=sys.stderr,
        )
        effect = "informed_decision"

    cid = _correlation_id()
    resolved_source = telemetry_source(source)
    row: dict[str, Any] = {
        "ts": _iso_utc(),
        "kind": KIND_READ,
        "schema_version": SCHEMA_VERSION,
        "correlation_id": cid,
        "phase": phase,
        "reader_or_writer": reader,
        "query": query,
        "memory_ids_seen": list(memory_ids_seen),
        "memory_ids_used": list(memory_ids_used or []),
        "effect": effect,
        "reason": reason,
        "source": resolved_source,
        "engine": engine,
        "returned_paths": list(returned_paths or []),
        "latency_ms": latency_ms,
        "zero_result": bool(zero_result) if zero_result is not None else not memory_ids_seen,
    }
    # Exposure record for propensity. `ranks[i]` is the position that
    # `memory_ids_seen[i]` was shown at, `scores[i]` its relevance score, and
    # `shown_count` how many candidates the caller received.
    #
    # These exist so a later `memory-use` row can be DEBIASED. Lower-ranked
    # items are examined less regardless of their relevance, so "this memory was
    # opened" is not equal evidence at rank 0 and rank 40; treating it as equal
    # builds a rich-get-richer loop where the ranker learns to promote whatever
    # it already promoted. Propensity cannot be reconstructed after the fact,
    # so it is captured at surface time or not at all.
    if ranks is not None:
        row["ranks"] = list(ranks)
    if scores is not None:
        row["scores"] = list(scores)
    if shown_count is not None:
        row["shown_count"] = int(shown_count)
    # Attribution. The store is shared and several agents run concurrently, so a
    # join on time alone credits one session's activity to another session's read.
    if session_id is not None:
        row["session_id"] = str(session_id)
    _append_row(telemetry_path or default_telemetry_path(resolved_source), row)
    return cid


def emit_write(
    *,
    phase: str,
    writer: str,
    memory_id: str,
    why_durable: str,
    action: str = "write",
    telemetry_path: Path | None = None,
    source: str | None = None,
) -> str:
    """Emit a `memory-write` row.

    `action` is informational ("write" | "update"); the canonical action enum
    lives in M5 INDEX.jsonl. `why_durable` is the writer's justification for
    persisting this lesson (must be non-empty for the row to be useful).
    """
    cid = _correlation_id()
    resolved_source = telemetry_source(source)
    row: dict[str, Any] = {
        "ts": _iso_utc(),
        "kind": KIND_WRITE,
        "schema_version": SCHEMA_VERSION,
        "correlation_id": cid,
        "phase": phase,
        "reader_or_writer": writer,
        "memory_id": memory_id,
        "action": action,
        "why_durable": why_durable,
        "source": resolved_source,
    }
    _append_row(telemetry_path or default_telemetry_path(resolved_source), row)
    return cid


def emit_effect(
    *,
    correlation_id: str,
    effect: str,
    reason: str = "",
    telemetry_path: Path | None = None,
    source: str | None = None,
) -> None:
    """Emit a follow-up `memory-effect` row joining back to an earlier read/write."""
    if effect not in VALID_EFFECTS:
        print(
            f"WARN: memory_telemetry invalid effect {effect!r}; coercing to 'informed_decision'",
            file=sys.stderr,
        )
        effect = "informed_decision"
    resolved_source = telemetry_source(source)
    row: dict[str, Any] = {
        "ts": _iso_utc(),
        "kind": KIND_EFFECT,
        "schema_version": SCHEMA_VERSION,
        "correlation_id": correlation_id,
        "effect": effect,
        "reason": reason,
        "source": resolved_source,
    }
    _append_row(telemetry_path or default_telemetry_path(resolved_source), row)


def emit_use(
    *,
    correlation_id: str,
    memory_ids_used: list[str],
    files_read: list[str],
    effect: str | None = None,
    reason: str = "",
    telemetry_path: Path | None = None,
    source: str | None = None,
) -> None:
    """Record which returned paths a consumer actually opened or used."""
    if effect is not None and effect not in VALID_EFFECTS:
        print(
            f"WARN: memory_telemetry invalid effect {effect!r}; coercing to 'informed_decision'",
            file=sys.stderr,
        )
        effect = "informed_decision"
    resolved_source = telemetry_source(source)
    row: dict[str, Any] = {
        "ts": _iso_utc(),
        "kind": KIND_USE,
        "schema_version": SCHEMA_VERSION,
        "correlation_id": correlation_id,
        "memory_ids_used": list(memory_ids_used),
        "files_read": list(files_read),
        "effect": effect,
        "reason": reason,
        "source": resolved_source,
    }
    _append_row(telemetry_path or default_telemetry_path(resolved_source), row)


def read_rows(path: Path | None = None) -> list[dict[str, Any]]:
    """Read all telemetry rows. Used by tests + Phase 6 Learn aggregation."""
    p = path or default_telemetry_path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"WARN: memory_telemetry malformed row: {exc}", file=sys.stderr)
    return out
