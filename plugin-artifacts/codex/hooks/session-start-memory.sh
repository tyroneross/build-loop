#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Bootstrap build-loop-memory on session start (idempotent, fail-open).
# Seeds the central memory store (constitution.md, MEMORY.md, indexes/,
# projects/) from the packaged PUBLIC seed only when the store is entirely
# absent. install_memory.py is idempotent (skip-if-exists) and copies no
# personal content. Never blocks, never overwrites, never errors the session.
# Repair of a partial store is the user-facing `/build-loop:setup-memory` job.
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-$PWD}}"
installer="${PLUGIN_ROOT}/scripts/install_memory.py"
[ -f "$installer" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# Seed only when the store ROOT dir is missing. Parse --check JSON properly so a
# nested per-file "exists" cannot be mistaken for the top-level store flag.
# (python3 -c, not a heredoc: a heredoc inside $() with an apostrophe trips
# macOS bash 3.2 tokenization.)
need="$(python3 -c '
import json, subprocess, sys
try:
    out = subprocess.run([sys.executable, sys.argv[1], "--check", "--json"], capture_output=True, text=True, timeout=5).stdout
    print("1" if not json.loads(out).get("exists") else "0")
except Exception:
    print("0")
' "$installer")"
[ "$need" = "1" ] && python3 "$installer" >/dev/null 2>&1
exit 0
