#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# route-guard.sh — minimal-PATH wrapper for route_guard.py. Hooks run under a
# stripped PATH, so resolve python via the shared resolver before dispatch.
# Fail-open: if no python is available, emit an empty decision and exit 0 so a
# routing hint can never break a session.
#
#   usage: route-guard.sh <prompt-submit|pre-skill>   (payload on stdin)

root="${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}"
. "$root/hooks/_resolve_python.sh"
[ -n "$_py" ] || { printf '{}'; exit 0; }
exec "$_py" "$root/hooks/route_guard.py" "$@"
