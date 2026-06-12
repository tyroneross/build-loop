#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""rally_merge_gate.py — pre-merge conflict gate (all agents).

Agents work in isolated git worktrees/branches by default (`rally run` defaults
to own-worktree), so isolated WORK is already deconflicted — there is no pre-edit
gate. The one real conflict point is the **merge**: two branches that touched the
same files conflict at integration, and merge order matters.

This gate fires before a branch/worktree merges to `main`. It computes the
changeset's target files (`git diff --name-only <base>...HEAD`), queries rally for
OTHER agents' ACTIVE CLAIMS on those paths, and warns (exit 3) on overlap so the
merger can sequence — let the other claim/merge land first, or coordinate. It is
**warn-first / advisory** (does not block) and **fail-open**: a git or rally
outage must never wedge the merge step (exit 0 + warning).

Mirrors `rally_poll_gate.py`: pure-function core, `--room-json`/`--diff-files`
test seams, fail-open on fetch, fail-closed on a real finding.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# rally `active_claims[]` carry claimed paths inside `evidence[]` as
# "claimhash:<path>=<hash>" strings. Broad/audit claims may carry zero such
# entries (no paths) and contribute nothing to overlap.
_CLAIMHASH_PREFIX = "claimhash:"


def claim_paths(claim: dict[str, Any]) -> set[str]:
    """Repo-relative paths a claim covers, parsed from its evidence[]."""
    out: set[str] = set()
    for ev in claim.get("evidence", []) or []:
        if isinstance(ev, str) and ev.startswith(_CLAIMHASH_PREFIX):
            body = ev[len(_CLAIMHASH_PREFIX):]
            path = body.split("=", 1)[0].strip()
            # defensive: tolerate a future "file:" prefix on the path form
            if path.startswith("file:"):
                path = path[len("file:"):]
            if path:
                out.add(path)
    return out


def others_claims(active_claims: list[dict[str, Any]], tool: str) -> list[dict[str, Any]]:
    """Active claims authored by a tool OTHER than `tool` (never flag self)."""
    out = []
    for c in active_claims or []:
        if isinstance(c, dict) and c.get("tool") and c.get("tool") != tool:
            out.append(c)
    return out


def overlaps(target_files: set[str], active_claims: list[dict[str, Any]],
             tool: str) -> list[dict[str, Any]]:
    """Pure: other-tool claims whose paths intersect the merge's target files."""
    hits = []
    for c in others_claims(active_claims, tool):
        inter = claim_paths(c) & target_files
        if inter:
            hits.append({"tool": c.get("tool"), "subject": c.get("subject"),
                         "event_id": c.get("event_id"), "overlap": sorted(inter)})
    return hits


def _load_room_json(source: str) -> dict[str, Any]:
    text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text()
    return json.loads(text)


def fetch_room(workdir: Path, room_json: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Return (room, error). Fail-open: any fetch error → (None, msg)."""
    if room_json is not None:
        try:
            return _load_room_json(room_json), None
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"room-json unreadable: {exc}"
    try:
        proc = subprocess.run(
            ["rally", "room", "--json"],
            cwd=str(workdir), capture_output=True, text=True, timeout=20, check=False,
        )
        if proc.returncode != 0:
            return None, f"rally room exited {proc.returncode}: {proc.stderr.strip()[:200]}"
        return json.loads(proc.stdout), None
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        return None, f"rally room failed: {exc}"


def _active_claims(room: dict[str, Any]) -> list[dict[str, Any]]:
    ac = room.get("data", {}).get("room", {}).get("active_claims", [])
    return ac if isinstance(ac, list) else []


def target_files(workdir: Path, base: str, diff_files: str | None) -> tuple[set[str], str | None]:
    """Changed files on this branch vs base. `diff_files` (path|-) is a test seam."""
    if diff_files is not None:
        try:
            text = sys.stdin.read() if diff_files == "-" else Path(diff_files).expanduser().read_text()
            return {ln.strip() for ln in text.splitlines() if ln.strip()}, None
        except OSError as exc:
            return set(), f"diff-files unreadable: {exc}"
    try:
        proc = subprocess.run(
            ["git", "-C", str(workdir), "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if proc.returncode != 0:
            return set(), f"git diff exited {proc.returncode}: {proc.stderr.strip()[:200]}"
        return {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}, None
    except (OSError, subprocess.SubprocessError) as exc:
        return set(), f"git diff failed: {exc}"


def _check(tool: str, workdir: Path, base: str, room_json: str | None,
           diff_files: str | None) -> tuple[int, dict[str, Any]]:
    files, gerr = target_files(workdir, base, diff_files)
    if gerr:  # fail-open: a git outage must never wedge the merge step
        return 0, {"ok": True, "warning": gerr, "overlaps": [], "gated": False}
    if not files:
        return 0, {"ok": True, "gated": False, "overlaps": [], "note": "no changed files vs base"}
    room, rerr = fetch_room(workdir, room_json)
    if rerr:  # fail-open on a rally outage
        return 0, {"ok": True, "warning": rerr, "overlaps": [], "gated": False}
    hits = overlaps(files, _active_claims(room), tool)
    if hits:
        return 3, {
            "ok": False, "gated": True, "overlaps": hits,
            "advice": "Another agent holds an ACTIVE CLAIM on files you are about to "
                      "merge. Sequence the merge: pull the room and let their claim/merge "
                      "land first, or coordinate via rally say. Advisory (warn-first) — "
                      "does not block.",
        }
    return 0, {"ok": True, "gated": False, "overlaps": []}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="Warn (exit 3) if another tool claims files you're about to merge.")
    c.add_argument("--tool", required=True, help="The merging agent's unique id (self-claims are ignored).")
    c.add_argument("--workdir", default=".")
    c.add_argument("--base", default="main", help="Merge base ref (default: main).")
    c.add_argument("--room-json", default=None, help="Inject room JSON (path or '-') for tests.")
    c.add_argument("--diff-files", default=None, help="Inject changed-file list (path or '-') for tests.")

    args = p.parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve()

    if args.cmd == "check":
        code, env = _check(args.tool, workdir, args.base, args.room_json, args.diff_files)
    else:
        return 2  # unreachable: subparser is required
    print(json.dumps(env, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
