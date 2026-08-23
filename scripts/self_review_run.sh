#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
#
# self_review_run.sh — cron/launchd wrapper for build-loop periodic self-review.
#
# Usage: self_review_run.sh <mode>   where mode = light | deep
#
# Environment:
#   BUILDLOOP_SELF_REVIEW_REPO  absolute path to the repo root (baked in by the
#                               installer at install time; fallback: dirname/../)
#
# Always exits 0 — fail-soft so launchd never spams throttle/restart logs.

set -uo pipefail

# ---------------------------------------------------------------------------
# 1. Resolve mode
# ---------------------------------------------------------------------------
MODE="${1:-light}"
if [[ "$MODE" != "light" && "$MODE" != "deep" ]]; then
    echo "[self-review] unknown mode '$MODE'; defaulting to light" >&2
    MODE="light"
fi

# ---------------------------------------------------------------------------
# 2. Resolve repo root
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${BUILDLOOP_SELF_REVIEW_REPO:-}" ]]; then
    REPO="$BUILDLOOP_SELF_REVIEW_REPO"
else
    # fallback: scripts/ lives one level below repo root
    REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

cd "$REPO" || { echo "[self-review] cannot cd to $REPO" >&2; exit 0; }

# ---------------------------------------------------------------------------
# 3. Ensure output directories exist
# ---------------------------------------------------------------------------
REVIEW_DIR=".build-loop/self-review"
mkdir -p "$REVIEW_DIR"

RUN_LOG="$REVIEW_DIR/run.log"
LAST_JSON="$REVIEW_DIR/last-${MODE}.json"
TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

log() {
    echo "[$TIMESTAMP] [self-review/$MODE] $*" | tee -a "$RUN_LOG" >&2
}

log "starting"

# ---------------------------------------------------------------------------
# 4. Run the gatherer
# ---------------------------------------------------------------------------
GATHER_JSON="$( python3 scripts/self_review/__main__.py --mode "$MODE" --workdir "$REPO" --json 2>>"$RUN_LOG" || true )"

if [[ -z "$GATHER_JSON" ]]; then
    log "gatherer produced no output; skipping"
    exit 0
fi

# Save snapshot
printf '%s\n' "$GATHER_JSON" > "$LAST_JSON"

QUEUED_COUNT="$( python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d.get('queued',[]))) " <<< "$GATHER_JSON" 2>/dev/null || echo "?" )"
ERRORS_COUNT="$( python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(len(d.get('errors',[]))) " <<< "$GATHER_JSON" 2>/dev/null || echo "?" )"

log "gatherer done; queued=$QUEUED_COUNT errors=$ERRORS_COUNT"

# ---------------------------------------------------------------------------
# 4a. Drain producer churn before any model reads the proposal queue
# ---------------------------------------------------------------------------
DRAIN_STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
DRAIN_JSON="$( python3 scripts/drain_self_review_proposals.py \
    --workdir "$REPO" --archive --stamp "$DRAIN_STAMP" --json \
    2>>"$RUN_LOG" || true )"
if [[ -n "$DRAIN_JSON" ]]; then
    ACTIONABLE_COUNT="$( python3 -c "import json,sys; d=json.loads(sys.stdin.read().split('\\narchived',1)[0]); print(d.get('actionable','?'))" <<< "$DRAIN_JSON" 2>/dev/null || echo "?" )"
    log "proposal drain complete; actionable=$ACTIONABLE_COUNT"
else
    log "proposal drain unavailable; continuing with queue unchanged"
fi

# ---------------------------------------------------------------------------
# 5. Light mode: done (digest + queue produced; no auto-apply)
# ---------------------------------------------------------------------------
if [[ "$MODE" == "light" ]]; then
    log "light mode complete"
    exit 0
fi

# ---------------------------------------------------------------------------
# 6. Deep mode: read autonomy config
# ---------------------------------------------------------------------------
AUTONOMY="apply_push"  # default

CONFIG_FILE=".build-loop/config.json"
if [[ -f "$CONFIG_FILE" ]]; then
    AUTONOMY_FROM_CONFIG="$( python3 -c "
import json, sys
try:
    d = json.load(open('$CONFIG_FILE'))
    print(d.get('selfReview', {}).get('autonomy', 'apply_push'))
except Exception:
    print('apply_push')
" 2>/dev/null || echo "apply_push" )"
    AUTONOMY="${AUTONOMY_FROM_CONFIG:-apply_push}"
fi

log "autonomy=$AUTONOMY"

if [[ "$AUTONOMY" == "propose" ]]; then
    log "deep mode complete (propose-only; $QUEUED_COUNT items queued for manual /build-loop:run)"
    exit 0
fi

# ---------------------------------------------------------------------------
# 7. Deep mode with auto-apply: check for claude CLI
# ---------------------------------------------------------------------------
# Resolve the CLI explicitly. Under launchd, PATH is the bare
# /usr/bin:/bin:/usr/sbin:/sbin — the user's shell profile is never sourced —
# so `command -v claude` ALWAYS failed here and this job skipped its actual
# work on every scheduled run since it was installed. Every entry in
# launchd-deep.log says "deep apply skipped — claude CLI not found": the job
# looked healthy, exited 0, and did nothing but queue.
#
# Search the usual install locations before giving up, and say WHERE we looked
# when we do, so the next failure is diagnosable instead of just "not found".
CLAUDE_BIN=""
for candidate in \
    "${CLAUDE_CLI:-}" \
    "$(command -v claude 2>/dev/null || true)" \
    "$HOME/.local/bin/claude" \
    "/opt/homebrew/bin/claude" \
    "/usr/local/bin/claude"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
        CLAUDE_BIN="$candidate"
        break
    fi
done

if [[ -z "$CLAUDE_BIN" ]]; then
    log "deep apply skipped — claude CLI not found on PATH ($PATH) or in \$CLAUDE_CLI, ~/.local/bin, /opt/homebrew/bin, /usr/local/bin; $QUEUED_COUNT items queued for manual /build-loop:run"
    exit 0
fi
log "using claude at $CLAUDE_BIN"

# ---------------------------------------------------------------------------
# 8. Extract the APPLY PROMPT from the reference doc (single source)
# ---------------------------------------------------------------------------
SELF_REVIEW_DOC="skills/build-loop/references/self-review.md"

APPLY_PROMPT="$( python3 - <<'PYEOF' 2>>"$RUN_LOG" || true
import sys, re
from pathlib import Path
doc = Path("skills/build-loop/references/self-review.md").read_text()
m = re.search(r'<!-- BEGIN_APPLY_PROMPT -->\n(.*?)<!-- END_APPLY_PROMPT -->', doc, re.DOTALL)
if m:
    print(m.group(1).strip())
else:
    sys.exit(1)
PYEOF
)"

if [[ -z "$APPLY_PROMPT" ]]; then
    log "could not extract APPLY PROMPT from $SELF_REVIEW_DOC; queued items left for manual processing"
    exit 0
fi

# ---------------------------------------------------------------------------
# 9. Invoke claude headlessly with the apply prompt
# ---------------------------------------------------------------------------
log "invoking claude headless apply"

CLAUDE_EXIT=0
"$CLAUDE_BIN" -p "$APPLY_PROMPT" >> "$REVIEW_DIR/apply-deep.log" 2>&1 || CLAUDE_EXIT=$?

log "claude exited with status $CLAUDE_EXIT"

if [[ "$CLAUDE_EXIT" -ne 0 ]]; then
    log "claude apply reported non-zero exit; review $REVIEW_DIR/apply-deep.log for details"
fi

log "deep mode complete"
exit 0
