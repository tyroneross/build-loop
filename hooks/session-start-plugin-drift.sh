#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Plugin-cache maintenance, fire-and-forget:
#   1. DRIFT  — detect cached install behind upstream main (worker: flock + 24h gate).
#   2. PRUNE  — auto-remove stale local cache versions via the canonical engine
#               scripts/prune_plugin_cache.py. The engine protects the in-use
#               version dir (CLAUDE_PLUGIN_ROOT) by default and only deletes dirs
#               whose cached manifest confirms the build-loop plugin name; CC
#               re-fetches on demand, so prunes are reversible. Claude host only —
#               a Claude session can't know Codex's active version, and Codex has
#               its own already-wired prune path. Opt out: BUILDLOOP_NO_CACHE_GC=1.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
[ -d "$HOME/.claude/plugins" ] || exit 0
[ -f "$D/_plugin_drift_check_bg.py" ] && bl_fire_bg "$D/_plugin_drift_check_bg.py"
ROOT="${CLAUDE_PLUGIN_ROOT:-$D/..}"
if [ "${BUILDLOOP_NO_CACHE_GC:-}" != "1" ] && [ -f "$ROOT/scripts/prune_plugin_cache.py" ]; then
  bl_fire_bg "$ROOT/scripts/prune_plugin_cache.py" --source "$ROOT" --host claude --apply --protect-installed
fi
exit 0
