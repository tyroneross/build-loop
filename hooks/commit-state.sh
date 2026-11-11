#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Bound interpreter startup, stdin and Git together below the 5s host limit.
set -u
_hook_dir="$(cd "$(dirname "$0")" && pwd)"
. "$_hook_dir/_bounded_hook.sh"
bl_bound_hook "${BUILD_LOOP_COMMIT_STATE_DEADLINE_MS:-3500}" 3500 advisory "$BASH" "$0" "$@"
. "$_hook_dir/_resolve_python.sh"
_root="${CLAUDE_PLUGIN_ROOT:-$(dirname "$_hook_dir")}"
if [ -n "$_py" ] && [ -f "$_root/scripts/commit_state_check.py" ]; then
    exec "$_py" "$_root/scripts/commit_state_check.py" --workdir "${CLAUDE_PROJECT_DIR:-$PWD}" --hook
fi
printf '{}'
