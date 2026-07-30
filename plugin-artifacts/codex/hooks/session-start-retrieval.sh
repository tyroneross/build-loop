#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Retrieval warmup + silent-failure canary. Fire-and-forget.
set +e
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
W="${CLAUDE_PROJECT_DIR:-$PWD}"
S="${CLAUDE_PLUGIN_ROOT:-$W}/scripts"
LD="${XDG_STATE_HOME:-$HOME/.local/state}/build-loop"
mkdir -p "$LD" 2>/dev/null

# Ollama daemon ping (warms loaded models in RAM).
( command -v ollama >/dev/null 2>&1 && nohup ollama list </dev/null >/dev/null 2>&1 & )

# Long-running rerank + embed daemons (single-flight).
bl_spawn_daemon "$S/rerank_daemon.py" "${RERANK_DAEMON_PORT:-8765}" rerank-daemon rerank-daemon "$LD"
bl_spawn_daemon "$S/embed_daemon.py"  "${EMBED_DAEMON_PORT:-8766}"  embed-daemon  embed-daemon  "$LD"

# Health canary -> state.json + rotating log.
[ -f "$S/backend_health.py" ] && nohup python3 "$S/backend_health.py" \
    --workdir "$W" --include-retrieval --quiet \
    </dev/null >>"$LD/retrieval-health.log" 2>&1 & disown 2>/dev/null || true

exit 0
