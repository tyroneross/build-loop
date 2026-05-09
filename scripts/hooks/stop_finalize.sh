#!/usr/bin/env bash
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

set -euo pipefail

INPUT=$(cat)

# Step 1: skip subagent stops
AGENT_ID=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('agent_id', ''))
except Exception:
    print('')
" 2>/dev/null) || AGENT_ID=""

if [ -n "$AGENT_ID" ]; then
    exit 0
fi

# Extract session_id and cwd from the Stop event
SESSION_ID=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('session_id', ''))
except Exception:
    print('')
" 2>/dev/null) || SESSION_ID=""

CWD=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('cwd', ''))
except Exception:
    print('')
" 2>/dev/null) || CWD=""

WORKDIR="${CWD:-.}"
STATE_FILE="${WORKDIR}/.build-loop/state.json"

# Step 2: check state.json exists and phase == "report"
if [ ! -f "$STATE_FILE" ]; then
    exit 0
fi

PHASE=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('phase', ''))
except Exception:
    print('')
" 2>/dev/null) || PHASE=""

if [ "$PHASE" != "report" ]; then
    exit 0
fi

# Step 3: idempotency — skip if session already recorded
if [ -n "$SESSION_ID" ]; then
    ALREADY=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    runs = d.get('runs', [])
    target = '$SESSION_ID'
    found = any(r.get('session_id') == target for r in runs if isinstance(r, dict))
    print('yes' if found else 'no')
except Exception:
    print('no')
" 2>/dev/null) || ALREADY="no"

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
    RUN_ENTRY_STATUS=$(python3 -c "
import json, fcntl, os, sys, tempfile, time
from datetime import datetime, timezone

state_path = '$STATE_FILE'
session_id = '$SESSION_ID'
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
        sys.exit(0)

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
" 2>/dev/null) || RUN_ENTRY_STATUS="inline_fallback_error"
    RUN_ENTRY_STATUS="${RUN_ENTRY_STATUS} (write_run_entry.py absent; full schema requires it)"
fi

# Step 5: check scorecard for failed F-criteria not held
EVALS_DIR="${WORKDIR}/.build-loop/evals"
FCRIT_BLOCK=""

if [ -d "$EVALS_DIR" ]; then
    LATEST_SCORECARD=$(ls -t "${EVALS_DIR}"/*-scorecard.md 2>/dev/null | head -1) || LATEST_SCORECARD=""

    if [ -n "$LATEST_SCORECARD" ] && [ -f "$LATEST_SCORECARD" ]; then
        FCRIT_BLOCK=$(python3 -c "
import sys, re

try:
    text = open('$LATEST_SCORECARD').read()
except Exception:
    sys.exit(0)

# Extract the Held section IDs (these are excused from blocking)
held_ids = set()
held_match = re.search(r'##\s+Held\s*\n(.*?)(?:\n##|\Z)', text, re.DOTALL)
if held_match:
    for m in re.findall(r'\b(F_\w+|Q_\w+)\b', held_match.group(1)):
        held_ids.add(m)

# Find failed criteria in Done/Blocked sections
failed = []
for m in re.finditer(r'\b(F_\w+|Q_\w+)\b.*?verdict[:\s]+fail', text, re.IGNORECASE):
    crit_id = re.search(r'\b(F_\w+|Q_\w+)\b', m.group(0))
    if crit_id:
        cid = crit_id.group(1)
        if cid not in held_ids:
            failed.append(cid)

if failed:
    print(failed[0])
" 2>/dev/null) || FCRIT_BLOCK=""
    fi
fi

# Emit the response JSON
if [ -n "$FCRIT_BLOCK" ]; then
    python3 -c "
import json
print(json.dumps({
    'decision': 'block',
    'reason': 'F-criterion $FCRIT_BLOCK failed; build cannot finalize'
}))
"
else
    python3 -c "
import json
status = '$RUN_ENTRY_STATUS'
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'Stop',
        'additionalContext': 'build-loop run entry ' + status + '; all F-criteria passed'
    }
}))
"
fi

exit 0
