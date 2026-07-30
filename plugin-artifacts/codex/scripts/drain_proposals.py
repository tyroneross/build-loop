#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""drain_proposals.py — cross-repo proposal-drain gate (the multiplier).

Proposals accrete unread across every repo's ``.build-loop/proposals/`` (self-review
miner output, ``enforce-from-retro/`` enforce-candidates) plus the ai-assistant
routing-refinement queue at ``~/.assistant/proposals/``. Nothing surfaces them as a
single decidable list, so they pile up invisibly. This scans every registered repo +
the assistant queue and emits ONE digest: per item — id, repo, one-line, age, status.

State is persisted (``drain-state.json``) keyed by a stable per-item key so an item
that has been decided (applied / rejected / deferred) never re-surfaces as ``new``.

NEVER auto-applies a proposal. ``scan`` reads and reports; ``set`` records a human
decision (driven by the ``/drain-proposals`` command or the weekly LaunchAgent digest).

Sources
-------
1. Repo registry (``build-loop-memory/registry/registry.json`` → ``repos[].path``);
   for each, ``<path>/.build-loop/proposals/**/*.md`` (recursive: includes
   ``enforce-from-retro/`` and ``self-review-*`` files).
2. ``~/.assistant/proposals/*.md``.

Commands
--------
    scan                produce the digest (JSON + markdown) at the state dir.
    list                print the current digest (new items first).
    set --key K --status S [--note ...]   record a decision for one item.
    path                print the resolved state dir + digest paths.

Exit codes: 0 always on scan/list/path (advisory tool). ``set`` returns 1 if the key
is unknown so the caller can detect a typo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_STATUSES = ("new", "reviewed", "applied", "rejected", "deferred")
DECIDED_STATUSES = ("reviewed", "applied", "rejected", "deferred")


def _state_dir(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "build-loop-drain"


def _memory_root() -> Path:
    env = os.environ.get("BUILD_LOOP_MEMORY_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / "dev" / "git-folder" / "build-loop-memory"


def _registry_repos() -> list[dict[str, str]]:
    reg = _memory_root() / "registry" / "registry.json"
    try:
        data = json.loads(reg.read_text())
    except Exception:
        return []
    out = []
    for r in data.get("repos", []):
        if isinstance(r, dict) and r.get("path"):
            out.append({"name": r.get("name") or Path(r["path"]).name, "path": r["path"]})
    return out


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HTML_META_RE = re.compile(r"<!--\s*([a-zA-Z0-9_-]+)\s*:\s*(.+?)\s*-->")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def _parse_meta(text: str) -> dict[str, str]:
    """Pull id/status hints from YAML frontmatter or HTML-comment metadata."""
    meta: dict[str, str] = {}
    m = _FM_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip().strip("'\"")
    for k, v in _HTML_META_RE.findall(text):
        meta.setdefault(k.strip().lower(), v.strip())
    return meta


def _one_line(text: str, meta: dict[str, str]) -> str:
    m = _HEADING_RE.search(text)
    if m:
        return m.group(1).strip()[:120]
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith(("<!--", "---")):
            return s[:120]
    return "(empty)"


def _item_id(path: Path, meta: dict[str, str]) -> str:
    for k in ("proposal_id", "id", "item", "run-id", "run_id"):
        if meta.get(k):
            return meta[k]
    return path.stem


def _stable_key(repo: str, path: Path) -> str:
    # Path is stable per item; content changes don't reset a decision.
    return hashlib.sha1(f"{repo}::{path}".encode()).hexdigest()[:16]


def _age_days(path: Path) -> int:
    try:
        return max(0, int((time.time() - path.stat().st_mtime) / 86400))
    except OSError:
        return -1


def _iter_sources() -> list[tuple[str, Path]]:
    """Yield (repo_label, proposals_dir) for every source that exists."""
    sources: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for repo in _registry_repos():
        d = Path(repo["path"]) / ".build-loop" / "proposals"
        rp = d.resolve()
        if d.is_dir() and rp not in seen:
            sources.append((repo["name"], d))
            seen.add(rp)
    assistant = Path.home() / ".assistant" / "proposals"
    if assistant.is_dir() and assistant.resolve() not in seen:
        sources.append(("ai-assistant", assistant))
    return sources


def _collect_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for repo_label, pdir in _iter_sources():
        for path in sorted(pdir.rglob("*.md")):
            if not path.is_file():
                continue
            # Skip archived / surfaced sub-trees so decided items don't re-surface via disk moves.
            if any(part in ("surfaced", "archive", "archived", "applied") for part in path.parts):
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            meta = _parse_meta(text)
            key = _stable_key(repo_label, path)
            items.append(
                {
                    "key": key,
                    "id": _item_id(path, meta),
                    "repo": repo_label,
                    "path": str(path),
                    "one_line": _one_line(text, meta),
                    "age_days": _age_days(path),
                    "meta_status": (meta.get("status") or "").lower(),
                }
            )
    return items


def _load_state(state_path: Path) -> dict[str, Any]:
    try:
        return json.loads(state_path.read_text())
    except Exception:
        return {}


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp")
    tmp.write(data)
    tmp.close()
    os.replace(tmp.name, path)


def _effective_status(item: dict[str, Any], state: dict[str, Any]) -> str:
    rec = state.get(item["key"])
    if isinstance(rec, dict) and rec.get("status") in VALID_STATUSES:
        return rec["status"]
    # A proposal whose OWN body says applied/rejected is already decided upstream.
    if item["meta_status"] in ("applied", "rejected", "done", "adopted"):
        return "applied" if item["meta_status"] in ("applied", "done", "adopted") else "rejected"
    return "new"


def build_digest(state_dir: Path) -> dict[str, Any]:
    state_path = state_dir / "drain-state.json"
    state = _load_state(state_path)
    items = _collect_items()
    for it in items:
        it["status"] = _effective_status(it, state)
    # New first, then by age desc.
    items.sort(key=lambda i: (i["status"] != "new", -(i["age_days"] if i["age_days"] >= 0 else 0)))
    counts: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(items),
        "counts": counts,
        "new": counts.get("new", 0),
        "items": items,
    }


def _render_md(digest: dict[str, Any]) -> str:
    lines = [
        "# Proposal Drain Digest",
        "",
        f"_Generated {digest['generated_at']}_  ",
        f"**{digest['new']} new** / {digest['total']} total — "
        + ", ".join(f"{k}: {v}" for k, v in digest["counts"].items() if v),
        "",
        "Decide each with: `/drain-proposals` (interactive) or "
        "`drain_proposals.py set --key <key> --status apply|reject|defer`.",
        "",
        "| status | repo | id | age | one-line | key |",
        "|--------|------|----|-----|----------|-----|",
    ]
    for it in digest["items"]:
        age = f"{it['age_days']}d" if it["age_days"] >= 0 else "?"
        one = it["one_line"].replace("|", "\\|")
        lines.append(
            f"| {it['status']} | {it['repo']} | {it['id']} | {age} | {one} | `{it['key']}` |"
        )
    return "\n".join(lines) + "\n"


def cmd_scan(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args.state_dir)
    digest = build_digest(state_dir)
    _atomic_write(state_dir / "proposal-digest.json", json.dumps(digest, indent=2))
    _atomic_write(state_dir / "proposal-digest.md", _render_md(digest))
    if args.notify:
        _notify(digest)
    if args.json:
        print(json.dumps(digest, indent=2))
    else:
        print(f"scanned: {digest['total']} proposals, {digest['new']} new")
        print(f"digest: {state_dir / 'proposal-digest.md'}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    digest = build_digest(_state_dir(args.state_dir))
    show = [i for i in digest["items"] if args.all or i["status"] == "new"]
    if args.json:
        print(json.dumps({**digest, "items": show}, indent=2))
        return 0
    if not show:
        print("no new proposals" if not args.all else "no proposals")
        return 0
    for it in show:
        age = f"{it['age_days']}d" if it["age_days"] >= 0 else "?"
        print(f"[{it['status']:8}] {it['repo']:22} {age:>4}  {it['one_line']}")
        print(f"           key={it['key']} id={it['id']}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args.state_dir)
    status_map = {"apply": "applied", "reject": "rejected", "defer": "deferred", "review": "reviewed"}
    status = status_map.get(args.status, args.status)
    if status not in VALID_STATUSES:
        print(f"invalid status: {args.status}", file=sys.stderr)
        return 2
    # Validate key exists in the current digest.
    digest = build_digest(state_dir)
    keys = {i["key"] for i in digest["items"]}
    if args.key not in keys:
        print(f"unknown key: {args.key}", file=sys.stderr)
        return 1
    state_path = state_dir / "drain-state.json"
    state = _load_state(state_path)
    state[args.key] = {
        "status": status,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "note": args.note or "",
    }
    _atomic_write(state_path, json.dumps(state, indent=2, sort_keys=True))
    print(f"{args.key} -> {status}")
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    sd = _state_dir(args.state_dir)
    print(json.dumps({
        "state_dir": str(sd),
        "digest_json": str(sd / "proposal-digest.json"),
        "digest_md": str(sd / "proposal-digest.md"),
        "state_json": str(sd / "drain-state.json"),
    }, indent=2))
    return 0


def _notify(digest: dict[str, Any]) -> None:
    n = digest.get("new", 0)
    msg = f"{n} new build-loop proposal(s) to review" if n else "Proposal drain: nothing new"
    # macOS user notification; silent no-op if osascript is unavailable.
    try:
        import subprocess
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg}" with title "Proposal Drain"'],
            check=False, timeout=8, capture_output=True,
        )
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drain_proposals", description=__doc__)
    ap.add_argument("--state-dir", default=None, help="override state/digest dir")
    sub = ap.add_subparsers(dest="cmd")

    p_scan = sub.add_parser("scan", help="build the digest")
    p_scan.add_argument("--json", action="store_true")
    p_scan.add_argument("--notify", action="store_true", help="post a macOS notification")
    p_scan.set_defaults(func=cmd_scan)

    p_list = sub.add_parser("list", help="print the digest")
    p_list.add_argument("--all", action="store_true", help="include decided items")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_set = sub.add_parser("set", help="record a decision")
    p_set.add_argument("--key", required=True)
    p_set.add_argument("--status", required=True,
                       help="apply|reject|defer|review (or a raw VALID_STATUS)")
    p_set.add_argument("--note", default="")
    p_set.set_defaults(func=cmd_set)

    p_path = sub.add_parser("path", help="print resolved paths")
    p_path.set_defaults(func=cmd_path)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        args = ap.parse_args((argv or []) + ["scan"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
