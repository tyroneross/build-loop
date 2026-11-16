#!/usr/bin/env python3
"""Quality and user-effort comparison, including the real run-writer/Learn seam."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from workflow_evidence import CONTEXT_KEYS, compare, validate_observation


def fixture():
    context = {key: f'fixed-{key}' for key in CONTEXT_KEYS}
    contract = {
        'frozen_at': '2026-11-16T00:00:00Z', 'freeze_evidence': 'manifest.json',
        'manifest_sha256': 'a' * 64, 'oracle_sha256': 'b' * 64,
        'baseline_config_sha256': 'c' * 64, 'candidate_config_sha256': 'd' * 64,
        'baseline_kind': 'no_loop', 'baseline_config_evidence': 'baseline.json',
        'context': context, 'minimum_quality_gain': .2, 'minimum_pairs': 2,
        'workflow_revisions': {'baseline': 'native-only-sha', 'candidate': 'compatibility-sha'},
        'cases': [{'case_id': cid, 'task_snapshot': 'snapshot-' + cid, 'split': 'holdout', 'baseline_run_id': cid + '-baseline', 'candidate_run_id': cid + '-candidate'} for cid in ('a', 'b')],
    }
    runs = []
    for case in contract['cases']:
        for arm in ('baseline', 'candidate'):
            runs.append({'run_id': case['case_id'] + '-' + arm, 'outcome': 'pass',
                         'active_experimental_artifacts': [] if arm == 'baseline' else ['compatibility'], 'harness': {'workflow_evaluation': {
                'experiment': 'compatibility', 'arm': arm, 'case_id': case['case_id'],
                'task_snapshot': case['task_snapshot'], 'context': copy.deepcopy(context),
                'workflow_revision': contract['workflow_revisions'][arm],
                'execution_config_sha256': contract[arm + '_config_sha256'], 'trace_sha256': hashlib.sha256((case['case_id'] + arm).encode()).hexdigest(),
                'loop_mode': 'native' if arm == 'baseline' else 'workflow',
                'started_at': '2026-11-16T01:00:00Z', 'completed': True, 'accepted': True,
                'quality': .5 if arm == 'baseline' else .8, 'user_interventions': 0,
                'tokens': 100 if arm == 'baseline' else 200, 'duration_s': 10,
                'evidence': ['grader.json', 'trace.jsonl'],
            }}})
    return contract, runs


def obs(runs, index=1):
    return runs[index]['harness']['workflow_evaluation']


def test_quality_first_accepts_higher_quality_even_with_more_tokens():
    c, runs = fixture()
    result = compare(c, runs, 'compatibility')
    assert result['status'] == 'review_candidate'
    assert result['eligible_for_review']
    assert result['pairs'][0]['tokens_delta'] == 100
    assert 'adoption' in result['evidence_boundary']


@pytest.mark.parametrize('change,reason', [
    ({'quality': .4, 'tokens': 1}, 'quality regression'),
    ({'quality': .5, 'tokens': 1}, 'quality_gain_not_met'),
    ({'user_interventions': 1}, 'increased user effort'),
    ({'completed': False, 'accepted': False}, 'incomplete'),
    ({'accepted': False}, 'unaccepted'),
    ({'quality': None}, 'unknown'),
    ({'user_interventions': None}, 'unknown'),
    ({'started_at': '2026-11-15T00:00:00Z'}, 'predates'),
    ({'workflow_revision': 'other'}, 'revision mismatch'),
])
def test_ineligible_measurements(change, reason):
    c, runs = fixture()
    obs(runs).update(change)
    # No average gain from the other task can hide a per-task regression.
    if change.get('quality') == .5:
        obs(runs, 3)['quality'] = .5
    result = compare(c, runs, 'compatibility')
    assert not result['eligible_for_review']
    assert reason in str(result['reasons'])


@pytest.mark.parametrize('key', CONTEXT_KEYS)
def test_context_mismatch_cannot_select_other_preferences_or_model(key):
    c, runs = fixture()
    obs(runs)['context'][key] = 'different'
    result = compare(c, runs, 'compatibility')
    assert not result['eligible_for_review']
    assert 'context mismatch' in str(result['reasons'])


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'same_run', 'extra', 'posthoc', 'snapshot', 'floor', 'identity'])
def test_assignment_integrity(mutation):
    c, runs = fixture()
    if mutation == 'missing':
        runs.pop()
    elif mutation == 'duplicate':
        runs.append(copy.deepcopy(runs[1])); runs[-1]['run_id'] = 'another'
    elif mutation == 'same_run':
        runs[1]['run_id'] = runs[0]['run_id']
    elif mutation == 'extra':
        runs.append(copy.deepcopy(runs[1])); runs[-1]['run_id'] = 'extra'; obs(runs, -1)['case_id'] = 'extra'
    elif mutation == 'posthoc':
        c['cases'][0]['split'] = 'posthoc'
    elif mutation == 'snapshot':
        obs(runs)['task_snapshot'] = 'changed'
    elif mutation == 'floor':
        c['minimum_pairs'] = 3
    else:
        c['context']['served_model'] = 'unknown'
    assert not compare(c, runs, 'compatibility')['eligible_for_review']


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, -1, 2, 10**1000])
def test_invalid_quality_rejected(value):
    _, runs = fixture()
    obs(runs)['quality'] = value
    with pytest.raises(ValueError):
        validate_observation(obs(runs))


def test_missing_cost_stays_unknown_not_zero():
    c, runs = fixture()
    obs(runs)['tokens'] = None
    result = compare(c, runs, 'compatibility')
    assert result['eligible_for_review']
    assert result['pairs'][0]['tokens_delta'] is None


def test_real_writer_to_learn_and_no_legacy_fallback(tmp_path):
    from learn.runner import _sample_sweep
    c, runs = fixture()
    loop = tmp_path / '.build-loop'
    loop.mkdir()
    (loop / 'config.json').write_text(json.dumps({'autoPromote': True}))
    artifact = loop / 'skills/experimental/compatibility/SKILL.md'
    artifact.parent.mkdir(parents=True)
    artifact.write_text('---\nname: compatibility\n---\nTest fixture')
    log = loop / 'experiments/compatibility.jsonl'
    log.parent.mkdir()
    log.write_text(json.dumps({'event': 'created', 'artifact': 'compatibility',
                               'workflow_contract': c, 'baseline_value': 0, 'target_value': 0}) + '\n')
    for run in runs:
        harness = tmp_path / 'harness.json'
        harness.write_text(json.dumps(run['harness']))
        result = subprocess.run([sys.executable, '-m', 'scripts.write_run_entry',
            '--workdir', str(tmp_path), '--run-id', run['run_id'], '--goal', 'evaluation fixture',
            '--host', 'codex', '--outcome', 'pass', '--harness-json', str(harness),
            '--active-experimental-artifacts', ','.join(run['active_experimental_artifacts'])],
            cwd=SCRIPTS.parent, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    orders, summary = _sample_sweep(tmp_path, 'test')
    assert len(orders) == 1
    assert orders[0]['target_metric']['status'] == 'review_candidate'
    assert summary['workflow_evaluations'][0]['assigned_pairs'] == 2
    # Exercise the real Learn entrypoint, not just an imported helper.
    compatibility = loop / 'compatibility/a-baseline.json'; compatibility.parent.mkdir()
    compatibility.write_text(json.dumps({'schema_version': 1, 'run_id': 'a-baseline',
        'disposition': 'not-applicable', 'reason': 'Synthetic record integration fixture.',
        'evidence': ['fixture']}))
    cli_learn = subprocess.run([sys.executable, str(SCRIPTS / 'learn/__main__.py'), 'run',
        '--workdir', str(tmp_path), '--run-id', 'a-baseline', '--source', 'test',
        '--skip-accrue', '--json'], text=True, capture_output=True)
    assert cli_learn.returncode == 0, cli_learn.stdout + cli_learn.stderr
    receipt = json.loads(cli_learn.stdout)
    order = next(o for o in receipt['work_orders'] if o['role'] == 'promotion-reviewer')
    # Record the completed review through the real writer; unchanged evidence
    # must not ask for another review during a later Learn run.
    from learn.runner import attest
    with pytest.raises(ValueError, match='verdict'):
        attest(tmp_path, run_id='a-baseline', work_order_id=order['id'], status='complete', verdict='typo')
    before = artifact.read_text(); artifact.write_text(before + '\nChanged during review')
    with pytest.raises(ValueError, match='changed since'):
        attest(tmp_path, run_id='a-baseline', work_order_id=order['id'], status='complete', verdict='rethink')
    artifact.write_text(before)
    attest(tmp_path, run_id='a-baseline', work_order_id=order['id'], status='complete', verdict='rethink')
    repeated, repeated_summary = _sample_sweep(tmp_path, 'later')
    assert not repeated
    assert repeated_summary['workflow_evaluations'][0]['review_dispatch'] == 'already_reviewed'
    artifact.write_text(artifact.read_text() + '\nChanged workflow')
    revised_orders, _ = _sample_sweep(tmp_path, 'revised')
    assert len(revised_orders) == 1
    # Writer preserves unknown burden. Learn must not use the legacy pass=1 proxy.
    state_file = loop / 'state.json'
    state = json.loads(state_file.read_text())
    state['runs'][1]['harness']['workflow_evaluation']['user_interventions'] = None
    state_file.write_text(json.dumps(state))
    orders, summary = _sample_sweep(tmp_path, 'test')
    assert not orders
    assert summary['workflow_evaluations'][0]['status'] == 'insufficient_evidence'
    cli = subprocess.run([sys.executable, str(SCRIPTS / 'workflow_evidence.py'),
        '--workdir', str(tmp_path), '--experiment', 'compatibility'], text=True, capture_output=True)
    assert cli.returncode == 0, cli.stderr
    assert not json.loads(cli.stdout)['eligible_for_review']


def test_writer_rejects_bad_evidence_before_any_state_write(tmp_path):
    c, runs = fixture()
    obs(runs)['quality'] = True
    harness = tmp_path / 'harness.json'; harness.write_text(json.dumps(runs[1]['harness']))
    result = subprocess.run([sys.executable, '-m', 'scripts.write_run_entry', '--workdir', str(tmp_path),
        '--goal', 'bad fixture', '--outcome', 'pass', '--harness-json', str(harness)],
        cwd=SCRIPTS.parent, text=True, capture_output=True)
    assert result.returncode == 1
    assert not (tmp_path / '.build-loop/state.json').exists()


def test_uncontrolled_treatment_cannot_qualify():
    c, runs = fixture()
    runs[1]['active_experimental_artifacts'].append('another')
    assert not compare(c, runs, 'compatibility')['eligible_for_review']
    c, runs = fixture()
    del runs[0]['active_experimental_artifacts']
    assert not compare(c, runs, 'compatibility')['eligible_for_review']


def test_tiny_threshold_does_not_qualify_quality_tie():
    c, runs = fixture()
    c['minimum_quality_gain'] = 1e-13
    obs(runs)['quality'] = obs(runs, 3)['quality'] = .5
    assert not compare(c, runs, 'compatibility')['eligible_for_review']


def test_large_resource_number_is_controlled_invalid_evidence():
    c, runs = fixture()
    obs(runs)['duration_s'] = 10**1000
    result = compare(c, runs, 'compatibility')
    assert not result['eligible_for_review']
    assert 'duration_s' in str(result['reasons'])


@pytest.mark.parametrize('kind', ['contradiction', 'missing_evaluation', 'typo', 'unassigned_retry'])
def test_failures_cannot_disappear(kind):
    c, runs = fixture()
    if kind == 'contradiction':
        runs[1]['outcome'] = 'fail'
    elif kind == 'missing_evaluation':
        del runs[1]['harness']
    elif kind == 'typo':
        obs(runs)['experiment'] = 'typo'
    else:
        runs.append({'run_id': 'unassigned-failure', 'outcome': 'fail',
                     'active_experimental_artifacts': ['compatibility']})
    assert not compare(c, runs, 'compatibility')['eligible_for_review']


@pytest.mark.parametrize('change', ['equal_revisions', 'missing_digest', 'mode', 'config_digest'])
def test_no_loop_baseline_configuration_is_explicit(change):
    c, runs = fixture()
    if change == 'equal_revisions':
        c['workflow_revisions']['candidate'] = c['workflow_revisions']['baseline']
    elif change == 'missing_digest':
        del c['baseline_config_sha256']
    elif change == 'mode':
        obs(runs, 0)['loop_mode'] = 'workflow'
    else:
        obs(runs, 0)['execution_config_sha256'] = 'f' * 64
    assert not compare(c, runs, 'compatibility')['eligible_for_review']


def test_baseline_outcome_change_invalidates_review_digest():
    c, runs = fixture()
    obs(runs, 0)['accepted'] = False
    first = compare(c, runs, 'compatibility')['evaluation_sha256']
    runs[0]['outcome'] = 'fail'
    second = compare(c, runs, 'compatibility')['evaluation_sha256']
    assert first != second


def test_mismatched_experiment_identity_cannot_dispatch_or_cli_compare(tmp_path):
    from learn.runner import _sample_sweep
    c, runs = fixture()
    loop = tmp_path / '.build-loop'; (loop / 'experiments').mkdir(parents=True)
    (loop / 'config.json').write_text(json.dumps({'autoPromote': True}))
    (loop / 'state.json').write_text(json.dumps({'runs': runs}))
    (loop / 'experiments/different.jsonl').write_text(json.dumps({
        'event': 'created', 'artifact': 'compatibility', 'workflow_contract': c}) + '\n')
    orders, summary = _sample_sweep(tmp_path, 'test')
    assert not orders
    assessment = summary['workflow_evaluations'][0]
    assert not assessment['eligible_for_review']
    assert 'artifact must match experiment filename' in assessment['reasons']
    cli = subprocess.run([sys.executable, str(SCRIPTS / 'workflow_evidence.py'),
        '--workdir', str(tmp_path), '--experiment', 'different'], text=True, capture_output=True)
    assert cli.returncode == 2
    assert 'artifact must match experiment filename' in cli.stderr


@pytest.mark.parametrize('kind', ['config', 'trace', 'contract_nan', 'observation_inf'])
def test_replayed_or_nonfinite_evidence_is_ineligible(kind):
    c, runs = fixture()
    if kind == 'config':
        c['candidate_config_sha256'] = c['baseline_config_sha256']
    elif kind == 'trace':
        obs(runs)['trace_sha256'] = obs(runs, 0)['trace_sha256']
    elif kind == 'contract_nan':
        c['extra'] = float('nan')
    else:
        obs(runs)['extra'] = float('inf')
    assert not compare(c, runs, 'compatibility')['eligible_for_review']


def test_nonfinite_extra_context_returns_serializable_invalid_result():
    c, runs = fixture()
    c['context']['extra'] = float('nan')
    for run in runs:
        run['harness']['workflow_evaluation']['context']['extra'] = c['context']['extra']
    result = compare(c, runs, 'compatibility')
    assert not result['eligible_for_review']
    json.dumps(result, allow_nan=False)
