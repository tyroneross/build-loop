#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Rally Point R2 announce + listen. Surfaces deltas/peers, then advances cursor.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
W="${CLAUDE_PROJECT_DIR:-$PWD}"
PKG="$(dirname "$D")/scripts/rally_point"
[ -d "$PKG" ] || exit 0

# Step 1: checkpoint_read restore line (verbose: surface delta if any).
python3 "$PKG/hooks.py" session-start-restore --workdir "$W" --verbose 2>/dev/null || true

# Step 2: probe announce + listen (fully detached watcher).
if [ "${BUILD_LOOP_RALLY_POINT_SKIP_WATCH:-}" = "1" ]; then
    [ -f "$PKG/session_probe.py" ] && WORKDIR="$W" python3 "$PKG/session_probe.py" \
        --workdir "$W" --tool claude_code --mode hook >/dev/null 2>&1 || true
else
    [ -f "$PKG/session_probe.py" ] && WORKDIR="$W" python3 "$PKG/session_probe.py" \
        --workdir "$W" --tool claude_code --mode hook --start-watch >/dev/null 2>&1 || true
fi

# Step 3: advance cursor past probe's own writes (silent; same script, no VERBOSE).
python3 "$PKG/hooks.py" session-start-advance --workdir "$W" 2>/dev/null || true

exit 0
