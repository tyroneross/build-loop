"""Real Stop launcher parity: output, locked state and exact-session release.

The facade is a recording fixture; these tests never contact a live Rally room.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
BL = Path(os.environ.get('BL_BIN', ROOT / 'target/debug/bl'))
pytestmark = pytest.mark.skipif(not BL.exists(), reason='bl not built')


def fixture(root):
    plugin = root / 'plugin'
    for directory in ['scripts/hooks', 'hooks', 'bin/native', 'crates']:
        (plugin / directory).mkdir(parents=True, exist_ok=True)
    for name in ['scripts/hooks/stop_finalize.sh', 'hooks/_bounded_hook.sh']:
        shutil.copy2(ROOT / name, plugin / name)
    (plugin / 'bin/native/bl').symlink_to(BL.resolve())
    for name in ['bl.sources', 'sources.current']:
        (plugin / 'bin/native' / name).write_text('fixture\n')
    (plugin / 'scripts/agent_rally.py').write_text(
        'import json,os,sys\nfrom pathlib import Path\n'
        'Path(os.environ["STOP_RELEASE"]).write_text(json.dumps(sys.argv[1:]))\n')
    wrapper = root / 'bin'
    wrapper.mkdir()
    python = wrapper / 'python3'
    python.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$STOP_PYTHON_CALLS"\n'
                      f'exec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    project = root / 'project'
    (project / '.build-loop/evals').mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('CLAUDE_', 'CODEX_', 'RALLY_', 'BUILD_LOOP_', '_BL_'))}
    env.pop('CURSOR_SESSION_ID', None)
    env.update(PATH=str(wrapper) + os.pathsep + env['PATH'], STOP_RELEASE=str(root / 'release.json'),
               STOP_PYTHON_CALLS=str(root / 'python-calls'))
    return plugin, project, env


def run(plugin, project, env, event, state, *, native=True):
    state_path = project / '.build-loop/state.json'
    state_path.unlink(missing_ok=True)
    if state is not None:
        state_path.write_text(json.dumps(state))
    release = Path(env['STOP_RELEASE'])
    release.unlink(missing_ok=True)
    calls = Path(env['STOP_PYTHON_CALLS'])
    calls.unlink(missing_ok=True)
    event = {'cwd': str(project), **event}
    proc = subprocess.run(['bash', str(plugin / 'scripts/hooks/stop_finalize.sh')],
                          input=json.dumps(event), text=True, capture_output=True, timeout=15,
                          env={**env, 'BUILD_LOOP_NATIVE_HOOKS': 'on' if native else 'off'})
    expected_release = bool(event.get('session_id')) and not event.get('stop_hook_active')
    deadline = time.monotonic() + (5 if expected_release else .1)
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert release.exists() == expected_release
    released = json.loads(release.read_text()) if release.exists() else None
    result_state = json.loads(state_path.read_text()) if state_path.exists() else None
    if result_state:
        for row in result_state.get('runs', []):
            if isinstance(row, dict) and 'date' in row:
                row['date'] = '<timestamp>'
    return (proc.returncode, proc.stdout, proc.stderr, result_state, released), calls.read_text().splitlines() if calls.exists() else []


CASES = [
    ({}, None),
    ({'session_id': 'ordinary'}, None),
    ({'session_id': 'ordinary'}, {'phase': 'execute'}),
    ({'session_id': 'ordinary', 'agent_id': 'child'}, {'phase': 'report'}),
    ({'session_id': 'ordinary', 'stop_hook_active': True}, {'phase': 'report'}),
    ({'session_id': 'ordinary', 'stop_hook_active': 'false'}, None),
    ({'session_id': 'ordinary'}, {'phase': 'report', 'runs': [{'session_id': 'ordinary'}]}),
    ({'session_id': 'ordinary'}, {'phase': 'report', 'runs': []}),
    ({'session_id': 'quoted-"-α'}, {'phase': 'report', 'runs': []}),
    ({'session_id': 'ordinary', 'padding': 'x' * 2_000_000}, {'phase': 'report'}),
    ({'session_id': 42}, {'phase': 'report', 'runs': []}),
    ({'session_id': 'ordinary'}, {'phase': 'report', 'padding': 'x' * 100_000}),
    ({'session_id': 'ordinary'}, {'phase': None}),
]


@pytest.mark.parametrize('event,state', CASES, ids=[f'case-{n}' for n in range(len(CASES))])
def test_native_and_python_preserve_output_state_and_release(tmp_path, event, state):
    plugin, project, env = fixture(tmp_path)
    reference, _ = run(plugin, project, env, event, state, native=False)
    actual, _ = run(plugin, project, env, event, state)
    assert actual == reference
    assert actual[0] == 0 and actual[2] == ''
    if actual[4]:
        args = actual[4]
        assert args[args.index('--session-id') + 1] == str(event['session_id'])
        assert args[args.index('--workdir') + 1] == str(project)


@pytest.mark.parametrize('blocking,held', [(False, False), (True, False), (True, True)])
def test_active_report_keeps_advisory_and_explicit_block_contract(tmp_path, blocking, held):
    plugin, project, env = fixture(tmp_path)
    (project / '.build-loop/evals/2026-scorecard.md').write_text(
        'F_tests verdict: fail\n' + ('## Held\nF_tests\n' if held else ''))
    env['BUILD_LOOP_STOP_HOOK_BLOCKING'] = '1' if blocking else '0'
    result, calls = run(plugin, project, env, {'session_id': 'active'}, {'phase': 'report'})
    response = json.loads(result[1])
    assert (response.get('decision') == 'block') == (blocking and not held)
    assert len(result[3]['runs']) == 1
    assert '/dev/fd/3' not in calls  # native preflight
    assert calls.count('') == 1  # one writer/scorecard/encoder interpreter


@pytest.mark.parametrize('mode', ['fresh', 'stale', 'absent', 'off'])
def test_freshness_fallback_and_noop_interpreter_count(tmp_path, mode):
    plugin, project, env = fixture(tmp_path)
    if mode == 'stale':
        (plugin / 'bin/native/bl.sources').write_text('stale\n')
    if mode == 'absent':
        (plugin / 'bin/native/bl').unlink()
    result, calls = run(plugin, project, env, {}, None, native=mode != 'off')
    assert result[:3] == (0, '{}\n', '')
    assert calls == ([] if mode == 'fresh' else ['/dev/fd/3'])


def test_unknown_arguments_do_not_consume_stdin():
    proc = subprocess.Popen([str(BL), 'hook', 'stop-context', '--unknown'], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.wait(timeout=2) == 75
    finally:
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()


def test_older_dangling_scorecard_does_not_hide_latest_failure(tmp_path):
    plugin, project, env = fixture(tmp_path)
    cards = project / '.build-loop/evals'
    dangling = cards / 'older-scorecard.md'
    dangling.symlink_to(cards / 'missing.md')
    os.utime(dangling, (1, 1), follow_symlinks=False)
    (cards / 'latest-scorecard.md').write_text('F_tests verdict: fail\n')
    env['BUILD_LOOP_STOP_HOOK_BLOCKING'] = 'true'
    result, _ = run(plugin, project, env, {'session_id': 'active'}, {'phase': 'report'})
    assert json.loads(result[1])['decision'] == 'block'


def test_stalled_native_input_returns_to_reference_parser():
    proc = subprocess.Popen([str(BL), 'hook', 'stop-context'], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.wait(timeout=5) == 75
        assert proc.stdout.read() == b''
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()
