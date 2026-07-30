#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Plugin-cache maintenance, fire-and-forget, HOST-AWARE. This hook runs on BOTH
# hosts — Claude (SessionStart) and Codex (hooks/hooks.json session_start); both
# set CLAUDE_PLUGIN_ROOT, but Codex's resolves under ~/.codex. So derive the host
# from that path and prune the RUNNING host's cache (never the other host's).
#   1. DRIFT  — cached-install-behind-upstream check. Claude only (worker keys on
#               ~/.claude/plugins).
#   2. PRUNE  — auto-remove stale cache versions for the running host via the
#               canonical engine scripts/prune_plugin_cache.py. Engine protects the
#               in-use dir (CLAUDE_PLUGIN_ROOT, default-on) + installed pins
#               (--protect-installed); re-fetched on demand, so prunes are
#               reversible. Opt out: BUILDLOOP_NO_CACHE_GC=1.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
ROOT="${CLAUDE_PLUGIN_ROOT:-$D/..}"
case "$ROOT" in
  */.codex/*) HOST=codex ;;
  *)          HOST=claude ;;
esac
if [ "$HOST" = claude ] && [ -d "$HOME/.claude/plugins" ] && [ -f "$D/_plugin_drift_check_bg.py" ]; then
  bl_fire_bg "$D/_plugin_drift_check_bg.py"
fi
if [ "${BUILDLOOP_NO_CACHE_GC:-}" != "1" ] && [ -f "$ROOT/scripts/prune_plugin_cache.py" ]; then
  bl_fire_bg "$ROOT/scripts/prune_plugin_cache.py" --source "$ROOT" --host "$HOST" --apply --protect-installed
fi
exit 0
