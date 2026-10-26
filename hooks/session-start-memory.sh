#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Bootstrap build-loop-memory on session start (idempotent, fail-open).
# Seeds the central memory store (constitution.md, MEMORY.md, indexes/,
# projects/) from the packaged PUBLIC seed only when the store is entirely
# absent or incomplete. install_memory.py is idempotent (skip-if-exists) and copies no
# personal content. Never blocks, never overwrites, never errors the session.
# Missing scaffold files are retried after an interrupted bootstrap; existing files are preserved.
# Bound the check AND any first-run install, including interpreter startup.
# The host allows six seconds; keep headroom for shell teardown and scheduling.
. "$(dirname "$0")/_bounded_hook.sh"
bl_bound_hook "${BUILD_LOOP_MEMORY_DEADLINE_MS:-4500}" 4500 text "$BASH" "$0" "$@"

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-$PWD}}"
installer="${PLUGIN_ROOT}/scripts/install_memory.py"
[ -f "$installer" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

# Check scaffold completeness too: the outer deadline may interrupt first-run
# seeding after the root directory has been created. Never overwrite existing files.
# (python3 -c, not a heredoc: a heredoc inside $() with an apostrophe trips
# macOS bash 3.2 tokenization.)
need="$(python3 -c '
import json, subprocess, sys
try:
    out = subprocess.run([sys.executable, sys.argv[1], "--check", "--json"], capture_output=True, text=True, timeout=5).stdout
    status = json.loads(out)
    files = status.get("files", {})
    incomplete = (isinstance(files, dict) and any(not item.get("exists") for item in files.values()))
    incomplete = incomplete or status.get("indexes_dir_exists") is False or status.get("projects_dir_exists") is False
    print("1" if not status.get("exists") or incomplete else "0")
except Exception:
    print("0")
' "$installer")"
[ "$need" = "1" ] && python3 "$installer" >/dev/null 2>&1
exit 0
