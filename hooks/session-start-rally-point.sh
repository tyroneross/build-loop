#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Rally Point R2 announce + listen. Surfaces deltas/peers, then advances cursor.
#
# Codex-parity hardening (2026-06-07):
# When ``CLAUDE_PROJECT_DIR`` is unset AND ``$PWD`` is NOT a git repo
# (e.g. ``$HOME`` for a Claude Code launched outside a repo), do NOT
# write presence to the launch dir's room. The per-tool-use rally hook
# (pre-edit-rally-point.sh, also registered on the Bash matcher) joins
# the OPERATIVE repo's room on the first tool-use, recovering from a
# cross-repo launch without polluting the home / ``_unscoped`` room.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
W="${CLAUDE_PROJECT_DIR:-$PWD}"
PKG="$(dirname "$D")/scripts/rally_point"
[ -d "$PKG" ] || exit 0

# Guard: if CLAUDE_PROJECT_DIR is unset and $PWD is not a git repo, the
# launch cwd is not a rally room — defer the join to pre-edit (operative
# repo resolution from the first Edit/Write/Bash). This is the no-op path.
if [ -z "${CLAUDE_PROJECT_DIR:-}" ]; then
    if ! git -C "$W" rev-parse --git-dir >/dev/null 2>&1; then
        exit 0
    fi
fi

# Step 1: checkpoint_read restore line (verbose: surface delta if any).
# ``session-start-safe`` no-ops cleanly when the resolved workdir is not a
# git repo (belt-and-braces — the shell guard above already filters).
python3 "$PKG/hooks.py" session-start-safe --workdir "$W" --verbose 2>/dev/null || true

# Step 2: probe announce + listen (fully detached watcher).
if [ "${BUILD_LOOP_RALLY_POINT_SKIP_WATCH:-}" = "1" ]; then
    [ -f "$PKG/session_probe.py" ] && WORKDIR="$W" python3 "$PKG/session_probe.py" \
        --workdir "$W" --tool claude_code --mode hook >/dev/null 2>&1 || true
else
    [ -f "$PKG/session_probe.py" ] && WORKDIR="$W" python3 "$PKG/session_probe.py" \
        --workdir "$W" --tool claude_code --mode hook --start-watch >/dev/null 2>&1 || true
fi

# Step 3: opportunistic reaper sweep — physical cleanup of over-TTL presence/claims/lead.
# Fire-and-forget: never blocks, exit 0 preserved. Python fallback path (actuator).
[ -f "$PKG/reaper.py" ] && python3 "$PKG/reaper.py" --workdir "$W" --apply >/dev/null 2>&1 || true

# Step 4: advance cursor past probe's own writes (silent; same script, no VERBOSE).
python3 "$PKG/hooks.py" session-start-advance --workdir "$W" 2>/dev/null || true

exit 0
