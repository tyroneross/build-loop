#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Architecture snapshot promotion — live .navgator/ → durable memory lane (WP-H/G4).

GOAL: a dynamic, ongoing, persistent dependency view that build-loop refreshes on
major architecture changes, so any change's blast radius (what breaks / what it
depends on) is one cross-session query. Engine = NavGator (wrapped, not rebuilt);
this script is the build-loop-side PERSISTENCE + CHANGE-TRIGGER + PROMOTION layer
(NavGator gap 3). It treats architecture exactly like the charter (WP-F/F3): live
mirror in the repo (`.navgator/architecture/`), canonical promoted into memory
(`build-loop-memory/projects/<slug>/architecture/`) stamped with commit sha +
provenance, promoted at a boundary or on a material graph change.

Graph-first: the durable artifact is the queryable graph (nodes/edges) + file_map,
NOT a flow diagram. The diagram is one export NavGator can render from the graph.

Change-driven, never time-driven: `dirty` is read from NavGator's freshness ledger
(`.navgator/architecture/freshness.json` dirty_count) AND/OR an explicit
`--mark-dirty` from a Phase-3/PostToolUse detector. A clean re-run is a near-free
no-op (`promote` skips when the snapshot's commit_sha already matches canonical).

Subcommands:
  status     Report live vs canonical snapshot state (sha, dirty, drift). No write.
  promote    Promote the live .navgator graph/file_map into the memory lane if the
             live commit_sha differs from the canonical snapshot (or --force). Stamps
             provenance. No-op when no .navgator data exists.
  mark-dirty Append a dirty reason to the build-loop-side dirty marker so the NEXT
             push-boundary promote knows a refresh is owed (change-trigger).
  is-dirty   Exit 0 if a refresh is owed (NavGator dirty_count>0 OR marker set),
             else exit 1. For a push-boundary debounce check.

Fail-soft: a missing .navgator or memory root is a normal state, not an error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAV_REL = Path(".navgator") / "architecture"
DIRTY_MARKER_REL = Path(".build-loop") / "architecture-dirty.json"
SNAPSHOT_FILES = ("graph.json", "file_map.json", "connections.jsonl")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_head_sha(workdir: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _resolve_paths(workdir: Path, slug: str | None) -> tuple[Path, Path]:
    """Return (live_nav_dir, canonical_arch_dir)."""
    sys.path.insert(0, str(HERE))
    try:
        import context_bootstrap as cb  # noqa: PLC0415
        resolved_slug = slug or cb.resolve_project(workdir)
        mem_root = cb.memory_store_root()
    finally:
        sys.path.pop(0)
    live = workdir / NAV_REL
    canonical = mem_root / "projects" / resolved_slug / "architecture"
    return live, canonical


def _read_freshness(live: Path) -> dict:
    f = live / "freshness.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _canonical_snapshot_meta(canonical: Path) -> dict:
    f = canonical / "snapshot.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def is_dirty(workdir: Path, slug: str | None = None) -> dict:
    live, _ = _resolve_paths(workdir, slug)
    fresh = _read_freshness(live)
    nav_dirty = int(fresh.get("dirty_count", 0) or 0) > 0
    marker = workdir / DIRTY_MARKER_REL
    marker_dirty = marker.is_file()
    reasons: list[str] = []
    if nav_dirty:
        reasons.append(f"navgator_dirty_count={fresh.get('dirty_count')}")
    if marker_dirty:
        try:
            mdata = json.loads(marker.read_text(encoding="utf-8"))
            reasons.extend(mdata.get("reasons", []))
        except (OSError, json.JSONDecodeError):
            reasons.append("dirty_marker_present")
    return {"dirty": bool(nav_dirty or marker_dirty), "reasons": reasons}


def mark_dirty(workdir: Path, reason: str, slug: str | None = None) -> dict:
    marker = workdir / DIRTY_MARKER_REL
    marker.parent.mkdir(parents=True, exist_ok=True)
    data = {"reasons": [], "updated_at": _utc_now()}
    if marker.is_file():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    data.setdefault("reasons", [])
    if reason and reason not in data["reasons"]:
        data["reasons"].append(reason)
    data["updated_at"] = _utc_now()
    marker.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"action": "marked_dirty", "reasons": data["reasons"]}


def status(workdir: Path, slug: str | None = None) -> dict:
    live, canonical = _resolve_paths(workdir, slug)
    fresh = _read_freshness(live)
    canon_meta = _canonical_snapshot_meta(canonical)
    live_sha = fresh.get("commit_sha") or _git_head_sha(workdir)
    canon_sha = canon_meta.get("commit_sha")
    dirty = is_dirty(workdir, slug)
    return {
        "live_nav_dir": str(live),
        "canonical_arch_dir": str(canonical),
        "live_present": (live / "graph.json").is_file(),
        "canonical_present": bool(canon_meta),
        "live_commit_sha": live_sha,
        "canonical_commit_sha": canon_sha,
        "needs_promote": (live / "graph.json").is_file() and live_sha != canon_sha,
        "dirty": dirty["dirty"],
        "dirty_reasons": dirty["reasons"],
    }


def promote(workdir: Path, slug: str | None = None, force: bool = False) -> dict:
    live, canonical = _resolve_paths(workdir, slug)
    if not (live / "graph.json").is_file():
        return {"action": "noop_no_navgator", "live_nav_dir": str(live)}
    fresh = _read_freshness(live)
    live_sha = fresh.get("commit_sha") or _git_head_sha(workdir)
    canon_meta = _canonical_snapshot_meta(canonical)
    canon_sha = canon_meta.get("commit_sha")
    if not force and live_sha is not None and live_sha == canon_sha:
        return {"action": "noop_unchanged", "commit_sha": live_sha}

    canonical.mkdir(parents=True, exist_ok=True)
    promoted: list[str] = []
    for name in SNAPSHOT_FILES:
        src = live / name
        if src.is_file():
            (canonical / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            promoted.append(name)

    meta = {
        "commit_sha": live_sha,
        "branch": fresh.get("branch"),
        "promoted_at": _utc_now(),
        "provenance": "navgator",
        "files": promoted,
        "dirty_reasons_at_promote": is_dirty(workdir, slug)["reasons"],
    }
    (canonical / "snapshot.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # Promotion clears the build-loop-side dirty marker (the refresh is now owed-done).
    marker = workdir / DIRTY_MARKER_REL
    if marker.is_file():
        try:
            marker.unlink()
        except OSError:
            pass
    return {"action": "promoted", "commit_sha": live_sha, "files": promoted, "canonical": str(canonical)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("status", "promote", "mark-dirty", "is-dirty"))
    ap.add_argument("--workdir", type=Path, default=Path.cwd())
    ap.add_argument("--slug", default=None)
    ap.add_argument("--reason", default="", help="dirty reason (mark-dirty)")
    ap.add_argument("--force", action="store_true", help="promote even if unchanged")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    workdir = args.workdir.resolve()
    rc = 0
    try:
        if args.command == "status":
            result = status(workdir, args.slug)
        elif args.command == "promote":
            result = promote(workdir, args.slug, force=args.force)
        elif args.command == "mark-dirty":
            result = mark_dirty(workdir, args.reason, args.slug)
        else:  # is-dirty
            result = is_dirty(workdir, args.slug)
            rc = 0 if result["dirty"] else 1
    except OSError as exc:
        result = {"action": "error", "reason": str(exc)}
        rc = 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result.get("action") or json.dumps(result))
    return rc


if __name__ == "__main__":
    sys.exit(main())
