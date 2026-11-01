# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class HookReliabilityTests(unittest.TestCase):
    def test_both_hosts_avoid_startup_maintenance(self):
        removed = ('retrieval', 'worktree-gc', 'git-hooks', 'plugin-drift', 'plugin-heal', 'memory', 'extensions', 'session-start-closeout')
        for name in ('hooks.json', 'codex.json'):
            commands = json.dumps(json.loads((ROOT / 'hooks' / name).read_text())['hooks']['SessionStart'])
            for item in removed:
                self.assertNotIn(item, commands)

    def test_failed_architecture_scan_does_not_publish_freshness(self):
        spec = importlib.util.spec_from_file_location('arch_worker', ROOT / 'hooks/_arch_scan_bg.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            (root / 'scripts/architecture_freshness.py').touch()
            with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run:
                self.assertEqual(module.main(['--workdir', tmp]), 1)
                self.assertEqual(run.call_count, 1)

    def test_concurrent_exclude_updates_do_not_lose_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / 'exclude')
            code = 'from pathlib import Path; import sys; from tool_state_paths import _append_to_exclude; _append_to_exclude(Path(sys.argv[1]), [sys.argv[2]])'
            env = {**os.environ, 'PYTHONPATH': str(ROOT / 'scripts')}
            jobs = [subprocess.Popen([sys.executable, '-c', code, target, f'tool-{i}'], env=env) for i in range(6)]
            self.assertEqual([j.wait(timeout=10) for j in jobs], [0] * 6)
            text = Path(target).read_text()
            for i in range(6):
                self.assertEqual(text.count(f'/tool-{i}/'), 1)

    def test_stop_marker_idempotency_is_inside_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'hooks').mkdir()
            script = root / 'hooks/stop_finalize.sh'
            shutil.copy(ROOT / 'scripts/hooks/stop_finalize.sh', script)
            state = root / '.build-loop/state.json'
            state.parent.mkdir()
            state.write_text(json.dumps({'phase': 'report', 'runs': []}))
            payload = json.dumps({'cwd': tmp, 'session_id': 'same-session'})
            jobs = [subprocess.Popen(['bash', str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) for _ in range(4)]
            for job in jobs:
                out, _ = job.communicate(payload, timeout=15)
                self.assertNotIn('all F-criteria passed', out)
            self.assertEqual(len(json.loads(state.read_text())['runs']), 1)
