#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# session-start-closeout.sh — drain a post-push armed baton and surface
# pending-lesson candidates to the host coding agent.
#
# Triggered by Claude Code SessionStart (see hooks/hooks.json). Fail-open
# (always ``exit 0``) so a broken hook can never wedge a session start.
#
# Two responsibilities:
#   1. If ``.build-loop/closeout/armed.json`` exists, the previous session
#      ran ``git push``. Drain the baton by invoking ``python3 -m closeout``
#      with ``--source post-push-armed`` and delete the baton on success.
#   2. Surface pending-lesson candidates (one-shot) so the host agent sees
#      them in the next turn without having to re-discover them.
#
# Minimal PATH safe: resolves ``python3`` via ``command -v``; bare ``set -e``
# is intentionally avoided.

set -u

# Resolve the project / plugin root.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${PWD}}"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${PROJECT_DIR}}"

# Resolve a python3 binary without depending on PATH being populated.
_py=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        _py="$candidate"
        break
    fi
done
for fallback in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if [ -z "$_py" ] && [ -x "$fallback" ]; then
        _py="$fallback"
    fi
done
[ -n "$_py" ] || exit 0

ARMED="${PROJECT_DIR}/.build-loop/closeout/armed.json"
CLOSEOUT_LOG_DIR="${PROJECT_DIR}/.build-loop/closeout"
mkdir -p "$CLOSEOUT_LOG_DIR" 2>/dev/null || true

# 1. Drain the armed baton if present.
if [ -f "$ARMED" ]; then
    RID="armed-$(date -u +%Y%m%dT%H%M%SZ)"
    "$_py" -m closeout \
        --workdir "$PROJECT_DIR" \
        --run-id "$RID" \
        --source post-push-armed \
        --json \
        2>/dev/null \
        >"${CLOSEOUT_LOG_DIR}/${RID}.stdout.json" \
        && rm -f "$ARMED" 2>/dev/null \
        || true
fi

# 2. Surface pending-lesson candidates (quiet — nothing prints when empty).
SURFACED="${CLOSEOUT_LOG_DIR}/surfaced-$(date -u +%Y%m%dT%H%M%SZ).md"
"$_py" "${PLUGIN_ROOT}/scripts/surface_pending_lessons.py" \
    --workdir "$PROJECT_DIR" \
    --quiet \
    2>/dev/null \
    >"$SURFACED" \
    || true
# Drop the surfaced file when empty so the directory stays clean.
if [ ! -s "$SURFACED" ]; then
    rm -f "$SURFACED" 2>/dev/null || true
fi

exit 0
