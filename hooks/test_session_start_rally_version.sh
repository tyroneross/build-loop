#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# Fixture-driven tests for the on-PATH-vs-pin rally version staleness guard
# (lane E-a) added to session-start-rally-point.sh Step 5. This is the
# control that would have caught the rally version-mismatch incident: the
# guard must be NON-BLOCKING and fail-open in every case below — it must
# NEVER affect the hook's exit code, whether rally is absent, matches the
# pin, or is stale.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SS_HOOK="${SCRIPT_DIR}/session-start-rally-point.sh"
PKG="${REPO_ROOT}/scripts/rally_point"

PASS=0
FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1 — $2"; FAIL=$((FAIL + 1)); }

# Resolve the REAL pinned version from binary_fetch.py so this test tracks
# the pin instead of hardcoding a copy that can silently drift.
PINNED_VER="$(python3 - <<PYEOF
import sys
sys.path.insert(0, "${REPO_ROOT}/scripts")
from rally_point import binary_fetch as bf
print(bf.PINNED_VERSION)
PYEOF
)"
if [ -z "$PINNED_VER" ]; then
  echo "FAIL: setup — could not resolve PINNED_VERSION from binary_fetch.py"
  exit 1
fi

REPO=$(mktemp -d)
APPS=$(mktemp -d)
( cd "$REPO" && git init -q && git config user.email t@e.com && git config user.name t )
export BUILD_LOOP_APPS_ROOT="$APPS"
export BUILD_LOOP_BRIDGE_INTERNAL_ONLY=1
export BUILD_LOOP_RALLY_POINT_SKIP_WATCH=1
export CLAUDE_PROJECT_DIR="$REPO"

STUBBIN=$(mktemp -d)
# Isolated PATH: the stub bin dir first, then just enough of the real system
# to run python3/git/bash — deliberately excludes any real `rally` that may
# be installed on this machine's normal PATH (e.g. ~/.local/bin/rally).
PYTHON3_DIR="$(dirname "$(command -v python3)")"
GIT_DIR="$(dirname "$(command -v git)")"
ISOLATED_PATH="$STUBBIN:$PYTHON3_DIR:$GIT_DIR:/usr/bin:/bin"

write_stub_rally() {
  # $1 = version string the stub's "version" subcommand should print
  local ver="$1"
  cat > "$STUBBIN/rally" <<STUBEOF
#!/usr/bin/env bash
if [ "\$1" = "version" ]; then
  echo "rally ${ver}"
fi
exit 0
STUBEOF
  chmod +x "$STUBBIN/rally"
}

run_hook_stderr() {
  PATH="$ISOLATED_PATH" bash "$SS_HOOK" 2>&1 1>/dev/null </dev/null
}

run_hook_rc() {
  PATH="$ISOLATED_PATH" bash "$SS_HOOK" >/dev/null 2>&1 </dev/null
  echo $?
}

# ---- Case 1: on-PATH version matches the pin -> no WARN, exit 0 --------
write_stub_rally "${PINNED_VER}+deadbeef"
ERR=$(run_hook_stderr)
RC=$(run_hook_rc)
if [ "$RC" -eq 0 ] && ! printf '%s' "$ERR" | grep -q "WARN"; then
  pass "matching on-PATH version: no WARN, exit 0"
else
  fail "matching version case" "rc=$RC stderr='$ERR'"
fi

# ---- Case 2: on-PATH version mismatches the pin -> WARN line, exit 0 ---
write_stub_rally "9.9.9+deadbeef"
ERR=$(run_hook_stderr)
RC=$(run_hook_rc)
if [ "$RC" -eq 0 ] \
    && printf '%s' "$ERR" | grep -q "\[rally\] WARN:" \
    && printf '%s' "$ERR" | grep -q "9.9.9" \
    && printf '%s' "$ERR" | grep -q "${PINNED_VER}"; then
  pass "mismatched on-PATH version: WARN line present, exit 0"
else
  fail "mismatched version case" "rc=$RC stderr='$ERR'"
fi

# ---- Case 3: rally absent from PATH -> no crash, no WARN, exit 0 -------
rm -f "$STUBBIN/rally"
ERR=$(run_hook_stderr)
RC=$(run_hook_rc)
if [ "$RC" -eq 0 ] && ! printf '%s' "$ERR" | grep -q "WARN"; then
  pass "rally absent from PATH: no crash, no WARN, exit 0"
else
  fail "rally absent case" "rc=$RC stderr='$ERR'"
fi

# ---- Case 4: rally on PATH but `rally version` itself errors (exit 1,
# empty output) -> guard silently no-ops, exit 0 preserved. ---------------
cat > "$STUBBIN/rally" <<'STUBEOF'
#!/usr/bin/env bash
exit 1
STUBEOF
chmod +x "$STUBBIN/rally"
ERR=$(run_hook_stderr)
RC=$(run_hook_rc)
if [ "$RC" -eq 0 ] && ! printf '%s' "$ERR" | grep -q "WARN"; then
  pass "rally version errors: no crash, no WARN, exit 0"
else
  fail "rally version error case" "rc=$RC stderr='$ERR'"
fi

echo "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
