#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Plugin-cache self-heal, fire-and-forget, plugin-relative.
#
# Heals a Claude Code plugin install whose versioned cache dir vanished while
# another session was bound to it. Two cases:
#   A) /plugin install archived the prior dir to ~/.claude/plugins/removed/
#      → move it back to its registered installPath.
#   B) /plugin update hard-deleted the prior dir after installing a newer one
#      → create a symlink old→new so the frozen ${CLAUDE_PLUGIN_ROOT} path
#        resolves to live scripts.
#
# Heals the NEXT session (CC re-resolves ${CLAUDE_PLUGIN_ROOT} only at session
# start). Cannot save a currently-bound dead-path session — restart-or-symlink
# remains the only in-session remedy.
#
# Opt out: BUILDLOOP_NO_PLUGIN_HEAL=1.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
ROOT="${CLAUDE_PLUGIN_ROOT:-$D/..}"
# Heal only runs against ~/.claude (Claude Code's pre-validate-dir-exists is
# the hazard; Codex doesn't have the same harness behaviour). Cheap-skip on
# Codex hosts by path heuristic.
case "$ROOT" in
  */.codex/*) exit 0 ;;
esac
if [ "${BUILDLOOP_NO_PLUGIN_HEAL:-}" = "1" ]; then
  exit 0
fi
if [ -f "$ROOT/scripts/hooks/plugin_dir_heal.py" ]; then
  bl_fire_bg "$ROOT/scripts/hooks/plugin_dir_heal.py"
fi
exit 0
