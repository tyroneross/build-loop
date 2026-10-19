"""Versioned outcome contracts in the existing runs[].createdRefs ledger.

Evidence is reviewer-authored, not proof or mutation authority. Missing or stale
contracts preserve work. A different implementation can satisfy the same outcome.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from atomic_io import LockedFile, atomic_write_bytes


def text(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    return isinstance(value, list) and bool(value) and all(text(v) for v in value)


_PLACEHOLDER_CRITERION = re.compile(
    r"<[^>]*(?:condition|criteria|criterion|acceptance)[^>]*>|\b(?:TBD|TODO|placeholder)\b", re.I)


def criteria_error(value):
    """Shared shape and placeholder validation; declarations are not outcome proof."""
    if not strings(value) or len(value) != len(set(value)):
        return 'success criteria must be nonempty, unique outcome statements'
    if any(_PLACEHOLDER_CRITERION.search(item) for item in value):
        return 'success criteria contain unresolved placeholders'
    return None


def retention_error(retention, repo, *, head=None, today=None):
    """Validate the declared recovery contract; this is not owner authority."""
    required = ('owner', 'hypothesis', 'base_head', 'experiment_head', 'target_branch',
                'recovery_ref', 'next_review', 'review_trigger', 'disposition_criteria')
    if not isinstance(retention, dict) or not all(text(retention.get(k)) for k in required):
        return 'experiment retention requires ' + ', '.join(required)
    if not strings(retention.get('evidence')):
        return 'experiment retention requires nonempty evidence references'
    try:
        due = date.fromisoformat(retention['next_review'])
        if due.isoformat() != retention['next_review']:
            raise ValueError('noncanonical date')
    except ValueError:
        return 'experiment retention next_review requires an ISO date (YYYY-MM-DD)'
    resolved = {}
    for key in ('base_head', 'experiment_head', 'target_branch', 'recovery_ref'):
        try:
            resolved[key] = subprocess.run(
                ['git', '-C', str(repo), 'rev-parse', '--verify', '--end-of-options',
                 retention[key] + '^{commit}'],
                check=True, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return f'experiment retention {key} does not resolve to a commit'
    if resolved['recovery_ref'] != resolved['experiment_head']:
        return 'experiment recovery_ref does not preserve experiment_head'
    if head is not None and any(retention[k] != resolved[k] for k in ('base_head', 'experiment_head')):
        return 'experiment retention requires pinned commit heads; refresh symbolic legacy records'
    if head is not None and resolved['experiment_head'] != head:
        return 'experiment retention head changed; refresh evidence and recovery'
    if today is not None and due <= today:
        return 'retained experiment review due on ' + due.isoformat()
    return None


def identity(entry):
    if text(entry.get('branch')):
        return ('branch', entry['branch'])
    if text(entry.get('path')):
        return ('worktree', str(Path(entry['path']).resolve()))
    raise ValueError('branch or detached worktree path is required')


def contract_error(entry):
    history = entry.get('goal_history')
    if not isinstance(history, list) or not history:
        return 'missing_goal: record the originating goal and observable success criteria'
    for index, version in enumerate(history, 1):
        if not isinstance(version, dict) or type(version.get('revision')) is not int or version.get('revision') != index:
            return 'invalid goal revision history'
        if not all(text(version.get(k)) for k in ('goal', 'source', 'reason', 'recorded_at')):
            return 'goal revisions require goal, source, change reason and timestamp'
        criteria = version.get('success_criteria')
        error = criteria_error(criteria)
        if error:
            return error
    return None


def closure_error(entry, source_head, target_head):
    error = contract_error(entry)
    if error:
        return error
    current = entry['goal_history'][-1]
    assessment = entry.get('goal_assessment')
    if not isinstance(assessment, dict):
        return 'missing goal assessment'
    if (type(assessment.get('revision')) is not int or assessment.get('revision') != current['revision'] or
        not source_head or not target_head or
        assessment.get('source_head') != source_head or assessment.get('target_head') != target_head):
        return 'stale goal assessment: refresh revision and source/target commit evidence'
    results = assessment.get('results')
    if not isinstance(results, list) or any(not isinstance(r, dict) for r in results):
        return 'criterion results must be objects'
    criteria = current['success_criteria']
    if len(results) != len(criteria) or [r.get('criterion') for r in results] != criteria:
        return 'assess every success criterion in recorded order'
    if any(r.get('met_on_target') is not True or not strings(r.get('evidence')) for r in results):
        return 'one or more outcomes remain unmet or lack evidence on the target'
    original = assessment.get('original_goal')
    if not isinstance(original, dict) or not strings(original.get('evidence')):
        return 'assess the original goal explicitly with evidence'
    if original.get('status') != 'met' and not (
        original.get('status') == 'superseded' and len(entry['goal_history']) > 1
    ):
        return 'original goal is unresolved; preserve work or record a sourced goal revision'
    if not text(assessment.get('approach')):
        return 'describe the achieved behavior and any different implementation approach'
    return None


def matching_entries(state, branch=None, path=None):
    """Return active matching contracts, including conflicting owners."""
    if not isinstance(state, dict) or not isinstance(state.get('runs', []), list):
        raise ValueError('invalid run ledger')
    matches = []
    for run in state.get('runs', []):
        if not isinstance(run, dict) or not isinstance(run.get('createdRefs', []), list):
            raise ValueError('invalid ref ledger')
        for ref in run.get('createdRefs', []):
            if not isinstance(ref, dict):
                raise ValueError('invalid ref record')
            if ref.get('status') == 'closed':
                continue
            same = ref.get('branch') == branch if branch else (
                not ref.get('branch') and path and text(ref.get('path')) and
                Path(ref['path']).resolve() == Path(path).resolve())
            if same:
                matches.append({'run_id': run.get('run_id'), **copy.deepcopy(ref)})
    return matches


def inventory(repo, branches, worktrees):
    """Read every visible ledger; missing/ambiguous intent is explicit."""
    worktrees = [{**w, 'path': w.get('path') or w.get('worktree'),
                  'head': w.get('head') or w.get('HEAD')} for w in worktrees]
    if any(not text(w['path']) for w in worktrees):
        raise ValueError('worktree inventory lacks a path')
    roots = {str(Path(repo).resolve()), *[str(Path(w['path']).resolve()) for w in worktrees]}
    states, failures = [], []
    for root in sorted(roots):
        path = Path(root) / '.build-loop/state.json'
        if not path.exists():
            continue
        try:
            state = json.loads(path.read_text())
            matching_entries(state, branch='')  # validate shape before trusting absence
            states.append((str(path), state))
        except (ValueError, OSError, RecursionError) as exc:
            failures.append(f'{path}: {exc}')
    items = [(b['name'], None, b['head']) for b in branches]
    items += [(w.get('branch'), w['path'], w.get('head')) for w in worktrees]
    output = []
    for branch, path, head in items:
        matches = []
        for ledger, state in states:
            for entry in matching_entries(state, branch, path):
                # Identical copies of a run ledger are one contract, not two owners.
                if not any(entry == row['contract'] for row in matches):
                    matches.append({'ledger': ledger, 'contract': entry})
        error = ('goal ledger unavailable: ' + '; '.join(failures)) if failures else (
            'conflicting goal records' if len(matches) > 1 else
            contract_error(matches[0]['contract']) if matches else 'missing_goal')
        if not error and matches[0]['contract'].get('review_hold'):
            error = retention_error(matches[0]['contract'].get('retention'), repo,
                                    head=head, today=datetime.now(timezone.utc).date())
        output.append({'branch': branch, 'path': path, 'head': head,
                       'goal_status': 'needs_review' if error else 'recorded',
                       'reason': error, 'records': matches})
    return output


def local_inventory(repo):
    """Project actual local Git refs through the existing goal ledger owner."""
    try:
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                  capture_output=True, text=True, timeout=5).stdout
        branches = []
        for line in git("for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads").splitlines():
            name, head = line.split("\t", 1)
            branches.append({"name": name, "head": head})
        worktrees = []
        for block in git("worktree", "list", "--porcelain").strip().split("\n\n"):
            row = {}
            for line in block.splitlines():
                key, _, value = line.partition(" ")
                if key == "worktree": row["path"] = value
                elif key == "HEAD": row["head"] = value
                elif key == "branch": row["branch"] = value.removeprefix("refs/heads/")
                elif key == "prunable": row["prunable"] = value or True
            if row: worktrees.append(row)
        items = inventory(repo, branches, worktrees)
        incomplete = any(str(row.get("reason", "")).startswith("goal ledger unavailable:") for row in items)
        return {"status": "incomplete" if incomplete else "complete", "items": items,
                "branch_count": len(branches), "worktree_count": len(worktrees),
                "prunable_count": sum("prunable" in row for row in worktrees),
                "scope": "local Git and accessible ledgers; branch/worktree rows may overlap"}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "items": [], "reason": str(exc)}


def record(workdir, payload):
    """Append revisions atomically; reject stale updates rather than overwrite."""
    if not isinstance(payload, dict) or not text(payload.get('run_id')):
        raise ValueError('an exact run_id is required')
    if 'goal' not in payload and 'assessment' not in payload and 'retention' not in payload:
        raise ValueError('supply a goal revision or an assessment')
    key = identity(payload)
    state_path = Path(workdir) / '.build-loop/state.json'
    with LockedFile(state_path):
        state = json.loads(state_path.read_text()) if state_path.exists() else {'runs': []}
        matching_entries(state, branch='')
        runs = [r for r in state.get('runs', []) if r.get('run_id') == payload['run_id']]
        if len(runs) != 1:
            raise ValueError('run_id must identify exactly one existing run')
        refs = runs[0].setdefault('createdRefs', [])
        if any(r.get('status') == 'closed' and identity(r) == key for r in refs):
            raise ValueError('closed ref identity requires a new run for a new lifecycle')
        matches = [r for r in refs if r.get('status') != 'closed' and identity(r) == key]
        if len(matches) > 1:
            raise ValueError('ambiguous active ref identity')
        entry = copy.deepcopy(matches[0]) if matches else {
            'kind': 'worktree' if payload.get('path') else 'branch',
            'branch': payload.get('branch'), 'path': payload.get('path'), 'status': 'open'}
        if payload.get('path'):
            if entry.get('path') and Path(entry['path']).resolve() != Path(payload['path']).resolve():
                raise ValueError('worktree path differs from the recorded ref')
            entry['path'] = str(Path(payload['path']).resolve())
        if payload.get('close_criteria') is not None:
            if not strings(payload['close_criteria']):
                raise ValueError('close_criteria must be nonempty strings')
            entry['close_criteria'] = payload['close_criteria']
        history = entry.setdefault('goal_history', [])
        if not isinstance(history, list) or (history and contract_error(entry)):
            raise ValueError('existing goal history is invalid; preserve it for repair')
        if type(payload.get('expected_revision')) is not int or payload.get('expected_revision') != len(history):
            raise ValueError('goal changed since read; expected_revision does not match')
        if 'goal' in payload:
            version = {'revision': len(history) + 1, 'goal': payload['goal'],
                       'success_criteria': payload.get('success_criteria'),
                       'source': payload.get('source'), 'reason': payload.get('reason'),
                       'recorded_at': datetime.now(timezone.utc).isoformat()}
            history.append(version)
            error = contract_error(entry)
            if error:
                raise ValueError(error)
            entry['purpose'] = version['goal']
            entry['success_criteria'] = version['success_criteria']
            entry.pop('goal_assessment', None)
        if 'retention' in payload:
            if contract_error(entry):
                raise ValueError('record a valid goal before retaining an experiment')
            retention = payload['retention']
            error = retention_error(retention, workdir)
            if error:
                raise ValueError(error)
            # Store immutable observations, not symbolic branches that can
            # move together with their alleged recovery target.
            retention = copy.deepcopy(retention)
            for key in ('base_head', 'experiment_head'):
                retention[key] = subprocess.run(
                    ['git', '-C', str(workdir), 'rev-parse', '--verify', '--end-of-options',
                     retention[key] + '^{commit}'],
                    check=True, capture_output=True, text=True, timeout=5,
                ).stdout.strip()
            entry['retention'] = copy.deepcopy(retention)
            entry['review_hold'] = True
            entry['review_hold_reason'] = retention['disposition_criteria']
            entry['disposition'] = 'retained_experiment'
        if 'assessment' in payload:
            if contract_error(entry):
                raise ValueError('record a valid goal before assessing it')
            entry['goal_assessment'] = payload['assessment']
            # Unmet assessments may be saved; closure_error will keep them held.
            if not isinstance(payload['assessment'], dict):
                raise ValueError('assessment must be an object')
        if matches:
            refs[refs.index(matches[0])] = entry
        else:
            refs.append(entry)
        atomic_write_bytes(state_path, (json.dumps(state, indent=2) + '\n').encode())
        return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', type=Path, default=Path.cwd())
    parser.add_argument('--payload-json', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(record(args.workdir, json.loads(args.payload_json.read_text())), indent=2))
    except (ValueError, OSError, TimeoutError, RecursionError) as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
