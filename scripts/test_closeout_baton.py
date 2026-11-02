# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from closeout_baton import reconcile


class BatonTests(unittest.TestCase):
    def test_milestone_failure_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baton = root / '.build-loop/closeout/armed.json'
            baton.parent.mkdir(parents=True)
            baton.write_text('{}')
            payload = {'closeout_status': 'no_durable_lesson', 'error': None,
                       'milestone': {'status': 'error', 'error': 'store unavailable'}}
            runner = lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(payload))
            self.assertTrue(reconcile(root, runner).get('pending'))
            self.assertEqual(list(baton.parent.glob('*.completed')), [])
            payload['milestone'] = {'status': 'recorded'}
            self.assertEqual(reconcile(root, runner)['milestone']['status'], 'recorded')
            self.assertEqual(len(list(baton.parent.glob('*.completed'))), 1)

    def test_degraded_zero_exit_keeps_baton(self):
        with tempfile.TemporaryDirectory() as tmp:
            baton = Path(tmp) / '.build-loop/closeout/armed.json'
            baton.parent.mkdir(parents=True)
            baton.write_text('{}')
            out = reconcile(Path(tmp), lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps({'error': 'partial'})))
            self.assertTrue(out['pending'])
            self.assertTrue(baton.exists())

    def test_success_consumes_only_original_baton(self):
        with tempfile.TemporaryDirectory() as tmp:
            baton = Path(tmp) / '.build-loop/closeout/armed.json'
            baton.parent.mkdir(parents=True)
            baton.write_text('{}')
            def run(*args, **kwargs):
                baton.write_text('{"new": true}')
                return subprocess.CompletedProcess([], 0, json.dumps({'closeout_status': 'no_durable_lesson'}))
            reconcile(Path(tmp), run)
            self.assertEqual(json.loads(baton.read_text()), {'new': True})
