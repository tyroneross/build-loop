#!/usr/bin/env bash
# SEC-008 regression test for hooks/pre-commit and hooks/post-commit.
#
# Both hooks compute the repo toplevel via `git rev-parse --show-toplevel`.
# If that resolution fails, the old code produced an empty path, the
# `-f` file test silently failed, and the guard / capture was skipped —
# fail-open. The fix: `exit 2` when toplevel cannot be resolved.
#
# This test runs each hook from a directory that is NOT inside a git
# repo, so `git rev-parse --show-toplevel` yields nothing, and asserts
# the hook exits 2 instead of 0.
#
# Any failure exits non-zero.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRE_HOOK="${SCRIPT_DIR}/pre-commit"
POST_HOOK="${SCRIPT_DIR}/post-commit"

PASS=0
FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1 — $2"; FAIL=$((FAIL + 1)); }

# A directory guaranteed not to be inside a git repo.
NOREPO=$(mktemp -d)
trap 'rm -rf "$NOREPO"' EXIT

# ---- Case 1: pre-commit exits 2 when toplevel cannot be resolved ------
( cd "$NOREPO" && sh "$PRE_HOOK" </dev/null >/dev/null 2>&1 )
RC=$?
if [ "$RC" -eq 2 ]; then
  pass "pre-commit fails closed (exit 2) with no resolvable toplevel"
else
  fail "pre-commit no-toplevel" "expected exit 2, got $RC"
fi

# ---- Case 2: post-commit exits 2 when toplevel cannot be resolved ----
( cd "$NOREPO" && sh "$POST_HOOK" </dev/null >/dev/null 2>&1 )
RC=$?
if [ "$RC" -eq 2 ]; then
  pass "post-commit fails closed (exit 2) with no resolvable toplevel"
else
  fail "post-commit no-toplevel" "expected exit 2, got $RC"
fi

# ---- Case 3: pre-commit exits 0 inside a repo with no guard script ---
# (the .private-slug-check.py is not installed, so the guard segment
#  finds no file and the hook proceeds to its trailing `exit 0`).
REPO=$(mktemp -d)
( cd "$REPO" && git init -q )
( cd "$REPO" && sh "$PRE_HOOK" </dev/null >/dev/null 2>&1 )
RC=$?
rm -rf "$REPO"
if [ "$RC" -eq 0 ]; then
  pass "pre-commit exits 0 inside a repo when guard script absent"
else
  fail "pre-commit in-repo-no-guard" "expected exit 0, got $RC"
fi

echo "----"
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
