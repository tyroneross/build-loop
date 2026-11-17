# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Exercise the real native SessionStart call against the Python snapshot.

Only this worker is present in the fixture plugin, isolating its actual files,
context and process boundary without adding a public testing command to bl.
"""
import fcntl
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


def git(p, *args):
    return subprocess.check_output(['git', '-C', str(p), *args], text=True).strip()


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    plugin = tmp_path / 'plugin'
    (plugin / 'scripts').mkdir(parents=True)
    for name in ['tool_state_paths.py', 'atomic_io.py']:
        shutil.copy2(ROOT / 'scripts' / name, plugin / 'scripts' / name)
    return repo, plugin


def run(world, native, sid='session/α', env=None):
    repo, plugin = world
    command = ([str(BL), 'hook', 'session-start', '--root', str(plugin)] if native else
               [sys.executable, str(plugin / 'scripts/tool_state_paths.py'), 'snapshot',
                '--workdir', str(repo), '--no-exclude', '--emit-context'])
    clean = {k: v for k, v in os.environ.items() if not k.startswith(('BUILD_LOOP_', 'CLAUDE_', 'RALLY_', 'CODEX_'))}
    clean.update(CLAUDE_PROJECT_DIR=str(repo), BUILD_LOOP_RALLY_POINT_SKIP_WATCH='1')
    clean.update(env or {})
    result = subprocess.run(command, input=json.dumps({'session_id': sid}), text=True,
                            env=clean, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout or '{}')


def state(repo):
    common = Path(git(repo, 'rev-parse', '--git-common-dir'))
    if not common.is_absolute(): common = repo / common
    return common / 'build-loop/tool-state'


def normalize(value):
    value.pop('snapshot_at')
    return value


@pytest.mark.parametrize('case', ['absent', 'dirs', 'hooks', 'linked', 'subdir', 'symlink', 'unicode', 'mode'])
def test_snapshot_matches_python_and_report_reader(world, tmp_path, case):
    repo, plugin = world
    if case in ('dirs', 'mode'):
        (repo / '.build-loop').mkdir()
        (repo / '.rally').mkdir()
    if case == 'linked':
        git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
            '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'init')
        linked = tmp_path / 'linked'
        git(repo, 'worktree', 'add', '-qb', 'test', str(linked))
        repo = linked
    if case == 'subdir':
        repo = repo / 'child'; repo.mkdir()
    if case == 'symlink':
        link = tmp_path / 'alias'; link.symlink_to(repo); repo = link
    if case == 'hooks':
        hooks = tmp_path / 'custom hooks'; hooks.mkdir()
        git(repo, 'config', 'core.hooksPath', str(hooks))
        for name, data in {'pre-commit': b'# BUILD-LOOP', 'post-commit': b'# rally',
                           'ignored.sample': b'rally', 'other': b'unrelated',
                           'old': b'rally', 'bad-utf8': b'ra\xfflly',
                           'replacement': 'ra�lly'.encode(), 'dotless': 'buıld-loop'.encode(),
                           'dotted': 'buİld-loop'.encode()}.items():
            path = hooks / name; path.write_bytes(data)
            if name == 'old': os.utime(path, (1, 1))
    world = repo, plugin
    sid = ' \u001c世界/α\t ' if case == 'unicode' else 'session/α'
    want = run(world, False, sid)
    files = {p.name: normalize(json.loads(p.read_text())) for p in state(repo).glob('*.json')}
    if case == 'mode':
        for p in state(repo).glob('*.json'): p.chmod(0o600)
    got = run(world, True, sid)
    assert got == want
    assert {p.name: normalize(json.loads(p.read_text())) for p in state(repo).glob('*.json')} == files
    if case == 'mode':
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in state(repo).glob('*.json'))
    report = subprocess.run([sys.executable, str(plugin / 'scripts/tool_state_paths.py'),
                             'report', '--workdir', str(repo), '--session-id', sid.strip(), '--json'],
                            capture_output=True, text=True, check=True)
    assert isinstance(json.loads(report.stdout), dict)
    assert not list(state(repo).glob('*.tmp.*'))
    exclude = Path(git(repo, 'rev-parse', '--git-path', 'info/exclude'))
    if not exclude.is_absolute(): exclude = repo / exclude
    assert 'build-loop: tool state' not in exclude.read_text()


def test_contended_session_lock_preserves_old_file_and_writes_latest(world):
    repo, _ = world
    run(world, False, 'locked')
    path = state(repo) / 'locked.json'
    before = path.read_bytes()
    with (state(repo) / 'locked.json.lock').open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        start = time.monotonic()
        run(world, True, 'locked')
        assert time.monotonic() - start < 3
        assert path.read_bytes() == before
        assert (state(repo) / 'latest.json').read_bytes() != before


def test_native_never_starts_python_snapshot(world, tmp_path):
    repo, _ = world
    bindir = tmp_path / 'bin'; bindir.mkdir()
    called = tmp_path / 'python-called'
    python = bindir / 'python3'
    python.write_text(f'#!/bin/sh\ntouch "{called}"\nexit 1\n'); python.chmod(0o755)
    result = run(world, True, env={'PATH': f'{bindir}:/usr/bin:/bin'})
    assert 'Treat these as tool state' in result['hookSpecificOutput']['additionalContext']
    assert not called.exists()


def test_large_hook_context_retains_supervisor_output_limit(world):
    repo, _ = world
    hooks = repo / '.git/hooks'
    for n in range(300):
        (hooks / (f'hook-{n:03}-' + 'x' * 225)).write_text('rally')
    assert run(world, True) == {}
    assert len((state(repo) / 'latest.json').read_bytes()) > 65536


@pytest.mark.skipif(sys.platform == 'darwin', reason='APFS rejects invalid UTF-8 filenames')
def test_non_utf8_hook_name_falls_back_before_writing(world):
    repo, _ = world
    name = os.fsencode(repo / '.git/hooks') + b'/bad-\xff'
    with open(name, 'wb') as file: file.write(b'rally')
    want = run(world, False)
    before = normalize(json.loads((state(repo) / 'latest.json').read_text()))
    assert run(world, True) == want
    assert normalize(json.loads((state(repo) / 'latest.json').read_text())) == before


@pytest.mark.parametrize("raw_prefix", [b"", b"\xff", b"\xed\xb3\xbf"])
def test_python_worker_surrogate_context_survives_native_merge(world, raw_prefix):
    """Exercise Python surrogateescape JSON on every OS, including APFS."""
    repo, plugin = world
    (repo / '.build-loop').mkdir()
    want = run(world, False)
    context = 'bad-\udcff; quote " and slash \\; emoji \U0001f600'
    response = json.dumps({'hookSpecificOutput': {'additionalContext': context}}).encode()
    response = response.replace(b'bad-', raw_prefix + b'bad-')
    (plugin / 'scripts/stop_closeout.py').write_text(
        f'import sys; sys.stdout.buffer.write({response!r})\n')
    # The host decodes child stdout with replacement before parsing JSON.
    expected_context = json.loads(response.decode('utf-8', errors='replace'))['hookSpecificOutput']['additionalContext']
    want['hookSpecificOutput']['additionalContext'] += '\n\n' + expected_context
    assert run(world, True) == want
