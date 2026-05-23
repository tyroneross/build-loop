#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# SessionStart hook — fire incremental architecture scan if manifest is stale.
#
# Contract (per ~/.claude/projects/-Users-tyroneross/memory/feedback_hook_design.md
# and feedback_hooks_decision_framework.md):
#   - command-type only (NEVER prompt-type)
#   - no stdout, no stderr (exit 0 always)
#   - fire-and-forget via `nohup ... &`; hook returns in <100ms
#   - silent bail-out when there is nothing to do
#
# Behavior:
#   1. Bail if `.build-loop/architecture/` is missing — engine has not been
#      initialized in this project; nothing to refresh.
#   2. Read manifest mtime; if age > 24h OR manifest missing while arch dir
#      exists, fire backgrounded scan + acp + mark-fresh via the
#      _arch_scan_bg.py worker.
#   3. Single-flight via `fcntl.flock` (handled inside the worker).

WORKDIR="${CLAUDE_PROJECT_DIR:-$PWD}"
ARCH_DIR="$WORKDIR/.build-loop/architecture"
MANIFEST="$ARCH_DIR/manifest.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER="$SCRIPT_DIR/_arch_scan_bg.py"

# Fast bail-out: arch engine not initialized.
[ -d "$ARCH_DIR" ] || exit 0
[ -f "$WORKER" ] || exit 0

# Decide whether to fire (manifest missing or age > 24h).
SHOULD_FIRE=$(MANIFEST="$MANIFEST" python3 - <<'PYEOF' 2>/dev/null
import os, time
from pathlib import Path
manifest = Path(os.environ["MANIFEST"])
if not manifest.exists():
    print("yes")
else:
    try:
        age = time.time() - manifest.stat().st_mtime
        print("yes" if age > 24 * 3600 else "no")
    except OSError:
        print("yes")
PYEOF
)

[ "$SHOULD_FIRE" = "yes" ] || exit 0

# Fire the background worker. Worker handles flock + scan + acp + mark-fresh.
nohup python3 "$WORKER" --workdir "$WORKDIR" </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
