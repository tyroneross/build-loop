#!/usr/bin/env bash
# SessionStart hook — Rally Point announce + listen (R2 auto-invoke).
#
# Contract (Stop-hook discipline, per feedback_hook_design.md):
#   - command-type only (NEVER prompt-type)
#   - exit 0 ALWAYS (never blocks a session start)
#   - SILENT when there is no delta and no peers (no stdout on empty)
#   - graceful absence: no channel for this app -> silent exit 0
#   - timeout-safe: watcher launched fully detached (nohup + start_new_session)
#     so session_probe.py returns immediately after spawning it
#
# Behaviour (R2 upgrade from passive to active):
#   1. Runs session_probe.py --start-watch: writes presence + posts
#      kind=phase payload.phase=rally-start + optionally launches watcher.
#      This is the "announce + listen" step that makes the session visible
#      to peers even in solo mode (Codex retro §6 solo-mode fix).
#   2. Then runs the original checkpoint_read restore line so any peer/commit/
#      dep delta that landed since last session is still surfaced.

WORKDIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PKG="$REPO_ROOT/scripts/rally_point"
PROBE="$PKG/session_probe.py"

[ -d "$PKG" ] || exit 0

# ---- Step 1: Checkpoint-read restore line (captures pre-probe delta) -----
# Runs FIRST so the cursor captures only changes that landed before THIS
# session. The probe (Step 2) will add its own rally-start record, but
# since the cursor already advanced past the current revision, a subsequent
# hook call won't resurface the probe's own writes as "new" changes.
#
# Graceful absence: no channel for this app → silent exit, then the probe
# in Step 2 will create the channel fresh.
WORKDIR="$WORKDIR" PKG="$PKG" python3 - <<'PYEOF' 2>/dev/null || true
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

# ---- Step 2: Active announce + listen via session_probe ------------------
# Run AFTER the checkpoint_read so the probe's own rally-start record does
# not appear as a "new change" in the restore line above.
# Output is suppressed (stdout + stderr) — the session_probe is fire-and-
# forget from the hook's perspective; the human-readable signal is the
# restore line from Step 1.
# --start-watch launches the watcher fully detached so this returns fast
# (well within the 2000ms hook budget).
if [ -f "$PROBE" ]; then
    WORKDIR="$WORKDIR" python3 "$PROBE" \
        --workdir "$WORKDIR" \
        --tool claude_code \
        --mode hook \
        --start-watch \
        >/dev/null 2>&1 || true
fi

# ---- Step 3: Advance reader cursor past probe's own writes ---------------
# The probe just posted a rally-start record. Run a silent checkpoint_read
# to consume it so the next hook call or pre-edit hook doesn't surface the
# probe's own writes as "new changes". This keeps the existing hook
# test contracts intact (silent on second call; pre-edit silent after fresh
# probe). Output suppressed — this is bookkeeping only.
WORKDIR="$WORKDIR" PKG="$PKG" python3 - <<'PYEOF2' 2>/dev/null || true
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
    sid = f"sessionstart-{slug.replace('/', '_')}"
    # Silently advance the cursor — output is suppressed at the shell level.
    cp.checkpoint_read(chan, session_id=sid, my_files=[])
except Exception:
    sys.exit(0)
PYEOF2

exit 0
