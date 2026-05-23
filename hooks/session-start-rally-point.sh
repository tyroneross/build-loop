#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Rally Point R2 announce + listen. Surfaces deltas/peers, then advances cursor.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
W="${CLAUDE_PROJECT_DIR:-$PWD}"
PKG="$(dirname "$D")/scripts/rally_point"
[ -d "$PKG" ] || exit 0

# Step 1: checkpoint_read restore line (verbose: surface delta if any).
WORKDIR="$W" PKG="$PKG" VERBOSE=1 python3 - <<'PY' 2>/dev/null || true
import os, sys
sys.path.insert(0, os.environ["PKG"])
try:
    import channel_paths as ap, checkpoint as cp
except Exception:
    sys.exit(0)
try:
    slug = ap.app_slug(cwd=os.environ["WORKDIR"])
    chan = ap.app_channel_dir(slug)
    if not chan.exists():
        sys.exit(0)
    sid = f"sessionstart-{slug.replace('/', '_')}"
    env = cp.checkpoint_read(chan, session_id=sid, my_files=[])
    if not os.environ.get("VERBOSE") or not env.get("changed"):
        sys.exit(0)
    bits = [f"{len(env.get('new_changes', []))} change(s)"]
    peers = len(env.get("active_peers", []))
    if peers: bits.append(f"{peers} live peer(s)")
    rxns = {r.get("type") for r in env.get("reactions", [])}
    if "reinstall"   in rxns: bits.append("dep-change: reinstall")
    if "re-baseline" in rxns: bits.append("arch changed: re-baseline")
    if "soft-claim"  in rxns: bits.append("peer owns files (warning)")
    print(f"Rally Point: {slug} — " + "; ".join(bits))
except Exception:
    sys.exit(0)
PY

# Step 2: probe announce + listen (fully detached watcher).
[ -f "$PKG/session_probe.py" ] && WORKDIR="$W" python3 "$PKG/session_probe.py" \
    --workdir "$W" --tool claude_code --mode hook --start-watch >/dev/null 2>&1 || true

# Step 3: advance cursor past probe's own writes (silent; same script, no VERBOSE).
WORKDIR="$W" PKG="$PKG" python3 - <<'PY' 2>/dev/null || true
import os, sys
sys.path.insert(0, os.environ["PKG"])
try:
    import channel_paths as ap, checkpoint as cp
    slug = ap.app_slug(cwd=os.environ["WORKDIR"])
    chan = ap.app_channel_dir(slug)
    if chan.exists():
        cp.checkpoint_read(chan, session_id=f"sessionstart-{slug.replace('/', '_')}", my_files=[])
except Exception:
    sys.exit(0)
PY

exit 0
