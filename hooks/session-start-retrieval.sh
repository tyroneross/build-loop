#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# SessionStart retrieval warmup + silent-failure canary.
#
# Two responsibilities, both fire-and-forget:
#   1. Warm Ollama by listing models — keeps daemon-loaded models in RAM.
#      Without OLLAMA_KEEP_ALIVE in the daemon's env, a 5-minute idle gap
#      evicts the model and re-pays ~250-500ms on the next call.
#   2. Run backend_health --include-retrieval and write a one-liner +
#      JSON envelope to state.json.architecture.backendHealth, so any
#      silent break (psycopg missing, MLX broken, daemon down, FTS index
#      missing) surfaces immediately instead of on first query.
#
# Exit 0 always. Hooks must be silent on success and non-blocking on
# failure (the build-loop pattern is "fire, never block, never fail
# the user's session").

set +e

WORKDIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
SCRIPT_DIR="${CLAUDE_PLUGIN_ROOT:-$WORKDIR}/scripts"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/build-loop"
mkdir -p "$LOG_DIR" 2>/dev/null

# 1. Ollama warmup. `ollama list` is enough to ping the daemon; if any
#    model was loaded, it's still warm. This is a no-op if Ollama isn't
#    installed or running.
( command -v ollama >/dev/null 2>&1 && nohup ollama list </dev/null >/dev/null 2>&1 & )

# 1b. Rerank daemon (Phase G). The fix for the cold-load cliff: a
#     long-running stdlib http.server holds bge-reranker-v2-m3 in memory
#     across recall.py invocations. SessionStart spawns it once,
#     fire-and-forget, with a PID-file single-instance guard so N
#     concurrent SessionStart hooks don't create N daemons.
#
#     Earlier note (kept for memory): a bash-subshell warm() doesn't help
#     because the warmed model dies with the subshell. The daemon fixes
#     that by being a separate, persistent process the SessionStart hook
#     only touches via spawn-if-not-running.
# Spawn a long-running daemon if not already alive. Args: script-name port pidfile-name.
# Liveness = PID-file + kill -0 OR /health probe (belt-and-suspenders against stale PIDs).
_spawn_daemon() {
    local script="$1" port="$2" pidfile="$LOG_DIR/$3.pid" log="$LOG_DIR/$3.log"
    [ -f "$SCRIPT_DIR/$script" ] || return 0
    if [ -f "$pidfile" ]; then
        local pid; pid=$(cat "$pidfile" 2>/dev/null)
        [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && return 0
    fi
    curl -fsS --max-time 0.2 "http://127.0.0.1:${port}/health" >/dev/null 2>&1 && return 0
    nohup python3 "$SCRIPT_DIR/$script" </dev/null >>"$log" 2>&1 &
    disown 2>/dev/null
}
_spawn_daemon rerank_daemon.py "${RERANK_DAEMON_PORT:-8765}" rerank-daemon
_spawn_daemon embed_daemon.py "${EMBED_DAEMON_PORT:-8766}"  embed-daemon

# 2. Health canary, backgrounded so SessionStart returns immediately.
#    Output goes to state.json + a rotating log; nothing reaches the
#    user's terminal.
if [ -f "$SCRIPT_DIR/backend_health.py" ]; then
    nohup python3 "$SCRIPT_DIR/backend_health.py" \
        --workdir "$WORKDIR" \
        --include-retrieval \
        --quiet \
        </dev/null \
        >>"$LOG_DIR/retrieval-health.log" 2>&1 &
fi

exit 0
