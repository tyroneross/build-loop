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

# =========================================================================
# Codex-parity autojoin tests (2026-06-07)
# =========================================================================

# Per-repo isolation: ALL new cases write into their own APPS subdir so
# they cannot read state from the cases above. Use NONREPO + REPO_X.
NONREPO=$(mktemp -d)
REPO_X=$(mktemp -d)
( cd "$REPO_X" && git init -q && git config user.email t@e.com \
    && git config user.name t )
APPS2=$(mktemp -d)

# macOS realpath: /var/folders/... -> /private/var/folders/... — the
# operative-repo resolver runs `git rev-parse --show-toplevel` which
# returns the resolved form, so compare against that.
REPO_X_REAL=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$REPO_X")

# Force a short throttle window so the throttle test does not have to
# sleep for the full 60s default.
export BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS=5

# ---- Case 6: SessionStart no-misregister when CLAUDE_PROJECT_DIR unset
# AND $PWD is not a repo (home-like scenario). The hook must exit 0 and
# write NOTHING under APPS2.
BUILD_LOOP_APPS_ROOT="$APPS2" \
  bash -c "cd '$NONREPO' && unset CLAUDE_PROJECT_DIR && BUILD_LOOP_APPS_ROOT='$APPS2' BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1 BUILD_LOOP_RALLY_POINT_SKIP_WATCH=1 bash '$SS_HOOK'" </dev/null >/tmp/_ss_nonrepo.out 2>/dev/null
RC=$?
WROTE=$(find "$APPS2" -type f 2>/dev/null | wc -l | tr -d ' ')
if [ "$RC" -eq 0 ] && [ "$WROTE" -eq 0 ]; then
  pass "SessionStart no-misregister when launch cwd is not a repo"
else
  fail "SessionStart no-misregister" "rc=$RC wrote=$WROTE out=$(cat /tmp/_ss_nonrepo.out 2>/dev/null)"
fi

# Reset APPS2 between cases (each new case starts clean).
rm -rf "$APPS2" && mkdir -p "$APPS2"

# ---- Case 7: pre-edit Edit/Write joins room of the OPERATIVE repo,
# not the launch dir. We invoke from NONREPO but the file_path lives
# inside REPO_X. Presence must land under REPO_X's slug.
EVENT_JSON=$(printf '{"tool_input":{"file_path":"%s/a.py"}}' "$REPO_X")
BUILD_LOOP_APPS_ROOT="$APPS2" BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1 \
  bash -c "cd '$NONREPO' && unset CLAUDE_PROJECT_DIR && \
           printf '%s' '$EVENT_JSON' | \
           BUILD_LOOP_APPS_ROOT='$APPS2' BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1 \
           BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS=5 \
           bash '$PE_HOOK'" >/tmp/_pe_edit.out 2>/dev/null
RC=$?
REPO_X_SLUG=$(basename "$REPO_X" | tr '[:upper:]' '[:lower:]')
PRESENCE_COUNT=$(find "$APPS2" -path "*/$REPO_X_SLUG/sessions/*.json" -type f 2>/dev/null | wc -l | tr -d ' ')
# Allow normalized slug variants (the slug normalizer maps unsafe chars).
ANY_PRESENCE=$(find "$APPS2" -path "*/sessions/*.json" -type f 2>/dev/null | wc -l | tr -d ' ')
if [ "$RC" -eq 0 ] && [ "$ANY_PRESENCE" -ge 1 ]; then
  # Verify the presence record's cwd points into REPO_X, not NONREPO.
  CWD_HIT=$(grep -l "\"cwd\":\"$REPO_X_REAL\"" "$APPS2"/*/sessions/*.json 2>/dev/null | head -1)
  if [ -n "$CWD_HIT" ]; then
    pass "pre-edit Edit joins operative repo room (not launch dir)"
  else
    fail "pre-edit Edit operative-repo cwd" "rc=$RC any_presence=$ANY_PRESENCE files=$(ls "$APPS2"/*/sessions/ 2>/dev/null)"
  fi
else
  fail "pre-edit Edit operative-repo" "rc=$RC any_presence=$ANY_PRESENCE out=$(cat /tmp/_pe_edit.out 2>/dev/null)"
fi

# Reset between cases.
rm -rf "$APPS2" && mkdir -p "$APPS2"

# ---- Case 8: pre-edit Bash with leading `cd <repoX>` resolves repoX
# from the command payload, even when the launch cwd is NONREPO.
EVENT_JSON=$(printf '{"tool_input":{"command":"cd %s && echo hi"}}' "$REPO_X")
bash -c "cd '$NONREPO' && unset CLAUDE_PROJECT_DIR && \
         printf '%s' '$EVENT_JSON' | \
         BUILD_LOOP_APPS_ROOT='$APPS2' BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1 \
         BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS=5 \
         bash '$PE_HOOK'" >/tmp/_pe_bash.out 2>/dev/null
RC=$?
ANY_PRESENCE=$(find "$APPS2" -path "*/sessions/*.json" -type f 2>/dev/null | wc -l | tr -d ' ')
if [ "$RC" -eq 0 ] && [ "$ANY_PRESENCE" -ge 1 ]; then
  CWD_HIT=$(grep -l "\"cwd\":\"$REPO_X_REAL\"" "$APPS2"/*/sessions/*.json 2>/dev/null | head -1)
  if [ -n "$CWD_HIT" ]; then
    pass "pre-edit Bash 'cd <repoX>' resolves operative repo"
  else
    fail "pre-edit Bash cwd" "rc=$RC files=$(ls "$APPS2"/*/sessions/ 2>/dev/null)"
  fi
else
  fail "pre-edit Bash operative-repo" "rc=$RC any_presence=$ANY_PRESENCE out=$(cat /tmp/_pe_bash.out 2>/dev/null)"
fi

# DO NOT reset between Case 8 and Case 9 — Case 9 verifies the throttle
# blocks a SECOND write within the throttle window.

# ---- Case 9: Throttle: a second rapid tool-use in the same room within
# the throttle window must NOT write a fresh presence file AND must NOT
# create a new presence file (each tool-use spawns a fresh python3 PID;
# a PID-keyed session id would defeat the throttle).
COUNT_BEFORE=$(find "$APPS2" -path "*/sessions/*.json" -type f 2>/dev/null | wc -l | tr -d ' ')
PRESENCE_FILE=$(find "$APPS2" -path "*/sessions/*.json" -type f 2>/dev/null | head -1)
if [ -z "$PRESENCE_FILE" ]; then
  fail "throttle setup" "no presence file from case 8"
else
  MTIME_BEFORE=$(stat -f '%m' "$PRESENCE_FILE" 2>/dev/null || stat -c '%Y' "$PRESENCE_FILE" 2>/dev/null)
  # Tiny sleep so any rewrite would change mtime (mtime resolution is 1s).
  sleep 1
  bash -c "cd '$NONREPO' && unset CLAUDE_PROJECT_DIR && \
           printf '%s' '$EVENT_JSON' | \
           BUILD_LOOP_APPS_ROOT='$APPS2' BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1 \
           BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS=30 \
           bash '$PE_HOOK'" </dev/null >/dev/null 2>&1
  COUNT_AFTER=$(find "$APPS2" -path "*/sessions/*.json" -type f 2>/dev/null | wc -l | tr -d ' ')
  MTIME_AFTER=$(stat -f '%m' "$PRESENCE_FILE" 2>/dev/null || stat -c '%Y' "$PRESENCE_FILE" 2>/dev/null)
  if [ "$MTIME_BEFORE" = "$MTIME_AFTER" ] && [ "$COUNT_BEFORE" = "$COUNT_AFTER" ]; then
    pass "pre-edit throttle: no double-write + no new file within window"
  else
    fail "pre-edit throttle" "mtime before=$MTIME_BEFORE after=$MTIME_AFTER; count before=$COUNT_BEFORE after=$COUNT_AFTER"
  fi
fi

# Reset between cases.
rm -rf "$APPS2" && mkdir -p "$APPS2"

# ---- Case 10: Fail-open under env -i / minimal PATH. Each changed hook
# must exit 0 under the Claude Code minimal-PATH simulation, regardless of
# rally state.
for HOOK in "$SS_HOOK" "$PE_HOOK"; do
  RC=$(env -i PATH=/usr/bin:/bin HOME=/tmp \
       BUILD_LOOP_APPS_ROOT="$APPS2" \
       BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1 \
       BUILD_LOOP_RALLY_POINT_SKIP_WATCH=1 \
       bash "$HOOK" </dev/null >/dev/null 2>&1; echo $?)
  if [ "$RC" -eq 0 ]; then
    pass "env -i PATH=/usr/bin:/bin exit 0 ($(basename "$HOOK"))"
  else
    fail "minimal-PATH exit 0 ($(basename "$HOOK"))" "rc=$RC"
  fi
done

# ---- Case 11: Fail-open when the rally_point package is missing. We
# point ``PKG`` (via shell-side directory absence check) at a stub root
# without scripts/rally_point — the hooks must still exit 0 cleanly.
STUB=$(mktemp -d)
mkdir -p "$STUB/hooks" "$STUB/scripts"
cp "$SS_HOOK" "$STUB/hooks/"
cp "$PE_HOOK" "$STUB/hooks/"
cp "$SCRIPT_DIR/_session_start_lib.sh" "$STUB/hooks/"
for HOOK_NAME in session-start-rally-point.sh pre-edit-rally-point.sh; do
  RC=$(BUILD_LOOP_APPS_ROOT="$APPS2" BUILD_LOOP_RALLY_POINT_SKIP_WATCH=1 \
       bash "$STUB/hooks/$HOOK_NAME" </dev/null >/dev/null 2>&1; echo $?)
  if [ "$RC" -eq 0 ]; then
    pass "fail-open exit 0 when rally_point package missing ($HOOK_NAME)"
  else
    fail "fail-open missing-package ($HOOK_NAME)" "rc=$RC"
  fi
done

echo "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
