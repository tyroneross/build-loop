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
        recs = recs + changes.read_archived_changes(channel_dir)
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
    slug, channel_dir, resolved_via = _resolve_channel_dir(workdir)
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

    # Requesting tool name (default: "claude_code") used for inbox lookup.
    requesting_tool = getattr(args, "tool", None) or "claude_code"

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

    recent_changes = _read_recent_changes(
        channel_dir,
        args.max_changes,
        workdir=workdir,
        include_archived=getattr(args, "include_archived", False),
    )
    current_revision = revision.read_revision(channel_dir)
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

    inbox_counts = _read_inbox_unread_counts(channel_dir, requesting_tool, session_id)
    inbox_latest_messages = _read_inbox_latest_messages(channel_dir, requesting_tool, session_id)
    task_heartbeat_status = _read_task_heartbeat(args, channel_dir, requesting_tool)
    rejection_count = _read_rejection_count(channel_dir)

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
    elif peer_overlap_files or dirty_outside_owned or heartbeat_warn or dirty_unknown:
        # warn only when a peer's ``owns`` intersects our files_in_flight,
        # OR when dirty files exist outside our owned set, OR when the dirty
        # probe timed out (unknown != clean — never silently suppress).  Raw
        # peer count does NOT trigger warn (prevents false positives when
        # peers share no files with us).
        status = "warn"
        if dirty_unknown and not (peer_overlap_files or dirty_outside_owned):
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
