#!/usr/bin/env bash
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
DAEMON_PID_FILE="$LOG_DIR/rerank-daemon.pid"
if [ -f "$SCRIPT_DIR/rerank_daemon.py" ]; then
    DAEMON_PORT="${RERANK_DAEMON_PORT:-8765}"
    daemon_alive=0
    if [ -f "$DAEMON_PID_FILE" ]; then
        existing_pid=$(cat "$DAEMON_PID_FILE" 2>/dev/null)
        if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
            daemon_alive=1
        fi
    fi
    # Belt-and-suspenders: also probe the port — handles the case where
    # the PID file got stale but a process is still bound to the port.
    if [ "$daemon_alive" = "0" ]; then
        if curl -fsS --max-time 0.2 "http://127.0.0.1:${DAEMON_PORT}/health" >/dev/null 2>&1; then
            daemon_alive=1
        fi
    fi
    if [ "$daemon_alive" = "0" ]; then
        nohup python3 "$SCRIPT_DIR/rerank_daemon.py" \
            </dev/null \
            >>"$LOG_DIR/rerank-daemon.log" 2>&1 &
        disown 2>/dev/null
    fi
fi

# 1c. Embed daemon (Phase H). Same architectural fix as 1b — long-running
#     stdlib http.server holds the embedder backend (MLX
#     mxbai-embed-large-v1, or Ollama bge-m3 fallback) in memory across
#     recall.py invocations, eliminating the ~3000ms cold-load cliff per
#     fresh process. Independent of the rerank daemon: different port
#     (8766 vs 8765), different PID file. Both daemons spawn here in
#     parallel and each survives across recall.py invocations.
EMBED_DAEMON_PID_FILE="$LOG_DIR/embed-daemon.pid"
if [ -f "$SCRIPT_DIR/embed_daemon.py" ]; then
    EMBED_DAEMON_PORT_VAL="${EMBED_DAEMON_PORT:-8766}"
    embed_daemon_alive=0
    if [ -f "$EMBED_DAEMON_PID_FILE" ]; then
        existing_pid=$(cat "$EMBED_DAEMON_PID_FILE" 2>/dev/null)
        if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
            embed_daemon_alive=1
        fi
    fi
    # Belt-and-suspenders: probe the port too — handles stale PID files
    # left after a hard kill.
    if [ "$embed_daemon_alive" = "0" ]; then
        if curl -fsS --max-time 0.2 "http://127.0.0.1:${EMBED_DAEMON_PORT_VAL}/health" >/dev/null 2>&1; then
            embed_daemon_alive=1
        fi
    fi
    if [ "$embed_daemon_alive" = "0" ]; then
        nohup python3 "$SCRIPT_DIR/embed_daemon.py" \
            </dev/null \
            >>"$LOG_DIR/embed-daemon.log" 2>&1 &
        disown 2>/dev/null
    fi
fi

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
