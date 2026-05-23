#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Plugin-cache drift check. Worker handles flock + freshness.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$D/_session_start_lib.sh"
[ -f "$D/_plugin_drift_check_bg.py" ] && [ -d "$HOME/.claude/plugins" ] || exit 0
bl_fire_bg "$D/_plugin_drift_check_bg.py"
exit 0
