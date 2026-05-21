#!/usr/bin/env python3
"""Rally Point checkpoint read — the single consume entry point (D3).

``checkpoint_read`` is the one call hooks and the orchestrator make to
learn what changed. Checkpoint-poll only (D3, no daemon). Flow:

  1. Read ``revision`` (one stat+read, no lock).
  2. Compare to this session's cursor. Unchanged → empty envelope,
     **no tail read** (the cheap fast path — the common case).
  3. Changed → read the change tail since the cursor offset, the active
     peers (reaper runs here), and derive reactions.
  4. Advance ONLY this session's own cursor (the sole write a reader
     makes; it never locks the change log — D-readers-never-lock).

Reactions (awareness only, never a lock — D4):
  - ``dep-change``        → ``{"type": "reinstall"}``
  - ``arch-scan-complete``→ ``{"type": "re-baseline"}``
  - peer files ∩ my files → ``{"type": "soft-claim", "severity":
    "warning", ...}`` (soft-claim is ALWAYS a warning, never a block)

Graceful absence: an absent channel/dir yields an empty envelope, lazy
creates nothing implicitly, and never errors (zero regression).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import changes as _ch  # noqa: E402
import presence as _pr  # noqa: E402
import revision as _rev  # noqa: E402

_ARCH_DIGEST_REL = ("arch", "digest.json")
_FILES_LANDED_TIMEOUT_S = 0.5
_FILES_LANDED_CAP = 10


def _peer_files_already_landed(cwd: str | None, files: list) -> bool:
    """Return True iff ``git diff origin/main -- <files>`` is empty in cwd.

    Squash-merge fallback: if the peer's branch tip is not an ancestor of
    main but the file content equals main, the peer's edits already
    landed via squash. Capped at ``_FILES_LANDED_CAP`` files, bounded by
    ``_FILES_LANDED_TIMEOUT_S``. On any failure (missing cwd, git error,
    timeout, non-git dir) returns False — the conservative answer
    preserves the existing warning.
    """
    if not cwd or not files:
        return False
    capped = list(files)[:_FILES_LANDED_CAP]
    for upstream in ("origin/main", "main"):
        try:
            v = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--verify", "--quiet",
                 upstream],
                capture_output=True, text=True,
                timeout=_FILES_LANDED_TIMEOUT_S,
            )
            if v.returncode != 0:
                continue
            r = subprocess.run(
                ["git", "-C", cwd, "diff", "--quiet", upstream, "--",
                 *capped],
                capture_output=True, text=True,
                timeout=_FILES_LANDED_TIMEOUT_S,
            )
            # exit 0 = no diff (content == main); exit 1 = diff present
            return r.returncode == 0
        except (subprocess.SubprocessError, OSError, ValueError):
            return False
    return False


def _empty(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "revision": 0,
        "changed": False,
        "new_changes": [],
        "active_peers": [],
        "arch_digest": None,
        "reactions": [],
    }


def _read_arch_digest(channel_dir: Path):
    """Return the parsed arch digest, or None (Stage 2 publishes it)."""
    import json

    p = Path(channel_dir).joinpath(*_ARCH_DIGEST_REL)
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, OSError, ValueError):
        return None


def _derive_reactions(new_changes: list, peers: list, my_files) -> list:
    reactions: list = []
    kinds = {c.get("kind") for c in new_changes}
    if "dep-change" in kinds:
        reactions.append({"type": "reinstall"})
    if "arch-scan-complete" in kinds:
        reactions.append({"type": "re-baseline"})
    mine = set(my_files or [])
    if mine:
        for p in peers:
            overlap = sorted(mine.intersection(p.get("files_in_flight", [])))
            if not overlap:
                continue
            # Three-way classification (2026-05-19 — peer-merged gate):
            #   1. peer's branch tip is ancestor of main -> merged_residue
            #   2. file content already on main         -> squash_landed
            #   3. otherwise                            -> active_conflict
            peer_status = p.get("branch_merge_status", "unknown")
            if peer_status == "merged":
                severity, reason = "informational", "merged_residue"
            elif _peer_files_already_landed(p.get("cwd"), overlap):
                severity, reason = "informational", "squash_landed"
            else:
                severity, reason = "warning", "active_conflict"
            reactions.append({
                "type": "soft-claim",
                "severity": severity,  # D4: never a block in any case
                "reason": reason,
                "peer": p.get("session_id"),
                "files": overlap,
            })
    return reactions


def checkpoint_read(
    channel_dir: Path,
    *,
    session_id: str,
    my_files=None,
) -> dict:
    """Return the delta envelope for ``session_id``. Never raises.

    Envelope shape::

        {session_id, revision, changed,
         new_changes[], active_peers[], arch_digest|null, reactions[]}
    """
    try:
        d = Path(channel_dir)
        if not d.exists():
            return _empty(session_id)

        current_rev = _rev.read_revision(d)
        cursor = _pr.get_cursor(d, session_id)

        if current_rev <= cursor.get("revision", 0):
            # Fast path: nothing new — no tail read, no peer scan.
            env = _empty(session_id)
            env["revision"] = current_rev
            return env

        new_changes, new_offset = _ch.read_changes_since(
            d, cursor.get("changes_offset", 0)
        )
        peers = _pr.read_active_presence(d, exclude_session=session_id)
        arch_digest = _read_arch_digest(d)
        reactions = _derive_reactions(new_changes, peers, my_files)

        # The only write a reader performs: advance its OWN cursor.
        _pr.set_cursor(
            d, session_id,
            revision=current_rev, changes_offset=new_offset,
        )

        return {
            "session_id": session_id,
            "revision": current_rev,
            "changed": True,
            "new_changes": new_changes,
            "active_peers": peers,
            "arch_digest": arch_digest,
            "reactions": reactions,
        }
    except Exception:  # noqa: BLE001 — never block/fail a host action
        return _empty(session_id)
