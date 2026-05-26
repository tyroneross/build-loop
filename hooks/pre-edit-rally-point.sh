#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# PreToolUse(Edit|Write) hook — Rally Point cheap revision-stat hint.
#
# Contract:
#   - command-type only (NEVER prompt-type)
#   - exit 0 ALWAYS — NEVER blocks the Edit/Write
#   - cheap: a single `revision` stat+read, NO tail read, NO lock
#   - prints one short hint ONLY when the channel revision is ahead of
#     this reader's last-seen revision; silent otherwise
#   - graceful absence: no channel -> silent exit 0
#
# This is intentionally lighter than checkpoint_read: it must not slow an
# edit. It only nudges "something changed since you last looked — run a
# checkpoint". The orchestrator/SessionStart hook does the full read.

WORKDIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="$SCRIPT_DIR/../scripts/rally_point"

[ -d "$PKG" ] || exit 0

python3 "$PKG/hooks.py" pre-edit --workdir "$WORKDIR" 2>/dev/null || exit 0

exit 0
