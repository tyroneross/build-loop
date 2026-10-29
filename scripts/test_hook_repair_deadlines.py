"""Optional startup maintenance must finish before the host cancels it."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def plugin(tmp_path):
    root = tmp_path / 'plugin'
    (root / 'hooks').mkdir(parents=True)
    (root / 'scripts/worktree_reaper').mkdir(parents=True)
    for name in ['_bounded_hook.sh', '_resolve_python.sh',
                 'session-start-worktree-gc.sh', 'session-start-closeout.sh',
                 'session-start-codex-hook-trust.sh']:
        shutil.copy2(ROOT / 'hooks' / name, root / 'hooks' / name)
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('CLAUDE_', 'CODEX_', 'BUILD_LOOP_', 'RALLY_'))}
    env.update(CLAUDE_PLUGIN_ROOT=str(root), CLAUDE_PROJECT_DIR=str(root))
    return root, env


def invoke(plugin, name, **overrides):
    root, env = plugin
    start = time.monotonic()
    result = subprocess.run(['bash', str(root / 'hooks' / name)], cwd=root,
                            env=dict(env, **overrides), capture_output=True,
                            text=True, input='{}', timeout=5)
    return result, time.monotonic() - start


@pytest.mark.parametrize('name,target,limit', [
    ('session-start-worktree-gc.sh', 'scripts/worktree_reaper/__main__.py', 2),
    ('session-start-closeout.sh', 'scripts/surface_pending_lessons.py', 3),
])
def test_slow_optional_maintenance_finishes_before_host(plugin, name, target, limit):
    root, _ = plugin
    (root / target).write_text('import time\ntime.sleep(10)\n')
    result, elapsed = invoke(plugin, name)
    assert result.returncode == 0
    assert elapsed < limit
    assert json.loads(result.stdout) == {}


def test_worktree_report_still_completes_when_fast(plugin):
    root, _ = plugin
    (root / 'scripts/worktree_reaper/__main__.py').write_text('print("{\\"reported\\": true}")\n')
    result, _ = invoke(plugin, 'session-start-worktree-gc.sh')
    assert result.returncode == 0
    assert 'reported' in (root / '.build-loop/worktree-gc-last.txt').read_text()


def test_closeout_keeps_armed_baton_when_cancelled(plugin):
    root, _ = plugin
    (root / '.build-loop/closeout').mkdir(parents=True)
    baton = root / '.build-loop/closeout/armed.json'
    baton.write_text('{}')
    (root / 'scripts/closeout.py').write_text('import time\ntime.sleep(10)\n')
    result, elapsed = invoke(plugin, 'session-start-closeout.sh',
                             BUILD_LOOP_START_CLOSEOUT_DEADLINE_MS='300')
    assert result.returncode == 0 and elapsed < 2
    assert baton.read_text() == '{}'


def test_import_delay_is_bounded_before_watchdog_modules_load(plugin):
    root, _ = plugin
    modules = root / 'perl-modules/JSON'
    modules.mkdir(parents=True)
    (modules / 'PP.pm').write_text('package JSON::PP; BEGIN { sleep 10; } 1;\n')
    result, elapsed = invoke(plugin, 'session-start-closeout.sh',
                             PERL5LIB=str(modules.parent))
    assert result.returncode == 0
    assert elapsed < 1.8
    assert json.loads(result.stdout) == {}


def test_slow_trust_preflight_returns_before_host_deadline(plugin):
    root, _ = plugin
    (root / '.codex').mkdir()
    (root / '.codex/hooks.json').write_text('{}')
    (root / 'scripts/codex_hook_trust_check.py').write_text('import time; time.sleep(10)\n')
    result, elapsed = invoke(plugin, 'session-start-codex-hook-trust.sh',
                             BUILD_LOOP_TRUST_CHECK_DEADLINE_MS='300')
    assert result.returncode == 0 and elapsed < 2
    assert json.loads(result.stdout) == {}
