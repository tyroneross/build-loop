#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# Fixture-driven tests for the Rally Point hooks:
#   - session-start-rally-point.sh : one-line restore only on delta,
#                                    silent + exit 0 otherwise
#   - pre-edit-rally-point.sh      : cheap revision-stat hint, never blocks
# Any failure exits non-zero.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SS_HOOK="${SCRIPT_DIR}/session-start-rally-point.sh"
PE_HOOK="${SCRIPT_DIR}/pre-edit-rally-point.sh"
PKG="${REPO_ROOT}/scripts/rally_point"

PASS=0
FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1 — $2"; FAIL=$((FAIL + 1)); }

# Build a temp git repo (so channel_paths.app_slug resolves) + isolated
# apps root.
REPO=$(mktemp -d)
APPS=$(mktemp -d)
( cd "$REPO" && git init -q && git config user.email t@e.com \
    && git config user.name t )
export BUILD_LOOP_APPS_ROOT="$APPS"
export BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1
export BUILD_LOOP_RALLY_POINT_SKIP_WATCH=1

# ---- Case 1: no channel yet -> SessionStart silent, exit 0 -------------
OUT=$(CLAUDE_PROJECT_DIR="$REPO" bash "$SS_HOOK" </dev/null 2>/dev/null)
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  pass "SessionStart silent + exit 0 when no channel"
else
  fail "SessionStart no-channel" "rc=$RC out='$OUT'"
fi

# ---- Case 2: pre-edit never blocks, exit 0, silent when no channel -----
OUT=$(CLAUDE_PROJECT_DIR="$REPO" bash "$PE_HOOK" </dev/null 2>/dev/null)
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  pass "pre-edit silent + exit 0 when no channel"
else
  fail "pre-edit no-channel" "rc=$RC out='$OUT'"
fi

# ---- Seed a delta: write a change + bump revision via the modules -----
REPO="$REPO" APPS="$APPS" PKG="$PKG" python3 - <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ["PKG"])
import channel_paths as ap, changes as ch, revision as rev, presence as pr
slug = ap.app_slug(cwd=os.environ["REPO"])
chan = ap.ensure_channel_dir(slug)
pr.write_presence(chan, session_id="peerX", tool="codex", model="m",
                  run_id="rp", app_slug=slug, phase="execute",
                  files_in_flight=["a.py"])
ch.append_change(chan, ch.make_record(
    kind="dep-change", tool="codex", model="m", run_id="rp",
    app_slug=slug, payload={"manifests": ["package.json"]}, revision=1))
rev.bump_revision(chan)
PYEOF

# ---- Case 3: pre-edit prints a hint when revision advanced -------------
OUT=$(CLAUDE_PROJECT_DIR="$REPO" bash "$PE_HOOK" </dev/null 2>/dev/null)
RC=$?
if [ "$RC" -eq 0 ] && echo "$OUT" | grep -q "channel advanced"; then
  pass "pre-edit prints revision-advanced hint"
else
  fail "pre-edit hint" "rc=$RC out='$OUT'"
fi

# ---- Case 4: SessionStart prints exactly one restore line on delta -----
OUT=$(CLAUDE_PROJECT_DIR="$REPO" bash "$SS_HOOK" </dev/null 2>/dev/null)
RC=$?
LINES=$(printf '%s' "$OUT" | grep -c . || true)
if [ "$RC" -eq 0 ] && [ "$LINES" -eq 1 ] \
    && echo "$OUT" | grep -q "Rally Point:" \
    && echo "$OUT" | grep -q "reinstall"; then
  pass "SessionStart one-line restore on delta"
else
  fail "SessionStart restore" "rc=$RC lines=$LINES out='$OUT'"
fi

# ---- Case 5: SessionStart silent on the SECOND call (cursor advanced) --
OUT=$(CLAUDE_PROJECT_DIR="$REPO" bash "$SS_HOOK" </dev/null 2>/dev/null)
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
  pass "SessionStart silent after cursor advanced (delta-only)"
else
  fail "SessionStart second-call" "rc=$RC out='$OUT'"
fi

echo "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
