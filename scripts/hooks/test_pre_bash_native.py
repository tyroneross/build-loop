"""Native dispatcher entry preserves supported recovery without executing policy."""
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('mode,pid_reused',
    [(m, False) for m in ['absent', 'stale', 'off', 'fresh', 'fallback75', 'exec126', 'exec127', 'deny2']]
    + [(m, True) for m in ['fresh', 'fallback75', 'exec126', 'exec127', 'deny2']])
def test_pre_bash_native_freshness_and_recovery(tmp_path, mode, pid_reused):
    hook = tmp_path / 'scripts/hooks/pre_bash_dispatch.sh'
    hook.parent.mkdir(parents=True)
    hook.write_bytes((ROOT / 'scripts/hooks/pre_bash_dispatch.sh').read_bytes())
    bounded = tmp_path / 'hooks/_bounded_hook.sh'
    bounded.parent.mkdir()
    bounded.write_text("bl_bound_hook() { printf '{\"fallback\":true}'; exit 0; }\n")
    native = tmp_path / 'bin/native/bl'
    native.parent.mkdir(parents=True)
    (tmp_path / 'crates').mkdir()
    (tmp_path / 'crates/SOURCES.digest').write_text('a\n')
    native.with_name('bl.sources').write_text('b\n' if mode == 'stale' else 'a\n')
    if mode != 'absent':
        rc = {'fallback75': 75, 'exec126':126, 'exec127':127, 'deny2':2}.get(mode, 0)
        body = "printf '{\"native\":true}'\n" if mode in ('fresh', 'deny2') else ''
        native.write_text(f'#!/bin/sh\n{body}exit {rc}\n')
        native.chmod(0o755)
    env = {'PATH':'/usr/bin:/bin', 'HOME':str(tmp_path)}
    if mode == 'off': env['BUILD_LOOP_NATIVE_HOOKS'] = 'off'
    if pid_reused:
        shim = tmp_path / 'pid-reused.sh'
        shim.write_text('kill() { if [ "$1" = -0 ]; then return 0; fi; builtin kill "$@"; }\n')
        env['BASH_ENV'] = str(shim)
    result = subprocess.run(['/bin/bash', str(hook)], input=b'fixture', env=env, capture_output=True, timeout=5)
    assert result.returncode == (2 if mode == 'deny2' else 0)
    assert result.stdout == (b'{"native":true}' if mode in ('fresh','deny2') else b'{"fallback":true}')
    assert result.stderr == b''

NATIVE = Path(__import__('os').environ.get('BL_PRE_BASH_BIN') or ROOT / 'target/release/bl')

@pytest.fixture
def real_launcher(tmp_path):
    import shutil
    if not NATIVE.is_file(): pytest.skip('native binary not built')
    hook = tmp_path / 'scripts/hooks/pre_bash_dispatch.sh'
    hook.parent.mkdir(parents=True)
    hook.write_bytes((ROOT / 'scripts/hooks/pre_bash_dispatch.sh').read_bytes())
    bounded = tmp_path / 'hooks/_bounded_hook.sh'
    bounded.parent.mkdir()
    bounded.write_bytes((ROOT / 'hooks/_bounded_hook.sh').read_bytes())
    native = tmp_path / 'bin/native/bl'
    native.parent.mkdir(parents=True)
    shutil.copyfile(NATIVE, native)
    native.chmod(0o755)
    (tmp_path / 'crates').mkdir()
    for path in ['crates/SOURCES.digest', 'bin/native/bl.sources']:
        (tmp_path / path).write_text('fixture\n')
    return hook, native


def _children(pid):
    result = subprocess.run(['pgrep', '-P', str(pid)], capture_output=True, text=True)
    return [int(p) for p in result.stdout.split()]


@pytest.mark.parametrize('signal_name', ['SIGTERM', 'SIGINT', 'SIGHUP'])
def test_host_cancels_original_launcher_pid_and_cleans_native_group(real_launcher, tmp_path, signal_name):
    import os
    import signal
    import time
    hook, _ = real_launcher
    env = {'PATH':'/usr/bin:/bin', 'HOME':str(tmp_path), 'BUILD_LOOP_BASH_DEADLINE_MS':'5000'}
    child = subprocess.Popen(['/bin/bash', str(hook)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    tracked = []
    try:
        until = time.monotonic() + 2
        while time.monotonic() < until:
            native = _children(child.pid)
            workers = _children(native[0]) if native else []
            if workers:
                tracked = native + workers
                break
            time.sleep(.005)
        assert tracked, 'native policy child did not start'
        os.kill(child.pid, getattr(signal, signal_name))
        child.wait(timeout=2)  # stdin stays open until after cancellation.
        stdout, stderr = child.communicate(timeout=2)
        assert child.returncode == 0 and stdout == b'' and stderr == b''
        for pid in tracked:
            with pytest.raises(ProcessLookupError): os.kill(pid, 0)
    finally:
        for pid in [child.pid, *tracked]:
            try: os.kill(pid, signal.SIGTERM)
            except ProcessLookupError: pass
        child.communicate(timeout=2)


@pytest.mark.parametrize('command,off', [('printf safe', False), ('security find-generic-password -s fixture', False),
                                        ('security find-generic-password -s fixture', True)])
def test_native_supervisor_preserves_actual_python_policy(real_launcher, tmp_path, command, off):
    import json
    hook, native = real_launcher
    # Only policy metadata is evaluated. No command in the event is executed.
    (hook.parent / 'pre_bash_fastpath.py').symlink_to(ROOT / 'scripts/hooks/pre_bash_fastpath.py')
    env = {'PATH':'/usr/bin:/bin', 'HOME':str(tmp_path), 'CLAUDE_PLUGIN_ROOT':str(ROOT)}
    if off: env['BUILD_LOOP_HOOKS'] = 'off'
    event = json.dumps({'tool_name':'Bash', 'tool_input':{'command':command}, 'cwd':str(tmp_path)}).encode()
    legacy = subprocess.run(['/bin/bash', str(hook)], input=event, env={**env,'BUILD_LOOP_NATIVE_HOOKS':'off'},
                            cwd=tmp_path, capture_output=True, timeout=5)
    result = subprocess.run([str(native), 'hook', 'pre-bash'], input=event, env=env,
                            cwd=tmp_path, capture_output=True, timeout=5)
    assert (result.returncode, result.stdout, result.stderr) == (legacy.returncode, legacy.stdout, legacy.stderr)
    decision = json.loads(result.stdout).get('hookSpecificOutput', {}).get('permissionDecision')
    assert decision == ('deny' if command.startswith('security') else None)
