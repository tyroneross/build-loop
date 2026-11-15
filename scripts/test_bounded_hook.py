"""Whole-invocation deadlines must include interpreter startup and pipe EOF."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def plugin(tmp_path):
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    (root / "scripts/rally_point").mkdir(parents=True)
    (root / ".build-loop").mkdir()
    for name in ("pre-edit-rally-point.sh", "closeout.sh", "_resolve_python.sh", "_bounded_hook.sh",
                 "session-start-git-hooks.sh", "session-start-tool-state.sh", "session-start-memory.sh"):
        source = ROOT / "hooks" / name
        if source.exists():
            shutil.copy2(source, root / "hooks" / name)
    stub = (
        "import json, os, sys, time\n"
        "time.sleep(float(os.environ.get('TEST_HELPER_DELAY', '0')))\n"
        "if '--tool' in sys.argv:\n"
        " print('host:' + sys.argv[sys.argv.index('--tool') + 1])\n"
        "else:\n"
        " print('{}')\n"
    )
    (root / "scripts/rally_point/hooks.py").write_text(stub)
    (root / "scripts/stop_closeout.py").write_text(stub)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "CLAUDE_", "RALLY_", "BUILD_LOOP_"))}
    env.update(CLAUDE_PLUGIN_ROOT=str(root), CLAUDE_PROJECT_DIR=str(root))
    return root, env


def run_hook(plugin, name, *args, **overrides):
    root, env = plugin
    started = time.monotonic()
    proc = subprocess.run(
        ["/bin/bash", str(root / "hooks" / name), *args],
        input='{"session_id":"fixture-session"}', text=True, capture_output=True,
        env=dict(env, **overrides), cwd=root, timeout=9,
    )
    return proc, time.monotonic() - started


def test_pre_edit_startup_and_helper_finish_before_host_cancels(plugin):
    proc, elapsed = run_hook(plugin, "pre-edit-rally-point.sh", TEST_HELPER_DELAY="2.3")
    assert elapsed < 2, f"pre-edit exceeded its 2s host deadline: {elapsed:.3f}s"
    assert proc.returncode == 0
    assert proc.stdout.strip() == "{}"


def test_closeout_finish_before_host_cancels(plugin):
    proc, elapsed = run_hook(plugin, "closeout.sh", TEST_HELPER_DELAY="6.3")
    assert elapsed < 6, f"closeout exceeded its 6s host deadline: {elapsed:.3f}s"
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


@pytest.mark.parametrize("host", ["codex", "claude_code"])
def test_pre_edit_preserves_host_identity_and_completed_output(plugin, host):
    overrides = {"CODEX_THREAD_ID": "fixture-session"} if host == "codex" else {}
    proc, _ = run_hook(plugin, "pre-edit-rally-point.sh", **overrides)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "host:" + host


def test_closeout_preserves_completed_json(plugin):
    proc, _ = run_hook(plugin, "closeout.sh")
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_closeout_stays_silent_outside_opted_in_repositories(plugin, tmp_path):
    root, env = plugin
    # The developer's temp directory may itself contain another run's .build-loop.
    # Stay outside the wrapper's bounded eight-parent search without touching it.
    outside = tmp_path.joinpath(*(["outside"] * 9))
    outside.mkdir(parents=True)
    proc = subprocess.run(["/bin/bash", str(root / "hooks/closeout.sh")], input="{}",
                          text=True, capture_output=True, cwd=outside,
                          env=dict(env, CLAUDE_PROJECT_DIR=str(outside)), timeout=2)
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_slow_interpreter_startup_is_inside_pre_edit_deadline(plugin):
    root, env = plugin
    bin_dir = root / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python3"
    python.write_text(f'#!/bin/sh\n/bin/sleep 2.3\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    proc, elapsed = run_hook(plugin, "pre-edit-rally-point.sh", PATH=str(bin_dir) + ":" + env["PATH"])
    assert elapsed < 2
    assert json.loads(proc.stdout) == {}


def test_pre_edit_deadline_cannot_extend_host_budget(plugin):
    proc, elapsed = run_hook(plugin, "pre-edit-rally-point.sh", TEST_HELPER_DELAY="2.3", BUILD_LOOP_RALLY_PRE_EDIT_DEADLINE_MS="999999")
    assert elapsed < 2
    assert json.loads(proc.stdout) == {}


def test_reaped_helper_with_open_output_pipe_still_times_out(plugin):
    root, _ = plugin
    (root / "scripts/stop_closeout.py").write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(6)'])\n"
        "print('{}')\n"
    )
    proc, elapsed = run_hook(plugin, "closeout.sh", BUILD_LOOP_CLOSEOUT_DEADLINE_MS="150")
    assert elapsed < 1
    assert json.loads(proc.stdout) == {}


def run_gate(plugin, helper, **overrides):
    root, env = plugin
    worker = root / "gate.py"
    worker.write_text(helper)
    wrapper = root / "gate.sh"
    wrapper.write_text(
        '#!/bin/bash\n. "$(dirname "$0")/hooks/_bounded_hook.sh"\n'
        'bl_bound_hook "${BUILD_LOOP_BASH_DEADLINE_MS:-300}" 500 gate "$BASH" "$0" "$@"\n'
        f'exec "{sys.executable}" "$(dirname "$0")/gate.py"\n'
    )
    return subprocess.run(["/bin/bash", str(wrapper)], capture_output=True, text=True,
                          env=dict(env, **overrides), timeout=2)


def test_slow_gate_returns_explicit_incomplete_check(plugin):
    proc = run_gate(plugin, "import time\ntime.sleep(3)\nprint('{}')\n")
    assert proc.returncode == 0
    hso = json.loads(proc.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "checks did not finish" in hso["permissionDecisionReason"]


def test_completed_deny_and_exit_two_survive_supervisor(plugin):
    denial = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}}
    proc = run_gate(plugin, f"import sys\nprint({json.dumps(denial)!r})\nsys.exit(2)\n")
    assert proc.returncode == 2
    assert json.loads(proc.stdout) == denial


@pytest.mark.parametrize("output", ["broken-json", "{}" * 40000])
def test_invalid_or_oversized_gate_output_never_becomes_allow(plugin, output):
    proc = run_gate(plugin, f"print({output!r})\n")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_gate_signal_or_failed_exec_never_becomes_allow(plugin):
    proc = run_gate(plugin, "import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_registered_bash_dispatcher_bounds_input_read(plugin):
    root, env = plugin
    proc = subprocess.Popen(
        ["/bin/bash", str(ROOT / "scripts/hooks/pre_bash_dispatch.sh")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=root, env=dict(env, BUILD_LOOP_BASH_DEADLINE_MS="150"),
    )
    try:
        # A missing EOF used to bypass every downstream timeout.
        proc.stdin.write('{"tool_name":"Bash"')
        proc.stdin.flush()
        assert proc.wait(timeout=2) == 0
        assert json.loads(proc.stdout.read())["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        proc.stdin.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=2)
        proc.stdout.close()
        proc.stderr.close()


@pytest.mark.parametrize("name", ["session-start-git-hooks.sh", "session-start-tool-state.sh"])
def test_helpers_respect_caller_deadline_with_slow_interpreter(plugin, name):
    root, env = plugin
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "scripts/tool_state_paths.py").write_text("print('{}')\n")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python3"
    python.write_text(f'#!/bin/sh\n/bin/sleep 3\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    proc, elapsed = run_hook(plugin, name, PATH=str(bin_dir) + ":" + env["PATH"],
                             BUILD_LOOP_GIT_HOOKS_DEADLINE_MS="1500")
    assert elapsed < 2, f"{name} exceeded its 2s host deadline: {elapsed:.3f}s"
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_startup_closeout_uses_its_shorter_host_deadline(plugin):
    proc, elapsed = run_hook(plugin, "closeout.sh", "session-start",
                             TEST_HELPER_DELAY="4", BUILD_LOOP_CLOSEOUT_DEADLINE_MS="999999")
    assert elapsed < 3, f"startup closeout exceeded its 3s host deadline: {elapsed:.3f}s"
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_tool_state_open_stdin_is_bounded(plugin):
    root, env = plugin
    (root / "scripts/tool_state_paths.py").write_text("print('{}')\n")
    proc = subprocess.Popen(["/bin/bash", str(root / "hooks/session-start-tool-state.sh")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=root, env=env)
    try:
        proc.stdin.write('{"session_id":')
        proc.stdin.flush()
        assert proc.wait(timeout=2) == 0
        assert json.loads(proc.stdout.read()) == {}
    finally:
        proc.stdin.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=2)
        proc.stdout.close()
        proc.stderr.close()


def test_memory_bootstrap_bounds_check_and_install_together(plugin):
    root, _ = plugin
    (root / "scripts/install_memory.py").write_text(
        "import json, sys, time\n"
        "if '--check' in sys.argv:\n"
        " time.sleep(0.2); print(json.dumps({'exists': False}))\n"
        "else:\n"
        " time.sleep(8)\n"
    )
    proc, elapsed = run_hook(plugin, "session-start-memory.sh")
    assert proc.returncode == 0
    assert elapsed < 6, f"memory bootstrap exceeded its host deadline: {elapsed:.3f}s"


def test_memory_bootstrap_still_installs_when_missing(plugin):
    root, _ = plugin
    marker = root / "installed"
    (root / "scripts/install_memory.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "if '--check' in sys.argv: print(json.dumps({'exists': False}))\n"
        f"else: Path({str(marker)!r}).write_text('seeded')\n"
    )
    proc, _ = run_hook(plugin, "session-start-memory.sh")
    assert proc.returncode == 0
    assert marker.read_text() == "seeded"


def test_memory_bootstrap_retries_partial_store_without_overwriting(plugin):
    root, _ = plugin
    dest = root / "memory"
    (root / "scripts/install_memory.py").write_text(
        "import json, sys, time, os\nfrom pathlib import Path\n"
        f"dest = Path({str(dest)!r})\n"
        "if '--check' in sys.argv:\n"
        " print(json.dumps({'exists': dest.exists(), 'files': {'seed': {'exists': (dest/'seed').exists()}}}))\n"
        "else:\n"
        " dest.mkdir(exist_ok=True)\n"
        " if not (dest/'personal').exists(): (dest/'personal').write_text('preserve')\n"
        " time.sleep(float(os.environ.get('TEST_HELPER_DELAY','0')))\n"
        " (dest/'seed').write_text('complete')\n"
    )
    first, _ = run_hook(plugin, "session-start-memory.sh",
                        BUILD_LOOP_MEMORY_DEADLINE_MS="500", TEST_HELPER_DELAY="8")
    assert first.returncode == 0 and dest.is_dir()
    assert not (dest / "seed").exists()
    second, _ = run_hook(plugin, "session-start-memory.sh")
    assert second.returncode == 0
    assert (dest / "seed").read_text() == "complete"
    assert (dest / "personal").read_text() == "preserve"


@pytest.mark.parametrize('cancel', [None, 'SIGTERM', 'SIGINT', 'SIGHUP'])
def test_resistant_worker_group_is_killed_on_timeout_or_cancel(plugin, cancel):
    import signal
    root, env = plugin
    pidfile = root / 'resistant.json'
    worker = root / 'resistant.py'
    worker.write_text(
        'import os, signal, subprocess, sys, json, time\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f'open({str(pidfile)!r}, "w").write(json.dumps([os.getpid(), child.pid]) + "\\n")\n'
        'time.sleep(30)\n'
    )
    wrapper = root / 'resistant.sh'
    wrapper.write_text(
        f'. "{root}/hooks/_bounded_hook.sh"\n'
        'bl_bound_hook 1200 1200 advisory "$BASH" "$0"\n'
        f'exec "{sys.executable}" "{worker}"\n'
    )
    p = subprocess.Popen(['/bin/bash', str(wrapper)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    pids = []
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                record = pidfile.read_text()
                if record.endswith('\n'):
                    pids = json.loads(record)
                    break
            except FileNotFoundError:
                pass
            time.sleep(.005)
        assert pids, 'worker did not start'
        started = time.monotonic()
        if cancel:
            p.send_signal(getattr(signal, cancel))
        out, err = p.communicate(timeout=2)
        assert p.returncode == 0 and not err
        assert out == (b'' if cancel else b'{}')
        assert time.monotonic() - started < (1 if cancel else 2)
        for pid in pids:
            # An orphan zombie has stopped executing; init owns its reaping.
            status = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
            assert not status or status.startswith('Z'), f'worker {pid} survived: {status}'
    finally:
        if pids:
            try:
                os.killpg(pids[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
        if p.poll() is None:
            p.kill()
            p.wait()


def test_timeout_preserves_intentionally_detached_worker(plugin):
    import signal
    root, env = plugin
    pidfile = root / 'detached.pid'
    worker = root / 'detach.py'
    worker.write_text(
        'import subprocess, sys, time\n'
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
        'start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
        f'open({str(pidfile)!r}, "w").write(str(p.pid))\n'
        'time.sleep(30)\n'
    )
    wrapper = root / 'detach.sh'
    wrapper.write_text(f'. "{root}/hooks/_bounded_hook.sh"\n'
                       'bl_bound_hook 1200 1200 advisory "$BASH" "$0"\n'
                       f'exec "{sys.executable}" "{worker}"\n')
    try:
        result = subprocess.run(['/bin/bash', str(wrapper)], stdin=subprocess.DEVNULL,
                                capture_output=True, env=env, timeout=3)
        assert result.returncode == 0 and result.stdout == b'{}'
        os.kill(int(pidfile.read_text()), 0)
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_timeout_before_child_sets_session_kills_direct_pid(plugin):
    root, env = plugin
    pidfile = root / 'before-setsid.pid'
    supervisor = root / 'hooks/_bounded_hook.sh'
    # Deterministically widen the fork/setsid window in the copied fixture.
    supervisor.write_text(supervisor.read_text().replace(
        '            POSIX::setsid();',
        '            open(my $ready, ">", $ENV{TEST_PRE_SETSID_PID}) or die $!;\n'
        '            print $ready "$$\\n"; close $ready;\n'
        '            Time::HiRes::sleep(0.5);\n'
        '            POSIX::setsid();'))
    wrapper = root / 'race.sh'
    wrapper.write_text(f'. "{supervisor}"\n'
                       'bl_bound_hook 150 150 advisory "$BASH" "$0"\n'
                       'printf "{}"\n')
    result = subprocess.run(['/bin/bash', str(wrapper)], stdin=subprocess.DEVNULL,
                            capture_output=True, env=dict(env, TEST_PRE_SETSID_PID=str(pidfile)), timeout=2)
    assert result.returncode == 0 and result.stdout == b'{}'
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_reaped_leader_does_not_leave_resistant_descendant(plugin):
    import signal
    root, _ = plugin
    pidfile = root / 'descendant.pid'
    child = (
        'import signal,time,os\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        f'open({str(pidfile)!r}, "w").write(str(os.getpid()))\n'
        'time.sleep(30)\n'
    )
    try:
        result = run_gate(plugin, f'import subprocess,sys\nsubprocess.Popen([sys.executable, "-c", {child!r}])\n')
        assert json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'
        status = subprocess.run(['ps', '-o', 'stat=', '-p', pidfile.read_text()], capture_output=True, text=True).stdout.strip()
        assert not status or status.startswith('Z')
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_cancellation_between_read_and_publish_discards_completed_output(plugin):
    root, _ = plugin
    supervisor = root / 'hooks/_bounded_hook.sh'
    # Deliver TERM in the narrow window after successful wait/read, immediately
    # before the publication check; no timing race in the regression fixture.
    supervisor.write_text(supervisor.read_text().replace(
        '            last if $cancelled;',
        '            kill "TERM", $$ if $reaped && $eof;\n'
        '            last if $cancelled;'))
    result = run_gate(plugin, "print('{}')\n")
    assert result.returncode == 0
    assert result.stdout == ''



def test_output_eof_does_not_spin_while_child_still_runs(plugin):
    root, env = plugin
    supervisor = root / 'hooks/_bounded_hook.sh'
    observed = root / 'eof-poll-observed'
    select_line = '            my $n = select(my $rout = $rin, undef, undef, $left < 0.02 ? $left : 0.02);'
    # Instrument the actual select result in the copied supervisor. A closed
    # output fd is perpetually readable; after EOF we must instead sleep while
    # waiting for the still-running child. This detects the first busy poll.
    supervisor.write_text(supervisor.read_text().replace(select_line, select_line + '\n' +
        '            if ($eof) {\n'
        '                open(my $audit, ">", $ENV{TEST_EOF_POLL}) or die $!;\n'
        '                print $audit "observed"; close $audit;\n'
        '                die "EOF pipe remained readable" if $n > 0;\n'
        '            }'))
    # Keep the child alive until the parent actually polls after EOF; this
    # handshake avoids relying on a short sleep under a loaded scheduler.
    result = run_gate(plugin,
                      "import os,time\nos.close(1)\nend=time.monotonic()+2\n"
                      "while not os.path.exists(os.environ['TEST_EOF_POLL']) and time.monotonic()<end: time.sleep(.005)\n",
                      TEST_EOF_POLL=str(observed), BUILD_LOOP_BASH_DEADLINE_MS='500')
    assert result.returncode == 0 and result.stderr == ''
    assert observed.read_text() == 'observed'
    assert json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'
