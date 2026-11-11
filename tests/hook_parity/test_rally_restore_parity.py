# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Run the real SessionStart restore against its unchanged Python oracle."""
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import time

import pytest
from .session_start_harness import Case, build_world, _write_cache, collect_spawns, REAL_PYTHON

ROOT = Path(__file__).resolve().parents[2]
BL = Path(os.environ.get('BL_BIN', ROOT / 'target/debug/bl'))


def world(tmp_path, *, kind='project', overrides=None):
    w = build_world(tmp_path / 'world', Case('restore', kind=kind, env={
        'RALLY_SESSION_ID': 'managed-restore', 'RALLY_OBSERVER_PID': '4242',
        'BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS': '0',
        'BUILD_LOOP_RALLY_POINT_SKIP_WATCH': '1', **(overrides or {}),
    }))
    plugin = w.root / 'plugin'
    (plugin / 'scripts').mkdir(parents=True)
    (plugin / 'scripts/rally_point').symlink_to(ROOT / 'scripts/rally_point')
    w.env['CLAUDE_PLUGIN_ROOT'] = str(plugin)
    # All background Python calls are recorded but not executed by the harness.
    rally = w.root / 'bin/rally'
    worker = w.root / 'bin/rally_worker.py'
    worker.write_text(f'''#!{REAL_PYTHON}
import json, os, sys, time
from pathlib import Path
root=Path({str(w.root)!r})
if len(sys.argv)==1:
    with (root/'probes').open('a') as out: out.write('probe\\n')
    time.sleep(float(os.environ.get('HELP_DELAY','0')))
    if os.environ.get('SLUG_AFTER_PROBE'):
        for file in (root/'cache').glob('*.json'):
            data=json.loads(file.read_text()); data['raw']['app_slug']=os.environ['SLUG_AFTER_PROBE']; file.write_text(json.dumps(data))
    print('rally enter --tool; rally say <kind>; rally whoami', file=sys.stderr if os.environ.get('HELP_STDERR') else sys.stdout)
else:
    with (root/'calls').open('a') as out:
        out.write(json.dumps([sys.argv[1:],os.getcwd(),os.environ.get('RALLY_SESSION_ID'),os.environ.get('RALLY_OBSERVER_PID')])+'\\n')
    time.sleep(float(os.environ.get('ROOM_DELAY','0')))
    print((root/'response').read_text())
    sys.exit(int(os.environ.get('ROOM_EXIT','0')))
''')
    # Keep the usage probe deterministic under host load; spawning an extra
    # Python interpreter here would test fixture startup, not Rally semantics.
    rally.write_text(f'''#!/bin/sh
if [ "$#" = 0 ] && [ -z "${{SLUG_AFTER_PROBE:-}}" ]; then
    printf 'probe\\n' >> {shlex.quote(str(w.root / 'probes'))}
    if [ -n "${{HELP_DELAY:-}}" ]; then sleep "$HELP_DELAY"; fi
    if [ -n "${{HELP_STDERR:-}}" ]; then
        printf 'rally enter --tool; rally say <kind>; rally whoami\\n' >&2
    else
        printf 'rally enter --tool; rally say <kind>; rally whoami\\n'
    fi
    exit 0
fi
exec {shlex.quote(REAL_PYTHON)} {shlex.quote(str(worker))} "$@"
''')
    rally.chmod(0o755)
    _write_cache(w, 0)
    return w


def run(w, native):
    command = ([str(BL), 'hook', 'session-start', '--root', w.env['CLAUDE_PLUGIN_ROOT']] if native else
        [REAL_PYTHON, str(ROOT / 'scripts/rally_point/hooks.py'), 'session-start-safe',
         '--workdir', str(w.proj), '--verbose', '--tool', 'claude_code', '--session-id', 'restore-session'])
    result = subprocess.run(command, input='{"session_id":"restore-session"}', env=w.env,
                            cwd=w.cwd, text=True, capture_output=True, timeout=5)
    (w.root / ('native-stderr' if native else 'oracle-stderr')).write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout or '{}')


def payload(squads, nested=False):
    room = {'squads': squads}
    return {'ok': True, 'product': 'rally', 'schema': 'agent-rally.command.room.v1',
            'data': {'room': {'room': room} if nested else room}}


@pytest.mark.parametrize('case', ['peers', 'nested', 'self', 'inactive', 'malformed', 'wrong-schema',
    'unknown', 'nested-unknown', 'nonzero', 'unicode', 'missing-tool', 'non-list', 'override', 'empty', 'large'])
def test_restore_matches_oracle_and_eliminates_foreground_python(tmp_path, case):
    w = world(tmp_path, overrides={'RALLY_TOOL_ID': 'easy-terminal:outer'} if case == 'override' else {})
    actor = 'easy-terminal:outer' if case == 'override' else 'claude_code:restore-session'
    squads = [{'tool': actor, 'status': 'active'}, {'tool': 'codex:peer', 'status': 'active'}]
    if case == 'self': squads = squads[:1]
    if case == 'inactive': squads[1]['status'] = 'idle'
    if case == 'missing-tool': squads = [{'status': 'active'}, None, 2]
    if case == 'non-list': squads = {'tool': 'peer'}
    if case == 'empty': squads = []
    response = payload(squads, nested=case == 'nested')
    if case == 'large': response['padding'] = 'x' * 70000
    if case == 'wrong-schema': response['schema'] = 'wrong'
    if case == 'unknown': response['data']['outcome_unknown'] = True
    if case == 'nested-unknown': response['extra'] = [{'code': 'outcome_unknown'}]
    if case == 'nonzero': w.env['ROOM_EXIT'] = '1'
    if case == 'unicode':
        for file in (w.root / 'cache').glob('*.json'):
            cache = json.loads(file.read_text()); cache['raw']['app_slug'] = 'répo 🔧'; file.write_text(json.dumps(cache))
    (w.root / 'response').write_text('not-json' if case == 'malformed' else json.dumps(response))
    expected = run(w, False)
    actual = run(w, True)
    assert actual == expected, (w.root / 'oracle-stderr').read_text()
    calls = [json.loads(line) for line in (w.root / 'calls').read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0] == calls[1] == [['room', '--json', '--readers'], str(w.proj), 'managed-restore', '4242']
    spawns, background = collect_spawns(w)
    assert not any('session-start-safe' in row[0] for row in spawns)
    assert background == ['session_probe.py', 'hooks.py', 'peer_collision.py']


@pytest.mark.parametrize('case', ['stale', 'missing', 'invalid', 'no-session', 'internal', 'negative-deadline', 'nan-deadline', 'bad-deadline'])
def test_unsupported_states_use_python(tmp_path, case):
    w = world(tmp_path)
    (w.root / 'response').write_text(json.dumps(payload([])))
    if case == 'stale': _write_cache(w, 120)
    if case == 'missing':
        for p in (w.root / 'cache').glob('*.json'): p.unlink()
    if case == 'invalid':
        for p in (w.root / 'cache').glob('*.json'): p.write_text('{')
    if case == 'no-session': w.env.pop('RALLY_SESSION_ID')
    if case.endswith('-deadline'): w.env['BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS'] = {'negative-deadline': '-1', 'nan-deadline': 'nan', 'bad-deadline': 'bad'}[case]
    if case == 'internal': w.env['BUILD_LOOP_BRIDGE_INTERNAL_ONLY'] = '1'
    run(w, True)
    spawns, _ = collect_spawns(w)
    assert sum('session-start-safe' in row[0] for row in spawns) == 1


def test_inner_deadline_keeps_background_and_does_not_replay(tmp_path):
    w = world(tmp_path, overrides={'BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS': '1.2', 'ROOM_DELAY': '3'})
    (w.root / 'response').write_text(json.dumps(payload([{'status': 'active'}])))
    assert run(w, True) == {}
    spawns, background = collect_spawns(w)
    assert len((w.root / 'calls').read_text().splitlines()) == 1
    assert not any('session-start-safe' in row[0] for row in spawns)
    assert background == ['session_probe.py', 'hooks.py', 'peer_collision.py']


def test_outside_git_does_not_read_room(tmp_path):
    w = world(tmp_path, kind='nogit')
    (w.root / 'response').write_text(json.dumps(payload([{'status': 'active'}])))
    assert run(w, False) == run(w, True) == {}
    assert not (w.root / 'calls').exists()
    assert not (w.root / 'probes').exists()


@pytest.mark.parametrize('overrides,actor,stable', [
    ({'BUILD_LOOP_RALLY_SESSION_ID': '  configured  '}, 'claude_code:restore-session', 'configured'),
    ({'RALLY_TOOL_ID': '  terminal:Named Actor  '}, 'terminal:Named Actor', 'managed-restore'),
    ({'RALLY_TOOL_ID': 'x' * 200}, 'x' * 200, 'managed-restore'),
])
def test_identity_precedence_and_whitespace(tmp_path, overrides, actor, stable):
    w = world(tmp_path, overrides=overrides)
    (w.root / 'response').write_text(json.dumps(payload([
        {'tool': actor, 'status': 'active'}, {'tool': 'peer', 'status': 'active'}])))
    expected = run(w, False)
    assert '1 live peer(s)' in expected['hookSpecificOutput']['additionalContext']
    assert run(w, True) == expected
    calls = [json.loads(line) for line in (w.root / 'calls').read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][2] == stable
    collect_spawns(w)


def test_stderr_usage_falls_back_without_selecting_another_binary(tmp_path):
    w = world(tmp_path, overrides={'HELP_STDERR': '1'})
    (w.root / 'response').write_text(json.dumps(payload([{'tool': 'peer', 'status': 'active'}])))
    expected = run(w, False)
    assert '1 live peer(s)' in expected['hookSpecificOutput']['additionalContext']
    assert run(w, True) == expected
    spawns, _ = collect_spawns(w)
    assert sum('session-start-safe' in row[0] for row in spawns) == 1
    assert len((w.root / 'calls').read_text().splitlines()) == 2


def test_short_probe_budget_falls_back_before_native_probe(tmp_path):
    w = world(tmp_path, overrides={'RALLY_HOOK_BUDGET_MS': '1000', 'HELP_DELAY': '0.7'})
    (w.root / 'response').write_text(json.dumps(payload([])))
    assert run(w, False) == {}
    oracle_probes = (w.root / 'probes').read_text().splitlines()
    assert oracle_probes
    (w.root / 'probes').unlink()
    assert run(w, True) == {}
    spawns, background = collect_spawns(w)
    assert sum('session-start-safe' in row[0] for row in spawns) == 1
    assert (w.root / 'probes').read_text().splitlines() == oracle_probes
    assert background == ['session_probe.py', 'hooks.py', 'peer_collision.py']


def test_cache_is_read_again_after_probe(tmp_path):
    w = world(tmp_path, overrides={'SLUG_AFTER_PROBE': 'refreshed-slug'})
    (w.root / 'response').write_text(json.dumps(payload([{'tool': 'peer', 'status': 'active'}])))
    expected = run(w, False)
    _write_cache(w, 0)  # Restore old slug before native probe refreshes it again.
    assert run(w, True) == expected
    assert 'refreshed-slug' in expected['hookSpecificOutput']['additionalContext']
    collect_spawns(w)
