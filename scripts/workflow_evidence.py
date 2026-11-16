#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr
# SPDX-License-Identifier: Apache-2.0
"""Matched workflow evidence for Learn. A review candidate, never an adoption verdict.

Uses existing runs[].harness records and experiment created rows. No new store,
model call, memory write, scheduler, or implicit conversion of closeout to quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, TypeGuard

CONTEXT_KEYS = ('model', 'effort', 'host', 'tools', 'permissions', 'budget',
                'checker', 'memory', 'preferences', 'task_family', 'served_model', 'intervention_metric')


def _text(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _date(value: Any) -> datetime:
    if not _text(value):
        raise ValueError('timestamp must be an ISO string with timezone')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    return parsed


def validate_observation(item: Any) -> None:
    """Reject malformed opt-in evidence; null measurements remain unknown."""
    if not isinstance(item, dict):
        raise ValueError('workflow_evaluation must be an object')
    for key in ('experiment', 'arm', 'case_id', 'task_snapshot', 'workflow_revision'):
        if not _text(item.get(key)):
            raise ValueError(f'workflow_evaluation.{key} is required')
    if item['arm'] not in ('baseline', 'candidate'):
        raise ValueError('workflow_evaluation.arm must be baseline or candidate')
    context = item.get('context')
    if not isinstance(context, dict) or any(not _text(context.get(k)) for k in CONTEXT_KEYS):
        raise ValueError('workflow_evaluation.context requires explicit context fingerprints')
    _date(item.get('started_at'))
    for key in ('execution_config_sha256', 'trace_sha256'):
        if not _digest(item.get(key)):
            raise ValueError(f'workflow_evaluation.{key} requires a SHA-256 digest')
    expected_mode = 'native' if item['arm'] == 'baseline' else 'workflow'
    if item.get('loop_mode') != expected_mode:
        raise ValueError('workflow_evaluation.loop_mode must distinguish native baseline from workflow')
    for key in ('completed', 'accepted'):
        if type(item.get(key)) is not bool:
            raise ValueError(f'workflow_evaluation.{key} must be boolean')
    if item['accepted'] and not item['completed']:
        raise ValueError('incomplete workflow cannot be accepted')
    score = item.get('quality')
    if score is not None and (not _number(score) or not 0 <= score <= 1):
        raise ValueError('workflow_evaluation.quality must be null or finite 0..1')
    for key in ('user_interventions', 'tokens'):
        value = item.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f'workflow_evaluation.{key} must be null or nonnegative integer')
    value = item.get('duration_s')
    if value is not None and (not _number(value) or value < 0):
        raise ValueError('workflow_evaluation.duration_s must be null or finite nonnegative number')
    refs = item.get('evidence')
    if not isinstance(refs, list) or not refs or not all(_text(ref) for ref in refs):
        raise ValueError('workflow_evaluation requires evidence references')


def compare(contract: dict, runs: list[Any], experiment: str) -> dict:
    """Evaluate every frozen assignment; retain exclusions and unknowns explicitly.

    Contract/measurement provenance is supplied by the experiment owner. Review
    must inspect the cited oracle, freeze and traces; JSON alone cannot prove it.
    """
    result: dict[str, Any] = {
        'status': 'insufficient_evidence', 'eligible_for_review': False,
        'comparison': 'matched quality and intervention count', 'reasons': [], 'pairs': [],
        'user_effort_coverage': 'intervention count only; duration and difficulty unmeasured',
        'evidence_boundary': 'recorded measurements; independent provenance review required; no population or adoption claim',
    }
    reasons = result['reasons']
    try:
        # Validate extensions too, before returning any caller-supplied context.
        json.dumps(contract, allow_nan=False)
        frozen = _date(contract.get('frozen_at'))
        context = contract['context']
        if not isinstance(context, dict) or any(not _text(context.get(k)) for k in CONTEXT_KEYS):
            raise ValueError('contract context is incomplete')
        gain = contract['minimum_quality_gain']
        if not _number(gain) or not 0 < gain <= 1:
            raise ValueError('minimum_quality_gain must be finite, positive and <= 1')
        minimum = contract['minimum_pairs']
        if type(minimum) is not int or minimum < 1:
            raise ValueError('minimum_pairs must be a positive prespecified count')
        if not _text(contract.get('freeze_evidence')):
            raise ValueError('freeze_evidence is required')
        for key in ('manifest_sha256', 'oracle_sha256', 'baseline_config_sha256', 'candidate_config_sha256'):
            digest = contract.get(key)
            if not _digest(digest):
                raise ValueError(f'{key} requires a SHA-256 digest')
        if contract['baseline_config_sha256'] == contract['candidate_config_sha256']:
            raise ValueError('baseline and candidate execution configs must differ')
        if contract.get('baseline_kind') != 'no_loop':
            raise ValueError('baseline_kind must explicitly identify no_loop')
        if not _text(contract.get('baseline_config_evidence')):
            raise ValueError('no-loop baseline configuration evidence is required')
        if context['served_model'].lower() in ('unknown', 'unverified', 'requested_only'):
            raise ValueError('served model identity is unknown')
        revisions = contract['workflow_revisions']
        if any(not _text(revisions.get(k)) for k in ('baseline', 'candidate')):
            raise ValueError('both workflow revisions are required')
        if revisions['baseline'] == revisions['candidate']:
            raise ValueError('baseline and candidate workflow revisions must differ')
        assignments = contract['cases']
        if not isinstance(assignments, list) or not assignments:
            raise ValueError('frozen cases are required')
        cases = {}
        assigned_runs = set()
        for case in assignments:
            if not isinstance(case, dict) or not _text(case.get('case_id')) or not _text(case.get('task_snapshot')):
                raise ValueError('case id and task snapshot required')
            if case['case_id'] in cases:
                raise ValueError('duplicate frozen case')
            if case.get('split') != 'holdout':
                raise ValueError('development or posthoc cases cannot qualify a workflow')
            for arm in ('baseline', 'candidate'):
                rid = case.get(arm + '_run_id')
                if not _text(rid) or rid in assigned_runs:
                    raise ValueError('unique frozen run IDs required for every assigned arm')
                assigned_runs.add(rid)
            cases[case['case_id']] = case
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        reasons.append(f'invalid_contract: {exc}')
        return result
    result['baseline_kind'] = contract['baseline_kind']
    result['baseline_config_evidence'] = contract['baseline_config_evidence']
    result['baseline_config_sha256'] = contract['baseline_config_sha256']
    result['freeze_evidence'] = contract['freeze_evidence']
    result['context'] = context
    result['assigned_pairs'] = len(cases)
    if len(cases) < minimum:
        reasons.append('prespecified_sample_incomplete')
    observations: dict[tuple[str, str], dict] = {}
    seen_runs: set[str] = set()
    seen_traces: set[str] = set()
    for run in runs:
        if not isinstance(run, dict):
            continue
        harness = run.get('harness')
        item = harness.get('workflow_evaluation') if isinstance(harness, dict) else None
        rid = run.get('run_id')
        active = run.get('active_experimental_artifacts')
        relevant = (isinstance(rid, str) and rid in assigned_runs) or (isinstance(active, list) and experiment in active)
        if not isinstance(item, dict) or item.get('experiment') != experiment:
            if relevant:
                reasons.append(f'{rid}: assigned or experimental run missing matching evaluation')
            continue
        try:
            validate_observation(item)
            if not _text(rid) or rid in seen_runs:
                raise ValueError('missing or duplicate run_id')
            seen_runs.add(rid)
            if item['trace_sha256'] in seen_traces:
                raise ValueError('trace reused across assigned runs')
            seen_traces.add(item['trace_sha256'])
            expected_active = [] if item['arm'] == 'baseline' else [experiment]
            if run.get('active_experimental_artifacts') != expected_active:
                raise ValueError('missing or uncontrolled experimental treatments')
            case = cases.get(item['case_id'])
            if case is None:
                raise ValueError('unassigned case')
            if rid != case[item['arm'] + '_run_id']:
                raise ValueError('run_id differs from frozen assignment')
            if run.get('outcome') not in ('pass', 'fail', 'partial'):
                raise ValueError('run outcome missing or unknown')
            if item['accepted'] and run['outcome'] != 'pass':
                raise ValueError('accepted observation contradicts run outcome')
            if item['task_snapshot'] != case['task_snapshot'] or item['context'] != context:
                raise ValueError('task or context mismatch')
            if item['execution_config_sha256'] != contract[item['arm'] + '_config_sha256']:
                raise ValueError('execution config digest mismatch')
            if item['workflow_revision'] != revisions[item['arm']]:
                raise ValueError('workflow revision mismatch')
            if _date(item['started_at']) <= frozen:
                raise ValueError('run predates frozen contract')
            key = (item['case_id'], item['arm'])
            if key in observations:
                raise ValueError('duplicate case arm')
            observations[key] = dict(item, run_id=rid, run_outcome=run['outcome'],
                                     active_experimental_artifacts=active)
        except ValueError as exc:
            reasons.append(f'{rid}: {exc}')
    for cid in cases:
        baseline = observations.get((cid, 'baseline'))
        candidate = observations.get((cid, 'candidate'))
        if baseline is None or candidate is None:
            reasons.append(f'{cid}: missing assigned arm')
            continue
        pair: dict[str, Any] = {'case_id': cid, 'baseline_run': baseline['run_id'],
                                'candidate_run': candidate['run_id'],
                                'evidence': baseline['evidence'] + candidate['evidence']}
        for key in ('quality', 'user_interventions', 'duration_s', 'tokens'):
            b, c = baseline.get(key), candidate.get(key)
            pair[key + '_delta'] = c - b if b is not None and c is not None else None
        pair['candidate_accepted'] = candidate['accepted']
        pair['candidate_completed'] = candidate['completed']
        result['pairs'].append(pair)
        if pair['quality_delta'] is None or pair['user_interventions_delta'] is None:
            reasons.append(f'{cid}: quality or user effort unknown')
    if reasons:
        return result
    pairs = result['pairs']
    mean_gain = sum(p['quality_delta'] for p in pairs) / len(pairs)
    result['mean_quality_gain'] = mean_gain
    for p in pairs:
        if not p['candidate_completed'] or not p['candidate_accepted']:
            reasons.append(f"{p['case_id']}: candidate incomplete or unaccepted")
        if p['quality_delta'] < 0:
            reasons.append(f"{p['case_id']}: quality regression")
        if p['user_interventions_delta'] > 0:
            reasons.append(f"{p['case_id']}: increased user effort")
    if mean_gain <= 0 or (mean_gain < gain and not math.isclose(mean_gain, gain, rel_tol=1e-12, abs_tol=0)):
        reasons.append('prespecified_quality_gain_not_met')
    result['status'] = 'not_improved' if reasons else 'review_candidate'
    result['eligible_for_review'] = not reasons
    try:
        result['evaluation_sha256'] = hashlib.sha256(json.dumps(
            {'contract': contract, 'observations': sorted(observations.values(), key=lambda x: x['run_id'])},
            sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError, OverflowError) as exc:
        result.update(status='insufficient_evidence', eligible_for_review=False)
        reasons.append(f'evidence is not finite JSON: {exc}')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', type=Path, default=Path('.'))
    parser.add_argument('--experiment', required=True)
    args = parser.parse_args()
    # Use the writer's containment policy rather than treating a name as a path.
    from write_run_entry.iohelpers import experiment_log_path
    path = experiment_log_path(args.workdir / '.build-loop/experiments', args.experiment)
    if path is None:
        parser.error('experiment must remain inside .build-loop/experiments')
    try:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError('experiment rows must be objects')
        created = next(row for row in rows if row.get('event') == 'created')
        if created.get('artifact', args.experiment) != args.experiment:
            raise ValueError('artifact must match experiment filename')
        state = json.loads((args.workdir / '.build-loop/state.json').read_text())
        if not isinstance(state, dict) or not isinstance(state.get('runs', []), list):
            raise ValueError('state must be an object with a runs list')
        result = compare(created.get('workflow_contract', {}), state.get('runs', []), args.experiment)
    except (OSError, ValueError, StopIteration) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
