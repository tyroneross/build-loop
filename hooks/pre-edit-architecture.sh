#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# PreToolUse hook (matcher: Edit|Write) — incremental scan trigger.
#
# Contract:
#   - command-type only (NEVER prompt-type)
#   - no stdout, no stderr (exit 0 always — never blocks the Edit/Write)
#   - fire-and-forget: a detached runner (own process group, deadline, SIGTERM only)
#     does the classify + mark-stale + scan; hook returns in <20ms
#   - tool input arrives on stdin as JSON: {"tool_input": {"file_path": "..."}}
#
# Behavior:
#   1. Bail silently if `.build-loop/architecture/file_map.json` does not exist
#      (engine not initialized).
#   2. Parse `file_path` from stdin JSON; bail on missing/invalid input.
#   3. Dependency-manifest edit (package.json, requirements.txt, uv.lock, …):
#      mark `.build-loop/architecture/.enrich-needed` and EXIT — DEFER the
#      actual enriched scan to the scout pass (OQ3); never run it inline.
#   4. Else bail silently if file extension is not in the source-code
#      allowlist (.py .ts .tsx .js .jsx .mjs .cjs .rs). Doc-only edits (.md,
#      .txt, plain .json, images) never mark architecture stale or fire a scan.
#   5. Resolve to repo-relative path; bail if not in file_map.
#   5. Mark stale (always — even if a scan is in flight, orchestrator must see
#      stale=true before reading ACP).
#   6. Fire `_arch_scan_bg.py` (single-flight flock inside the worker).

WORKDIR="${CLAUDE_PROJECT_DIR:-$PWD}"
ARCH_DIR="$WORKDIR/.build-loop/architecture"
FILE_MAP="$ARCH_DIR/file_map.json"
FRESHNESS_SCRIPT="$WORKDIR/scripts/architecture_freshness.py"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER="$SCRIPT_DIR/_arch_scan_bg.py"

# Fast bail-out: arch engine not initialized.
[ -f "$FILE_MAP" ] || exit 0
[ -f "$FRESHNESS_SCRIPT" ] || exit 0
[ -f "$WORKER" ] || exit 0

# Everything past the three stat() checks runs in a detached background job
# (hooks/_arch_pre_edit_bg.py): classify the path, mark stale / .enrich-needed,
# start _arch_scan_bg.py. The hook itself does no python, no JSON parsing and
# no waiting, so it returns in single-digit ms regardless of host load.
# Nothing reads the stale mark within the hook window (readers run at later
# phase boundaries via architecture_freshness.py --check), and the job still
# marks stale BEFORE it starts the scan.
RUNNER="$SCRIPT_DIR/_arch_pre_edit_bg.py"
[ -f "$RUNNER" ] || exit 0

# Spill stdin to a file: the Write tool's payload can be megabytes, too big for
# argv/env. The runner deletes the file.
PAYLOAD=$(mktemp "${TMPDIR:-/tmp}/bl-pre-edit.XXXXXX" 2>/dev/null) || exit 0
cat > "$PAYLOAD" 2>/dev/null
[ -s "$PAYLOAD" ] || { rm -f "$PAYLOAD"; exit 0; }

# `set -m` gives the background job its own process group (so the runner can
# SIGTERM the whole tree at its deadline); stdio is fully detached.
set -m
( exec python3 "$RUNNER" --workdir "$WORKDIR" --payload "$PAYLOAD" \
    </dev/null >/dev/null 2>&1 ) &
disown 2>/dev/null || true

exit 0
