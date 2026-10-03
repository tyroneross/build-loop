"""Compatibility receipts preserve the boundary between claims and artifacts."""
import json
import hashlib
import subprocess
from unittest.mock import patch
from compatibility_review import check


def packet(root, **overrides):
    path = root / '.build-loop/compatibility/run.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    value = dict(schema_version=1, run_id='run', disposition='not-applicable', reason='Reviewed transport changes.', evidence=['test output'])
    value.update(overrides)
    path.write_text(json.dumps(value))
    return path


def test_missing_invalid_and_deferred(tmp_path):
    assert check(tmp_path, 'run')['status'] == 'error'
    packet(tmp_path, reason='')
    assert check(tmp_path, 'run')['status'] == 'error'
    packet(tmp_path, disposition='deferred')
    assert check(tmp_path, 'run')['status'] == 'pending'
    packet(tmp_path)
    assert check(tmp_path, 'run')['status'] == 'complete'


def test_updated_needs_real_artifacts_and_matching_ids(tmp_path):
    packet(tmp_path, disposition='updated', record_ids=['wire.v1'], registry='registry.json', memory='lesson.md')
    assert check(tmp_path, 'run')['status'] == 'error'
    (tmp_path / 'lesson.md').write_text('Local lesson.')
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps(dict(schema_version=1, records=[])))
    assert check(tmp_path, 'run')['status'] == 'error'
    registry.write_text(json.dumps(dict(schema_version=1, records=[dict(id='wire.v1')])))
    with patch('compatibility_review.validate_with_lsp', return_value={'validated': True}):
        first = check(tmp_path, 'run')
    assert first['status'] == 'complete'
    (tmp_path / 'lesson.md').write_text('Changed evidence.')
    with patch('compatibility_review.validate_with_lsp', return_value={'validated': True}):
        assert check(tmp_path, 'run')['artifact_sha256'] != first['artifact_sha256']


def test_no_change_requires_actual_lsp_validation(tmp_path):
    packet(tmp_path, disposition='no-change', registry='registry.json')
    registry = tmp_path / 'registry.json'
    registry.write_text('{"schema_version":1,"records":[]}')
    digest = hashlib.sha256(registry.read_bytes()).hexdigest()
    with patch('compatibility_review.shutil.which', return_value=None):
        assert 'not on PATH' in check(tmp_path, 'run')['error']
    with patch('compatibility_review.shutil.which', return_value='/tools/rosslabs-lsp'), patch('compatibility_review.subprocess.run') as run:
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({'validated': True, 'registry_sha256': digest}), '')
        result = check(tmp_path, 'run')
        assert result['status'] == 'complete'
        assert run.call_args.args[0] == ['/tools/rosslabs-lsp', 'compat', str(registry.resolve())]
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({'validated': True, 'registry_sha256': 'stale'}), '')
        assert check(tmp_path, 'run')['status'] == 'error'
        run.side_effect = subprocess.TimeoutExpired('rosslabs-lsp', 15)
        assert 'timed out' in check(tmp_path, 'run')['error']
