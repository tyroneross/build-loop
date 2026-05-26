#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point hook helpers used by the shell hook wrappers.

The shell files in ``hooks/`` are compatibility entrypoints. The behavior
lives here so the future agent-rally-point plugin can carry one namespaced
implementation instead of inline Python snippets embedded in build-loop
hook scripts.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = _HERE.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from rally_point import checkpoint, presence, revision
    from rally_point.discovery_bridge import resolve as _bridge_resolve
except ImportError:
    from . import checkpoint, presence, revision
    from .discovery_bridge import resolve as _bridge_resolve


def _session_start_id(slug: str) -> str:
    return f"sessionstart-{slug.replace('/', '_')}"


def _resolve_existing_channel(workdir: Path) -> tuple[str, Path] | None:
    envelope = _bridge_resolve(workdir)
    channel_dir = Path(envelope.channel_dir)
    if not channel_dir.exists():
        return None
    return envelope.app_slug, channel_dir


def session_start_restore(workdir: Path, *, verbose: bool = True) -> int:
    resolved = _resolve_existing_channel(workdir)
    if resolved is None:
        return 0
    slug, channel_dir = resolved
    env = checkpoint.checkpoint_read(
        channel_dir,
        session_id=_session_start_id(slug),
        my_files=[],
    )
    if not verbose or not env.get("changed"):
        return 0
    bits = [f"{len(env.get('new_changes', []))} change(s)"]
    peers = len(env.get("active_peers", []))
    if peers:
        bits.append(f"{peers} live peer(s)")
    reactions = {r.get("type") for r in env.get("reactions", [])}
    if "reinstall" in reactions:
        bits.append("dep-change: reinstall")
    if "re-baseline" in reactions:
        bits.append("arch changed: re-baseline")
    if "soft-claim" in reactions:
        bits.append("peer owns files (warning)")
    print(f"Rally Point: {slug} - " + "; ".join(bits))
    return 0


def session_start_advance(workdir: Path) -> int:
    resolved = _resolve_existing_channel(workdir)
    if resolved is None:
        return 0
    slug, channel_dir = resolved
    checkpoint.checkpoint_read(
        channel_dir,
        session_id=_session_start_id(slug),
        my_files=[],
    )
    return 0


def pre_edit_hint(workdir: Path) -> int:
    resolved = _resolve_existing_channel(workdir)
    if resolved is None:
        return 0
    slug, channel_dir = resolved
    current = revision.read_revision(channel_dir)
    session_id = _session_start_id(slug)
    seen = presence.get_cursor(channel_dir, session_id).get("revision", 0)
    if current > seen:
        print(
            f"Rally Point: {slug} channel advanced "
            f"(rev {seen} -> {current}) - run a checkpoint before editing."
        )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("session-start-restore", "session-start-advance", "pre-edit"):
        sp = sub.add_parser(name)
        sp.add_argument("--workdir", default=".")
        if name == "session-start-restore":
            sp.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve()
    try:
        if args.command == "session-start-restore":
            return session_start_restore(workdir, verbose=args.verbose)
        if args.command == "session-start-advance":
            return session_start_advance(workdir)
        if args.command == "pre-edit":
            return pre_edit_hint(workdir)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
