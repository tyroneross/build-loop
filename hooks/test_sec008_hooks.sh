#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
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

# Exercise the production legacy source bodies against helpers installed in
# Git's effective directory, including linked worktrees whose .git is a file.
python3 - "$PRE_HOOK" "$POST_HOOK" <<'PY'
import subprocess
import sys
import tempfile
import time
from pathlib import Path

pre, post = sys.argv[1:]
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for layout in ("normal", "linked", "custom", "linked-custom"):
        repo = root / layout
        repo.mkdir()
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.DEVNULL, text=True).strip()
        git("init", "-q")
        cwd = repo
        if layout.startswith("linked"):
            git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "init")
            cwd = root / (layout + "-checkout")
            git("worktree", "add", "-b", "linked", str(cwd))
        if layout.endswith("custom"):
            git("config", "core.hooksPath", str(root / (layout + " hooks")))
        hooks = Path(subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"], cwd=cwd, text=True).strip())
        hooks.mkdir(parents=True, exist_ok=True)
        receipt = root / (layout + ".receipt")
        def stub(name, label, code):
            (hooks / name).write_text(
                "from pathlib import Path\n"
                f"with Path({str(receipt)!r}).open('a') as f: f.write({label + chr(10)!r})\n"
                f"raise SystemExit({code})\n")
        stub(".private-slug-check.py", "private", 1)
        stub(".runtime-memory-tracking-check.py", "runtime", 1)
        assert subprocess.run(["sh", pre], cwd=cwd, capture_output=True).returncode == 1, layout
        assert receipt.read_text().splitlines() == ["private"], layout
        stub(".private-slug-check.py", "allowed", 0)
        assert subprocess.run(["sh", pre], cwd=cwd, capture_output=True).returncode == 1, layout
        assert receipt.read_text().splitlines() == ["private", "allowed", "runtime"], layout
        stub(".rally-point-capture.py", "capture", 0)
        assert subprocess.run(["sh", post], cwd=cwd, capture_output=True).returncode == 0, layout
        deadline = time.monotonic() + 10
        while "capture\n" not in receipt.read_text() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert receipt.read_text().splitlines()[-1] == "capture", layout
        print(f"verified legacy guard/capture: {layout}")
PY
if [ "$?" -eq 0 ]; then
  pass "legacy hooks use effective helper directory across checkout layouts"
else
  fail "legacy effective directory" "guard or capture failed"
fi

echo "----"
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
