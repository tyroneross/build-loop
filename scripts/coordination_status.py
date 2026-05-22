#!/usr/bin/env python3
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
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from rally_point import changes, channel_paths, inbox, presence, revision  # noqa: E402

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


def _read_inbox_unread_counts(slug: str, tool: str) -> dict[str, int]:
    """Count direct, broadcast, and total unread inbox lines for ``tool``."""
    return inbox.unread_counts(channel_paths.app_channel_dir(slug), tool)


def _read_rejection_count(slug: str) -> int:
    """Count MECE rejections logged to ``<channel_dir>/rejections.jsonl``.

    Surfaces the C4 ``mece_gate.log_rejection`` output so peers can see
    when malformed handoff posts are being rejected without inspecting
    the file directly.  Blank lines are ignored.  Returns 0 when the file
    is absent or unreadable.
    """
    try:
        rej_file = channel_paths.app_channel_dir(slug) / "rejections.jsonl"
        text = rej_file.read_text(encoding="utf-8")
        return sum(1 for line in text.splitlines() if line.strip())
    except OSError:
        return 0


def _default_coordination_file(workdir: Path) -> Path | None:
    root = workdir / ".build-loop" / "coordination"
    pointed = _active_coordination_pointer(root, workdir)
    if pointed is not None:
        return pointed
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


def _active_coordination_pointer(root: Path, workdir: Path) -> Path | None:
    """Return an explicit active coordination file if the repo declares one.

    The fallback must not guess "newest markdown": fresh handoff stubs are often
    newer than the run ledger they point at. A tiny repo-local pointer gives
    orchestrators a deterministic way to name the active run while preserving a
    safe no-pointer fallback below.
    """
    for name in ("active.json", "active", "current"):
        pointer = root / name
        try:
            raw = pointer.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw:
            continue
        value = raw
        if name.endswith(".json"):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                for key in ("coordination_file", "coord_file", "path", "active"):
                    candidate = data.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        value = candidate.strip()
                        break
                else:
                    continue
            elif isinstance(data, str):
                value = data.strip()
            else:
                continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = workdir / path
        try:
            path = path.resolve(strict=False)
            root_resolved = root.resolve(strict=False)
        except OSError:
            continue
        if path.suffix == ".md" and path.is_file() and (
            path == root_resolved or root_resolved in path.parents
        ):
            return path
    return None


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


def _read_recent_changes(channel_dir: Path, max_changes: int) -> list[dict[str, Any]]:
    recs, _offset = changes.read_changes_since(channel_dir, 0)
    return recs[-max_changes:] if len(recs) > max_changes else recs


def _git_dirty_files(workdir: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    out: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        out.append(path)
    return out


def build_status(args: argparse.Namespace) -> dict[str, Any]:
    workdir = Path(args.workdir).expanduser().resolve()
    session_id = args.session_id
    slug = channel_paths.app_slug(workdir)
    channel_dir = channel_paths.app_channel_dir(slug)
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

    dirty = _git_dirty_files(workdir)
    dirty_outside_owned = []
    for path in dirty:
        keys = _path_keys(path, workdir)
        if owned_keys and keys.intersection(owned_keys):
            continue
        dirty_outside_owned.append(path)

    recent_changes = _read_recent_changes(channel_dir, args.max_changes)
    current_revision = revision.read_revision(channel_dir)
    new_changes = [
        c for c in recent_changes
        if args.since_revision is None
        or int(c.get("revision", 0)) > args.since_revision
    ]

    inbox_counts = _read_inbox_unread_counts(slug, requesting_tool)
    rejection_count = _read_rejection_count(slug)

    if unresolved:
        status = "blocked"
        required_action = "resolve_unresolved_coordination_verdicts"
    elif peer_overlap_files or dirty_outside_owned:
        # warn only when a peer's ``owns`` intersects our files_in_flight,
        # OR when dirty files exist outside our owned set.  Raw peer count
        # does NOT trigger warn (prevents false positives when peers share
        # no files with us).
        status = "warn"
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
        "rejection_count": rejection_count,
        "coordination_file": str(coordination_file) if coordination_file else None,
        "latest_verdicts": verdict_entries,
        "unresolved": unresolved,
        "dirty_files": dirty,
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
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    status = build_status(args)
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(f"{status['status']}: {status['required_action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
