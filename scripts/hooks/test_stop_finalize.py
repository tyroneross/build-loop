"""Stop event transport must not depend on exec argument/environment limits."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run_finalize(tmp_path, padding='', overrides=None):
    plugin = tmp_path / 'plugin'
    (plugin / 'scripts/hooks').mkdir(parents=True)
    shutil.copy2(ROOT / 'scripts/hooks/stop_finalize.sh', plugin / 'scripts/hooks/stop_finalize.sh')
    (plugin / 'hooks').mkdir()
    shutil.copy2(ROOT / 'hooks/_bounded_hook.sh', plugin / 'hooks/_bounded_hook.sh')
    release = tmp_path / 'release.json'
    # Stub the facade: never contact a real Rally room.
    (plugin / 'scripts/agent_rally.py').write_text(
        f'import json,sys\nfrom pathlib import Path\nPath({str(release)!r}).write_text(json.dumps(sys.argv[1:]))\n')
    project = tmp_path / 'project'
    (project / '.build-loop').mkdir(parents=True)
    state = project / '.build-loop/state.json'
    state.write_text(json.dumps({'phase': 'report', 'runs': []}))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE_', 'CODEX_', 'RALLY_', 'BUILD_LOOP_', '_BL_'))}
    env.pop('CURSOR_SESSION_ID', None)
    env['CLAUDE_PROJECT_DIR'] = str(project)
    env.update(overrides or {})
    payload = {'cwd': str(project), 'session_id': 'transport-session', 'padding': padding}
    result = subprocess.run(['/bin/bash', str(plugin / 'scripts/hooks/stop_finalize.sh')],
                            input=json.dumps(payload), text=True, capture_output=True,
                            env=env, cwd=tmp_path, timeout=5)
    assert result.returncode == 0 and result.stderr == ''
    assert 'systemMessage' in json.loads(result.stdout)
    assert json.loads(state.read_text())['runs'][0]['session_id'] == 'transport-session'
    deadline = time.monotonic() + 2
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    args = json.loads(release.read_text())
    assert args[args.index('--session-id') + 1] == 'transport-session'
    assert args[args.index('--workdir') + 1] == str(project)

    return args


@pytest.mark.parametrize('padding', ['', 'x' * 2_000_000], ids=['small', 'large'])
def test_payload_reaches_release_and_lifecycle_recorder(tmp_path, padding):
    _run_finalize(tmp_path, padding)


@pytest.mark.parametrize('overrides,expected', [
    ({'CODEX_SESSION_ID': 'codex-session'}, 'codex'),
    ({'BUILD_LOOP_RALLY_TOOL': 'codex', 'RALLY_HOST_FAMILY': 'claude_code'}, 'codex'),
    ({'RALLY_TOOL_ID': '  easy-terminal:ExactActor  ', 'BUILD_LOOP_RALLY_TOOL': 'codex',
      'RALLY_HOST_FAMILY': 'claude_code'}, 'easy-terminal:ExactActor'),
    ({'RALLY_TOOL_ID': ' ', 'BUILD_LOOP_RALLY_TOOL': 'cursor:ExactActor',
      'RALLY_HOST_FAMILY': 'claude_code'}, 'cursor:ExactActor'),
])
def test_legacy_stop_release_host_precedence(tmp_path, overrides, expected):
    args = _run_finalize(tmp_path, overrides=overrides)
    assert args[args.index('--tool') + 1] == expected
