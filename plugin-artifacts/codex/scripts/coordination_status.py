#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Cheap sensor poll of multi-session coordination state (clear/warn/blocked).
#   application: coordination
#   status: active
"""Summarize Build Loop coordination state without spending LLM tokens.

This is the cheap sensor layer for multi-agent coordination. It reads App
Pulse, the repo-local coordination note, and git status, then emits a compact
``clear | warn | blocked`` JSON envelope. Agents should read the full
coordination markdown only when this script reports ``warn`` or ``blocked``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from rally_point import (  # noqa: E402
    actor_identity,
    changes,
    channel_paths,
    decay,
    hook_budget,
    inbox,
    presence,
    revision,
    task_heartbeat,
)
from rally_point.checkpoint import sanitize_change_for_surface  # noqa: E402
from rally_point.backend_adapter import (  # noqa: E402
    NativeResult,
    native_inbox_snapshot,
    native_room_summary,
    recent as _native_recent,
    resolve_context,
    room_snapshot,
    status_read,
)
from rally_point.coordination_policy import load_policy as _load_coord_policy  # noqa: E402
from rally_point.discovery_bridge import resolve as _bridge_resolve  # noqa: E402


def _resolve_channel_dir(workdir: Path) -> tuple[str, Path, str]:
    """Resolve (slug, channel_dir, resolved_via) via the shared bridge.

    β1: delegates to ``scripts/rally_point/discovery_bridge.resolve``.
    The bridge handles env override → PATH binary → Python import →
    internal fallback in priority order, and refuses to operate on a
    protocol-version mismatch. Returns the legacy three-tuple shape for
    backward compatibility with the existing call sites; new callers
    should call ``_bridge_resolve`` directly and consume the full
    envelope.
    """
    envelope = _bridge_resolve(workdir)
    return envelope.app_slug, Path(envelope.channel_dir), envelope.resolved_via

VERDICT_RE = re.compile(
    r"^###\s+(?P<stamp>\d{4}-\d{2}-\d{2}.*?)\s+—\s+"
    r"(?P<actor>[A-Za-z0-9_-]+)\s+(?P<label>[A-Z]+(?:[/-][A-Z]+)*)\s*$"
)
STEP_RE = re.compile(r"^\*\*Step:\*\*\s*(?P<step>.+?)\s*$")
VERDICT_LINE_RE = re.compile(r"^\*\*Verdict:\*\*\s*(?P<verdict>.+?)\s*$")
BLOCKING_VERDICTS = {"BLOCKED", "VARIANCE", "PARTIAL / BLOCKED"}


def _path_keys(value: str, workdir: Path) -> set[str]:
    p = Path(value)
    keys = {value, p.as_posix()}
    try:
        abs_path = p if p.is_absolute() else (workdir / p)
        abs_resolved = abs_path.resolve(strict=False)
        keys.add(abs_resolved.as_posix())
        try:
            keys.add(abs_resolved.relative_to(workdir.resolve()).as_posix())
        except ValueError:
            pass
    except OSError:
        pass
    return {k for k in keys if k}


def _load_owned_files(args: argparse.Namespace, workdir: Path) -> list[str]:
    out: list[str] = []
    out.extend(args.owned_file or [])
    if args.owned_files:
        p = Path(args.owned_files)
        if not p.is_absolute():
            p = workdir / p
        try:
            raw = p.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        if raw:
            if raw.startswith("["):
                try:
                    vals = json.loads(raw)
                    if isinstance(vals, list):
                        out.extend(str(v) for v in vals)
                except json.JSONDecodeError:
                    out.extend(line.strip() for line in raw.splitlines())
            else:
                out.extend(line.strip() for line in raw.splitlines())
    if args.owned_files_csv:
        out.extend(v.strip() for v in args.owned_files_csv.split(","))
    return [v for v in out if v]


def _load_files_in_flight(args: argparse.Namespace) -> list[str]:
    """Return the list of files this session is currently touching.

    Populated from ``--files-in-flight`` (comma-separated).  When the flag
    is omitted the list is empty and peer-overlap detection is skipped
    (can't compute intersection without our side declared).

    Uses ``getattr`` so callers that share the ``args`` namespace without
    defining ``--files-in-flight`` (e.g. ``coordination_watch.py``) degrade
    gracefully to an empty list rather than raising ``AttributeError``.
    """
    raw = getattr(args, "files_in_flight", None)
    if not raw:
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


def _read_inbox_unread_counts(channel_dir: Path, tool: str, session_id: str) -> dict[str, int]:
    """Count direct, broadcast, and total unread inbox lines for ``tool``.

    β1 channel-split fix: takes the resolved ``channel_dir`` directly
    instead of re-deriving it via ``channel_paths.app_channel_dir(slug)``.
    The legacy form silently read inbox counts from the wrong root when
    discovery returned a canonical path but this helper still resolved
    via the internal apps root. See ``coordination-substrate-canonical``
    §"channel-consistency invariant".
    """
    return inbox.unread_counts(channel_dir, tool, session_id=session_id)


def _read_inbox_latest_messages(channel_dir: Path, tool: str, session_id: str) -> list[dict[str, Any]]:
    """Return compact inbox doorbell summaries for ``tool``."""
    return inbox.latest_message_summaries(
        channel_dir,
        tool=tool,
        limit=3,
        unread_only=True,
        session_id=session_id,
    )


def _read_task_heartbeat(args: argparse.Namespace, channel_dir: Path, tool: str) -> dict[str, Any]:
    """Return task heartbeat health for the current session/tool."""
    return task_heartbeat.summarize_task_health(
        channel_dir,
        tool=tool,
        session_id=args.session_id,
        expected_ref=getattr(args, "task_ref", None),
        now=getattr(args, "task_heartbeat_now", None),
        grace_seconds=getattr(
            args,
            "task_heartbeat_grace_seconds",
            task_heartbeat.DEFAULT_GRACE_SECONDS,
        ),
    )


def _require_native(result: NativeResult, operation: str) -> None:
    """Refuse a shadow read when the selected native authority cannot answer."""
    if not result.ok:
        detail = result.reason or result.status
        raise RuntimeError(f"native Rally {operation} failed: {detail}")


def _coordination_refusal_status(
    args: argparse.Namespace,
    *,
    context: Any,
    workdir: Path,
) -> dict[str, Any]:
    """Return a useful warning without reading any private fallback store."""
    envelope = context.envelope
    identity = actor_identity.resolve_identity(
        getattr(args, "tool", None) or "claude_code",
        args.session_id,
    )
    counts = {"direct": 0, "broadcast": 0, "total": 0}
    return {
        "schema_version": "1.0",
        "status": "warn",
        "required_action": "restore_coordination_authority",
        "coordination_refused": True,
        "coordination_unavailable": envelope.coordination_unavailable,
        "reason": envelope.refusal_reason,
        "remedy": envelope.refusal_remedy,
        "workdir": str(workdir),
        "app_slug": envelope.app_slug,
        "channel_dir": str(envelope.channel_dir),
        "resolved_via": envelope.resolved_via,
        "backend": envelope.backend,
        "transport": envelope.transport,
        "session_id": args.session_id,
        "tool": identity.base_tool,
        "rally_tool": identity.native_tool,
        "revision": 0,
        "active_peers": [],
        "overlaps": [],
        "peer_overlap_files": [],
        "direct_inbox_unread_count": 0,
        "broadcast_inbox_unread_count": 0,
        "inbox_unread_count": 0,
        "inbox_unread_counts": counts,
        "inbox_latest_messages": [],
        "inbox_coverage_incomplete": True,
        "inbox_coverage": {"repo_recent_available": False, "reasons": ["coordination_refused"]},
        "task_heartbeat": {"health": "unknown", "reason": "coordination_refused"},
        "rejection_count": 0,
        "escalation_count": 0,
        "blocked_verdict_count": 0,
        "latest_escalation": None,
        "open_escalations": [],
        "coordination_file": None,
        "latest_verdicts": [],
        "unresolved": [],
        "dirty_files": [],
        "dirty_files_unknown": True,
        "dirty_outside_owned": [],
        "new_changes": [],
    }


def _native_status_states(result: NativeResult) -> dict[str, dict[str, Any]]:
    payload = result.payload if isinstance(result.payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    status = data.get("status_read")
    status = status if isinstance(status, dict) else {}
    rows = status.get("states")
    rows = rows if isinstance(rows, list) else []
    return {
        str(row["tool"]): row
        for row in rows
        if isinstance(row, dict) and row.get("tool")
    }


def _claim_owned_paths(claim: dict[str, Any]) -> list[str]:
    """Project path ownership from canonical Rally claim scope/evidence."""
    owned: list[str] = []
    for raw_scope in claim.get("scope") or []:
        if not isinstance(raw_scope, str):
            continue
        scope = raw_scope.strip()
        for prefix in (
            "exclusive:",
            "shared_read:",
            "shared-read:",
            "advisory:",
            "namespace:",
        ):
            if scope.startswith(prefix):
                scope = scope[len(prefix):]
                break
        kind, separator, identifier = scope.partition(":")
        if separator and kind in {"file", "dir"} and identifier.strip():
            owned.append(identifier.strip())
    # Older claims may expose only source-grounding evidence. Preserve that
    # compatibility without treating arbitrary evidence strings as paths.
    for evidence in claim.get("evidence") or []:
        if not isinstance(evidence, str) or not evidence.startswith("claimhash:"):
            continue
        path = evidence[len("claimhash:"):].split("=", 1)[0].strip()
        if path.startswith("file:"):
            path = path[len("file:"):]
        if path:
            owned.append(path)
    return list(dict.fromkeys(owned))


def _native_active_peers(
    room_result: NativeResult,
    status_result: NativeResult,
    *,
    requesting_tool: str,
) -> list[dict[str, Any]]:
    """Merge live squads, typed status, and active claim ownership."""
    summary = native_room_summary(room_result)
    states = _native_status_states(status_result)
    claims = summary.get("active_claims")
    claims = claims if isinstance(claims, list) else []
    owns_by_tool: dict[str, list[str]] = {}
    for item in claims:
        if not isinstance(item, dict):
            continue
        fact = item.get("fact") if isinstance(item.get("fact"), dict) else item
        owner = fact.get("tool")
        if not owner:
            continue
        current = owns_by_tool.setdefault(str(owner), [])
        current.extend(
            path for path in _claim_owned_paths(fact) if path not in current
        )

    squads = summary.get("squads")
    squads = squads if isinstance(squads, list) else []
    peers: list[dict[str, Any]] = []
    for squad in squads:
        if not isinstance(squad, dict) or not squad.get("tool"):
            continue
        tool = str(squad["tool"])
        state = states.get(tool, {})
        if (
            tool == requesting_tool
            or squad.get("status") != "active"
            or state.get("stale") is True
            or state.get("state") == "done"
        ):
            continue
        state_name = str(state.get("state") or "active")
        working_file = state.get("file") if state_name == "working" else None
        peers.append(
            {
                # Native Rally identities are already session-unique tool ids.
                "session_id": tool,
                "tool": tool,
                "phase": state_name,
                "files_in_flight": [str(working_file)] if working_file else [],
                "owns": owns_by_tool.get(tool, []),
            }
        )
    return peers


def _native_recent_rows(result: NativeResult) -> list[dict[str, Any]]:
    payload = result.payload if isinstance(result.payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    recent = data.get("recent")
    recent = recent if isinstance(recent, dict) else {}
    rows = recent.get("rows")
    return rows if isinstance(rows, list) else []


def _native_recent_coverage(result: NativeResult) -> dict[str, Any]:
    """Describe whether the bounded native recent window proves absence."""
    rows = _native_recent_rows(result)
    payload = result.payload if isinstance(result.payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    recent = data.get("recent") if isinstance(data.get("recent"), dict) else {}
    limit = recent.get("limit")
    if type(limit) is not int or limit <= 0:
        limit = 500
    saturated = len(rows) >= limit
    return {
        "coverage_incomplete": saturated,
        "rows_inspected": len(rows),
        "limit": limit,
        "reasons": ["native_recent_limit_saturated"] if saturated else [],
    }


def _same_canonical_path(left: Any, right: Path) -> bool:
    if not isinstance(left, (str, os.PathLike)):
        return False
    try:
        return Path(left).expanduser().resolve(strict=False) == right
    except OSError:
        return False


def _normalize_native_recent(
    result: NativeResult,
    *,
    repo_root: Path,
    app_slug: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Normalize only the current repo's native recent facts, oldest-first."""
    normalized: list[dict[str, Any]] = []
    for row in _native_recent_rows(result):
        if not isinstance(row, dict) or not _same_canonical_path(
            row.get("repo_root"), repo_root
        ):
            continue
        fact = row.get("fact")
        record = row.get("record")
        if isinstance(fact, dict):
            source = {
                "seq": row.get("seq") or fact.get("seq"),
                "occurred_at": row.get("created_at") or fact.get("created_at"),
                "event_type": fact.get("kind") or "unknown",
                "payload": fact,
                "engagement": app_slug,
            }
        elif isinstance(record, dict):
            source = dict(record)
        else:
            continue
        change = changes.normalize_record(source)
        if not isinstance(change, dict):
            continue
        if not change.get("app_slug"):
            change["app_slug"] = app_slug
        revision_value = row.get("seq") or row.get("local_seq")
        if not change.get("revision") and type(revision_value) is int:
            change["revision"] = max(0, revision_value)
        normalized.append(change)
    normalized.sort(
        key=lambda record: (
            int(record.get("revision") or 0),
            str(record.get("event_id") or ""),
        )
    )
    if limit <= 0:
        return []
    return normalized[-limit:]


def _heartbeat_records_from_changes(
    recent_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for change in recent_changes:
        payload = change.get("payload")
        if not isinstance(payload, dict):
            continue
        candidate = payload.get("task_heartbeat")
        if not isinstance(candidate, dict) and payload.get("kind") == "task-heartbeat":
            candidate = payload
        if isinstance(candidate, dict):
            records.append(candidate)
    return records


def _read_rejection_count(channel_dir: Path) -> int:
    """Count MECE rejections logged to ``<channel_dir>/rejections.jsonl``.

    Surfaces the C4 ``mece_gate.log_rejection`` output so peers can see
    when malformed handoff posts are being rejected without inspecting
    the file directly.  Blank lines are ignored.  Returns 0 when the file
    is absent or unreadable.

    β1 channel-split fix: takes the resolved ``channel_dir`` (not a slug)
    so the rejection count sources from the same root the rest of the
    envelope uses.
    """
    try:
        rej_file = Path(channel_dir) / "rejections.jsonl"
        text = rej_file.read_text(encoding="utf-8")
        return sum(1 for line in text.splitlines() if line.strip())
    except OSError:
        return 0


def _default_coordination_file(workdir: Path) -> Path | None:
    """Pick the active coordination file by direct directory scan.

    SEC-001: this function never dereferences a path value read from a
    writable JSON pointer (``active.json``). A pointer file is attacker-
    controllable — any process that can write ``coordination/`` could aim
    it at an arbitrary ``.md`` (and ``resolve()`` ran before the
    containment check, so a symlink could escape the directory). Instead
    we enumerate ``coordination/*.md`` directly: ``glob`` only yields
    real entries inside ``root``, and each candidate is confirmed to be a
    regular file. The selection heuristic (prefer ``audit-execution-*``,
    then oldest mtime) is deterministic and reads no external pointer.
    """
    root = workdir / ".build-loop" / "coordination"
    try:
        candidates = [p for p in root.glob("*.md") if p.is_file()]
    except OSError:
        return None
    if not candidates:
        return None
    audit_runs = [p for p in candidates if p.name.startswith("audit-execution-")]
    pool = audit_runs or candidates
    try:
        return min(pool, key=lambda p: p.stat().st_mtime)
    except OSError:
        return sorted(pool)[0]


def _parse_coordination_verdicts(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        m = VERDICT_RE.match(line)
        if m:
            if current:
                entries.append(current)
            current = {
                "stamp": m.group("stamp").strip(),
                "actor": m.group("actor").strip(),
                "label": m.group("label").strip(),
                "step": "",
                "verdict": m.group("label").strip(),
            }
            continue
        if current is None:
            continue
        m = STEP_RE.match(line)
        if m:
            current["step"] = m.group("step").strip()
            continue
        m = VERDICT_LINE_RE.match(line)
        if m:
            current["verdict"] = m.group("verdict").strip()
    if current:
        entries.append(current)
    return entries


def _latest_by_step(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for entry in entries:
        step = entry.get("step") or "(unspecified)"
        latest[step] = entry
    return list(latest.values())


def _change_recency_weight(rec: dict[str, Any], now: float, half_life_secs: int) -> float:
    """Recency weight for a change record from its epoch-float ``ts``.

    Fails OPEN: a record with a missing/unparseable ``ts`` is treated as fresh
    (weight 1.0) and never hidden by decay.
    """
    raw = rec.get("ts")
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if ts <= 0:
        return 1.0
    return decay.recency_weight(now - ts, half_life_secs)


def _read_recent_changes(
    channel_dir: Path,
    max_changes: int,
    *,
    workdir: Path | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Recent coordination changes, recency-ordered with an archive floor.

    Records are ordered fresh-first by recency weight (the historical-message
    listing surface — the build-loop equivalent of ``rally room``/``recent``).
    A record whose weight falls below the archive floor is excluded unless
    ``include_archived`` is set. Active state is unaffected — this only orders
    the change-log stream. Fails open on a malformed ``ts``.
    """
    recs, _offset = changes.read_changes_since(channel_dir, 0)
    policy = _load_coord_policy(Path(workdir) if workdir else Path.cwd())
    now = time.time()
    hl = policy.half_life_secs
    floor = policy.archive_floor_weight
    if include_archived:
        # Fold in physically-rotated (archived) change logs for retrieval.
        # Each source resolves retractions within its own batch, so re-resolve
        # across the merged list: a fact rotated into the archive can be
        # retracted by a record that only exists in the live log.
        recs = changes.apply_retractions(
            recs + changes.read_archived_changes(channel_dir)
        )
    weighted = [(_change_recency_weight(r, now, hl), r) for r in recs]
    if not include_archived:
        weighted = [(w, r) for (w, r) in weighted if not decay.is_archivable(w, floor)]
    # Fresh-first by weight; preserve original order for equal weights (stable).
    weighted.sort(key=lambda wr: wr[0], reverse=True)
    ordered = [r for (_w, r) in weighted]
    return ordered[:max_changes]


def _git_dirty_files(workdir: Path) -> tuple[list[str], bool]:
    """Return (dirty_paths, unknown). ``unknown`` is True when the git probe
    timed out — the caller must NOT read an empty list as 'clean' in that case
    (a timed-out probe masking a real dirty repo would silently suppress a
    peer-overlap warning)."""
    try:
        result = subprocess.run(
            # --no-optional-locks: never block on index.lock during concurrent
            # git/rally ops (the transient trigger of the 3s-budget overrun).
            ["git", "--no-optional-locks", "-C", str(workdir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            # Child budget < parent (session_probe) budget < outer hook budget.
            timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_CHILD),
        )
    except subprocess.TimeoutExpired:
        return [], True  # could-not-determine — distinct from clean
    except (OSError, subprocess.SubprocessError):
        return [], False
    if result.returncode != 0:
        return [], False
    out: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        out.append(path)
    return out, False


def build_status(args: argparse.Namespace) -> dict[str, Any]:
    workdir = Path(args.workdir).expanduser().resolve()
    session_id = args.session_id
    context = resolve_context(workdir)
    envelope = context.envelope
    slug = envelope.app_slug
    channel_dir = Path(envelope.channel_dir)
    resolved_via = envelope.resolved_via
    if not context.native and envelope.backend != "build-loop-local":
        return _coordination_refusal_status(
            args,
            context=context,
            workdir=workdir,
        )
    owned_files = _load_owned_files(args, workdir)
    owned_key_map = {f: _path_keys(f, workdir) for f in owned_files}
    owned_keys = set().union(*owned_key_map.values()) if owned_key_map else set()

    # Files this session is actively touching right now (from --files-in-flight).
    # Used for ownership-aware peer overlap detection: warn only when a peer's
    # declared ``owns`` set intersects *our* files_in_flight, not merely because
    # peers exist.
    this_session_files_in_flight = _load_files_in_flight(args)
    fif_key_map = {f: _path_keys(f, workdir) for f in this_session_files_in_flight}
    fif_keys = set().union(*fif_key_map.values()) if fif_key_map else set()

    # Keep the CLI's user-facing host family separate from native Rally's
    # session-unique routing actor. Local fallback inbox/presence semantics
    # remain base-tool + session-id scoped.
    identity = actor_identity.resolve_identity(
        getattr(args, "tool", None) or "claude_code",
        session_id,
    )
    requesting_base_tool = identity.base_tool
    requesting_tool = identity.native_tool if context.native else requesting_base_tool

    native_room_result: NativeResult | None = None
    native_recent_result: NativeResult | None = None
    if context.native:
        native_room_result = room_snapshot(
            context,
            actor=requesting_tool,
            readers=True,
        )
        native_status_result = status_read(context)
        # ``recent --all`` is workspace-wide. Fetch the bounded maximum first,
        # then filter to this exact canonical repo root before applying the
        # caller's requested display limit.
        native_recent_result = _native_recent(context, limit=500)
        _require_native(native_room_result, "room read")
        _require_native(native_status_result, "status read")
        _require_native(native_recent_result, "recent read")
        active_peers = _native_active_peers(
            native_room_result,
            native_status_result,
            requesting_tool=requesting_tool,
        )
    else:
        active_peers = presence.read_active_presence(
            channel_dir, exclude_session=session_id
        )

    # Legacy overlap: peer's files_in_flight vs our owned_files.
    overlaps: list[dict[str, Any]] = []
    for peer in active_peers:
        peer_files = peer.get("files_in_flight") or []
        peer_keys: dict[str, set[str]] = {
            str(f): _path_keys(str(f), workdir) for f in peer_files
        }
        matched: list[str] = []
        for peer_file, keys in peer_keys.items():
            if keys.intersection(owned_keys):
                matched.append(peer_file)
        if matched:
            overlaps.append({
                "peer": peer.get("session_id"),
                "tool": peer.get("tool"),
                "phase": peer.get("phase"),
                "files": sorted(matched),
                "severity": "warning",
                "reason": "active_conflict",
            })

    # Ownership-aware overlap: peer's ``owns`` vs our files_in_flight.
    # This is the primary warn trigger.  ``overlaps`` (legacy) is retained
    # for backward compat but does NOT drive warn independently.
    peer_overlap_files: list[str] = []
    for peer in active_peers:
        peer_owns = peer.get("owns") or []
        if not peer_owns or not fif_keys:
            continue
        peer_owns_keys: dict[str, set[str]] = {
            str(f): _path_keys(str(f), workdir) for f in peer_owns
        }
        for owned_file, keys in peer_owns_keys.items():
            if keys.intersection(fif_keys) and owned_file not in peer_overlap_files:
                peer_overlap_files.append(owned_file)
    peer_overlap_files = sorted(peer_overlap_files)

    coordination_file = (
        Path(args.coordination_file).expanduser()
        if args.coordination_file else _default_coordination_file(workdir)
    )
    if coordination_file and not coordination_file.is_absolute():
        coordination_file = workdir / coordination_file
    verdict_entries = _latest_by_step(_parse_coordination_verdicts(coordination_file))
    unresolved = [
        v for v in verdict_entries
        if (v.get("verdict") or v.get("label", "")).upper() in BLOCKING_VERDICTS
    ]

    dirty, dirty_unknown = _git_dirty_files(workdir)
    dirty_outside_owned = []
    for path in dirty:
        keys = _path_keys(path, workdir)
        if owned_keys and keys.intersection(owned_keys):
            continue
        dirty_outside_owned.append(path)

    if context.native:
        assert native_room_result is not None
        assert native_recent_result is not None
        repo_root = channel_dir.expanduser().parent.resolve(strict=False)
        native_changes = _normalize_native_recent(
            native_recent_result,
            repo_root=repo_root,
            app_slug=slug,
            limit=500,
        )
        recent_changes = (
            native_changes[-args.max_changes:]
            if args.max_changes > 0
            else []
        )
        room_summary = native_room_summary(native_room_result)
        max_seq = room_summary.get("max_seq")
        current_revision = max_seq if type(max_seq) is int and max_seq >= 0 else 0
        native_inbox = native_inbox_snapshot(
            native_room_result,
            tool=requesting_tool,
            recent_result=native_recent_result,
        )
        inbox_counts = native_inbox["counts"]
        inbox_latest_messages = native_inbox["latest"]
        inbox_coverage_incomplete = bool(
            native_inbox.get("coverage_incomplete")
        )
        inbox_coverage = dict(native_inbox.get("coverage") or {})
        task_heartbeat_status = task_heartbeat.summarize_task_health_records(
            _heartbeat_records_from_changes(native_changes),
            tool=requesting_tool,
            session_id=identity.session_id,
            expected_ref=getattr(args, "task_ref", None),
            now=getattr(args, "task_heartbeat_now", None),
            grace_seconds=getattr(
                args,
                "task_heartbeat_grace_seconds",
                task_heartbeat.DEFAULT_GRACE_SECONDS,
            ),
        )
        heartbeat_recent_coverage = _native_recent_coverage(native_recent_result)
        if (
            heartbeat_recent_coverage["coverage_incomplete"]
            and task_heartbeat_status.get("health") in {"missing", "none"}
        ):
            task_heartbeat_status = {
                **task_heartbeat_status,
                "health": "unknown",
                "reason": "native_recent_limit_saturated",
                "coverage_incomplete": True,
                "coverage": heartbeat_recent_coverage,
            }
        else:
            task_heartbeat_status = {
                **task_heartbeat_status,
                "coverage_incomplete": False,
                "coverage": heartbeat_recent_coverage,
            }
        # Native Rally rejects malformed mutations before commit and exposes
        # typed command failures; it has no Build Loop rejections sidecar.
        rejection_count = 0
    else:
        recent_changes = _read_recent_changes(
            channel_dir,
            args.max_changes,
            workdir=workdir,
            include_archived=getattr(args, "include_archived", False),
        )
        current_revision = revision.read_revision(channel_dir)
        inbox_counts = _read_inbox_unread_counts(
            channel_dir,
            requesting_tool,
            session_id,
        )
        inbox_latest_messages = _read_inbox_latest_messages(
            channel_dir,
            requesting_tool,
            session_id,
        )
        inbox_coverage_incomplete = False
        inbox_coverage = {
            "repo_recent_available": True,
            "reasons": [],
        }
        task_heartbeat_status = _read_task_heartbeat(
            args,
            channel_dir,
            requesting_tool,
        )
        rejection_count = _read_rejection_count(channel_dir)
    # SEC-002 — ``new_changes`` is surfaced into orchestrator LLM context.
    # changes.jsonl is unauthenticated (trusted-local-peers-only); sanitize
    # each record to known structured metadata + length-capped free text
    # before it reaches a prompt. Escalation derivation below still reads
    # the RAW ``recent_changes`` (it only inspects structured fields).
    new_changes = [
        sanitize_change_for_surface(c)
        for c in recent_changes
        if args.since_revision is None
        or int(c.get("revision", 0)) > args.since_revision
    ]

    # G3 — escalation salience. An `escalation`-kind change record marks
    # "needs lead or user attention now", distinct from routine phase/
    # feedback. Surface the open count + the most-recent escalation, and
    # treat an open escalation as `blocked` so the cheap sensor flags it
    # without the caller reading the full changes.jsonl. An escalation is
    # acknowledged once a later record carries `payload.acknowledges`.
    escalation_records = [
        c for c in recent_changes if c.get("kind") == "escalation"
    ]
    acknowledged_revs: set[int] = set()
    for rec in escalation_records:
        payload = rec.get("payload") or {}
        ack = payload.get("acknowledges") if isinstance(payload, dict) else None
        if isinstance(ack, int):
            acknowledged_revs.add(ack)
    open_escalations = [
        rec for rec in escalation_records
        if int(rec.get("revision", 0)) not in acknowledged_revs
        and not (rec.get("payload") or {}).get("acknowledges")
    ]
    escalation_count = len(open_escalations)
    latest_escalation = open_escalations[-1] if open_escalations else None
    # BLOCKED-verdict count: the most-urgent slice of `unresolved`.
    blocked_verdict_count = sum(
        1 for v in unresolved
        if "BLOCKED" in (v.get("verdict") or v.get("label", "")).upper()
    )

    heartbeat_health = task_heartbeat_status.get("health")
    heartbeat_coverage_incomplete = bool(
        task_heartbeat_status.get("coverage_incomplete")
    )
    heartbeat_blocking = heartbeat_health in {"blocked", "needs_attention"}
    heartbeat_warn = heartbeat_health in {
        "stale_check_in",
        "missing",
        "wrong_task",
        "drift_risk",
    }

    if unresolved or escalation_count or heartbeat_blocking:
        status = "blocked"
        if heartbeat_blocking and not escalation_count and not unresolved:
            required_action = "review_task_heartbeat_attention"
        elif heartbeat_blocking:
            required_action = "resolve_escalations_verdicts_or_heartbeat_attention"
        elif escalation_count and not unresolved:
            required_action = "resolve_open_escalations"
        elif escalation_count:
            required_action = "resolve_escalations_and_coordination_verdicts"
        else:
            required_action = "resolve_unresolved_coordination_verdicts"
    elif (
        peer_overlap_files
        or dirty_outside_owned
        or heartbeat_warn
        or dirty_unknown
        or inbox_coverage_incomplete
        or heartbeat_coverage_incomplete
    ):
        # warn only when a peer's ``owns`` intersects our files_in_flight,
        # OR when dirty files exist outside our owned set, OR when the dirty
        # probe timed out (unknown != clean — never silently suppress).  Raw
        # peer count does NOT trigger warn (prevents false positives when
        # peers share no files with us).
        status = "warn"
        if (
            heartbeat_coverage_incomplete
            and inbox_coverage_incomplete
            and not (
                peer_overlap_files
                or dirty_outside_owned
                or heartbeat_warn
                or dirty_unknown
            )
        ):
            required_action = "inspect_native_coordination_coverage"
        elif heartbeat_coverage_incomplete and not (
            peer_overlap_files
            or dirty_outside_owned
            or heartbeat_warn
            or dirty_unknown
            or inbox_coverage_incomplete
        ):
            required_action = (
                "inspect_native_heartbeat_coverage"
                if task_heartbeat_status.get("reason")
                == "native_recent_limit_saturated"
                else "inspect_task_heartbeat_coverage"
            )
        elif inbox_coverage_incomplete and not (
            peer_overlap_files
            or dirty_outside_owned
            or heartbeat_warn
            or dirty_unknown
            or heartbeat_coverage_incomplete
        ):
            required_action = "inspect_native_inbox_coverage"
        elif dirty_unknown and not (peer_overlap_files or dirty_outside_owned):
            required_action = "dirty_probe_timed_out_rerun_status"
        elif heartbeat_warn and not (peer_overlap_files or dirty_outside_owned):
            required_action = "review_task_heartbeat_health"
        else:
            required_action = "review_peer_overlap_or_dirty_files"
    else:
        status = "clear"
        required_action = "none"

    return {
        "schema_version": "1.0",
        "status": status,
        "required_action": required_action,
        "workdir": str(workdir),
        "app_slug": slug,
        "channel_dir": str(channel_dir),
        "resolved_via": resolved_via,
        "session_id": session_id,
        "tool": requesting_base_tool,
        "rally_tool": identity.native_tool,
        "revision": current_revision,
        "active_peers": [
            {
                "session_id": p.get("session_id"),
                "tool": p.get("tool"),
                "phase": p.get("phase"),
                "files_in_flight_count": len(p.get("files_in_flight") or []),
            }
            for p in active_peers
        ],
        "overlaps": overlaps,
        "peer_overlap_files": peer_overlap_files,
        "direct_inbox_unread_count": inbox_counts["direct"],
        "broadcast_inbox_unread_count": inbox_counts["broadcast"],
        "inbox_unread_count": inbox_counts["total"],
        "inbox_unread_counts": inbox_counts,
        "inbox_latest_messages": inbox_latest_messages,
        "inbox_coverage_incomplete": inbox_coverage_incomplete,
        "inbox_coverage": inbox_coverage,
        "task_heartbeat": task_heartbeat_status,
        "rejection_count": rejection_count,
        "escalation_count": escalation_count,
        "blocked_verdict_count": blocked_verdict_count,
        "latest_escalation": (
            sanitize_change_for_surface(latest_escalation)
            if latest_escalation else None
        ),
        "open_escalations": [
            sanitize_change_for_surface(rec) for rec in open_escalations
        ],
        "coordination_file": str(coordination_file) if coordination_file else None,
        "latest_verdicts": verdict_entries,
        "unresolved": unresolved,
        "dirty_files": dirty,
        "dirty_files_unknown": dirty_unknown,
        "dirty_outside_owned": dirty_outside_owned,
        "new_changes": new_changes,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--session-id", required=True)
    p.add_argument("--owned-file", action="append", default=[])
    p.add_argument("--owned-files", default=None, help="Path to newline or JSON list")
    p.add_argument("--owned-files-csv", default=None)
    p.add_argument(
        "--files-in-flight",
        default=None,
        help="Comma-separated list of files this session is currently touching. "
             "Used for ownership-aware peer overlap detection: warn fires when a "
             "peer's ``owns`` set intersects these paths. Omit when unknown — "
             "peer_overlap_files will be [] (cannot compute without our side).",
    )
    p.add_argument(
        "--tool",
        default="claude_code",
        help="Tool name for inbox unread count lookup (default: claude_code).",
    )
    p.add_argument("--coordination-file", default=None)
    p.add_argument("--since-revision", type=int, default=None)
    p.add_argument("--max-changes", type=int, default=20)
    p.add_argument(
        "--include-archived",
        action="store_true",
        help="re-include recency-decayed (archived) coordination changes",
    )
    p.add_argument(
        "--task-ref",
        default=None,
        help="Expected active task/claim/run ref for task-heartbeat health.",
    )
    p.add_argument(
        "--task-heartbeat-grace-seconds",
        type=int,
        default=task_heartbeat.DEFAULT_GRACE_SECONDS,
    )
    p.add_argument(
        "--task-heartbeat-now",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    status = build_status(args)
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        # Channel-discovery header (first line): the system gap was that
        # fresh agents had no way to learn where they'd joined. Surface the
        # global channel_dir up-front; `rally where` is the standalone form.
        # JSON output already carries `channel_dir` — do not duplicate there.
        print(f"channel: {status['channel_dir']}")
        line = f"{status['status']}: {status['required_action']}"
        # G3 — escalation/BLOCKED salience in the plain-text line.
        salience = []
        if status.get("escalation_count"):
            salience.append(f"{status['escalation_count']} open escalation(s)")
        if status.get("blocked_verdict_count"):
            salience.append(f"{status['blocked_verdict_count']} BLOCKED verdict(s)")
        if salience:
            line += "  [!] " + ", ".join(salience)
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
