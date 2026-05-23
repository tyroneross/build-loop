#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Fire plugin-cache drift check. Worker handles flock + freshness. Silent, exit 0.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$D/_plugin_drift_check_bg.py" ] && [ -d "$HOME/.claude/plugins" ] || exit 0
nohup python3 "$D/_plugin_drift_check_bg.py" </dev/null >/dev/null 2>&1 & disown 2>/dev/null || true
exit 0
