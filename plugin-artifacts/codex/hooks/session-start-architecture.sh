#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Incremental architecture scan when manifest is missing or >24h old.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
W="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -d "$W/.build-loop/architecture" ] && [ -f "$D/_arch_scan_bg.py" ] || exit 0
[ "$(bl_stale "$W/.build-loop/architecture/manifest.json" 86400)" = "yes" ] || exit 0
bl_fire_bg "$D/_arch_scan_bg.py" --workdir "$W"
exit 0
