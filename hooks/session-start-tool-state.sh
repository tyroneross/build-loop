#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Snapshot .build-loop/ and .rally/ dir + git-hook state at session start so a
# later report can attribute them to build-loop's own hooks instead of
# calling them "pre-existing untracked folders". Runs first so it beats
# sibling hooks that create these dirs (30s grace window absorbs the race).
# Bound stdin EOF and interpreter startup as well as the snapshot itself.
. "$(dirname "$0")/_bounded_hook.sh"
bl_bound_hook "${BUILD_LOOP_TOOL_STATE_DEADLINE_MS:-1500}" 1500 text "$BASH" "$0" "$@"

D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$D")"
W="${CLAUDE_PROJECT_DIR:-$PWD}"
SCRIPT="$ROOT/scripts/tool_state_paths.py"
[ -f "$SCRIPT" ] || exit 0

STDIN_JSON=""
if [ ! -t 0 ]; then
    STDIN_JSON="$(head -c 65536 2>/dev/null || true)"
fi

printf '%s' "$STDIN_JSON" | python3 "$SCRIPT" snapshot --workdir "$W" --emit-context
exit 0
