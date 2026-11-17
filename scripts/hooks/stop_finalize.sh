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
#   4. Append a minimal lifecycle marker under the state lock
#   5. Check latest scorecard for failed F-criteria not held => advisory by default
#
# Always exits 0 (hook contract: non-zero = hook failure, not a build block).
# Blocking Stop is opt-in only: set BUILD_LOOP_STOP_HOOK_BLOCKING=1 for an
# explicit safety/integrity gate.
#
# Security: event JSON travels on stdin; scalar filesystem values use the
# environment. Neither is interpolated into Python source.

set -euo pipefail

emit_empty() {
    printf '{}\n'
}

INPUT=$(cat)
# Keep event bytes off argv/environment: valid large payloads can exceed exec
# limits. fd 3 carries the program; stdin carries the original JSON pipe.

# Prefer the bounded native read-only preflight. INPUT remains available for
# Python fallback on large/unusual payloads. Policy and writes stay in Python.
_STOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_STOP_ROOT="$_STOP_DIR/../.."
STOP_CONTEXT=""
if [ "${BUILD_LOOP_NATIVE_HOOKS:-}" != off ] && [ -x "$_STOP_ROOT/bin/native/bl" ] \
    && { read -r _STOP_BUILT < "$_STOP_ROOT/bin/native/bl.sources"; } 2>/dev/null \
    && { read -r _STOP_TREE < "$_STOP_ROOT/crates/SOURCES.digest"; } 2>/dev/null \
    && [ -n "$_STOP_TREE" ] && [ "$_STOP_BUILT" = "$_STOP_TREE" ]; then
    STOP_CONTEXT=$(printf '%s' "$INPUT" | "$_STOP_ROOT/bin/native/bl" hook stop-context) || STOP_CONTEXT=""
fi
if [ -z "$STOP_CONTEXT" ]; then
    STOP_CONTEXT=$(printf '%s' "$INPUT" | python3 /dev/fd/3 3<<'PY'
import json, os, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
stop_hook_active = bool(d.get('stop_hook_active', False))
agent_id = d.get('agent_id', '') or ''
session_id = d.get('session_id', '') or ''
cwd = d.get('cwd', '') or ''
# Tab-separated, no special chars in JSON-derived values would survive python's print()
# But guard against newlines / tabs anyway by replacing them with spaces.
def safe(v): return str(v).replace('\t', ' ').replace('\n', ' ')
# Same actor/host order as SessionStart and stop_closeout._stop_tool.
host = next((os.environ[name].strip() for name in
             ("RALLY_TOOL_ID", "BUILD_LOOP_RALLY_TOOL", "RALLY_HOST_FAMILY")
             if os.environ.get(name, "").strip()), None)
if host is None:
    host = ("codex" if "turn_id" in d or os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
            else "cursor" if os.environ.get("CURSOR_SESSION_ID") else "claude_code")
has_state, phase, already = 'no', '', 'no'
if not stop_hook_active and not agent_id:
    state_file = os.path.join(cwd or '.', '.build-loop', 'state.json')
    if os.path.isfile(state_file):
        has_state = 'yes'
        try:
            with open(state_file) as f:
                st = json.load(f)
            phase = st.get('phase', '')
            if phase == 'report' and session_id:
                already = 'yes' if any(
                    r.get('session_id') == session_id
                    for r in st.get('runs', []) if isinstance(r, dict)
                ) else 'no'
        except Exception:
            phase, already = '', 'no'
print(
    f"{safe(host)}\t"
    f"{'true' if stop_hook_active else 'false'}\t"
    f"{safe(agent_id) or '-'}\t"
    f"{safe(session_id) or '-'}\t"
    f"{has_state}\t"
    f"{safe(phase) or '-'}\t"
    f"{already}\t"
    f"{safe(cwd) or '-'}"
)
PY
)
fi
read -r HOST_TOOL STOP_HOOK_ACTIVE AGENT_ID SESSION_ID HAS_STATE PHASE ALREADY CWD <<EOF_VARS
$STOP_CONTEXT
EOF_VARS

# Convert "-" sentinels back to empty strings
[ "$STOP_HOOK_ACTIVE" = "-" ] && STOP_HOOK_ACTIVE="false"
[ "$AGENT_ID" = "-" ] && AGENT_ID=""
[ "$SESSION_ID" = "-" ] && SESSION_ID=""
[ "$PHASE" = "-" ] && PHASE=""
[ "$CWD" = "-" ] && CWD=""

if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
    emit_empty
    exit 0
fi

WORKDIR="${CWD:-.}"

# Release the exact session before every ordinary early-return path. Stop fires
# outside Build Loop runs too, and stale Rally claims must not depend on a
# state.json/report-phase marker. The re-entry guard above remains first so the
# facade cannot recursively invoke itself.
#
# Release stays detached, but the shared supervisor owns its full process group
# and cleans up descendants at the deadline, not just the facade interpreter.
FACADE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/agent_rally.py"
SUPERVISOR="$(dirname "$FACADE")/../hooks/_bounded_hook.sh"
if [ -n "$SESSION_ID" ] && [ -f "$FACADE" ] && [ -f "$SUPERVISOR" ] && command -v perl >/dev/null 2>&1; then
    ( perl -e 'use POSIX (); POSIX::setsid(); exec @ARGV' \
        /bin/bash -c '. "$1"; shift; bl_bound_hook 8000 8000 optional-json "$@"' \
        build-loop-stop-release "$SUPERVISOR" python3 "$FACADE" stop \
        --workdir "$WORKDIR" --tool "$HOST_TOOL" --session-id "$SESSION_ID" \
        --json </dev/null >/dev/null 2>&1 & ) >/dev/null 2>&1 || true
fi

if [ -n "$AGENT_ID" ]; then
    emit_empty
    exit 0
fi

STATE_FILE="${WORKDIR}/.build-loop/state.json"
export _BL_STATE_FILE="$STATE_FILE"
export _BL_SESSION_ID="$SESSION_ID"

# Step 2: state.json must exist and phase == "report" (read above)
if [ "$HAS_STATE" != "yes" ] || [ "$PHASE" != "report" ]; then
    emit_empty
    exit 0
fi

# Step 3: idempotency — skip if session already recorded (read above)
if [ -n "$SESSION_ID" ] && [ "$ALREADY" = "yes" ]; then
    emit_empty
    exit 0
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
# Recording, scorecard inspection and response encoding share one interpreter.
# The writer still rechecks session idempotency inside its existing flock.
python3 <<'PY'
import json, fcntl, os, tempfile, time, glob, re
from datetime import datetime, timezone

def record_marker():
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
            return 'lock_timeout'

        with open(state_path) as f:
            data = json.load(f)
        if 'runs' not in data or not isinstance(data['runs'], list):
            data['runs'] = []
        if session_id and any(isinstance(r, dict) and r.get('session_id') == session_id for r in data['runs']):
            return 'already_recorded'
        data['runs'].append({'run_id': run_id, 'date': now, 'session_id': session_id})

        tmp = tempfile.NamedTemporaryFile(
            mode='w', dir=os.path.dirname(state_path), delete=False, suffix='.tmp'
        )
        json.dump(data, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, state_path)
        return 'marker:' + run_id
    except Exception as e:
        return 'marker_error:' + str(e)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fd.close()

def failed_criterion():
    directory = os.path.join(os.path.dirname(os.environ['_BL_STATE_FILE']), 'evals')
    try:
        cards = glob.glob(os.path.join(directory, '*-scorecard.md'))
        cards.sort(key=lambda p: (-os.lstat(p).st_mtime_ns, os.path.basename(p)))
        if not cards:
            return ''
        with open(cards[0]) as file:
            text = file.read()
    except (OSError, UnicodeError):
        return ''
    held = re.search(r'##\s+Held\s*\n(.*?)(?:\n##|\Z)', text, re.DOTALL)
    held_ids = set(re.findall(r'\b(F_\w+|Q_\w+)\b', held.group(1))) if held else set()
    for match in re.finditer(r'\b(F_\w+|Q_\w+)\b.*?verdict[:\s]+fail', text, re.IGNORECASE):
        criterion = re.search(r'\b(F_\w+|Q_\w+)\b', match.group(0)).group(1)
        if criterion not in held_ids:
            return criterion
    return ''

try:
    status = record_marker()
except Exception:
    status = 'marker_error'
fcrit = failed_criterion()
blocking = os.environ.get('BUILD_LOOP_STOP_HOOK_BLOCKING', '').strip().lower() in {'1', 'true', 'yes', 'on'}
if fcrit and blocking:
    print(json.dumps({'decision': 'block', 'reason': f'F-criterion {fcrit} failed; build cannot finalize'}))
elif fcrit:
    print(json.dumps({'systemMessage': (
        f'build-loop run entry {status}; F-criterion {fcrit} failed. '
        'Advisory only; continue investigation and do not claim done until validation passes.'
    )}))
else:
    print(json.dumps({'systemMessage': f'build-loop run entry {status}; no failed F-criterion was found; validation is not certified by this hook'}))
PY

exit 0
