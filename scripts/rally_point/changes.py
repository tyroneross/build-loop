#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Rally Point change log — append-only, immutable, cross-tool event stream.

One ``changes.jsonl`` per channel. Each line is one self-contained JSON
record. Writes use ``O_APPEND`` with a single ``os.write`` of one
``json.dumps(...) + "\\n"``; POSIX guarantees an ``O_APPEND`` write up to
``PIPE_BUF`` is atomic, so concurrent writers from any tool never tear a
line and need no lock. The log is **immutable**: this module exposes
only ``append_change`` and ``read_changes_since`` — no rewrite, delete,
or truncate entry point exists (by design, see ``test_no_mutation_api``).

Record schema (defined here once; D7 — unknown ``kind`` warns, never
drops):

    {ts, kind, tool, model, run_id, app_slug, payload{...}, revision}

NON-GOAL guard: records carry structure/data-flow only — never any
call-frequency / invocation-count field.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_LOG_NAME = "changes.jsonl"

# Known kinds (D7: this is advisory — unknown kinds warn but are kept).
# Added 2026-05-20 for coordination dogfood: "feedback" (verifier verdicts:
# PASS/VARIANCE/BLOCKED) and "handoff" (plan owner → verifier work-item
# transfer). Both surface in checkpoint_read like any other kind.
# Added 2026-05-22 for the leadership lease (G1) + escalation salience (G3):
# the four "lead-*" kinds form the durable audit trail of who held the lead;
# "escalation" marks a record needing lead/user attention now (distinct from
# routine "phase"/"feedback"). All five are additive — D7 means any older
# reader that predates them warns-not-drops, so no existing consumer breaks.
KNOWN_KINDS = (
    "commit",
    "dep-change",
    "phase",
    "arch-scan-complete",
    "feedback",
    "handoff",
    "message",
    "lead-claim",
    "lead-renew",
    "lead-transfer",
    "lead-relinquish",
    "escalation",
)

_RECORD_KEYS = (
    "ts", "kind", "tool", "model", "run_id", "app_slug", "payload",
    "revision",
)


def _log_path(channel_dir: Path) -> Path:
    return Path(channel_dir) / _LOG_NAME


def make_record(
    *,
    kind: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    payload: dict,
    revision: int,
) -> dict:
    """Build a well-formed change record (schema single source of truth)."""
    return {
        "ts": time.time(),
        "kind": kind,
        "tool": tool or "unknown",
        "model": model or "unknown",
        "run_id": run_id or "unknown",
        "app_slug": app_slug,
        "payload": payload or {},
        "revision": int(revision),
    }


def validate_record(record: dict) -> dict:
    """Return ``record`` always (immutable, warns-not-drops — D7).

    Emits a stderr warning for an unknown ``kind`` or a missing required
    key but never raises and never mutates/drops — the consumer decides.
    """
    missing = [k for k in _RECORD_KEYS if k not in record]
    if missing:
        print(
            f"rally-point: change record missing keys {missing} (kept anyway)",
            file=sys.stderr,
        )
    if record.get("kind") not in KNOWN_KINDS:
        print(
            f"rally-point: unknown change kind {record.get('kind')!r} "
            f"(warns-not-drops per D7)",
            file=sys.stderr,
        )
    return record


def append_change(channel_dir: Path, record: dict) -> None:
    """Atomically append one record line. Fire-and-forget, never raises.

    O_APPEND single-write — atomic up to PIPE_BUF, no lock, safe across
    tools/processes. Failure is swallowed (never blocks a host action).
    """
    try:
        d = Path(channel_dir)
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        fd = os.open(
            str(_log_path(d)),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o644,
        )
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 — fire-and-forget contract
        return


def read_changes_since(channel_dir: Path, offset: int) -> tuple[list, int]:
    """Return ``(new_records, new_byte_offset)`` from byte ``offset``.

    Absent log → ``([], 0)``. Reader takes no lock and never writes the
    log. A trailing partial line (writer mid-append) is left for the
    next poll: the returned offset only advances past complete lines.
    """
    p = _log_path(channel_dir)
    try:
        size = p.stat().st_size
    except (FileNotFoundError, OSError):
        return [], 0
    if offset < 0 or offset > size:
        offset = 0
    records: list = []
    new_offset = offset
    try:
        with open(p, "rb") as fh:
            fh.seek(offset)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # partial trailing line — re-read next poll
                new_offset += len(raw)
                try:
                    records.append(json.loads(raw.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    continue  # skip a corrupt line, keep offset advancing
    except OSError:
        return [], offset
    return records, new_offset
