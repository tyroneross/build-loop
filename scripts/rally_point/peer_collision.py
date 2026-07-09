#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Peer-on-same-workdir commit-collision WARN (EC-03 rca).

The SessionStart rally hook surfaces peers but never warns that a peer is active
on the SAME workdir — the exact precondition for the concurrent-write race
(two writers on one checkout race on HEAD/index; commits land on the wrong
branch — CLAUDE.md §"Concurrent dispatch isolation"). This module emits a
fail-open, advisory WARN naming the collision so the session mints a worktree
before committing.

Advisory + fail-open by contract: any error → empty string / exit 0. It reads
live presence from the workdir's rally room via a NON-MUTATING read
(reap=False) — it never unlinks a presence file or writes the SHA cache.

Self-exclusion: when the caller passes ``--session-id`` (or `self_session`), that
session is excluded and any remaining live peer triggers the WARN. When no self
id is available (the SessionStart shell hook does not parse stdin), the room
includes this session too, so the WARN needs ≥ 2 live sessions to prove a real
peer — conservative (under-warns rather than false-positives).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:  # package import
    from . import channel_paths
    from . import presence
except ImportError:  # script import
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import channel_paths  # type: ignore
    import presence  # type: ignore


def _has_peer(peers: list, self_session: str) -> bool:
    """True when the live-presence list proves a peer besides this session.

    With a known self id the list already excludes self → any entry is a peer.
    Without one, this session is still in the list → need ≥ 2 to prove a peer."""
    if self_session:
        return len(peers) >= 1
    return len(peers) >= 2


def _peer_labels(peers: list) -> str:
    return ", ".join(sorted({str(p.get("tool") or "peer") for p in peers})) or "peer"


def warn_line_for(peers: list, self_session: str) -> str:
    """Return the advisory WARN string, or '' when no peer collision is proven."""
    if not _has_peer(peers, self_session):
        return ""
    return (
        f"[rally] WARN: peer active on this workdir ({_peer_labels(peers)}) — "
        "mint a worktree before committing (two writers on one checkout race on "
        "HEAD/index; commits can land on the wrong branch)."
    )


def _channel_dir(workdir: Path):
    slug = channel_paths.app_slug(workdir)
    return channel_paths.app_channel_dir(slug)


def collision_warn(workdir: Path, self_session: str = "") -> str:
    """Advisory WARN string for a peer active on ``workdir``'s room, else ''.

    Fail-open: rally absent, no room, or any error → '' (never raises)."""
    try:
        cdir = _channel_dir(workdir)
        if not cdir.is_dir():
            return ""
        # reap=False → strictly non-mutating read: an advisory SessionStart hook
        # must never unlink presence files or write the SHA cache (would race
        # peers). Stale sessions are excluded via a dry-run instead.
        peers = presence.read_active_presence(
            cdir, exclude_session=self_session or "", reap=False)
        return warn_line_for(peers, self_session)
    except Exception:  # noqa: BLE001 — advisory hook must never raise
        return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--session-id", default="", help="This session's id (excluded from the peer set).")
    args = p.parse_args(argv)
    line = collision_warn(Path(args.workdir).expanduser(), args.session_id)
    if line:
        print(line, file=sys.stderr)
    return 0  # advisory: always exit 0


if __name__ == "__main__":
    raise SystemExit(main())
