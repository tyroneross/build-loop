#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point orphan-session NOTICE — advisory, read-only, fail-open.

The missing NOTIFY half of EC-04 coord. Physical reaping already runs
capability-gated at SessionStart (``reaper.py --apply``, Step 3 of
``session-start-rally-point.sh``); what was missing is *surfacing* to the human
that stale/orphan sessions linger in the room. This module does exactly that and
NOTHING ELSE:

- It reads eligibility via ``presence.reap_stale(channel_dir, apply=False)`` — a
  DRY-RUN that reports which sessions are over-TTL "regardless of capability"
  without unlinking a file or writing the SHA cache. It NEVER reaps: reaping is
  Rust-only (see ``reaper.py``); a Python hook that deleted coordination records
  it cannot prove dead is the exact shadow-implementation the facade forbids.
- Above a small threshold it emits one advisory WARN naming the count + the
  canonical cleanup command. Below it, silence (don't nag on one just-departed
  session).

Contract: fail-open. Any error → empty string / exit 0. Never raises, never
blocks session start, never mutates room state.
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

# Under-warn: a single just-departed session is normal churn, not clutter worth
# a line. Two or more over-TTL sessions is a real "cleanup is pending" signal.
DEFAULT_THRESHOLD = 2
_MAX_IDS_SHOWN = 5


def _channel_dir(workdir: Path):
    slug = channel_paths.app_slug(workdir)
    return channel_paths.app_channel_dir(slug)


def warn_line_for(stale: list, threshold: int = DEFAULT_THRESHOLD) -> str:
    """Advisory line for ``stale`` orphan session ids, or '' below threshold."""
    if len(stale) < threshold:
        return ""
    shown = ", ".join(str(s) for s in stale[:_MAX_IDS_SHOWN])
    if len(stale) > _MAX_IDS_SHOWN:
        shown += ", …"
    return (
        f"⚠️  rally: {len(stale)} orphan/stale session(s) in this room "
        f"({shown}). A full-capability `rally` session reaps them automatically; "
        f"or run `rally sessions --reap` to clean now."
    )


def orphan_notice(workdir: Path, threshold: int = DEFAULT_THRESHOLD) -> str:
    """Advisory WARN string for orphan sessions in ``workdir``'s room, else ''.

    Read-only (dry-run reap eligibility — no unlink, no cache write). Fail-open:
    rally absent, no room, or any error → '' (never raises)."""
    try:
        cdir = _channel_dir(workdir)
        if not cdir.is_dir():
            return ""
        # apply=False → eligibility only, no mutation, works below full capability.
        stale = presence.reap_stale(cdir, apply=False)
        return warn_line_for(stale, threshold)
    except Exception:  # noqa: BLE001 — advisory hook must never raise
        return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    args = p.parse_args(argv)
    line = orphan_notice(Path(args.workdir).expanduser(), args.threshold)
    if line:
        print(line, file=sys.stderr)
    return 0  # advisory: always exit 0


if __name__ == "__main__":
    raise SystemExit(main())
