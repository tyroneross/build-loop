#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# Stop hook: build-loop run-entry finalizer + F-criteria advisory
#
# Fires on every top-level Claude Stop event. Emits valid JSON and skips unless this
# is a completed build-loop run (cwd/.build-loop/state.json exists and
# phase == "report").
#
# Checks performed in order:
#   1. agent_id present => subagent stop, emit {}
#   2. state.json missing or phase != "report" => not a build-loop run, emit {}
#   3. Idempotency: session_id already in runs[] => emit {}
#   4. Invoke write_run_entry.py (or minimal inline append if absent)
#   5. Check latest scorecard for failed F-criteria not held => advisory by default
#
# Always exits 0 (hook contract: non-zero = hook failure, not a build block).
# Blocking Stop is opt-in only: set BUILD_LOOP_STOP_HOOK_BLOCKING=1 for an
# explicit safety/integrity gate.
#
# Security: all values from the stdin event JSON or filesystem are passed
# to embedded Python via environment variables, never via shell string
# interpolation into Python source. Shell injection class is not possible.

set -euo pipefail

emit_empty() {
    printf '{}\n'
}

INPUT=$(cat)
export _BL_INPUT="$INPUT"

# Step 1: skip re-entered Stop hooks, subagent stops, then extract session_id/cwd
read -r STOP_HOOK_ACTIVE AGENT_ID SESSION_ID CWD <<EOF_VARS
$(python3 <<'PY'
import json, os, sys
try:
    d = json.loads(os.environ.get('_BL_INPUT', '{}'))
except Exception:
    d = {}
stop_hook_active = bool(d.get('stop_hook_active', False))
agent_id = d.get('agent_id', '') or ''
session_id = d.get('session_id', '') or ''
cwd = d.get('cwd', '') or ''
# Tab-separated, no special chars in JSON-derived values would survive python's print()
# But guard against newlines / tabs anyway by replacing them with spaces.
def safe(v): return str(v).replace('\t', ' ').replace('\n', ' ')
print(
    f"{'true' if stop_hook_active else 'false'}\t"
    f"{safe(agent_id) or '-'}\t"
    f"{safe(session_id) or '-'}\t"
    f"{safe(cwd) or '-'}"
)
PY
)
EOF_VARS

# Convert "-" sentinels back to empty strings
[ "$STOP_HOOK_ACTIVE" = "-" ] && STOP_HOOK_ACTIVE="false"
[ "$AGENT_ID" = "-" ] && AGENT_ID=""
[ "$SESSION_ID" = "-" ] && SESSION_ID=""
[ "$CWD" = "-" ] && CWD=""

if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
    emit_empty
    exit 0
fi

if [ -n "$AGENT_ID" ]; then
    emit_empty
    exit 0
fi

WORKDIR="${CWD:-.}"
STATE_FILE="${WORKDIR}/.build-loop/state.json"
export _BL_STATE_FILE="$STATE_FILE"
export _BL_SESSION_ID="$SESSION_ID"

# Step 2: check state.json exists and phase == "report"
if [ ! -f "$STATE_FILE" ]; then
    emit_empty
    exit 0
fi

PHASE=$(python3 <<'PY'
import json, os
try:
    with open(os.environ['_BL_STATE_FILE']) as f:
        print(json.load(f).get('phase', ''))
except Exception:
    print('')
PY
) || PHASE=""

if [ "$PHASE" != "report" ]; then
    emit_empty
    exit 0
fi

# Step 3: idempotency — skip if session already recorded
if [ -n "$SESSION_ID" ]; then
    ALREADY=$(python3 <<'PY'
import json, os
try:
    with open(os.environ['_BL_STATE_FILE']) as f:
        d = json.load(f)
    target = os.environ.get('_BL_SESSION_ID', '')
    runs = d.get('runs', [])
    found = any(r.get('session_id') == target for r in runs if isinstance(r, dict))
    print('yes' if found else 'no')
except Exception:
    print('no')
PY
) || ALREADY="no"

    if [ "$ALREADY" = "yes" ]; then
        emit_empty
        exit 0
    fi
fi

# Step 4: record an honest run-lifecycle marker.
#
# A Stop hook does not know the real build goal/outcome/phases, so it must NOT
# call the rich Review-F writer (scripts/write_run_entry/__main__.py requires
# real --goal/--outcome — calling it with only --workdir always fails argparse,
# which is what produced the recurring "returned empty" status). The rich entry
# is owned by the orchestrator's Review-G/Report (real goal/outcome/scope), and
# the modern Stop-time recorder (hooks/closeout.sh -> scripts/stop_closeout.py)
# derives goal/outcome honestly and refuses to clobber a richer record.
#
# Here we append only the honest minimal marker {run_id, date, session_id} — a
# run-lifecycle boundary, no fabricated data. Idempotency (Step 3) already
# guards against a duplicate append for the same session.
RUN_ENTRY_STATUS=$(python3 <<'PY' 2>/dev/null
import json, fcntl, os, tempfile, time
from datetime import datetime, timezone

state_path = os.environ['_BL_STATE_FILE']
session_id = os.environ.get('_BL_SESSION_ID', '')
now = datetime.now(timezone.utc).isoformat()
run_id = 'run-' + now[:10] + '-' + session_id[:8] if session_id else 'run-' + now[:10]

lock_path = state_path + '.lock'
lock_fd = open(lock_path, 'w')
try:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            time.sleep(0.1)
    else:
        print('lock_timeout', end='')
        raise SystemExit(0)

    with open(state_path) as f:
        data = json.load(f)
    if 'runs' not in data or not isinstance(data['runs'], list):
        data['runs'] = []
    data['runs'].append({'run_id': run_id, 'date': now, 'session_id': session_id})

    tmp = tempfile.NamedTemporaryFile(
        mode='w', dir=os.path.dirname(state_path), delete=False, suffix='.tmp'
    )
    json.dump(data, tmp, indent=2)
    tmp.close()
    os.replace(tmp.name, state_path)
    print('marker:' + run_id, end='')
except Exception as e:
    print('marker_error:' + str(e), end='')
finally:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass
    lock_fd.close()
PY
) || RUN_ENTRY_STATUS="marker_error"

# Step 5: check scorecard for failed F-criteria not held
EVALS_DIR="${WORKDIR}/.build-loop/evals"
FCRIT_BLOCK=""

if [ -d "$EVALS_DIR" ]; then
    LATEST_SCORECARD=$(ls -t "${EVALS_DIR}"/*-scorecard.md 2>/dev/null | head -1) || LATEST_SCORECARD=""

    if [ -n "$LATEST_SCORECARD" ] && [ -f "$LATEST_SCORECARD" ]; then
        export _BL_SCORECARD="$LATEST_SCORECARD"
        FCRIT_BLOCK=$(python3 <<'PY' 2>/dev/null
import os, re
try:
    text = open(os.environ['_BL_SCORECARD']).read()
except Exception:
    raise SystemExit(0)
held_ids = set()
held_match = re.search(r'##\s+Held\s*\n(.*?)(?:\n##|\Z)', text, re.DOTALL)
if held_match:
    for m in re.findall(r'\b(F_\w+|Q_\w+)\b', held_match.group(1)):
        held_ids.add(m)
failed = []
for m in re.finditer(r'\b(F_\w+|Q_\w+)\b.*?verdict[:\s]+fail', text, re.IGNORECASE):
    crit_id = re.search(r'\b(F_\w+|Q_\w+)\b', m.group(0))
    if crit_id:
        cid = crit_id.group(1)
        if cid not in held_ids:
            failed.append(cid)
if failed:
    print(failed[0])
PY
) || FCRIT_BLOCK=""
    fi
fi

# Emit the response JSON
export _BL_FCRIT_BLOCK="$FCRIT_BLOCK"
export _BL_RUN_ENTRY_STATUS="$RUN_ENTRY_STATUS"
export _BL_STOP_HOOK_BLOCKING="${BUILD_LOOP_STOP_HOOK_BLOCKING:-}"

python3 <<'PY'
import json, os
fcrit = os.environ.get('_BL_FCRIT_BLOCK', '')
status = os.environ.get('_BL_RUN_ENTRY_STATUS', 'unknown')
blocking_raw = os.environ.get('_BL_STOP_HOOK_BLOCKING', '').strip().lower()
blocking = blocking_raw in {'1', 'true', 'yes', 'on'}
if fcrit and blocking:
    print(json.dumps({
        'decision': 'block',
        'reason': f'F-criterion {fcrit} failed; build cannot finalize'
    }))
elif fcrit:
    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'Stop',
            'additionalContext': (
                f'build-loop run entry {status}; F-criterion {fcrit} failed. '
                'Advisory only; continue investigation and do not claim done until validation passes.'
            )
        }
    }))
else:
    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'Stop',
            'additionalContext': f'build-loop run entry {status}; all F-criteria passed'
        }
    }))
PY

# Fire-and-forget self-release: frees claude_code's presence + claims so peers
# see the stop immediately. Guarded — never blocks, exit 0 preserved.
command -v rally >/dev/null 2>&1 && rally stop claude_code --json >/dev/null 2>&1 || true

exit 0
