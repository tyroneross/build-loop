#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
# Stop hook: build-loop run-entry finalizer + F-criteria gate
#
# Fires on every top-level Claude Stop event. Skips silently unless this
# is a completed build-loop run (cwd/.build-loop/state.json exists and
# phase == "report").
#
# Checks performed in order:
#   1. agent_id present => subagent stop, exit 0 silently
#   2. state.json missing or phase != "report" => not a build-loop run, exit 0
#   3. Idempotency: session_id already in runs[] => exit 0 silently
#   4. Invoke write_run_entry.py (or minimal inline append if absent)
#   5. Check latest scorecard for failed F-criteria not held => block if found
#
# Always exits 0 (hook contract: non-zero = hook failure, not a build block).
# Blocking Claude from stopping is done via JSON {"decision":"block",...}.
#
# Security: all values from the stdin event JSON or filesystem are passed
# to embedded Python via environment variables, never via shell string
# interpolation into Python source. Shell injection class is not possible.

set -euo pipefail

INPUT=$(cat)
export _BL_INPUT="$INPUT"

# Step 1: skip subagent stops + extract session_id, cwd
read -r AGENT_ID SESSION_ID CWD <<EOF_VARS
$(python3 <<'PY'
import json, os, sys
try:
    d = json.loads(os.environ.get('_BL_INPUT', '{}'))
except Exception:
    d = {}
agent_id = d.get('agent_id', '') or ''
session_id = d.get('session_id', '') or ''
cwd = d.get('cwd', '') or ''
# Tab-separated, no special chars in JSON-derived values would survive python's print()
# But guard against newlines / tabs anyway by replacing them with spaces.
def safe(v): return str(v).replace('\t', ' ').replace('\n', ' ')
print(f"{safe(agent_id) or '-'}\t{safe(session_id) or '-'}\t{safe(cwd) or '-'}")
PY
)
EOF_VARS

# Convert "-" sentinels back to empty strings
[ "$AGENT_ID" = "-" ] && AGENT_ID=""
[ "$SESSION_ID" = "-" ] && SESSION_ID=""
[ "$CWD" = "-" ] && CWD=""

if [ -n "$AGENT_ID" ]; then
    exit 0
fi

WORKDIR="${CWD:-.}"
STATE_FILE="${WORKDIR}/.build-loop/state.json"
export _BL_STATE_FILE="$STATE_FILE"
export _BL_SESSION_ID="$SESSION_ID"

# Step 2: check state.json exists and phase == "report"
if [ ! -f "$STATE_FILE" ]; then
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
        exit 0
    fi
fi

# Resolve CLAUDE_PLUGIN_ROOT
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PLUGIN_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
fi

WRITE_ENTRY="${PLUGIN_ROOT}/scripts/write_run_entry.py"
RUN_ENTRY_STATUS="skipped"
RUN_ID=""

# Step 4: write run entry
if [ -f "$WRITE_ENTRY" ]; then
    RUN_ID=$(python3 "$WRITE_ENTRY" --workdir "$WORKDIR" 2>/dev/null) || RUN_ID=""
    if [ -n "$RUN_ID" ]; then
        RUN_ENTRY_STATUS="written:${RUN_ID}"
    else
        RUN_ENTRY_STATUS="write_run_entry.py returned empty (check stderr for details)"
    fi
else
    # Minimal inline fallback: append {run_id, date, session_id} to runs[]
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
    print('inline:' + run_id, end='')
except Exception as e:
    print('inline_error:' + str(e), end='')
finally:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass
    lock_fd.close()
PY
) || RUN_ENTRY_STATUS="inline_fallback_error"
    RUN_ENTRY_STATUS="${RUN_ENTRY_STATUS} (write_run_entry.py absent; full schema requires it)"
fi

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

python3 <<'PY'
import json, os
fcrit = os.environ.get('_BL_FCRIT_BLOCK', '')
status = os.environ.get('_BL_RUN_ENTRY_STATUS', 'unknown')
if fcrit:
    print(json.dumps({
        'decision': 'block',
        'reason': f'F-criterion {fcrit} failed; build cannot finalize'
    }))
else:
    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'Stop',
            'additionalContext': f'build-loop run entry {status}; all F-criteria passed'
        }
    }))
PY

exit 0
