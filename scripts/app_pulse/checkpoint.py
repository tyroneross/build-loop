#!/usr/bin/env python3
"""App Pulse checkpoint read — the single consume entry point (D3).

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

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import changes as _ch  # noqa: E402
import presence as _pr  # noqa: E402
import revision as _rev  # noqa: E402

_ARCH_DIGEST_REL = ("arch", "digest.json")


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
            if overlap:
                reactions.append({
                    "type": "soft-claim",
                    "severity": "warning",  # D4: never a block
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
