# SPDX-License-Identifier: Apache-2.0
# Shared SessionStart helpers. Source, do not exec.
# Contract for all SessionStart hooks (see feedback_hook_design.md):
# command-type only, exit 0 always, silent on success, fire-and-forget,
# <100ms return via nohup + disown.

# Background-fire a python worker with stdin/stdout/stderr fully detached.
bl_fire_bg() {
  nohup python3 "$@" </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null || true
}

# Echo "yes" if $1 is missing or older than $2 seconds; "no" otherwise.
bl_stale() {
  F="$1" MAX="$2" python3 - <<'PY' 2>/dev/null
import os, time
from pathlib import Path
f = Path(os.environ["F"])
m = float(os.environ["MAX"])
if not f.exists():
    print("yes")
else:
    try:
        print("yes" if time.time() - f.stat().st_mtime > m else "no")
    except OSError:
        print("yes")
PY
}

# Spawn a long-running daemon once; PID-file + /health probe single-flight.
# Args: script_path port pidfile_basename logfile_basename log_dir
bl_spawn_daemon() {
  local script="$1" port="$2" pidfile="$5/$3.pid" log="$5/$4.log"
  [ -f "$script" ] || return 0
  if [ -f "$pidfile" ]; then
    local pid; pid=$(cat "$pidfile" 2>/dev/null)
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && return 0
  fi
  curl -fsS --max-time 0.2 "http://127.0.0.1:${port}/health" >/dev/null 2>&1 && return 0
  nohup python3 "$script" </dev/null >>"$log" 2>&1 &
  disown 2>/dev/null || true
}
