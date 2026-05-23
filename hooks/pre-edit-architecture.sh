#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
# PreToolUse hook (matcher: Edit|Write) — incremental scan trigger.
#
# Contract:
#   - command-type only (NEVER prompt-type)
#   - no stdout, no stderr (exit 0 always — never blocks the Edit/Write)
#   - fire-and-forget via `nohup ... &`; hook returns in <100ms
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
#      allowlist (.py .ts .tsx .js .jsx .mjs .cjs). Doc-only edits (.md,
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

# Read stdin (may be empty — bail cleanly).
STDIN_JSON=$(cat 2>/dev/null)
[ -n "$STDIN_JSON" ] || exit 0

# Classify the edited path. Emits one of:
#   "MANIFEST"  → dependency manifest changed: mark enrich-needed, DEFER the
#                 actual enrich to the scout pass (OQ3) — do NOT scan inline.
#   "<rel>"     → tracked source file: the existing mark-stale + bg-scan path.
#   ""          → bail (doc-only, untracked, unparseable).
# The manifest check runs BEFORE the source-extension allowlist so a
# package.json / requirements.txt edit is no longer excluded.
REL_PATH=$(WORKDIR="$WORKDIR" FILE_MAP="$FILE_MAP" STDIN_JSON="$STDIN_JSON" python3 - <<'PYEOF' 2>/dev/null
import json, os, sys
from pathlib import Path
ALLOWED_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
MANIFESTS = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "requirements.txt",
    "pyproject.toml", "uv.lock", "Cargo.toml", "Cargo.lock", "go.mod", "Gemfile",
}
try:
    payload = json.loads(os.environ["STDIN_JSON"])
except (KeyError, json.JSONDecodeError):
    sys.exit(0)
ti = payload.get("tool_input") or {}
fp = ti.get("file_path") or ti.get("path") or ti.get("filename")
if not fp:
    sys.exit(0)
# Dependency-manifest unblock (OQ3) — runs before the extension allowlist.
if Path(fp).name in MANIFESTS:
    print("MANIFEST")
    sys.exit(0)
# Extension allowlist gate — bail on .md/.txt/.json/etc. before any I/O.
ext = Path(fp).suffix.lower()
if ext not in ALLOWED_EXTS:
    sys.exit(0)
workdir = Path(os.environ["WORKDIR"]).resolve()
abs_path = (workdir / fp).resolve() if not os.path.isabs(fp) else Path(fp).resolve()
try:
    rel = abs_path.relative_to(workdir).as_posix()
except ValueError:
    sys.exit(0)
try:
    file_map = json.loads(Path(os.environ["FILE_MAP"]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    sys.exit(0)
files = file_map.get("files") or {}
if rel in files:
    print(rel)
PYEOF
)

[ -n "$REL_PATH" ] || exit 0

# Dependency-manifest edit: mark enrich-needed and EXIT. The actual enriched
# scan is deferred to the scout pass (OQ3) — never run inline here.
if [ "$REL_PATH" = "MANIFEST" ]; then
  : > "$ARCH_DIR/.enrich-needed" 2>/dev/null || true
  exit 0
fi

# Mark stale immediately (cheap, atomic).
python3 "$FRESHNESS_SCRIPT" --mark-stale --file "$REL_PATH" --workdir "$WORKDIR" >/dev/null 2>&1 || true

# Fire the background worker. Single-flight handled inside the worker.
nohup python3 "$WORKER" --workdir "$WORKDIR" </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
