"""Outcome-led ref review on real Git state and the shared run ledger."""
import copy
import json
import subprocess
from pathlib import Path

import pytest
import ref_goals as goals


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@example.test')
    (tmp_path/'outcome.txt').write_text('working outcome through implementation B')
    git(tmp_path, 'add', 'outcome.txt');git(tmp_path,'commit','-m','alternative implementation')
    git(tmp_path,'branch','original-approach')
    (tmp_path/'.build-loop').mkdir()
    (tmp_path/'.git/info/exclude').write_text('.build-loop/\n')
    (tmp_path/'.build-loop/state.json').write_text(json.dumps({'runs':[{'run_id':'ours'},{'run_id':'peer','keep':True}]}))
    return tmp_path


def payload(**extra):
    return {'run_id':'ours','branch':'original-approach','expected_revision':0,
            'goal':'Provide a working outcome','success_criteria':['outcome.txt contains working outcome'],
            'source':'user request 2026-09-27','reason':'initial scope',**extra}


def assessment(repo, revision=1):
    return {'revision':revision,'source_head':git(repo,'rev-parse','original-approach'),
            'target_head':git(repo,'rev-parse','main'),
            'results':[{'criterion':'outcome.txt contains working outcome','met_on_target':True,
                        'evidence':['Read target outcome.txt: working outcome through implementation B']}],
            'original_goal':{'status':'met','evidence':['Target outcome satisfies originating request']},
            'approach':'Implementation B satisfies the same outcome; original approach A is unnecessary'}


def test_revision_history_and_optimistic_update_preserve_original_and_peer(repo):
    first=goals.record(repo,payload())
    second=goals.record(repo,payload(expected_revision=1,goal='Provide a working and documented outcome',
                                   reason='User extended requested outcome',source='user followup'))
    assert second['goal_history'][0]==first['goal_history'][0]
    assert second['goal_history'][1]['revision']==2
    with pytest.raises(ValueError,match='expected_revision'):
        goals.record(repo,payload())
    state=json.loads((repo/'.build-loop/state.json').read_text())
    assert state['runs'][1]=={'run_id':'peer','keep':True}


def test_alternative_implementation_and_stale_source_target_or_goal(repo):
    entry=goals.record(repo,payload())
    entry=goals.record(repo,{'run_id':'ours','branch':'original-approach','expected_revision':1,'assessment':assessment(repo)})
    source,target=assessment(repo)['source_head'],assessment(repo)['target_head']
    assert goals.closure_error(entry,source,target) is None
    assert goals.closure_error(entry,'changed',target).startswith('stale')
    assert goals.closure_error(entry,source,'changed').startswith('stale')
    revised=goals.record(repo,payload(expected_revision=1,reason='Revised acceptance'))
    assert 'goal_assessment' not in revised
    assert goals.closure_error(revised,source,target)=='missing goal assessment'


def test_missing_partial_malformed_and_unresolved_original_hold(repo):
    assert goals.closure_error({},'source','target').startswith('missing_goal')
    entry=goals.record(repo,payload()); entry['goal_assessment']=assessment(repo)
    for mutate in [lambda a:a.update(results=[]),lambda a:a['results'][0].update(met_on_target=False),
                   lambda a:a['results'][0].update(evidence=[]),lambda a:a['original_goal'].update(status='unknown')]:
        candidate=copy.deepcopy(entry);mutate(candidate['goal_assessment'])
        assert goals.closure_error(candidate,assessment(repo)['source_head'],assessment(repo)['target_head'])


def test_detached_inventory_and_closed_branch_identity(repo):
    detached=repo.parent/(repo.name+'-detached')
    git(repo,'worktree','add','--detach',str(detached),'HEAD')
    goals.record(repo,payload(branch=None,path=str(detached)))
    goals.record(repo,payload())
    state_path=repo/'.build-loop/state.json';state=json.loads(state_path.read_text())
    state['runs'][0]['createdRefs'][1]['status']='closed';state_path.write_text(json.dumps(state))
    head=git(repo,'rev-parse','HEAD')
    items=goals.inventory(repo,[{'name':'original-approach','head':head}],
                           [{'branch':None,'path':str(detached),'head':head}])
    assert items[0]['reason']=='missing_goal'
    assert items[1]['goal_status']=='recorded'
    with pytest.raises(ValueError,match='requires a new run'):
        goals.record(repo,payload(goal='New lifecycle'))
    goals.record(repo,payload(run_id='peer',goal='New lifecycle'))
    assert len(json.loads(state_path.read_text())['runs'][0]['createdRefs'])==2
    assert goals.inventory(repo,[{'name':'original-approach','head':head}],[])[0]['goal_status']=='recorded'


def test_conflicting_active_contracts_and_corrupt_ledger_hold(repo):
    goals.record(repo,payload());goals.record(repo,payload(run_id='peer'))
    branches=[{'name':'original-approach','head':git(repo,'rev-parse','HEAD')}]
    assert goals.inventory(repo,branches,[])[0]['reason']=='conflicting goal records'
    (repo/'.build-loop/state.json').write_text('{')
    assert 'unavailable' in goals.inventory(repo,branches,[])[0]['reason']


def test_strict_closeout_holds_merged_branch_without_goal(repo):
    import collapse_run
    path=repo/'.build-loop/state.json';state=json.loads(path.read_text())
    state['runs'][0]['createdRefs']=[{'branch':'original-approach','status':'open'}];path.write_text(json.dumps(state))
    result=collapse_run.collapse(repo,run_id='ours',branch='original-approach',strict=True,dry_run=True)
    assert any('goal' in e for e in result['errors'])
    assert git(repo,'rev-parse','original-approach')


def seed_closeout_goal(repo, run_id, branch):
    """Give existing closeout fixtures an explicit, verified README outcome."""
    path=Path(repo)/'.build-loop/state.json';state=json.loads(path.read_text())
    run=next((r for r in state['runs'] if r.get('run_id')==run_id or r.get('build_loop_id')==run_id),None)
    if run is None:
        return
    refs=run.setdefault('createdRefs',[])
    entries=[r for r in refs if r.get('branch')==branch]
    if entries and entries[0].get('goal_history'):
        return
    entry=entries[0] if entries else {'branch':branch,'status':'open'}
    try:
        source=git(repo,'rev-parse',branch);target=git(repo,'rev-parse','main')
        body=git(repo,'show','main:README.md')
    except subprocess.CalledProcessError:
        return
    assert body
    criterion='The repository README remains available on main'
    entry.update(goal_history=[{'revision':1,'goal':'Retain the repository README',
        'success_criteria':[criterion],'source':'closeout fixture specification',
        'reason':'initial fixture goal','recorded_at':'2026-09-27T00:00:00Z'}],goal_assessment={
        'revision':1,'source_head':source,'target_head':target,
        'results':[{'criterion':criterion,'met_on_target':True,'evidence':[f'git show {target}:README.md: {body}']}],
        'original_goal':{'status':'met','evidence':[f'Read README from {target}']},
        'approach':'Fixture target retains the original README'})
    if not entries:refs.append(entry)
    path.write_text(json.dumps(state))


def test_guard_failed_creation_has_no_active_goal_and_retry_preserves_cleanup(repo):
    import worktree_guard
    kwargs={'purpose':'Provide working outcome','run_id':'ours',
            'success_criteria':['Working outcome available'],'goal_source':'user request',
            'close_criteria':['Recovery bundle verified']}
    failed=worktree_guard.create_guarded_worktree(repo,'retry',base='missing',**kwargs)
    assert failed['created'] is False
    assert 'createdRefs' not in json.loads((repo/'.build-loop/state.json').read_text())['runs'][0]
    success=worktree_guard.create_guarded_worktree(repo,'retry',**kwargs)
    assert success['created'] is True
    entry=json.loads((repo/'.build-loop/state.json').read_text())['runs'][0]['createdRefs'][0]
    assert entry['close_criteria']==['Recovery bundle verified']


def test_strict_closeout_rejects_linked_worktree_revision_conflict(repo):
    import collapse_run
    goals.record(repo,payload())
    goals.record(repo,{'run_id':'ours','branch':'original-approach','expected_revision':1,'assessment':assessment(repo)})
    linked=repo/'.build-loop/worktrees/alternative';linked.parent.mkdir(parents=True)
    git(repo,'worktree','add',str(linked),'original-approach')
    (linked/'.build-loop').mkdir()
    state=json.loads((repo/'.build-loop/state.json').read_text())
    state['runs'][0]['createdRefs'][0]['goal_history'].append({
        'revision':2,'goal':'Also meet a new requirement','success_criteria':['new requirement'],
        'source':'later user request','reason':'scope extended','recorded_at':'2026-09-27T01:00:00Z'})
    (linked/'.build-loop/state.json').write_text(json.dumps(state))
    result=collapse_run.collapse(repo,run_id='ours',branch='original-approach',strict=True,dry_run=True,owner_released=True)
    assert any('conflicting goal' in e for e in result['errors'])
    assert result['retained']


def test_actual_maintenance_cli_lists_attached_and_detached_goal_status(repo):
    script=Path(__file__).resolve().parents[1]/'skills/repo-maintenance/scripts/audit_repo_maintenance.py'
    result=subprocess.run(['python3',str(script),'--repo',str(repo),'--json'],text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    inventory=json.loads(result.stdout)['goals']
    assert any(row['path']==str(repo) for row in inventory)
    assert any(row['branch']=='original-approach' and row['reason']=='missing_goal' for row in inventory)


@pytest.mark.parametrize('json_output', [False, True])
def test_guard_cli_reports_retained_worktree_when_goal_write_fails(repo, capsys, json_output):
    import worktree_guard
    args=['--workdir',str(repo),'--slug','partial','--run-id','unknown',
          '--purpose','Working outcome','--success-criterion','Outcome available',
          '--goal-source','user request']
    if json_output:args.append('--json')
    assert worktree_guard.main(args)==1
    captured=capsys.readouterr()
    assert 'goal recording failed' in captured.out+captured.err
    assert (repo/'.build-loop/worktrees/partial').exists()


def test_empty_ledger_reports_missing_run_without_traceback(repo):
    (repo/'.build-loop/state.json').write_text('{}')
    with pytest.raises(ValueError,match='exactly one existing run'):
        goals.record(repo,payload())
