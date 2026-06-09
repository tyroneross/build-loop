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

# PostToolUse passes the tool command; Claude Code exposes it as $TOOL_INPUT
# (matching the sibling Bash matcher in hooks.json). Fall back to reading stdin
# JSON if the env var is absent (host portability).
CMD="${TOOL_INPUT:-}"
if [ -z "$CMD" ]; then
    # Best-effort: some hosts deliver the payload on stdin as JSON.
    STDIN_JSON="$(cat 2>/dev/null || true)"
    CMD="$STDIN_JSON"
fi

# Only act on a real `git push`. Anchor on the verb pair to avoid matching
# `git push --help`-style noise loosely; a trailing `--dry-run` push still
# closes out harmlessly (closeout is idempotent + fail-open).
printf '%s' "$CMD" | grep -qE '\bgit[[:space:]]+push\b' || exit 0

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-${PWD}}"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${PROJECT_DIR}}"

# A closeout only makes sense inside a build-loop project.
[ -d "${PROJECT_DIR}/.build-loop" ] || exit 0

# Resolve python3 without depending on a populated PATH.
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
