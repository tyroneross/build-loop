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

# Note on cross-encoder rerank: a SessionStart bash subshell warm() doesn't
# help because the warmed model dies with the subshell — every recall.py
# call is a fresh Python process that re-pays the ~5-6s sentence-transformers
# load. Real fix is a long-running rerank daemon (FastAPI/gRPC) that holds
# the model in memory across calls. Tracked as Phase G in research entry
# build-loop-search-architecture. Until then, accept the cold-load cost on
# the first recall.py invocation per session.

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
