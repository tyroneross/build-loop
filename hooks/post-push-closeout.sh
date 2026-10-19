#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# post-push-closeout.sh — warm-context soft closeout after a `git push`.
#
# Triggered by Claude Code PostToolUse:Bash (see hooks/hooks.json). When the
# command that just ran was a `git push`, fire the EXISTING closeout in the
# BACKGROUND (nohup) while context is still warm. This complements — does NOT
# replace — the crash-fallback path:
#
#   - pre-push (hooks/git/pre-push) arms .build-loop/closeout/armed.json.
#   - next SessionStart (hooks/session-start-closeout.sh) drains the baton.
#
# This hook short-circuits that fallback in the common case by draining
# immediately in the background. It reuses `python3 -m closeout` verbatim — NO
# duplicated closeout logic. Because closeout writes durable memory + clears the
# baton on success, the next SessionStart simply finds nothing to drain.
#
# Discipline: PostToolUse hooks observe; this one never blocks the tool result.
# Fail-open (always exit 0). The actual closeout runs detached so a slow memory
# write never stalls the agent's turn. Minimal-PATH safe (resolves python3 via
# command -v); no bare `set -e`.

set -u

INPUT="$(cat 2>/dev/null)" || exit 0
# Cheap prefilter only; the shared classifier below decides whether it is code.
case "$INPUT" in *git*push*) ;; *) exit 0 ;; esac

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${PWD}}"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${PROJECT_DIR}}"
_HOOK_DIR="$(dirname "$0")"

# Claude supplies one JSON event on stdin, not a TOOL_INPUT variable. Parse
# only tool_input.command so quoted output or documentation cannot fire this
# observer. Reuse the pre-action classifier rather than adding another parser.
_py=""
[ -f "${_HOOK_DIR}/_resolve_python.sh" ] && . "${_HOOK_DIR}/_resolve_python.sh"
[ -n "$_py" ] || exit 0
CMD="$(printf '%s' "$INPUT" | PYTHONPATH="${PLUGIN_ROOT}/scripts/hooks" "$_py" -c '
import json, sys
from git_command_classifier import classify_command
try:
    event = json.load(sys.stdin)
    command = event.get("tool_input", {}).get("command", "")
    if (event.get("hook_event_name") == "PostToolUse"
            and event.get("tool_name") == "Bash"
            and isinstance(command, str) and "push" in classify_command(command)):
        print(command)
except (ValueError, AttributeError, TypeError):
    pass
' 2>/dev/null)" || exit 0
[ -n "$CMD" ] || exit 0

# Attribution audit: files the owner's credit-link gaps as backlog items for
# the pushed repo (see skills/attribution-standard/SKILL.md). Fully detached so
# it never delays the turn; the script itself no-ops without an owner profile,
# for repos the owner doesn't own, or with BUILDLOOP_ATTRIBUTION_AUDIT=0.
(
    _before_push="${CMD%%git push*}"
    _dir="$(printf '%s' "$CMD" | sed -nE 's/.*git[[:space:]]+-C[[:space:]]+([^[:space:];&|"]+).*/\1/p' | head -1)"
    [ -n "$_dir" ] || _dir="$(printf '%s' "$_before_push" | sed -nE 's/(^|.*[";&[:space:]])cd[[:space:]]+([^[:space:];&|"]+).*/\2/p' | head -1)"
    [ -n "$_dir" ] || _dir="$PROJECT_DIR"
    case "$_dir" in "~"*) _dir="${HOME}${_dir#\~}" ;; esac
    _common="$(git -C "$_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
    [ -f "${PLUGIN_ROOT}/scripts/attribution_audit.py" ] || exit 0
    _py=""
    [ -f "${_HOOK_DIR}/_resolve_python.sh" ] && . "${_HOOK_DIR}/_resolve_python.sh"
    [ -n "$_py" ] || exit 0
    "$_py" "${PLUGIN_ROOT}/scripts/attribution_audit.py" file --repo "$(dirname "$_common")"
) </dev/null >/dev/null 2>&1 &

# A closeout only makes sense inside a build-loop project.
[ -d "${PROJECT_DIR}/.build-loop" ] || exit 0

# Resolve python3 without depending on a populated PATH (shared helper).
_py=""
[ -f "${_HOOK_DIR}/_resolve_python.sh" ] && . "${_HOOK_DIR}/_resolve_python.sh"
[ -n "$_py" ] || exit 0

CLOSEOUT_LOG_DIR="${PROJECT_DIR}/.build-loop/closeout"
mkdir -p "$CLOSEOUT_LOG_DIR" 2>/dev/null || true
RID="postpush-$(date -u +%Y%m%dT%H%M%SZ)"

# Ensure the closeout package (scripts/closeout/) is importable regardless of
# the invoking environment's PYTHONPATH (hooks run under minimal PATH; the package
# is not installed globally — it lives under PLUGIN_ROOT/scripts/).
export PYTHONPATH="${PLUGIN_ROOT}/scripts${PYTHONPATH:+:$PYTHONPATH}"

# Fire the existing closeout in the background; never block the turn. On success
# the closeout module clears the armed baton, so the SessionStart fallback finds
# nothing to do. Detached + redirected so the agent's turn returns immediately.
nohup "$_py" -m closeout \
    --workdir "$PROJECT_DIR" \
    --run-id "$RID" \
    --source post-push \
    --json \
    >"${CLOSEOUT_LOG_DIR}/${RID}.stdout.json" 2>/dev/null &

exit 0
