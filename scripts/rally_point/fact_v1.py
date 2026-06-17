# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""fact.v1 emitter — lossless ARP-ingestible records from build-loop coordination writes.

Build-loop's embedded fallback historically wrote its own ``changes.jsonl`` shape
(``changes.make_record``). The installed Rust agent-rally-point binary only ingests
``agent-rally.fact.v1``-shaped JSONL via ``rally migrate-legacy`` — any line whose
``schema`` field is not exactly ``"agent-rally.fact.v1"`` is SILENTLY SKIPPED
(discovery.rs:713-716). So for the fallback→ARP transition to migrate build-loop's
actual coordination history (not zero facts), the fallback store must already be in
fact.v1 shape.

This module is the emitter. ``to_fact_v1`` maps a build-loop coordination write onto
the upstream ``Fact`` wire shape (store.rs:229-272, FACT_SCHEMA lib.rs:40). Kind mapping
delegates to ``post._native_kind`` so the two can never diverge (single source of truth).

Key fidelity points (verified against the upstream Rust source this run):
  - ``schema`` must equal ``"agent-rally.fact.v1"`` exactly or migrate-legacy skips the line.
  - ``ref`` is the wire name for the upstream ``ref_id`` field (``#[serde(rename = "ref")]``).
  - ``seq`` is store-assigned; the emitter writes ``0`` and migrate-legacy resets it anyway
    (``Fact { seq: 0, ..fact }`` discovery.rs:742).
  - ``session`` / ``from_session_id`` are skip-if-none upstream — the emitter omits them.
  - The Fact struct has NO ``deny_unknown_fields`` (verified store.rs), so additive private
    keys are tolerated on deserialize: the emitter carries build-loop's own ``revision`` as
    ``bl_revision`` and the original producer/payload signal as ``bl_payload`` /
    ``bl_producer`` so nothing is lost. ``changes.normalize_record`` reads these back; ARP
    ignores them.

Pure / stdlib-only. NEVER imports agent-rally-point.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

try:  # package import
    from .post import _native_kind, _native_subject
except ImportError:  # script import (sys.path-inserted, no parent package)
    from post import _native_kind, _native_subject  # type: ignore

FACT_SCHEMA = "agent-rally.fact.v1"
_LOG_NAME = "changes.jsonl"

# Kinds excluded from claimable-work surfaces upstream; the build-loop fallback
# does not produce them, so the emitter refuses them defensively.
_NON_EMITTED_KINDS = frozenset({"read", "receipt"})


def map_kind(kind: str) -> str:
    """Return the fact.v1 wire kind for a build-loop ``kind``.

    Delegates to ``post._native_kind`` verbatim — single source of truth for the
    build-loop→ARP kind mapping, so the emitter and the native-CLI post path can
    never drift. (handoff/standby/wake pass through; phase→presence; escalation→risk;
    feedback/message/dep-change/arch-scan-complete and everything else incl. commit
    and the four lead-* kinds → the catch-all ``artifact``.)
    """
    return _native_kind(kind)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _event_id(*, kind: str, tool: str, run_id: str, subject: str, created_at: str) -> str:
    """Deterministic, stable event_id so migrate-legacy dedup works across replays.

    Same (kind, tool, run_id, subject, created_at) → same id. migrate-legacy keys
    idempotency on ``event_id`` (discovery.rs:736-739), so a stable derivation makes
    a re-migrated store a no-op rather than a duplicate.
    """
    canonical = "\x1f".join((kind, tool, run_id, subject, created_at))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"blf_{digest[:24]}"


def to_fact_v1(
    *,
    kind: str,
    tool: str,
    model: str,
    run_id: str,
    app_slug: str,
    payload: dict | None,
    subject: str | None = None,
    revision: int = 0,
    producer: dict | None = None,
    created_at: str | None = None,
) -> dict:
    """Build a fact.v1-shaped dict from a build-loop coordination write.

    The returned dict serializes (json.dumps) to a line ``rally migrate-legacy``
    ingests losslessly. Build-loop-private signal (revision, original payload,
    producer metadata) rides along as additive ``bl_*`` keys that ARP ignores and
    ``changes.normalize_record`` reads back.
    """
    payload = payload or {}
    if kind in _NON_EMITTED_KINDS:
        # Defensive: the fallback should never emit these; coerce to artifact so a
        # stray call still produces a valid, ingestible line rather than crashing.
        wire_kind = "artifact"
    else:
        wire_kind = map_kind(kind)

    # Subject derivation delegates to post._native_subject (DRY — same source of
    # truth the native-CLI post path uses), so a phase event yields
    # "phase: rally-start" rather than the bare "phase", and the event_id hash
    # stays consistent with the native path. An explicit subject arg still wins.
    subj = str(subject) if subject else _native_subject(kind, payload)
    ts = created_at or _iso_now()

    fact: dict = {
        "schema": FACT_SCHEMA,
        "event_id": _event_id(
            kind=wire_kind, tool=tool or "unknown", run_id=run_id or "",
            subject=subj, created_at=ts,
        ),
        "seq": 0,  # store-assigned; migrate-legacy resets to 0 anyway
        "thread_id": run_id or app_slug or "",
        "kind": wire_kind,
        "subject": subj,
        "scope": _as_list(payload.get("scope") or payload.get("paths") or payload.get("path")),
        "created_at": ts,
        "evidence": _as_list(payload.get("evidence")),
    }
    if tool:
        fact["tool"] = tool

    summary = payload.get("summary") or payload.get("reason")
    if summary:
        fact["summary"] = str(summary)
    target = payload.get("to") or payload.get("to_tool") or payload.get("target")
    if target:
        fact["target"] = str(target)
    ref = payload.get("ref") or payload.get("ref_id")
    if ref:
        fact["ref"] = str(ref)  # wire name for upstream ref_id
    status = payload.get("status") or payload.get("verdict")
    if status:
        fact["status"] = str(status)
    severity = payload.get("severity")
    if severity:
        fact["severity"] = str(severity)
    uri = payload.get("uri")
    if uri:
        fact["uri"] = str(uri)

    # Build-loop-private additive keys (ARP tolerates: no deny_unknown_fields).
    fact["bl_revision"] = int(revision)
    if model:
        fact["bl_model"] = str(model)
    if app_slug:
        fact["bl_app_slug"] = str(app_slug)
    fact["bl_kind"] = str(kind)  # original (pre-map) build-loop kind, for lossless read-back
    if payload:
        fact["bl_payload"] = payload
    if producer:
        fact["bl_producer"] = producer
    return fact


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if v is not None]
    return [value]


def write_fact_v1_line(channel_dir: Path, fact: dict) -> None:
    """Atomically append one fact.v1 line. Fire-and-forget, never raises.

    Same O_APPEND single-write contract as ``changes.append_change`` (atomic up to
    PIPE_BUF, no lock, safe across tools/processes). Writes to ``<channel_dir>/changes.jsonl``
    — the exact filename ``rally migrate-legacy`` reads (discovery.rs:677).
    """
    try:
        d = Path(channel_dir)
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps(fact, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        fd = os.open(
            str(d / _LOG_NAME),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o644,
        )
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 — fire-and-forget contract
        return
