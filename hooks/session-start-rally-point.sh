#!/usr/bin/env bash
# SessionStart hook — Rally Point cross-session restore line.
#
# Contract (Stop-hook discipline, per feedback_hook_design.md):
#   - command-type only (NEVER prompt-type)
#   - exit 0 ALWAYS (never blocks a session start)
#   - SILENT when there is no delta (no stdout on empty)
#   - prints exactly ONE compact restore line when a peer/commit/dep
#     change has landed since this reader's cursor
#   - graceful absence: no channel for this app -> silent exit 0
#
# It does the cheap revision check first; only on a real delta does it
# read the tail. checkpoint_read itself is non-locking and fire-safe.

WORKDIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$SCRIPT_DIR/../scripts/rally_point"

[ -d "$PKG" ] || exit 0

WORKDIR="$WORKDIR" PKG="$PKG" python3 - <<'PYEOF' 2>/dev/null || exit 0
import os
import sys

sys.path.insert(0, os.environ["PKG"])
try:
    import channel_paths as ap
    import checkpoint as cp
except Exception:
    sys.exit(0)

try:
    slug = ap.app_slug(cwd=os.environ["WORKDIR"])
    chan = ap.app_channel_dir(slug)
    if not chan.exists():
        sys.exit(0)
    # Stable per-reader session id for the SessionStart consumer so the
    # cursor persists across sessions in this checkout.
    sid = f"sessionstart-{slug.replace('/', '_')}"
    env = cp.checkpoint_read(chan, session_id=sid, my_files=[])
    if not env.get("changed"):
        sys.exit(0)  # silent — Stop-hook discipline
    n = len(env.get("new_changes", []))
    peers = len(env.get("active_peers", []))
    rxns = {r.get("type") for r in env.get("reactions", [])}
    bits = [f"{n} change(s)"]
    if peers:
        bits.append(f"{peers} live peer(s)")
    if "reinstall" in rxns:
        bits.append("dep-change: reinstall")
    if "re-baseline" in rxns:
        bits.append("arch changed: re-baseline")
    if "soft-claim" in rxns:
        bits.append("peer owns files (warning)")
    print(f"Rally Point: {slug} — " + "; ".join(bits))
except Exception:
    sys.exit(0)
PYEOF

exit 0
