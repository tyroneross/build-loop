"""Pin event ownership where a turn and a session have different lifetimes."""
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("binary", ["75", "126", "127", "directory", "bad-interpreter", "0", "1", "2"])
def test_startup_recovers_only_when_native_did_not_consume_input(tmp_path, binary):
    import os
    import shutil
    import subprocess
    import session_start_hook_command as registration
    for path in ("bin/native", "crates", "hooks", "scripts"):
        (tmp_path / path).mkdir(parents=True)
    for path in ("bin/native/bl.sources", "crates/SOURCES.digest"):
        (tmp_path / path).write_text("a" * 64 + "\n")
    native = tmp_path / "bin/native/bl"
    if binary == "directory":
        native.mkdir()
    elif binary == "bad-interpreter":
        native.write_text("#!/nonexistent/bl-interpreter\n")
        native.chmod(0o755)
    else:
        consume = 'cat > "$CLAUDE_PLUGIN_ROOT/native-input"\nprintf "{}"\n' if binary in ("0", "1", "2") else ""
        native.write_text(f"#!/bin/sh\n{consume}exit {binary}\n")
        native.chmod(0o755)
    shutil.copy2(ROOT / "scripts/session_start_hook_command.py", tmp_path / "scripts/session_start_hook_command.py")
    (tmp_path / "hooks/session-start-tool-state.sh").write_text('''#!/bin/sh
[ "$BUILD_LOOP_NATIVE_HOOKS" = off ] || exit 0
cat > "$CLAUDE_PLUGIN_ROOT/recovered-input"
printf 'recovery context'
''')
    payload = '{"session_id":"fixture-🌍"}\n'.encode()
    result = subprocess.run(["/bin/sh", "-c", registration.NATIVE], input=payload,
        capture_output=True, timeout=5, env=dict(os.environ,
            CLAUDE_PLUGIN_ROOT=str(tmp_path), CLAUDE_PROJECT_DIR=str(tmp_path),
            BUILD_LOOP_NATIVE_HOOKS="1", BUILD_LOOP_SESSION_START_DEADLINE_MS="7500"))
    assert result.returncode == 0 and result.stderr == b""
    if binary in ("0", "1", "2"):
        assert result.stdout == b"{}"
        assert (tmp_path / "native-input").read_bytes() == payload
        assert not (tmp_path / "recovered-input").exists(), "never replay after a started worker"
    else:
        assert json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"] == "recovery context"
        assert (tmp_path / "recovered-input").read_bytes() == payload


def registrations(name):
    hooks = json.loads((ROOT / name).read_text())["hooks"]
    return [(event, handler) for event, groups in hooks.items()
            for group in groups for handler in group["hooks"]]


def test_retrospective_runs_at_session_end_in_every_registered_adapter():
    for manifest in ("hooks/hooks.json", "hooks/codex.json", ".codex/hooks.json"):
        entries = [(event, handler) for event, handler in registrations(manifest)
                   if "session_end_retro_sweep.py" in handler["command"]]
        assert len(entries) == 1, manifest
        assert entries[0][0] == "SessionEnd", manifest


def test_repository_codex_retains_turn_consumers():
    commands = [handler["command"] for event, handler in registrations(".codex/hooks.json")
                if event == "Stop"]
    assert len(commands) == 3
    assert any('closeout.sh" stop' in command for command in commands)
    assert any('agent_rally.py" stop' in command for command in commands)
    assert any('stop-transcript-sweep.sh" all' in command for command in commands)


def test_startup_has_one_registered_response_per_plugin():
    for manifest in ("hooks/hooks.json", "hooks/codex.json"):
        startup = [h for event, h in registrations(manifest) if event == "SessionStart"]
        assert len(startup) == 1
        assert "hook session-start" in startup[0]["command"]
        assert "--fallback" in startup[0]["command"]
        assert startup[0]["timeout"] == 8


def _command(program):
    import shlex
    import sys
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"


def test_recovery_copies_payload_and_merges_in_order(tmp_path):
    import session_start_hook_command as registration
    marker = str(tmp_path / "first-done")
    def program(label, delay, first=False):
        return ("import json,sys,time; from pathlib import Path; "
                + (f"Path({marker!r}).touch(); " if first else f"assert Path({marker!r}).exists(); ")
                + f"time.sleep({delay}); "
                + "print(json.dumps({'hookSpecificOutput': {'additionalContext': "
                + f"{label!r}+sys.stdin.read()" + "}}))")
    commands = [(label, _command(program(label, delay, first)), 1, 0)
                for label, delay, first in [("first:", 0, True), ("second:", .08, False), ("third:", 0, False)]]
    out = registration.fallback(str(tmp_path), b'payload\n\xf0\x9f\x8c\x8d', commands)
    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert context == "\n\n".join(label + "payload\n🌍" for label in ("first:", "second:", "third:"))


def _gone_or_zombie(pid):
    import subprocess
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return not result.stdout.strip() or result.stdout.strip().startswith("Z")


def test_recovery_timeout_kills_term_resistant_group(tmp_path):
    import time
    import session_start_hook_command as registration
    pidfile = tmp_path / "child.pid"
    program = ("import os,signal,time; from pathlib import Path; "
               "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
               f"Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(20)")
    start = time.monotonic()
    assert registration.fallback(str(tmp_path), b"", [("slow", _command(program), .2, 0)]) == b"{}"
    assert time.monotonic() - start < 2
    assert _gone_or_zombie(int(pidfile.read_text()))


def test_recovery_cancellation_cleans_first_child(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time
    pidfile = tmp_path / "child.pid"
    child = ("import os,signal,time; from pathlib import Path; "
             "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
             f"Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(20)")
    program = (f"import sys; sys.path.insert(0,{str(ROOT / 'scripts')!r}); "
               "import session_start_hook_command as r; "
               f"sys.stdout.buffer.write(r.fallback({str(tmp_path)!r},b'',"
               f"[('slow',{_command(child)!r},5,0)]))")
    proc = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.PIPE)
    try:
        until = time.monotonic() + 3
        while not pidfile.exists() and time.monotonic() < until:
            time.sleep(.01)
        assert pidfile.exists()
        os.kill(proc.pid, signal.SIGTERM)
        stdout, _ = proc.communicate(timeout=3)
        assert stdout == b"{}"
        assert proc.returncode == 0
        assert _gone_or_zombie(int(pidfile.read_text()))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_supervisor_consumes_only_numeric_aggregator_parent(tmp_path):
    import os
    import shlex
    import subprocess
    hook = tmp_path / "observe.sh"
    hook.write_text(f'''#!/bin/bash
. {shlex.quote(str(ROOT / 'hooks/_bounded_hook.sh'))}
bl_bound_hook 1000 1000 text "$BASH" "$0"
printf '%s:%s' "$BUILD_LOOP_RALLY_START_PARENT_PID" "${{BUILD_LOOP_SESSION_START_AGGREGATOR_PARENT_PID-unset}}"
''')
    for override in ("12345", "not-a-pid"):
        result = subprocess.run(["bash", str(hook)], capture_output=True, text=True,
            env=dict(os.environ, BUILD_LOOP_SESSION_START_AGGREGATOR_PARENT_PID=override), timeout=3, check=True)
        parent, remaining = result.stdout.split(":")
        assert parent == ("12345" if override == "12345" else str(os.getpid()))
        assert remaining == "unset"


def test_recovery_open_stdin_preserves_independent_and_provenance_context(tmp_path):
    import os
    import subprocess
    import sys
    import time
    def output(text):
        return _command(f"print({text!r})")
    commands = [('tool-state', output('provenance'), 2, 0),
                ('closeout', output('pending closeout'), 3, 0),
                ('codex-hook-trust', output('trust context'), 5, 0)]
    program = (f"import sys; sys.path.insert(0,{str(ROOT / 'scripts')!r}); "
               "import session_start_hook_command as r; "
               f"sys.stdout.buffer.write(r.fallback({str(tmp_path)!r},None,{commands!r}))")
    proc = subprocess.Popen([sys.executable, '-c', program], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    try:
        started = time.monotonic()
        # Deliberately leave stdin open; communicate() would close it.
        proc.wait(timeout=3)
        assert time.monotonic() - started < 3
        context = json.loads(proc.stdout.read())['hookSpecificOutput']['additionalContext']
        assert context == 'provenance\n\npending closeout\n\ntrust context'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdin.close()
        proc.stdout.close()


@pytest.mark.parametrize("runtime", ["native", "recovery"])
@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGINT", "SIGHUP"])
def test_registered_startup_cancellation_reaches_supervisor_and_worker(tmp_path, runtime, signal_name):
    import os
    import shutil
    import signal
    import subprocess
    import sys
    import time
    import session_start_hook_command as registration
    for path in ("bin/native", "crates", "hooks", "scripts", ".build-loop", "home", "path"):
        (tmp_path / path).mkdir(parents=True)
    (tmp_path / "path/python3").symlink_to(sys.executable)
    for path in ("bin/native/bl.sources", "crates/SOURCES.digest"):
        (tmp_path / path).write_text("fixture\n")
    if runtime == "native":
        binary = Path(os.environ.get("BL_BIN") or ROOT / "target/release/bl")
        assert binary.is_file(), "build native binary before launcher lifecycle tests"
        shutil.copyfile(binary, tmp_path / "bin/native/bl")
        (tmp_path / "bin/native/bl").chmod(0o755)
    shutil.copyfile(ROOT / "scripts/session_start_hook_command.py", tmp_path / "scripts/session_start_hook_command.py")
    (tmp_path / "scripts/stop_closeout.py").write_text(
        "import os,signal,time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        "Path(os.environ['CLAUDE_PLUGIN_ROOT'],'worker.pid').write_text(str(os.getpid()))\n"
        "time.sleep(30)\n")
    (tmp_path / "hooks/closeout.sh").write_text('exec python3 "$CLAUDE_PLUGIN_ROOT/scripts/stop_closeout.py"\n')
    env = dict(PATH=f"{tmp_path}/path:/usr/bin:/bin", HOME=str(tmp_path / "home"),
               CLAUDE_PLUGIN_ROOT=str(tmp_path), CLAUDE_PROJECT_DIR=str(tmp_path),
               BUILD_LOOP_SESSION_START_DEADLINE_MS="7500")
    child = subprocess.Popen(["/bin/sh", "-c", registration.NATIVE], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    supervisor = worker = None
    try:
        until = time.monotonic() + 2
        while time.monotonic() < until:
            try:
                worker = int((tmp_path / "worker.pid").read_text())
                break
            except (OSError, ValueError):
                time.sleep(.005)
        assert worker, "owned worker did not start"
        pids = subprocess.run(["pgrep", "-P", str(child.pid)], capture_output=True, text=True).stdout.split()
        assert len(pids) == 1
        supervisor = int(pids[0])
        os.kill(child.pid, getattr(signal, signal_name))
        child.wait(timeout=2)  # Keep event stdin open until host cancellation.
        stdout, stderr = child.communicate(timeout=2)
        assert child.returncode == 0 and stderr == b""
        assert json.loads(stdout) == {}
        until = time.monotonic() + 1
        while not _gone_or_zombie(worker) and time.monotonic() < until:
            time.sleep(.01)
        assert _gone_or_zombie(worker)
        assert _gone_or_zombie(supervisor)
    finally:
        for pid in (supervisor, worker, child.pid):
            if pid is not None:
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass
        child.communicate(timeout=2)


@pytest.mark.parametrize("status", [0, 2, 75, 127, 143])
def test_registered_child_runner_finishes_completed_wait_even_if_pid_is_reused(tmp_path, status):
    import subprocess
    import session_start_hook_command as registration
    # Model the problematic race: a process with the old PID is alive after wait.
    # Successful completion must depend on wait's result, never kill -0.
    command = ("kill() { return 0; }; " + registration.CHILD_RUNNER
               + f" _bl_session_child /bin/sh -c 'exit {status}'; exit $?")
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, timeout=2)
    assert result.returncode == status
