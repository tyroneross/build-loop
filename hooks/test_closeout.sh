#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# test_closeout.sh — integration tests for hooks/closeout.sh + scripts/stop_closeout.py (f6).
#
# Exercises the bash shim end-to-end (the part the python unit tests can't cover):
# self-gating, minimal-PATH safety, interpreter fail-open, and the session-id
# stdin path. Run directly: bash hooks/test_closeout.sh

set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO/hooks/closeout.sh"
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; }

# Build a throwaway project with a recordable inline run (matching session,
# stakes-gated, not yet in runs[]).
make_project() {
    local dir; dir="$(mktemp -d)"
    mkdir -p "$dir/.build-loop"
    cat >"$dir/.build-loop/state.json" <<'JSON'
{
  "phase": "done",
  "triggers": {"riskSurfaceChange": true},
  "execution": {
    "build_loop_id": "bl-itest-001",
    "current_session_id": "sess-itest",
    "started_by_session_id": "sess-itest",
    "last_heartbeat_at": "2999-01-01T00:00:00Z",
    "run_label": "itest#001",
    "run_worktree_branch": "bl/run-itest-001",
    "run_worktree_path": ".build-loop/worktrees/run-itest-001"
  },
  "runs": []
}
JSON
    printf '%s' "$dir"
}

runs_count() { python3 -c "import json,sys;print(len(json.load(open(sys.argv[1])).get('runs',[])))" "$1/.build-loop/state.json"; }
branch_ledger_ok() { python3 -c 'import json,sys;s=json.load(open(sys.argv[1]));r=s["runs"][0];x=r["createdRefs"][0];raise SystemExit(0 if s["execution"]=={} and x["branch"]=="bl/run-itest-001" and x["status"]=="open" and r["branch_closeout"]["status"]=="pending_external_merge" and "owner_release" not in r["branch_closeout"] else 1)' "$1/.build-loop/state.json"; }

# --- 1. present + matching session → records + surfaces WARN + exit 0 -------
P="$(make_project)"
OUT="$(printf '{"session_id":"sess-itest"}' | CLAUDE_PROJECT_DIR="$P" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK" stop)"
RC=$?
if [ "$RC" -eq 0 ] && [ "$(runs_count "$P")" = "1" ] && printf '%s' "$OUT" | grep -q '"systemMessage"' && printf '%s' "$OUT" | grep -q 'WARN'; then
    ok "present+stakes → append_run ran + WARN surfaced + exit 0"
else
    bad "present+stakes (rc=$RC runs=$(runs_count "$P") out=$OUT)"
fi
if [ -f "$P/.build-loop/closeout-pending/bl-itest-001.md" ]; then
    ok "closeout-pending marker written"
else
    bad "marker not written"
fi
if branch_ledger_ok "$P"; then
    ok "Stop archived identity after persisting open branch ownership"
else
    bad "branch ownership missing or over-authorized"
fi

# --- 1b. second Stop in the same run → no-op (no double-record, no advisory) -
OUT2="$(printf '{"session_id":"sess-itest"}' | CLAUDE_PROJECT_DIR="$P" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK" stop)"
if [ "$(runs_count "$P")" = "1" ] && ! printf '%s' "$OUT2" | grep -q 'systemMessage'; then
    ok "second Stop same run → no-op"
else
    bad "second Stop not idempotent (runs=$(runs_count "$P") out=$OUT2)"
fi

# --- 1c. session-start surfaces the marker once, then archives it -----------
OUT3="$(CLAUDE_PROJECT_DIR="$P" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK" session-start </dev/null)"
if printf '%s' "$OUT3" | grep -q 'additionalContext' && printf '%s' "$OUT3" | grep -q 'bl-itest-001' \
   && [ ! -f "$P/.build-loop/closeout-pending/bl-itest-001.md" ] \
   && [ -f "$P/.build-loop/closeout-pending/surfaced/bl-itest-001.md" ]; then
    ok "session-start surfaced marker once + archived"
else
    bad "session-start surfacing (out=$OUT3)"
fi
rm -rf "$P"

# --- 2. no .build-loop/ → silent exit 0 ------------------------------------
P2="$(mktemp -d /tmp/build-loop-closeout-none.XXXXXX)"
OUT="$(printf '{"session_id":"x"}' | CLAUDE_PROJECT_DIR="$P2" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK" stop)"
RC=$?
if [ "$RC" -eq 0 ] && [ -z "$OUT" ]; then
    ok "no .build-loop/ → silent exit 0"
else
    bad "no .build-loop (rc=$RC out=$OUT)"
fi
rm -rf "$P2"

# --- 3. minimal PATH (/usr/bin:/bin — the real hook env) → still works ------
P3="$(make_project)"
OUT="$(printf '{"session_id":"sess-itest"}' | env -i PATH="/usr/bin:/bin" CLAUDE_PROJECT_DIR="$P3" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK" stop)"
RC=$?
if [ "$RC" -eq 0 ] && [ "$(runs_count "$P3")" = "1" ]; then
    ok "minimal PATH=/usr/bin:/bin → records + exit 0"
else
    bad "minimal PATH (rc=$RC runs=$(runs_count "$P3") out=$OUT)"
fi
rm -rf "$P3"

# --- 4. broken PATH (python not on PATH) → resolves via absolute fallback ---
# Invoke bash by absolute path (so the harness itself survives the broken PATH);
# inside, command -v python3 fails and the script must fall back to an absolute
# python (/usr/bin/python3 …). Contract: exit 0, never crash, valid/empty JSON.
P4="$(make_project)"
OUT="$(printf '{"session_id":"sess-itest"}' | env -i PATH="/nonexistent" CLAUDE_PROJECT_DIR="$P4" CLAUDE_PLUGIN_ROOT="$REPO" /bin/bash "$HOOK" stop 2>/dev/null)"
RC=$?
if [ "$RC" -eq 0 ] && { [ -z "$OUT" ] || printf '%s' "$OUT" | python3 -c 'import json,sys;json.load(sys.stdin)' 2>/dev/null; }; then
    ok "broken PATH → fail-open exit 0 (absolute-fallback python or silent skip)"
else
    bad "broken PATH not fail-open (rc=$RC out=$OUT)"
fi
rm -rf "$P4"

# --- 5. helper script missing (fake plugin root) → silent exit 0 -----------
P5="$(make_project)"
FAKE="$(mktemp -d)"  # no scripts/stop_closeout.py here
OUT="$(printf '{"session_id":"sess-itest"}' | CLAUDE_PROJECT_DIR="$P5" CLAUDE_PLUGIN_ROOT="$FAKE" bash "$HOOK" stop)"
RC=$?
if [ "$RC" -eq 0 ] && [ "$(runs_count "$P5")" = "0" ]; then
    ok "missing helper script → silent exit 0 (no record)"
else
    bad "missing helper not fail-open (rc=$RC runs=$(runs_count "$P5") out=$OUT)"
fi
rm -rf "$P5" "$FAKE"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
