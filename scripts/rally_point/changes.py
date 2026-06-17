#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
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
_NATIVE_LOG_DIR = "log"

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
    "standby",
    "wake",
)

_RECORD_KEYS = (
    "ts", "kind", "tool", "model", "run_id", "app_slug", "payload",
    "revision",
)


def _log_path(channel_dir: Path) -> Path:
    return Path(channel_dir) / _LOG_NAME


def _int_or_zero(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


try:  # package import
    from .fact_v1 import FACT_SCHEMA
except ImportError:  # script import (sys.path-inserted, no parent package)
    from fact_v1 import FACT_SCHEMA  # type: ignore

# Single source of truth: the schema string lives in fact_v1.FACT_SCHEMA (the
# emitter). ``FACT_V1_SCHEMA`` is kept as the local read-back alias so the
# detection below and any importer of changes.FACT_V1_SCHEMA stay stable, while
# the literal can never drift from the emitter's constant.
FACT_V1_SCHEMA = FACT_SCHEMA


def normalize_record(record: dict) -> dict:
    """Return a legacy-shaped change record for all Rally log formats."""
    if not isinstance(record, dict):
        return record
    # fact.v1 lines (build-loop's own ARP-ingestible fallback shape) are caught
    # FIRST: the fallback writes these via ``fact_v1.write_fact_v1_line`` so the
    # store is losslessly readable by ``rally migrate-legacy``. Build-loop-private
    # ``bl_*`` keys carry the original revision / payload / kind for lossless
    # read-back; ARP ignores them on its side (no deny_unknown_fields).
    if record.get("schema") == FACT_V1_SCHEMA:
        return _normalize_fact_v1_record(record)
    if "event_type" in record and isinstance(record.get("payload"), dict):
        return _normalize_repo_local_record(record)
    event = record.get("event")
    if not isinstance(event, dict):
        return record

    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    normalized = {
        "ts": event.get("time") or record.get("received_at") or record.get("ts"),
        "kind": event.get("kind") or event.get("type") or "unknown",
        "tool": event.get("tool") or payload.get("from_tool") or "unknown",
        "model": event.get("model") or payload.get("model") or "unknown",
        "run_id": event.get("run_id") or payload.get("run_id") or "unknown",
        "app_slug": event.get("app_slug") or record.get("app_slug") or "",
        "payload": payload,
        "revision": _int_or_zero(record.get("local_seq") or record.get("revision")),
    }
    if event.get("id"):
        normalized["event_id"] = event["id"]
    if event.get("subject") and "subject" not in payload:
        normalized["subject"] = event["subject"]
    normalized["_source_format"] = "hash-chain"
    return normalized


def _normalize_repo_local_record(record: dict) -> dict:
    """Normalize native repo-local ``.rally/log/*.jsonl`` rows.

    Native Rally stores rows as ``{seq, occurred_at, event_type, payload}``
    where ``payload`` is a Rally fact. Build-loop status consumers expect
    the older ``changes.jsonl`` shape, so map only the stable fact fields.
    """
    fact = record.get("payload") or {}
    if not isinstance(fact, dict):
        fact = {}
    payload = {
        "subject": fact.get("subject"),
        "scope": fact.get("scope"),
        "status": fact.get("status"),
        "reason": fact.get("summary"),
        "from_tool": fact.get("tool"),
        "to_tool": fact.get("target"),
        "run_id": fact.get("run_id"),
    }
    normalized = {
        "ts": record.get("occurred_at") or fact.get("created_at") or record.get("ts"),
        "kind": record.get("event_type") or fact.get("kind") or "unknown",
        "tool": fact.get("tool") or "unknown",
        "model": fact.get("model") or "unknown",
        "run_id": fact.get("run_id") or "unknown",
        "app_slug": record.get("engagement") or "",
        "payload": {k: v for k, v in payload.items() if v is not None},
        "revision": _int_or_zero(record.get("seq") or fact.get("seq")),
        "event_id": fact.get("event_id"),
        "subject": fact.get("subject"),
        "_source_format": "repo-local-rally",
    }
    return normalized


def _normalize_fact_v1_record(record: dict) -> dict:
    """Normalize a build-loop fact.v1 line back to the legacy reader shape.

    The fallback emits fact.v1 (``fact_v1.to_fact_v1``) so the store is
    ARP-ingestible. Build-loop's 7 production readers consume the legacy
    ``{ts, kind, tool, model, run_id, app_slug, payload, revision}`` shape via
    this single chokepoint, so the fact.v1 line is mapped back here.

    ``revision`` comes from the private ``bl_revision`` key (NOT ``seq``, which is
    0 in fallback stores) so the ``revision == channel_rev`` equality that
    ``coordination_rally.py`` handoff-verify relies on is preserved. ``kind``
    comes from the private ``bl_kind`` (the original build-loop kind) when present,
    falling back to the fact.v1 wire kind.
    """
    payload = record.get("bl_payload")
    if not isinstance(payload, dict):
        # Reconstruct a minimal payload from the fact fields so readers that
        # inspect payload (e.g. subject/status/target) still work.
        payload = {
            k: v for k, v in (
                ("subject", record.get("subject")),
                ("summary", record.get("summary")),
                ("status", record.get("status")),
                ("to_tool", record.get("target")),
            ) if v is not None
        }
    normalized = {
        "ts": record.get("created_at") or record.get("ts"),
        "kind": record.get("bl_kind") or record.get("kind") or "unknown",
        "tool": record.get("tool") or "unknown",
        "model": record.get("bl_model") or "unknown",
        "run_id": record.get("thread_id") or (payload.get("run_id") if isinstance(payload, dict) else None) or "unknown",
        "app_slug": record.get("bl_app_slug") or "",
        "payload": payload,
        "revision": _int_or_zero(record.get("bl_revision")),
        "subject": record.get("subject"),
        "_source_format": "fact-v1",
    }
    if record.get("event_id"):
        normalized["event_id"] = record["event_id"]
    return normalized


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
    d = Path(channel_dir)
    native_log_dir = d / _NATIVE_LOG_DIR
    if native_log_dir.is_dir():
        return _read_repo_local_changes_since(d, offset)

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
                    records.append(normalize_record(json.loads(raw.decode("utf-8"))))
                except (ValueError, UnicodeDecodeError):
                    continue  # skip a corrupt line, keep offset advancing
    except OSError:
        return [], offset
    return records, new_offset


def _read_repo_local_changes_since(channel_dir: Path, offset: int) -> tuple[list, int]:
    """Return normalized rows from native repo-local ``.rally/log`` files.

    ``offset`` is interpreted as the last seen sequence number for this
    layout. The returned offset is the highest complete sequence observed.
    """
    records: list = []
    latest_seq = _int_or_zero(offset)
    try:
        paths = sorted((Path(channel_dir) / _NATIVE_LOG_DIR).glob("*.jsonl"))
    except OSError:
        return [], latest_seq
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    normalized = normalize_record(raw)
                    seq = _int_or_zero(normalized.get("revision"))
                    if seq > latest_seq:
                        latest_seq = seq
                    if seq > _int_or_zero(offset):
                        records.append(normalized)
        except OSError:
            continue
    records.sort(key=lambda r: _int_or_zero(r.get("revision")))
    return records, latest_seq
