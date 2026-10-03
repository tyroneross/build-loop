"""Compatibility receipts preserve the boundary between claims and artifacts."""
import json
from compatibility_review import check


def packet(root, **overrides):
    path = root / '.build-loop/compatibility/run.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    value = dict(schema_version=1, run_id='run', disposition='no-change', reason='Reviewed transport changes.', evidence=['test output'])
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
    first = check(tmp_path, 'run')
    assert first['status'] == 'complete'
    (tmp_path / 'lesson.md').write_text('Changed evidence.')
    assert check(tmp_path, 'run')['artifact_sha256'] != first['artifact_sha256']
