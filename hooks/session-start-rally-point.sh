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
#
# Timeout budget (2026-10-05): Claude Code runs this hook with a 2s timeout
# and discards ALL output of a hook it cancels. Measured end to end, Steps 1-7
# took 6-8s on a quiet room and 15-23s on a busy one, so the restore line was
# never delivered. Only Step 1 prints to stdout (the context the agent sees);
# Steps 2-7 write side effects or print to stderr, which hooks.json already
# sends to /dev/null. Step 1 therefore runs in the foreground under an
# internal deadline below the host timeout, and Steps 2-7 run in a detached
# background stage, in their original order, after this hook has returned.
# BUILD_LOOP_RALLY_SESSION_START_FOREGROUND=1 runs them inline (tests, manual
# diagnosis -- the stderr WARNs of Steps 5-7 are only visible that way).
# Bound the entire foreground lifecycle, including stdin, interpreter startup
# and identity resolution, before the Python helper arms its narrower timer.
# Buffer stdout so a cancelled child never returns a partial JSON envelope.
# If startup exhausts its budget or the supervisor runtime is unavailable,
# return a no-op; the background stage is skipped until a later lifecycle hook.
# Explicit manual foreground diagnosis retains its longer inline background work.
if [ "${BUILD_LOOP_RALLY_SESSION_START_FOREGROUND:-0}" != "1" ]; then
    . "$(dirname "$0")/_bounded_hook.sh"
    bl_bound_hook "${BUILD_LOOP_RALLY_START_DEADLINE_MS:-1500}" 1600 json "$BASH" "$0" "$@"
fi
unset BUILD_LOOP_RALLY_START_CHILD
HOST_PARENT_PID="${BUILD_LOOP_RALLY_START_PARENT_PID:-${PPID:-0}}"
unset BUILD_LOOP_RALLY_START_PARENT_PID

D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
W="${CLAUDE_PROJECT_DIR:-$PWD}"
PKG="$(dirname "$D")/scripts/rally_point"
[ -d "$PKG" ] || exit 0
# RALLY_TOOL_ID (a host-named actor, e.g. Easy Terminal's easy-terminal:<sid>)
# wins, as in the Rally CLI hook runtime; then the declared host family.
HOST_TOOL="${RALLY_TOOL_ID:-${BUILD_LOOP_RALLY_TOOL:-${RALLY_HOST_FAMILY:-}}}"
if [ -z "$HOST_TOOL" ]; then
    if [ -n "${CODEX_THREAD_ID:-${CODEX_SESSION_ID:-}}" ]; then
        HOST_TOOL=codex
    else
        HOST_TOOL=claude_code
    fi
fi

# Claude's hook event is the authoritative session identity. Do not depend on
# CLAUDE_SESSION_ID being exported: plugin hooks receive the same session_id on
# stdin at SessionStart, PreToolUse, and Stop. The shared Python resolver keeps
# explicit/event, environment, and process fallbacks identical for every path.
STDIN_JSON=""
if [ ! -t 0 ]; then
    STDIN_JSON=$(head -c 65536 2>/dev/null || true)
fi
EVENT_SESSION_ID=""
if [ -n "$STDIN_JSON" ]; then
    EVENT_SESSION_ID=$(STDIN_JSON="$STDIN_JSON" python3 - <<'PYEOF' 2>/dev/null
import json, os
try:
    event = json.loads(os.environ.get("STDIN_JSON") or "{}")
except (TypeError, ValueError):
    event = {}
value = event.get("session_id") if isinstance(event, dict) else ""
print(str(value or "").replace("\n", " ").replace("\t", " "), end="")
PYEOF
    ) || EVENT_SESSION_ID=""
fi
RALLY_HOOK_SESSION_ID=""
if [ -f "$PKG/actor_identity.py" ]; then
    RALLY_HOOK_SESSION_ID=$(RALLY_OBSERVER_PID="$HOST_PARENT_PID" \
        python3 "$PKG/actor_identity.py" --tool "$HOST_TOOL" \
        --session-id "$EVENT_SESSION_ID" --field session-id 2>/dev/null || true)
fi
[ -n "$RALLY_HOOK_SESSION_ID" ] || \
    RALLY_HOOK_SESSION_ID="${EVENT_SESSION_ID:-${BUILD_LOOP_RALLY_SESSION_ID:-${CODEX_THREAD_ID:-${CODEX_SESSION_ID:-${CLAUDE_SESSION_ID:-${RALLY_AGENT_ID:-${RALLY_SESSION_ID:-${HOST_TOOL}-${HOST_PARENT_PID}}}}}}}}"

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
BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS="${BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS:-1.2}" \
    python3 "$PKG/hooks.py" session-start-safe --workdir "$W" --verbose \
    --tool "$HOST_TOOL" --session-id "$RALLY_HOOK_SESSION_ID" 2>/dev/null || true

_rally_session_start_background() {

# Step 2: probe announce + listen (fully detached watcher).
if [ "${BUILD_LOOP_RALLY_POINT_SKIP_WATCH:-}" = "1" ]; then
    [ -f "$PKG/session_probe.py" ] && WORKDIR="$W" python3 "$PKG/session_probe.py" \
        --workdir "$W" --tool "$HOST_TOOL" --session-id "$RALLY_HOOK_SESSION_ID" \
        --mode hook >/dev/null 2>&1 || true
else
    [ -f "$PKG/session_probe.py" ] && WORKDIR="$W" python3 "$PKG/session_probe.py" \
        --workdir "$W" --tool "$HOST_TOOL" --session-id "$RALLY_HOOK_SESSION_ID" \
        --mode hook --start-watch \
        --watch-parent-pid "$HOST_PARENT_PID" >/dev/null 2>&1 || true
fi

# Reaping and version reconciliation are explicit maintenance, not startup work.

# Step 4: advance cursor past probe's own writes (silent; same script, no VERBOSE).
python3 "$PKG/hooks.py" session-start-advance --workdir "$W" \
    --tool "$HOST_TOOL" --session-id "$RALLY_HOOK_SESSION_ID" 2>/dev/null || true

# Step 6: peer-on-same-workdir commit-collision WARN (lane EC-03 rca). Advisory +
# fail-open: warns (never blocks) when a peer session is active on THIS workdir —
# the precondition for the concurrent-write race (two writers on one checkout race
# on HEAD/index; commits land on the wrong branch). On any error this silently
# no-ops and NEVER affects the hook's exit code.
[ -f "$PKG/peer_collision.py" ] && python3 "$PKG/peer_collision.py" --workdir "$W" 1>/dev/null || true


}

if [ "${BUILD_LOOP_RALLY_SESSION_START_FOREGROUND:-0}" = "1" ]; then
    _rally_session_start_background
else
    # Fully detached: no inherited stdio (the host waits for its pipes to
    # close), and SIGHUP ignored so the stage outlives this hook's shell.
    # Preserve the host parent through the supervisor and detached subshell.
    ( trap '' HUP; _rally_session_start_background ) </dev/null >/dev/null 2>&1 &
    disown 2>/dev/null || true
fi

exit 0
