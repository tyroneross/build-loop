#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""agent_ledger.py — the build-loop agent-activity ledger (the instrument).

One append-only JSONL file, `.build-loop/agent-ledger.jsonl`, that every
agent-action writes to. Single writer = the orchestrator (sub-agents return
envelopes; the orchestrator appends a line per dispatch and per return). This
replaces today's scattered `state.json.escalations` / `judge-decisions.json` /
`*_status` fields with one joinable trail so you can answer "which model
designed this plan / executed each chunk / where did the Advisor step in / how
often did the fallback fire" at a glance.

Design constraints honored:
- **Stdlib only.** No third-party deps (KISS / minimal-dependencies rule).
- **Append-only JSONL.** Crash-safe and concurrency-safe (a partial final line
  is tolerated on read), matching the "progress in JSON, not markdown" rule.
- **Multi-process safe.** Build Loop orchestrators serialize append and
  reconciliation with repository-local locks; torn tails are quarantined before
  the next complete row is appended.
- **Fail-open.** A ledger write must never wedge a build. `append()` swallows
  OSErrors and reports them in its return envelope rather than raising.

One line per agent-action, fields (see `LEDGER_FIELDS`):

    ts · run_id · phase · chunk_id ·
    agent · tier · model (resolved id) ·
    action (author|execute|re-plan|take-over|verify|gate) ·
    rung (0-3) · status (pass|fail|blocked|partial|variance) ·
    trigger ("2 fails@opus" | "riskSurfaceChange" | "planning-miss") ·
    refs (input plan / output commit) · note (failure evidence, why retry justified)

The local ledger path shells out to nothing — the orchestrator passes the
already-resolved commit SHA / model id in. Its downstream coordination
projection delegates to ``rally_point.post.post`` so Rally availability and
Build Loop fallback selection stay owned by the shared adapter.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

# The canonical action + status vocabularies (mirror the spec's ledger schema).
ACTIONS = {"author", "execute", "re-plan", "take-over", "verify", "gate"}
STATUSES = {"pass", "fail", "blocked", "partial", "variance"}

# Ordered field list — the row is a dict, but this names the contract and lets
# `--summarize` and tests assert the shape without re-deriving it.
LEDGER_FIELDS = (
    "ts",
    "run_id",
    "phase",
    "chunk_id",
    "agent",
    "tier",
    "model",
    "action",
    "rung",
    "status",
    "trigger",
    "refs",
    "note",
)

LEDGER_RELPATH = (".build-loop", "agent-ledger.jsonl")
SYNC_MARKER_NAME = "agent-ledger.rally-sync.json"
SYNC_MARKER_SCHEMA = "build-loop.agent-ledger-rally-sync.v2"

# Reconciliation is deliberately bounded. The cursor means rows before it are
# either durably projected or represented by one of the bounded pending holes.
# Successful rows therefore do not require an ever-growing per-row marker.
MAX_RECONCILE_ATTEMPTS = 32
MAX_PENDING_RETRIES_PER_APPEND = 8
MAX_PENDING_PROJECTIONS = 64
MAX_TERMINAL_DIAGNOSTICS = 64
MAX_RECONCILE_ROWS = 256
MAX_STREAM_LINE_BYTES = 128 * 1024
PREFIX_TAIL_PROBE_BYTES = 4096
MAX_CORRUPT_TAIL_FILES = 32
MAX_CORRUPT_TAIL_BYTES = 2 * 1024 * 1024

# Projection is downstream telemetry, never the authority for build gates. The
# guard prevents a future Rally/post hook that records its own agent action from
# recursively projecting that nested append.
_PROJECTION_ACTIVE: ContextVar[bool] = ContextVar(
    "agent_ledger_projection_active", default=False
)


def default_ledger_path(workdir: Path) -> Path:
    """`.build-loop/agent-ledger.jsonl` under the given workdir."""
    return workdir.joinpath(*LEDGER_RELPATH)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_row(
    *,
    run_id: str,
    agent: str,
    action: str,
    phase: str | None = None,
    chunk_id: str | None = None,
    tier: str | None = None,
    model: str | None = None,
    rung: int | None = None,
    status: str | None = None,
    trigger: str | None = None,
    refs: dict[str, Any] | None = None,
    note: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:  # noqa: D401
    """Construct a ledger row dict in canonical field order.

    Required: `run_id`, `agent`, `action`. Everything else is optional so a
    minimal dispatch line is cheap to write. `action` and (when given) `status`
    are validated against the canonical vocabularies — an unknown value raises,
    because a typo'd action silently corrupts the joinable trail the ledger
    exists to provide (this is a build-time author error, not a runtime path,
    so raising here is correct; the fail-open boundary is `append()`'s I/O).
    """
    if not run_id:
        raise ValueError("run_id is required")
    if not agent:
        raise ValueError("agent is required")
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {sorted(ACTIONS)}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {sorted(STATUSES)}")
    if rung is not None and not (0 <= int(rung) <= 3):
        raise ValueError(f"rung must be 0-3, got {rung!r}")
    if refs is not None and not isinstance(refs, dict):
        # `refs` is a {input/output: ...} object by contract; a list/string would
        # silently corrupt downstream ledger consumers that index it as a dict.
        raise ValueError(f"refs must be a JSON object (dict), got {type(refs).__name__}")

    return {
        "ts": ts or _utc_now_iso(),
        "run_id": run_id,
        "phase": phase,
        "chunk_id": chunk_id,
        "agent": agent,
        "tier": tier,
        "model": model,
        "action": action,
        "rung": int(rung) if rung is not None else None,
        "status": status,
        "trigger": trigger,
        "refs": refs or {},
        "note": note,
    }


def _inferred_projection_workdir(path: Path) -> Path | None:
    """Infer a repo only for its canonical ``.build-loop`` ledger path."""
    resolved = path.expanduser().resolve()
    if resolved.name != LEDGER_RELPATH[1] or resolved.parent.name != LEDGER_RELPATH[0]:
        return None
    candidate = resolved.parent.parent
    return candidate if (candidate / ".git").exists() else None


def _rally_adapter() -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Load Rally lazily so a missing adapter cannot break local ledger use."""
    try:
        from scripts.rally_point.discovery_bridge import resolve
        from scripts.rally_point.post import post
    except ImportError:
        from rally_point.discovery_bridge import resolve  # type: ignore
        from rally_point.post import post  # type: ignore
    return resolve, post


def _projection_payload(
    row: dict[str, Any], *, native_identity: Any | None = None
) -> dict[str, Any]:
    """Return the lossless Rally payload for one exact local ledger row."""
    evidence = json.dumps(row, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:24]
    payload = {
        "subject": f"agent-ledger:{digest}",
        "agent_ledger": row,
    }
    if native_identity is not None:
        payload.update(
            host_tool=native_identity.base_tool,
            session_id=native_identity.session_id,
        )
    return payload


def _row_digest(row: dict[str, Any]) -> str:
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _native_repo_root(envelope: Any) -> Path | None:
    raw = getattr(envelope, "raw", None)
    if not isinstance(raw, dict):
        return None
    whoami_envelope = raw.get("whoami")
    if not isinstance(whoami_envelope, dict):
        return None
    data = whoami_envelope.get("data")
    whoami = data.get("whoami") if isinstance(data, dict) else None
    root = whoami.get("repo_root") if isinstance(whoami, dict) else None
    if not root:
        return None
    try:
        return Path(str(root)).expanduser().resolve()
    except OSError:
        return None


def _native_projection_receipts(
    workdir: Path,
    envelope: Any,
    *,
    native_identity: Any | None = None,
) -> tuple[bool, dict[str, dict[str, Any]], str | None]:
    """Read exact agent-ledger projections from this repo's native Rally room."""
    repo_root = _native_repo_root(envelope)
    if repo_root is None:
        # Production native envelopes always include authenticated whoami data.
        # Legacy/test envelopes cannot be safely filtered, so do not treat their
        # global read surface as evidence that a row exists in this repository.
        return True, {}, None
    try:
        try:
            from scripts.rally_point.backend_adapter import invoke_native, resolve_context
            from scripts.rally_point.discovery_bridge import maybe_auto_migrate
            from scripts.rally_point.payload_codec import decode_event
        except ImportError:
            from rally_point.backend_adapter import invoke_native, resolve_context  # type: ignore
            from rally_point.discovery_bridge import maybe_auto_migrate  # type: ignore
            from rally_point.payload_codec import decode_event  # type: ignore

        # A fallback fact may be the very row whose marker write was lost. Move
        # that spool first, then use the same native read to prove its arrival.
        maybe_auto_migrate(workdir, envelope)
        context = resolve_context(workdir)
        if native_identity is None:
            native_identity, synthetic = _projection_actor_identity({})
            if synthetic:
                return False, {}, "native Rally dedup read requires an identified host actor"
        result = invoke_native(
            context,
            ["recent", "--json", "--limit", "500", "--include-archived"],
            expected_schema="agent-rally.command.recent.v1",
            tool=native_identity.native_tool,
            session_id=native_identity.session_id,
        )
        if not result.ok or not isinstance(result.payload, dict):
            return False, {}, result.reason or "native Rally dedup read failed"
        data = result.payload.get("data")
        recent_data = data.get("recent") if isinstance(data, dict) else None
        rows = recent_data.get("rows") if isinstance(recent_data, dict) else None
        if not isinstance(rows, list):
            return False, {}, "native Rally recent response omitted rows"

        receipts: dict[str, dict[str, Any]] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            item_root = item.get("repo_root")
            try:
                if not item_root or Path(str(item_root)).expanduser().resolve() != repo_root:
                    continue
            except OSError:
                continue
            fact = item.get("fact")
            if not isinstance(fact, dict):
                continue
            event = decode_event(fact.get("evidence"))
            if (
                not isinstance(event, dict)
                or event.get("app_slug") != str(envelope.app_slug)
            ):
                continue
            payload = event.get("payload")
            row = payload.get("agent_ledger") if isinstance(payload, dict) else None
            if not isinstance(row, dict):
                continue
            digest = _row_digest(row)
            if fact.get("subject") != f"agent-ledger:{digest[:24]}":
                continue
            revision = fact.get("seq")
            receipts[digest] = _projection_result(
                "projected",
                backend="rally",
                transport="rally-cli",
                revision=revision if type(revision) is int else None,
                reason="already-present-in-rally",
                event_id=(str(fact["event_id"]) if fact.get("event_id") else None),
                write_attempted=False,
            )
        return True, receipts, None
    except Exception as exc:  # read-side proof remains fail-open for local builds
        return False, {}, str(exc)


def _projection_is_oversize(
    row: dict[str, Any],
    envelope: Any,
    *,
    native_identity: Any | None = None,
) -> bool:
    try:
        try:
            from scripts.rally_point.payload_codec import encode_event, has_oversize_marker
        except ImportError:
            from rally_point.payload_codec import encode_event, has_oversize_marker  # type: ignore
        evidence = encode_event(
            kind="artifact",
            payload=_projection_payload(row, native_identity=native_identity),
            model=str(row.get("model") or ""),
            run_id=str(row.get("run_id") or ""),
            app_slug=str(envelope.app_slug),
        )
        return has_oversize_marker(evidence)
    except Exception:
        return False


def _projection_source_identity(row: dict[str, Any]) -> tuple[str, bool]:
    """Return the configured host id and whether it is a synthetic fallback."""
    del row
    configured = os.environ.get("BUILD_LOOP_RALLY_TOOL")
    synthetic = False
    if not configured:
        try:
            from scripts.host_capabilities import detect_host
        except ImportError:
            from host_capabilities import detect_host  # type: ignore
        detected = detect_host(None)
        if detected == "unknown":
            configured = "build_loop"
            synthetic = True
        else:
            configured = detected
    safe = "".join(
        char if char.isascii() and (char.isalnum() or char in "_-:") else "-"
        for char in configured
    ).strip("-")
    return (safe or "build_loop")[:64], synthetic or not bool(safe)


def _projection_actor_identity(row: dict[str, Any]) -> tuple[Any, bool]:
    """Return one session-qualified native actor while retaining host metadata."""
    configured, synthetic = _projection_source_identity(row)
    try:
        from scripts.rally_point import actor_identity
    except ImportError:
        from rally_point import actor_identity  # type: ignore
    return actor_identity.resolve_identity(configured), synthetic


def _projection_identity(row: dict[str, Any]) -> tuple[str, bool]:
    """Return the session-qualified native actor and synthetic-host marker."""
    identity, synthetic = _projection_actor_identity(row)
    return identity.native_tool, synthetic


def _projection_tool(row: dict[str, Any]) -> str:
    """Use one real Build Loop actor; row agent/run identity stays evidence."""
    return _projection_identity(row)[0]


def _projection_result(
    status: str,
    *,
    backend: str | None = None,
    transport: str | None = None,
    revision: int | None = None,
    error: str | None = None,
    reason: str | None = None,
    event_id: str | None = None,
    remedy: str | None = None,
    write_attempted: bool | None = None,
) -> dict[str, Any]:
    if status == "projected":
        ok: bool | None = True
    elif status in {"skipped", "pending", "outcome_unknown"}:
        ok = None
    else:
        ok = False
    return {
        "status": status,
        "ok": ok,
        "backend": backend,
        "transport": transport,
        "revision": revision,
        "error": error,
        "reason": reason,
        "event_id": event_id,
        "remedy": remedy,
        "write_attempted": write_attempted,
    }


def _project_to_rally(
    workdir: Path | None,
    row: dict[str, Any],
    *,
    receipt_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a local row through Rally without affecting local append status."""
    if workdir is None:
        return _projection_result(
            "skipped", reason="no-workdir", write_attempted=False
        )
    if _PROJECTION_ACTIVE.get():
        return _projection_result(
            "skipped", reason="recursive-projection", write_attempted=False
        )

    token = _PROJECTION_ACTIVE.set(True)
    backend: str | None = None
    transport: str | None = None
    try:
        resolve, post = _rally_adapter()
        envelope = resolve(workdir)
        backend = str(
            getattr(envelope, "backend", None)
            or getattr(envelope, "resolved_via", "unknown")
        )
        transport = getattr(envelope, "transport", None)
        source_tool, synthetic_actor = _projection_source_identity(row)
        native_identity = None
        projection_tool = source_tool
        if transport == "rally-cli":
            native_identity, synthetic_actor = _projection_actor_identity(row)
            projection_tool = native_identity.native_tool
        if transport == "rally-cli" and synthetic_actor:
            return _projection_result(
                "pending",
                backend=backend,
                transport=transport,
                reason="native Rally projection requires an identified host actor",
                remedy=(
                    "set BUILD_LOOP_RALLY_TOOL to the active Codex, Claude, or "
                    "Cursor tool id and retry reconciliation"
                ),
                write_attempted=False,
            )
        if _projection_is_oversize(
            row,
            envelope,
            native_identity=native_identity,
        ):
            return _projection_result(
                "oversize",
                backend=backend,
                transport=transport,
                reason="agent-ledger row exceeds lossless Rally evidence boundary",
                remedy="row remains authoritative in the local Build Loop ledger",
                write_attempted=False,
            )
        if transport == "rally-cli":
            cache = receipt_cache if receipt_cache is not None else {}
            if not cache.get("loaded"):
                available, receipts, read_reason = _native_projection_receipts(
                    workdir,
                    envelope,
                    native_identity=native_identity,
                )
                cache.update(
                    {
                        "loaded": True,
                        "available": available,
                        "receipts": receipts,
                        "reason": read_reason,
                    }
                )
            if not cache.get("available"):
                return _projection_result(
                    "failed",
                    backend=backend,
                    transport=transport,
                    reason=str(
                        cache.get("reason") or "native Rally dedup read unavailable"
                    ),
                    remedy="retry after the current repo room is readable",
                    write_attempted=False,
                )
            prior = (cache.get("receipts") or {}).get(_row_digest(row))
            if isinstance(prior, dict):
                return dict(prior)
        post_outcome: dict[str, Any] = {}
        revision = post(
            channel_dir=Path(envelope.channel_dir),
            kind="artifact",
            tool=projection_tool,
            model=str(row.get("model") or ""),
            run_id=str(row.get("run_id") or ""),
            app_slug=str(envelope.app_slug),
            payload=_projection_payload(row, native_identity=native_identity),
            workdir=workdir,
            outcome=post_outcome,
            local_tool=(
                native_identity.base_tool
                if native_identity is not None
                else source_tool
            ),
            local_session_id=(
                native_identity.session_id if native_identity is not None else None
            ),
        )
        backend = str(post_outcome.get("backend") or backend)
        transport = str(post_outcome.get("transport") or transport or "") or None
        if revision is None:
            outcome_status = str(post_outcome.get("status") or "failed")
            outcome_revision = post_outcome.get("revision")
            if type(outcome_revision) is not int:
                outcome_revision = None
            return _projection_result(
                outcome_status,
                backend=backend,
                transport=transport,
                revision=outcome_revision,
                error=(
                    None
                    if post_outcome.get("status")
                    else "rally post returned no revision"
                ),
                reason=(
                    str(post_outcome["reason"])
                    if post_outcome.get("reason") is not None
                    else None
                ),
                event_id=(
                    str(post_outcome["event_id"])
                    if post_outcome.get("event_id") is not None
                    else None
                ),
                remedy=(
                    str(post_outcome["remedy"])
                    if post_outcome.get("remedy") is not None
                    else (
                        "locate the stable agent-ledger subject in the current "
                        "Rally room before retrying"
                        if outcome_status == "outcome_unknown"
                        else None
                    )
                ),
                write_attempted=True,
            )
        projected = _projection_result(
            "projected",
            backend=backend,
            transport=transport,
            revision=revision,
            reason=(
                str(post_outcome["reason"])
                if post_outcome.get("reason") is not None
                else None
            ),
            event_id=(
                str(post_outcome["event_id"])
                if post_outcome.get("event_id") is not None
                else None
            ),
            write_attempted=True,
        )
        if transport == "rally-cli" and receipt_cache is not None:
            receipt_cache.setdefault("receipts", {})[_row_digest(row)] = projected
        return projected
    except Exception as exc:  # projection is telemetry and must stay fail-open
        return _projection_result(
            "failed",
            backend=backend,
            transport=transport,
            error=str(exc),
            write_attempted=True,
        )
    finally:
        _PROJECTION_ACTIVE.reset(token)


_EMPTY_PREFIX_SHA256 = hashlib.sha256(b"").hexdigest()
def _advance_prefix_digest(previous: str, line_sha256: str, length: int) -> str:
    """Extend a serializable rolling digest without retaining prior rows."""
    digest = hashlib.sha256()
    digest.update(bytes.fromhex(previous))
    digest.update(int(length).to_bytes(8, "big"))
    digest.update(bytes.fromhex(line_sha256))
    return digest.hexdigest()


def _prefix_tail_probe(path: Path, offset: int) -> str:
    if offset <= 0:
        return hashlib.sha256(b"").hexdigest()
    with path.open("rb") as fh:
        start = max(0, offset - PREFIX_TAIL_PROBE_BYTES)
        fh.seek(start)
        data = fh.read(offset - start)
    return hashlib.sha256(data).hexdigest()


def _ledger_identity(path: Path) -> tuple[int | None, int | None]:
    try:
        stat = path.stat()
        return int(stat.st_dev), int(stat.st_ino)
    except OSError:
        return None, None


def _empty_sync_state(path: Path) -> dict[str, Any]:
    device, inode = _ledger_identity(path)
    return {
        "schema": SYNC_MARKER_SCHEMA,
        "cursor": 0,
        "cursor_offset": 0,
        "prefix_sha256": _EMPTY_PREFIX_SHA256,
        "prefix_tail_sha256": hashlib.sha256(b"").hexdigest(),
        "ledger_device": device,
        "ledger_inode": inode,
        "pending": [],
        "terminal": [],
    }


def _read_row_span(path: Path, start: int, end: int) -> dict[str, Any] | None:
    length = end - start
    if start < 0 or end <= start or length > MAX_STREAM_LINE_BYTES:
        return None
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            raw = fh.read(length)
    except OSError:
        return None
    if len(raw) != length:
        return None
    try:
        value = json.loads(raw.strip().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_sync_state(marker: Path, path: Path) -> dict[str, Any]:
    """Load one bounded offset marker; reset without materializing the ledger."""
    empty = _empty_sync_state(path)
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        file_size = path.stat().st_size
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return empty
    if not isinstance(value, dict) or value.get("schema") != SYNC_MARKER_SCHEMA:
        return empty
    cursor = value.get("cursor")
    offset = value.get("cursor_offset")
    if (
        type(cursor) is not int
        or cursor < 0
        or type(offset) is not int
        or offset < 0
        or offset > file_size
    ):
        return empty
    device, inode = _ledger_identity(path)
    if value.get("ledger_device") != device or value.get("ledger_inode") != inode:
        return empty
    try:
        if value.get("prefix_tail_sha256") != _prefix_tail_probe(path, offset):
            return empty
    except OSError:
        return empty
    prefix = value.get("prefix_sha256")
    if not isinstance(prefix, str) or len(prefix) != 64:
        return empty
    try:
        bytes.fromhex(prefix)
    except ValueError:
        return empty

    raw_pending = value.get("pending")
    raw_terminal = value.get("terminal", [])
    if (
        not isinstance(raw_pending, list)
        or len(raw_pending) > MAX_PENDING_PROJECTIONS
        or not isinstance(raw_terminal, list)
        or len(raw_terminal) > MAX_TERMINAL_DIAGNOSTICS
    ):
        return empty

    def validated_entries(
        raw_entries: list[Any], *, require_row: bool
    ) -> list[dict[str, Any]] | None:
        entries: list[dict[str, Any]] = []
        seen: set[int] = set()
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                return None
            entry = dict(raw_entry)
            index = entry.get("index")
            start = entry.get("line_start")
            end = entry.get("line_end")
            if (
                type(index) is not int
                or index < 0
                or index >= cursor
                or index in seen
                or type(start) is not int
                or type(end) is not int
                or start < 0
                or end <= start
                or end > offset
            ):
                return None
            if require_row:
                row = _read_row_span(path, start, end)
                if row is None or entry.get("row_sha256") != _row_digest(row):
                    return None
            seen.add(index)
            entries.append(entry)
        entries.sort(key=lambda item: item["index"])
        return entries

    pending = validated_entries(raw_pending, require_row=True)
    terminal = validated_entries(raw_terminal, require_row=False)
    if pending is None or terminal is None:
        return empty
    if {entry["index"] for entry in pending} & {
        entry["index"] for entry in terminal
    }:
        return empty
    return {
        "schema": SYNC_MARKER_SCHEMA,
        "cursor": cursor,
        "cursor_offset": offset,
        "prefix_sha256": prefix,
        "prefix_tail_sha256": value["prefix_tail_sha256"],
        "ledger_device": device,
        "ledger_inode": inode,
        "pending": pending,
        "terminal": terminal,
    }


def _write_sync_state(marker: Path, state: dict[str, Any]) -> str | None:
    """Atomically replace Build Loop's sync marker; return an error on failure."""
    temp = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
        with temp.open("w", encoding="utf-8") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, marker)
        return None
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return str(exc)


def _bounded_marker_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:1024]


def _pending_projection(
    index: int,
    row: dict[str, Any],
    projection: dict[str, Any],
    *,
    attempts: int,
    line_start: int,
    line_end: int,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "index": index,
        "row_sha256": _row_digest(row),
        "status": str(projection.get("status") or "failed"),
        "attempts": max(1, int(attempts)),
        "line_start": line_start,
        "line_end": line_end,
    }
    for key in ("backend", "transport", "error", "reason", "event_id", "remedy"):
        value = _bounded_marker_text(projection.get(key))
        if value is not None:
            entry[key] = value
    return entry


def _projection_from_pending(entry: dict[str, Any]) -> dict[str, Any]:
    return _projection_result(
        str(entry.get("status") or "pending"),
        backend=entry.get("backend"),
        transport=entry.get("transport"),
        error=entry.get("error"),
        reason=entry.get("reason"),
        event_id=entry.get("event_id"),
        remedy=entry.get("remedy"),
    )


def _retryable_projection(entry: dict[str, Any]) -> bool:
    # Retrying an outcome_unknown can duplicate a mutation that committed before
    # the response was lost. Preserve it for an operator/read-side reconciliation
    # instead of authorizing another write. Deterministic validation failures also
    # remain visible without consuming every future append's retry budget.
    return entry.get("status") in {"failed", "refused", "unavailable", "pending"}


def _set_sync_entries(
    state: dict[str, Any],
    pending_by_index: dict[int, dict[str, Any]],
    terminal_by_index: dict[int, dict[str, Any]],
) -> None:
    state["pending"] = [pending_by_index[key] for key in sorted(pending_by_index)]
    terminal_keys = sorted(terminal_by_index)[-MAX_TERMINAL_DIAGNOSTICS:]
    state["terminal"] = [terminal_by_index[key] for key in terminal_keys]


def _reconcile_to_rally(
    path: Path,
    workdir: Path,
    *,
    current_index: int | None = None,
    current_offset: int | None = None,
) -> dict[str, Any]:
    """Stream a bounded JSONL slice and return the current row's projection."""
    snapshot_end = _stable_ledger_end(path)
    if snapshot_end is None:
        return _projection_result(
            "pending",
            reason="ledger writer is active; reconciliation deferred",
            write_attempted=False,
        )
    marker = path.parent / SYNC_MARKER_NAME
    state = _load_sync_state(marker, path)
    if int(state["cursor_offset"]) > snapshot_end:
        return _projection_result(
            "pending",
            reason="sync cursor extends past the last complete ledger row",
            write_attempted=False,
        )
    pending_by_index = {
        int(entry["index"]): entry for entry in state["pending"]
    }
    terminal_by_index = {
        int(entry["index"]): entry for entry in state["terminal"]
    }
    receipt_cache: dict[str, Any] = {}
    write_attempts = 0
    processed = 0
    pending_retries = 0
    current_result: dict[str, Any] | None = None
    marker_error: str | None = None

    # Schema upgrades and prior interrupted runs may leave terminal states in
    # pending. Move them to the bounded diagnostic ring before retry selection.
    moved_terminal = False
    for index in list(pending_by_index):
        if _retryable_projection(pending_by_index[index]):
            continue
        terminal_by_index[index] = pending_by_index.pop(index)
        moved_terminal = True
    _set_sync_entries(state, pending_by_index, terminal_by_index)
    if moved_terminal:
        marker_error = _write_sync_state(marker, state)

    # Retry old transient holes first, while reserving most of the bounded batch
    # for rows that have never been attempted.
    for index in sorted(pending_by_index):
        if (
            marker_error is not None
            or pending_retries >= MAX_PENDING_RETRIES_PER_APPEND
            or write_attempts >= MAX_RECONCILE_ATTEMPTS
            or processed >= MAX_RECONCILE_ROWS
        ):
            break
        prior = pending_by_index[index]
        row = _read_row_span(path, int(prior["line_start"]), int(prior["line_end"]))
        if row is None:
            del pending_by_index[index]
            terminal_by_index[index] = {
                **prior,
                "status": "corrupt",
                "reason": "pending ledger row is no longer readable",
            }
            _set_sync_entries(state, pending_by_index, terminal_by_index)
            marker_error = _write_sync_state(marker, state)
            continue
        result = _project_to_rally(workdir, row, receipt_cache=receipt_cache)
        processed += 1
        pending_retries += 1
        if result.get("write_attempted") is not False:
            write_attempts += 1
        if result.get("status") == "projected":
            del pending_by_index[index]
        elif _retryable_projection(result):
            pending_by_index[index] = _pending_projection(
                index,
                row,
                result,
                attempts=int(prior.get("attempts") or 0) + 1,
                line_start=int(prior["line_start"]),
                line_end=int(prior["line_end"]),
            )
        else:
            del pending_by_index[index]
            terminal_by_index[index] = _pending_projection(
                index,
                row,
                result,
                attempts=int(prior.get("attempts") or 0) + 1,
                line_start=int(prior["line_start"]),
                line_end=int(prior["line_end"]),
            )
        if index == current_index:
            current_result = result
        _set_sync_entries(state, pending_by_index, terminal_by_index)
        marker_error = _write_sync_state(marker, state)
        if marker_error:
            break

    # Cursor rows are streamed from the persisted byte offset. No hot append
    # materializes or re-hashes the already-reconciled prefix.
    if marker_error is None:
        for line_start, line_end, line_sha, line_length, row, row_error in _iter_ledger_lines(
            path,
            int(state["cursor_offset"]),
            snapshot_end=snapshot_end,
        ):
            if (
                write_attempts >= MAX_RECONCILE_ATTEMPTS
                or processed >= MAX_RECONCILE_ROWS
                or len(pending_by_index) >= MAX_PENDING_PROJECTIONS
            ):
                break
            state["cursor_offset"] = line_end
            state["prefix_sha256"] = _advance_prefix_digest(
                str(state["prefix_sha256"]), line_sha, line_length
            )
            state["prefix_tail_sha256"] = _prefix_tail_probe(path, line_end)
            if row is None and row_error == "blank":
                marker_error = _write_sync_state(marker, state)
                if marker_error:
                    break
                continue
            index = int(state["cursor"])
            state["cursor"] = index + 1
            processed += 1
            is_current = (
                (current_offset is not None and line_start == current_offset)
                or (current_offset is None and current_index == index)
            )
            if row is None:
                result = _projection_result(
                    "oversize" if row_error == "oversize" else "corrupt",
                    reason=f"local ledger row is {row_error}",
                    write_attempted=False,
                )
                terminal_by_index[index] = {
                    "index": index,
                    "row_sha256": line_sha,
                    "status": result["status"],
                    "attempts": 1,
                    "line_start": line_start,
                    "line_end": line_end,
                    "reason": result["reason"],
                }
            else:
                result = _project_to_rally(
                    workdir, row, receipt_cache=receipt_cache
                )
                if result.get("write_attempted") is not False:
                    write_attempts += 1
                if result.get("status") == "projected":
                    pass
                elif _retryable_projection(result):
                    pending_by_index[index] = _pending_projection(
                        index,
                        row,
                        result,
                        attempts=1,
                        line_start=line_start,
                        line_end=line_end,
                    )
                else:
                    terminal_by_index[index] = _pending_projection(
                        index,
                        row,
                        result,
                        attempts=1,
                        line_start=line_start,
                        line_end=line_end,
                    )
            if is_current:
                current_result = result
            _set_sync_entries(state, pending_by_index, terminal_by_index)
            marker_error = _write_sync_state(marker, state)
            if marker_error:
                break

    if current_result is None:
        pending = pending_by_index.get(current_index)
        if pending is not None:
            current_result = _projection_from_pending(pending)
        elif current_index is not None and current_index in terminal_by_index:
            current_result = _projection_from_pending(
                terminal_by_index[current_index]
            )
        elif current_index is not None and current_index < int(state["cursor"]):
            current_result = _projection_result("projected", reason="already-marked")
        else:
            current_result = _projection_result(
                "pending", reason="reconciliation-batch-limit"
            )
    current_result = dict(current_result)
    current_result["reconciliation"] = {
        "attempted": write_attempts,
        "processed": processed,
        "cursor": state["cursor"],
        "pending": len(pending_by_index),
        "terminal": len(state["terminal"]),
        "remaining_bytes": max(
            0, path.stat().st_size - int(state["cursor_offset"])
        ),
    }
    if marker_error:
        current_result["sync_marker_error"] = marker_error
    return current_result


def _iter_ledger_lines(
    path: Path,
    start_offset: int,
    *,
    snapshot_end: int | None = None,
) -> Iterable[tuple[int, int, str, int, dict[str, Any] | None, str | None]]:
    """Yield bounded decoded rows and hashes from one byte offset."""
    with path.open("rb") as fh:
        file_size = os.fstat(fh.fileno()).st_size
        stable_end = (
            file_size if snapshot_end is None else min(file_size, snapshot_end)
        )
        fh.seek(start_offset)
        while fh.tell() < stable_end:
            line_start = fh.tell()
            line_hash = hashlib.sha256()
            chunks: list[bytes] = []
            total = 0
            ended = False
            while True:
                part = fh.readline(min(64 * 1024, stable_end - fh.tell()))
                if not part:
                    break
                total += len(part)
                line_hash.update(part)
                if total <= MAX_STREAM_LINE_BYTES:
                    chunks.append(part)
                else:
                    chunks.clear()
                if part.endswith(b"\n"):
                    ended = True
                    break
            if total == 0:
                return
            line_end = fh.tell()
            row: dict[str, Any] | None = None
            error: str | None = None
            if not ended:
                error = "torn"
            elif total > MAX_STREAM_LINE_BYTES:
                error = "oversize"
            else:
                raw = b"".join(chunks).strip()
                if not raw:
                    error = "blank"
                else:
                    try:
                        value = json.loads(raw.decode("utf-8"))
                        row = value if isinstance(value, dict) else None
                        if row is None:
                            error = "non-object"
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        error = "malformed"
            yield line_start, line_end, line_hash.hexdigest(), total, row, error


def _acquire_ledger_lock(lock_path: Path, timeout: float = 0.75) -> int | None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.01)
        except OSError:
            os.close(fd)
            raise


def _release_ledger_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("zero-byte append while writing agent ledger")
        offset += written


def _last_newline_offset(fd: int, size: int) -> int:
    cursor = size
    while cursor > 0:
        start = max(0, cursor - 64 * 1024)
        chunk = os.pread(fd, cursor - start, start)
        found = chunk.rfind(b"\n")
        if found >= 0:
            return start + found + 1
        cursor = start
    return 0


def _stable_ledger_end(path: Path) -> int | None:
    """Snapshot the last complete immutable byte while excluding active writes."""
    lock = _acquire_ledger_lock(path.with_name(f".{path.name}.write.lock"))
    if lock is None:
        return None
    try:
        try:
            fd = os.open(str(path), os.O_RDONLY)
        except FileNotFoundError:
            return 0
        try:
            size = os.fstat(fd).st_size
            if size == 0 or os.pread(fd, 1, size - 1) == b"\n":
                return size
            return _last_newline_offset(fd, size)
        finally:
            os.close(fd)
    finally:
        _release_ledger_lock(lock)


def _prune_corrupt_tail_quarantine(
    quarantine: Path,
    *,
    newest: Path,
) -> None:
    """Keep the newest bounded set of torn-tail evidence artifacts."""
    try:
        entries = []
        for candidate in quarantine.glob("*.partial"):
            try:
                stat = candidate.stat()
            except OSError:
                continue
            if candidate.is_file():
                entries.append((candidate, int(stat.st_mtime_ns), int(stat.st_size)))
        entries.sort(
            key=lambda item: (item[0] == newest, item[1], item[0].name),
            reverse=True,
        )
        kept_count = 0
        kept_bytes = 0
        for candidate, _mtime, size in entries:
            keep = bool(
                candidate == newest
                or (
                    kept_count < MAX_CORRUPT_TAIL_FILES
                    and kept_bytes + size <= MAX_CORRUPT_TAIL_BYTES
                )
            )
            if keep:
                kept_count += 1
                kept_bytes += size
                continue
            try:
                candidate.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _repair_torn_ledger_tail(path: Path, fd: int, size: int) -> int:
    clean_size = _last_newline_offset(fd, size)
    quarantine = path.parent / "agent-ledger-corrupt-tails"
    quarantine.mkdir(parents=True, exist_ok=True)
    target = quarantine / f"tail-{time.time_ns()}-{os.getpid()}.partial"
    qfd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        cursor = clean_size
        preserve_end = min(size, clean_size + MAX_CORRUPT_TAIL_BYTES)
        while cursor < preserve_end:
            chunk = os.pread(fd, min(64 * 1024, preserve_end - cursor), cursor)
            if not chunk:
                raise OSError("could not preserve torn agent-ledger tail")
            _write_all(qfd, chunk)
            cursor += len(chunk)
        os.fsync(qfd)
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(qfd)
    _prune_corrupt_tail_quarantine(quarantine, newest=target)
    os.ftruncate(fd, clean_size)
    os.fsync(fd)
    return clean_size


def _append_local_row(path: Path, row: dict[str, Any]) -> tuple[int, int]:
    data = (json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n").encode(
        "utf-8"
    )
    lock = _acquire_ledger_lock(path.with_name(f".{path.name}.write.lock"))
    if lock is None:
        raise OSError("agent-ledger append lock timed out")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            size = os.lseek(fd, 0, os.SEEK_END)
            if size and os.pread(fd, 1, size - 1) != b"\n":
                size = _repair_torn_ledger_tail(path, fd, size)
            start = size
            try:
                _write_all(fd, data)
                os.fsync(fd)
            except Exception:
                try:
                    os.ftruncate(fd, start)
                    os.fsync(fd)
                except OSError:
                    pass
                raise
            return start, start + len(data)
        finally:
            os.close(fd)
    finally:
        _release_ledger_lock(lock)


def append(
    path: Path,
    row: dict[str, Any],
    *,
    workdir: Path | None = None,
) -> dict[str, Any]:
    """Append one row as a JSON line. Fail-open: never raises on I/O error.

    The local JSONL remains authoritative for build gates. After a successful
    local write, its exact row is projected through Rally. ``workdir`` is
    inferred only for ``<repo>/.build-loop/agent-ledger.jsonl``; callers using
    an arbitrary path must opt in by supplying it explicitly. Projection is
    fail-open and reported in the additive ``projection`` envelope field.
    """
    try:
        current_offset, _current_end = _append_local_row(path, row)
    except OSError as exc:  # fail-open: I/O problems don't wedge the build
        return {
            "ok": False,
            "path": str(path),
            "error": str(exc),
            "projection": _projection_result(
                "skipped", reason="local-append-failed"
            ),
        }

    try:
        projection_workdir = (
            Path(workdir).expanduser().resolve()
            if workdir is not None
            else _inferred_projection_workdir(path)
        )
        if (
            projection_workdir is not None
            and path.expanduser().resolve()
            == default_ledger_path(projection_workdir).expanduser().resolve()
        ):
            if _PROJECTION_ACTIVE.get():
                projection = _projection_result(
                    "skipped", reason="recursive-projection"
                )
            else:
                sync_lock = _acquire_ledger_lock(
                    path.with_name(f".{path.name}.sync.lock")
                )
                if sync_lock is None:
                    projection = _projection_result(
                        "pending",
                        reason="another Build Loop is reconciling this ledger",
                        write_attempted=False,
                    )
                else:
                    try:
                        projection = _reconcile_to_rally(
                            path,
                            projection_workdir,
                            current_offset=current_offset,
                        )
                    finally:
                        _release_ledger_lock(sync_lock)
        else:
            projection = _project_to_rally(projection_workdir, row)
    except Exception as exc:  # local append already succeeded; projection stays fail-open
        projection = _projection_result("failed", error=str(exc))
    return {
        "ok": True,
        "path": str(path),
        "error": None,
        "projection": projection,
    }


def read(path: Path) -> list[dict[str, Any]]:
    """Read all rows. Tolerates a torn final line (crash-during-append).

    A JSONL file written append-only can have at most a partial *last* line if
    the process died mid-write; any earlier malformed line is a genuine
    corruption and is skipped (not raised) so a single bad row can't blind the
    whole instrument. Missing file → empty list.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            # Torn/partial line — skip it rather than failing the whole read.
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate rows into the at-a-glance answers the ledger is for.

    Returns counts by action, by status, by (agent, model), by rung, and the
    advisor-specific tally (how often each rung fired, how often the fallback
    fired) — the numbers the A/B test reads to find whether Frontier planning
    actually pays.
    """
    rows = list(rows)
    by_action: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_agent_model: dict[str, int] = {}
    by_rung: dict[str, int] = {}
    advisor_rows: list[dict[str, Any]] = []

    for r in rows:
        a = r.get("action")
        if a:
            by_action[a] = by_action.get(a, 0) + 1
        s = r.get("status")
        if s:
            by_status[s] = by_status.get(s, 0) + 1
        agent = r.get("agent") or "?"
        model = r.get("model") or "?"
        key = f"{agent}:{model}"
        by_agent_model[key] = by_agent_model.get(key, 0) + 1
        rung = r.get("rung")
        if rung is not None:
            by_rung[str(rung)] = by_rung.get(str(rung), 0) + 1
        if agent == "advisor":
            advisor_rows.append(r)

    return {
        "total": len(rows),
        "by_action": by_action,
        "by_status": by_status,
        "by_agent_model": by_agent_model,
        "by_rung": by_rung,
        "advisor_invocations": len(advisor_rows),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=None)
    p.add_argument("--path", default=None, help="Override ledger path (default .build-loop/agent-ledger.jsonl).")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="Append one ledger row.")
    a.add_argument("--run-id", required=True)
    a.add_argument("--agent", required=True)
    a.add_argument("--action", required=True, choices=sorted(ACTIONS))
    a.add_argument("--phase", default=None)
    a.add_argument("--chunk-id", default=None)
    a.add_argument("--tier", default=None)
    a.add_argument("--model", default=None)
    a.add_argument("--rung", type=int, default=None)
    a.add_argument("--status", default=None, choices=sorted(STATUSES))
    a.add_argument("--trigger", default=None)
    a.add_argument("--refs", default=None, help="JSON object of input/output refs.")
    a.add_argument("--note", default=None)

    sub.add_parser("read", help="Print all rows as a JSON array.")
    sub.add_parser("summarize", help="Print the aggregate summary as JSON.")

    args = p.parse_args(argv)
    workdir = Path(args.workdir or ".").expanduser().resolve()
    path = Path(args.path).expanduser() if args.path else default_ledger_path(workdir)

    if args.cmd == "append":
        refs = None
        if args.refs:
            try:
                refs = json.loads(args.refs)
            except json.JSONDecodeError as exc:
                print(json.dumps({"ok": False, "error": f"--refs not valid JSON: {exc}"}), file=sys.stderr)
                return 1
        try:
            row = build_row(
                run_id=args.run_id,
                agent=args.agent,
                action=args.action,
                phase=args.phase,
                chunk_id=args.chunk_id,
                tier=args.tier,
                model=args.model,
                rung=args.rung,
                status=args.status,
                trigger=args.trigger,
                refs=refs,
                note=args.note,
            )
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
        envelope = append(
            path,
            row,
            workdir=workdir if args.workdir is not None else None,
        )
        print(json.dumps(envelope, indent=2))
        # Fail-OPEN on I/O: a ledger (telemetry) outage must never wedge a build
        # whose only "failure" was that the instrument couldn't write. The build is
        # the product; the ledger is the instrument. Input/caller errors above
        # (bad action / bad --refs JSON) still exit nonzero — those are author
        # mistakes, not runtime outages — but a write failure here exits 0 with
        # ok:false in the envelope so the orchestrator can surface it without halting.
        return 0

    if args.cmd == "read":
        print(json.dumps(read(path), indent=2))
        return 0

    if args.cmd == "summarize":
        print(json.dumps(summarize(read(path)), indent=2))
        return 0

    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
