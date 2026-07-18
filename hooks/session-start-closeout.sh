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

# Resolve a python3 binary without depending on PATH being populated (shared helper).
_HOOK_DIR="$(dirname "$0")"
_py=""
[ -f "${_HOOK_DIR}/_resolve_python.sh" ] && . "${_HOOK_DIR}/_resolve_python.sh"
[ -n "$_py" ] || exit 0

ARMED="${PROJECT_DIR}/.build-loop/closeout/armed.json"
CLOSEOUT_LOG_DIR="${PROJECT_DIR}/.build-loop/closeout"
mkdir -p "$CLOSEOUT_LOG_DIR" 2>/dev/null || true

# Ensure the closeout package (scripts/closeout/) is importable in hook env.
# PLUGIN_ROOT already resolves to the repo root (see above); scripts/ is the
# package parent. Set once here so both the closeout call and surface_pending use it.
export PYTHONPATH="${PLUGIN_ROOT}/scripts${PYTHONPATH:+:$PYTHONPATH}"

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

# 3. Durable fallback for the scope-gated post-push RETRO trigger. Git has no
#    native post-push hook, so the retro job is spawned detached at pre-push
#    time; if the machine slept or crashed before it finished, a stale armed
#    baton survives. This drains it, and escalates any stale queued Fable
#    upgrade to the backlog/Ops-Center fallback so "medium -> run the judge"
#    can never silently become "never". Zero-LLM, fail-open (PYTHONPATH set above).
RETRO_DRAIN="${CLOSEOUT_LOG_DIR}/retro-drain-$(date -u +%Y%m%dT%H%M%SZ).json"
"$_py" -m post_push_retro drain \
    --workdir "$PROJECT_DIR" \
    --json \
    2>/dev/null \
    >"$RETRO_DRAIN" \
    || true
# The drain emits a machine-checkable `"did_work": true` + a one-line `summary`.
# When work happened, ECHO the summary to stdout (SessionStart's injection
# surface, same as the sibling closeout step) so the in-context agent sees a
# pending upgrade / re-filed witness this turn; keep the log. Otherwise drop it.
if [ -s "$RETRO_DRAIN" ] && grep -q '"did_work": true' "$RETRO_DRAIN" 2>/dev/null; then
    "$_py" -c "import json,sys; print(json.load(open(sys.argv[1])).get('summary',''))" \
        "$RETRO_DRAIN" 2>/dev/null || true
else
    rm -f "$RETRO_DRAIN" 2>/dev/null || true
fi

exit 0
