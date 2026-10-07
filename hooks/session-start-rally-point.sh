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
if [ "${BUILD_LOOP_RALLY_START_CHILD:-0}" != "1" ]; then
    if ! command -v perl >/dev/null 2>&1; then
        printf '{}'
        exit 0
    fi
    exec perl -e '
        use strict;
        use POSIX qw(setsid :sys_wait_h);
        use Time::HiRes qw(time);
        eval { require JSON::PP; 1 } or do { print "{}"; exit 0; };
        my $host_parent = shift @ARGV;
        my $ms = $ENV{BUILD_LOOP_RALLY_START_DEADLINE_MS} // 1500;
        $ms = 1500 unless $ms =~ /\A[0-9]+\z/ && $ms >= 100 && $ms <= 1600;
        my $deadline = time + $ms / 1000;
        $ENV{BUILD_LOOP_RALLY_START_CHILD} = "1";
        $ENV{BUILD_LOOP_RALLY_START_PARENT_PID} = $host_parent;
        my ($r, $w);
        pipe($r, $w) or do { print "{}"; exit 0; };
        my $pid = fork();
        defined($pid) or do { print "{}"; exit 0; };
        if (!$pid) {
            close $r;
            setsid();
            open(STDOUT, ">&", $w) or exit 0;
            close $w;
            exec @ARGV;
            exit 0;
        }
        close $w;
        my $stop = sub { kill "TERM", -$pid; kill "TERM", $pid; };
        $SIG{TERM} = $SIG{INT} = $SIG{HUP} = sub { $stop->(); exit 0; };
        my ($buf, $eof, $reaped) = ("", 0, 0);
        while (time < $deadline) {
            my $left = $deadline - time;
            last if $left <= 0;
            my $rin = "";
            vec($rin, fileno($r), 1) = 1;
            my $n = select(my $rout = $rin, undef, undef, $left < 0.02 ? $left : 0.02);
            if ($n > 0 && !$eof) {
                my $got = sysread($r, $buf, 65536, length $buf);
                $eof = 1 if defined($got) && !$got;
            }
            $reaped = 1 if !$reaped && waitpid($pid, WNOHANG) == $pid;
            if ($reaped && $eof) {
                my $value = eval { JSON::PP::decode_json($buf) };
                print !$@ && ref($value) eq "HASH" ? $buf : "{}";
                exit 0;
            }
        }
        $stop->();
        print "{}";
        exit 0;
    ' "${PPID:-0}" "$BASH" "$0" "$@"
fi
unset BUILD_LOOP_RALLY_START_CHILD
HOST_PARENT_PID="${BUILD_LOOP_RALLY_START_PARENT_PID:-${PPID:-0}}"
unset BUILD_LOOP_RALLY_START_PARENT_PID

D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
W="${CLAUDE_PROJECT_DIR:-$PWD}"
PKG="$(dirname "$D")/scripts/rally_point"
[ -d "$PKG" ] || exit 0
HOST_TOOL="${BUILD_LOOP_RALLY_TOOL:-${RALLY_HOST_FAMILY:-}}"
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

# Step 3: opportunistic reaper sweep — physical cleanup of over-TTL presence/claims/lead.
# Fire-and-forget: never blocks, exit 0 preserved. Python fallback path (actuator).
[ -f "$PKG/reaper.py" ] && python3 "$PKG/reaper.py" --workdir "$W" --apply >/dev/null 2>&1 || true

# Step 4: advance cursor past probe's own writes (silent; same script, no VERBOSE).
python3 "$PKG/hooks.py" session-start-advance --workdir "$W" \
    --tool "$HOST_TOOL" --session-id "$RALLY_HOOK_SESSION_ID" 2>/dev/null || true

# Step 5: on-PATH-vs-pin rally version staleness guard (lane E-a). This is
# the control that would have caught the rally version-mismatch incident:
# warn (never block) when the `rally` binary resolved from PATH is not the
# version build-loop is pinned to. NON-BLOCKING + fail-open — on any error
# (rally absent, python3 failure, unparseable output) this silently no-ops
# and NEVER affects the hook's exit code.
if command -v rally >/dev/null 2>&1; then
    _rally_raw_ver="$(rally version 2>&1 | head -n1)"
    _rally_on_path_ver="${_rally_raw_ver#rally }"
    _rally_on_path_ver="${_rally_on_path_ver%%+*}"
    _rally_on_path_ver="$(printf '%s' "$_rally_on_path_ver" | tr -d '[:space:]')"
    _rally_pinned_ver="$(python3 "$PKG/binary_fetch.py" --print-pin 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$_rally_on_path_ver" ] && [ -n "$_rally_pinned_ver" ] \
        && [ "$_rally_on_path_ver" != "$_rally_pinned_ver" ]; then
        echo "[rally] WARN: on-PATH rally ${_rally_on_path_ver} != pinned ${_rally_pinned_ver} — run 'python3 scripts/rally_point/binary_fetch.py --force' to reconcile" >&2
    fi
fi

# Step 6: peer-on-same-workdir commit-collision WARN (lane EC-03 rca). Advisory +
# fail-open: warns (never blocks) when a peer session is active on THIS workdir —
# the precondition for the concurrent-write race (two writers on one checkout race
# on HEAD/index; commits land on the wrong branch). On any error this silently
# no-ops and NEVER affects the hook's exit code.
[ -f "$PKG/peer_collision.py" ] && python3 "$PKG/peer_collision.py" --workdir "$W" 1>/dev/null || true

# Step 7: orphan/stale-session NOTICE (lane EC-04 coord — notify half). Advisory +
# fail-open + READ-ONLY: a dry-run count of over-TTL sessions surfaced as a WARN.
# It never reaps (Step 3 already does that capability-gated); it only tells the
# human cleanup is pending. Prints only to stderr; exit 0 preserved.
[ -f "$PKG/orphan_notice.py" ] && python3 "$PKG/orphan_notice.py" --workdir "$W" 1>/dev/null || true
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
