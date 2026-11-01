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
  python3 - "$script" "$port" "$pidfile" "$log" <<'PY_LAUNCH' 2>/dev/null
import fcntl, os, subprocess, sys, tempfile, urllib.request
from pathlib import Path
script, port, pidfile, logfile = sys.argv[1:]
Path(pidfile).parent.mkdir(parents=True, exist_ok=True)
with open(pidfile + '.lock', 'a') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    try:
        pid = int(Path(pidfile).read_text().strip())
        command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, timeout=.2)
        if command.returncode == 0 and script in command.stdout:
            raise SystemExit(0)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        pass
    try:
        urllib.request.urlopen('http://127.0.0.1:' + port + '/health', timeout=.2).close()
        raise SystemExit(0)
    except OSError:
        pass
    with open(logfile, 'ab') as log:
        child = subprocess.Popen([sys.executable, script], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    fd, temporary = tempfile.mkstemp(dir=str(Path(pidfile).parent))
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(str(child.pid))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, pidfile)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
PY_LAUNCH
}
