"""Memory-read / memory-write telemetry writer.

Writes append-only rows to ``~/.build-loop/memory/TELEMETRY.jsonl`` recording
when a memory fact was read or written and what effect (if any) it had on
the build-loop's downstream behavior. Distinct from
``~/.build-loop/memory/INDEX.jsonl`` (M5 discovery index, owned by
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

import fcntl
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOCK_TIMEOUT_S = 5
DEFAULT_TELEMETRY_PATH = Path.home() / ".build-loop" / "memory" / "TELEMETRY.jsonl"
SCHEMA_VERSION = "1.0"

KIND_READ = "memory-read"
KIND_WRITE = "memory-write"
KIND_EFFECT = "memory-effect"
VALID_KINDS = {KIND_READ, KIND_WRITE, KIND_EFFECT}

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


def _append_row(path: Path, row: dict[str, Any]) -> None:
    """Atomic append with sidecar flock. Fire-and-forget — swallows errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        line = json.dumps(row, separators=(",", ":")) + "\n"
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        with open(lock_path, "a+") as lock_fh:
            while True:
                try:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        # Best-effort; give up rather than block the consumer.
                        print(
                            f"WARN: memory_telemetry lock timeout after {LOCK_TIMEOUT_S}s on {lock_path}",
                            file=sys.stderr,
                        )
                        return
                    time.sleep(0.02)
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
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
    }
    _append_row(telemetry_path or DEFAULT_TELEMETRY_PATH, row)
    return cid


def emit_write(
    *,
    phase: str,
    writer: str,
    memory_id: str,
    why_durable: str,
    action: str = "write",
    telemetry_path: Path | None = None,
) -> str:
    """Emit a `memory-write` row.

    `action` is informational ("write" | "update"); the canonical action enum
    lives in M5 INDEX.jsonl. `why_durable` is the writer's justification for
    persisting this lesson (must be non-empty for the row to be useful).
    """
    cid = _correlation_id()
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
    }
    _append_row(telemetry_path or DEFAULT_TELEMETRY_PATH, row)
    return cid


def emit_effect(
    *,
    correlation_id: str,
    effect: str,
    reason: str = "",
    telemetry_path: Path | None = None,
) -> None:
    """Emit a follow-up `memory-effect` row joining back to an earlier read/write."""
    if effect not in VALID_EFFECTS:
        print(
            f"WARN: memory_telemetry invalid effect {effect!r}; coercing to 'informed_decision'",
            file=sys.stderr,
        )
        effect = "informed_decision"
    row: dict[str, Any] = {
        "ts": _iso_utc(),
        "kind": KIND_EFFECT,
        "schema_version": SCHEMA_VERSION,
        "correlation_id": correlation_id,
        "effect": effect,
        "reason": reason,
    }
    _append_row(telemetry_path or DEFAULT_TELEMETRY_PATH, row)


def read_rows(path: Path | None = None) -> list[dict[str, Any]]:
    """Read all telemetry rows. Used by tests + Phase 6 Learn aggregation."""
    p = path or DEFAULT_TELEMETRY_PATH
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
