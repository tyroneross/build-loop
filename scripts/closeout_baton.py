# SPDX-License-Identifier: Apache-2.0
"""Claim and reconcile an armed closeout without consuming partial work."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from atomic_io import atomic_write_text


def reconcile(workdir: Path, runner=subprocess.run) -> dict:
    baton = workdir / '.build-loop/closeout/armed.json'
    if not baton.is_file():
        return {}
    with baton.with_suffix('.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'pending': True}
        if not baton.is_file():
            return {}
        original = baton.read_bytes()
        run_id = 'armed-' + hashlib.sha256(original).hexdigest()[:24]
        if (baton.parent / f'{run_id}.completed').is_file():
            return {'status': 'already_reconciled', 'pending': False}
        try:
            result = runner([sys.executable, '-m', 'closeout', '--workdir', str(workdir),
                             '--run-id', run_id, '--source', 'post-push-armed', '--json'],
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).parent)},
                            capture_output=True, text=True, timeout=min(120, max(.1, float(os.environ.get("BUILD_LOOP_START_CLOSEOUT_DEADLINE_MS", "120000")) / 1000)))
            output = json.loads(result.stdout)
            if result.returncode or output.get('error') or output.get('closeout_status') not in {
                'wrote_memory', 'queued_pending_lesson', 'no_durable_lesson'
            }:
                return {'status': 'incomplete', 'pending': True}
            atomic_write_text(baton.parent / f'{run_id}.stdout.json', json.dumps(output) + '\n')
            # Record the exact generation; never unlink a concurrently replaced baton.
            atomic_write_text(baton.parent / f'{run_id}.completed', original.decode('utf-8'))
            return output
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return {'status': 'unavailable', 'pending': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workdir', required=True)
    print(json.dumps(reconcile(Path(parser.parse_args().workdir).resolve())))
