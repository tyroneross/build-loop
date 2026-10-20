from __future__ import annotations

import json
from pathlib import Path

import tool_trace as trace


def _line(role: str, content: list, timestamp: str) -> str:
    return json.dumps({"type": role, "timestamp": timestamp, "message": {"content": content}})


def test_span_redacts_secrets_and_bounds_content(tmp_path: Path) -> None:
    span = trace.build_span(
        workdir=tmp_path,
        session_id="session-1",
        tool_name="Bash",
        tool_use_id="call-1",
        phase="end",
        tool_input={
            "command": "curl -H 'Authorization: Bearer abcdef123456' -H 'Cookie: session=private'",
            "api_key": "secret",
        },
        tool_result="x" * 2_000,
    )
    attrs = span["attributes"]
    assert "abcdef123456" not in json.dumps(span)
    assert "session=private" not in json.dumps(span)
    assert "secret" not in json.dumps(span)
    assert attrs["gen_ai.tool.call.result.truncated"] is True
    assert len(attrs["gen_ai.tool.call.result.preview"]) == trace.MAX_PREVIEW_CHARS
    assert len(span["trace_id"]) == 32 and len(span["span_id"]) == 16

    inline = trace.build_span(
        workdir=tmp_path,
        session_id="session-1",
        tool_name="Bash",
        tool_use_id="call-secret",
        phase="end",
        tool_result=(
            "OPENAI_API_KEY=sk-proj-THISSHOULDNOTAPPEAR "
            "DB_PASSWORD='THISQUOTEDSECRETLEAKS'"
        ),
    )
    encoded = json.dumps(inline)
    assert "THISSHOULDNOTAPPEAR" not in encoded
    assert "THISQUOTEDSECRETLEAKS" not in encoded
    assert "[REDACTED]" in encoded


def test_transcript_ingest_is_idempotent_and_summarizes_learning_signals(tmp_path: Path) -> None:
    transcript = tmp_path / "session-1.jsonl"
    transcript.write_text("\n".join([
        _line("assistant", [{"type": "tool_use", "id": "a1", "name": "Bash", "input": {"command": "run"}}], "2026-08-10T00:00:00Z"),
        _line("user", [{"type": "tool_result", "tool_use_id": "a1", "is_error": True, "content": "429 rate limit"}], "2026-08-10T00:00:01Z"),
        _line("assistant", [{"type": "tool_use", "id": "a2", "name": "Bash", "input": {"command": "run"}}], "2026-08-10T00:00:02Z"),
        _line("user", [{"type": "tool_result", "tool_use_id": "a2", "is_error": False, "content": "ok"}], "2026-08-10T00:00:03Z"),
    ]), encoding="utf-8")
    assert trace.ingest_transcript(tmp_path, transcript) == {"seen": 2, "written": 2}
    assert trace.ingest_transcript(tmp_path, transcript) == {"seen": 2, "written": 0}
    summary = trace.summarize(tmp_path)
    assert summary["tool_calls"] == 2
    assert summary["tool_errors"] == 1
    assert summary["provider_429s"] == 1
    assert summary["repeated_calls"] == 1
    assert summary["p95_duration_ms"] == 1000.0


def test_hook_without_tool_name_is_fail_soft(tmp_path: Path) -> None:
    assert trace.record_hook(tmp_path, "start", {"session_id": "s"}) is None
    assert not (tmp_path / trace.TRACE_PATH).exists()


def test_codex_response_items_are_reconciled(tmp_path: Path) -> None:
    transcript = tmp_path / "codex-session.jsonl"
    rows = [
        {"type": "response_item", "timestamp": "2026-08-10T00:00:00Z", "payload": {
            "type": "function_call", "call_id": "c1", "name": "exec_command",
            "arguments": json.dumps({"cmd": "pytest"}),
        }},
        {"type": "response_item", "timestamp": "2026-08-10T00:00:02Z", "payload": {
            "type": "function_call_output", "call_id": "c1",
            "output": "Process exited with code 2\n429 rate limit",
        }},
        {"type": "response_item", "timestamp": "2026-08-10T00:00:03Z", "payload": {
            "type": "custom_tool_call", "call_id": "c2", "name": "apply_patch",
            "input": "*** Begin Patch",
        }},
        {"type": "response_item", "timestamp": "2026-08-10T00:00:04Z", "payload": {
            "type": "custom_tool_call_output", "call_id": "c2", "output": "Done!",
        }},
    ]
    transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    assert trace.ingest_transcript(tmp_path, transcript) == {"seen": 2, "written": 2}
    summary = trace.summarize(tmp_path)
    assert summary["tool_calls"] == 2
    assert summary["tool_errors"] == 1
    assert summary["provider_429s"] == 1
    assert summary["tools"] == {"exec_command": 1, "apply_patch": 1}


def test_trace_summary_streams_without_materializing_file(tmp_path, monkeypatch):
    source = tmp_path / "session.jsonl"
    source.write_text(_line("assistant", [{"type":"tool_use","id":"id","name":"Bash","input":{"command":"run"}}], "2026-08-10T00:00:00Z") + "\n" + _line("user", [{"type":"tool_result","tool_use_id":"id","content":"ok"}], "2026-08-10T00:00:01Z"))
    trace.ingest_transcript(tmp_path,source)
    original = Path.read_text
    def no_trace_read(path,*args,**kwargs):
        if path.name.endswith("jsonl"):
            raise AssertionError("whole-file trace read")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"read_text",no_trace_read)
    assert trace.summarize(tmp_path)["tool_calls"] == 1


def test_trace_budget_failure_is_not_zero_coverage(tmp_path,monkeypatch):
    import pytest, transcript_adapter
    path=tmp_path/"large.jsonl";path.write_text("x"*100)
    monkeypatch.setattr(transcript_adapter,"MAX_SOURCE_BYTES",50)
    with pytest.raises(transcript_adapter.TranscriptLimitError):
        trace.summarize_path(path)


def test_source_session_deduplicates_raw_normalized_and_revision_copy(tmp_path):
    from transcript_adapter import normalize_to_file
    source=tmp_path/'rollout.jsonl'
    rows=[{"type":"session_meta","payload":{"id":"original"}},
          {"type":"response_item","payload":{"type":"function_call","call_id":"same-call","name":"exec","arguments":"{}"}}]
    source.write_text("\n".join(json.dumps(row) for row in rows))
    normalized=tmp_path/'normalized-one.jsonl';normalize_to_file(source,normalized)
    revised=tmp_path/'normalized-two.jsonl';normalize_to_file(normalized,revised)
    assert trace.ingest_transcript(tmp_path,source)["written"] == 1
    assert trace.ingest_transcript(tmp_path,normalized)["written"] == 0
    assert trace.ingest_transcript(tmp_path,revised)["written"] == 0
    import pytest
    with pytest.raises(ValueError,match="conflicting"):
        trace.ingest_transcript(tmp_path,source,session_id="different")


def test_named_pipe_summary_is_rejected_without_waiting(tmp_path):
    import os,pytest
    from transcript_adapter import TranscriptReadError
    path=tmp_path/'pipe';os.mkfifo(path)
    with pytest.raises(TranscriptReadError):
        trace.summarize_path(path)



def test_public_trace_readers_reject_fifo_in_actual_child(tmp_path):
    import os,subprocess,sys
    path=tmp_path/'pipe';os.mkfifo(path)
    for expression in ("t.summarize_path(p)","t.ingest_transcript(p.parent,p)"):
        code=f"import sys;sys.path.insert(0,{str(Path(__file__).resolve().parent)!r});from pathlib import Path;import tool_trace as t;from transcript_adapter import TranscriptReadError;p=Path({str(path)!r})\ntry:\n {expression}\nexcept TranscriptReadError:\n pass"
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=2)
        assert result.returncode==0,result.stderr


def test_trace_fd_rejects_regular_to_fifo_replacement(tmp_path,monkeypatch):
    import os,pytest,transcript_adapter as adapter
    path=tmp_path/'source';path.write_text('{}\n')
    original=os.open
    def replacement(source,flags,*args,**kwargs):
        if Path(source)==path:
            path.unlink();os.mkfifo(path)
        return original(source,flags,*args,**kwargs)
    monkeypatch.setattr(adapter.os,'open',replacement)
    with pytest.raises(adapter.TranscriptReadError):trace.summarize_path(path)


def test_historical_and_peer_calls_do_not_inherit_active_run(tmp_path):
    state=tmp_path/'.build-loop/state.json';state.parent.mkdir()
    state.write_text(json.dumps({'execution':{'build_loop_id':'run-B','started_at':'2026-10-19T12:00:00Z','current_session_id':'session-B','started_by_session_id':'session-B'}}))
    span=trace.record_hook(tmp_path,'start',{'tool_name':'Bash','session_id':'session-A','timestamp':'2026-10-18T12:00:00Z'})
    assert span['attributes']['session.id']=='session-A'
    assert span['attributes']['build_loop.run_id'] is None



def test_run_attachment_requires_exact_session_and_call_time(tmp_path,monkeypatch):
    import pytest
    state=tmp_path/'.build-loop/state.json';state.parent.mkdir()
    execution={'build_loop_id':'run-B','started_at':'2026-10-19T12:00:00Z','current_session_id':'session-B','started_by_session_id':'starter'}
    for session,timestamp,expected in [
        ('session-B','2026-10-19T12:00:00Z','run-B'),
        ('starter','2026-10-19T12:00:01Z','run-B'),
        ('peer','2026-10-19T12:00:01Z',None),
        ('session-B','2026-10-19T11:59:59Z',None),
        ('session-B',None,None),('session-B','bad',None),
    ]:
        state.write_text(json.dumps({'execution':execution}))
        span=trace.record_hook(tmp_path,'start',{'tool_name':'Bash','session_id':session,'timestamp':timestamp})
        assert span['attributes']['build_loop.run_id']==expected
    for malformed in [[],{}, {'execution':[]}, {'execution':{**execution,'run_id':'conflict'}},
                      {'execution':{**execution,'started_at':None}},
                      {'execution':{**execution,'current_session_id':None,'started_by_session_id':None}},
                      {'execution':{**execution,'ended_at':'bad'}}]:
        state.write_text(json.dumps(malformed))
        span=trace.record_hook(tmp_path,'start',{'tool_name':'Bash','session_id':'session-B','timestamp':'2026-10-19T12:01:00Z'})
        assert span['attributes']['build_loop.run_id'] is None
    state.write_text('{broken')
    assert trace._run_context(tmp_path) is None
    state.unlink()
    assert trace._run_context(tmp_path) is None


def test_ingestion_resolves_run_once_and_keeps_prestart_calls_unattributed(tmp_path,monkeypatch):
    state=tmp_path/'.build-loop/state.json';state.parent.mkdir()
    state.write_text(json.dumps({'execution':{'build_loop_id':'run-B','started_at':'2026-10-19T12:00:00Z','current_session_id':'session-B'}}))
    source=tmp_path/'source.jsonl'
    rows=[{'type':'session_meta','payload':{'id':'session-B'}}]
    for call,timestamp in [('old','2026-10-19T11:59:59Z'),('new','2026-10-19T12:00:00Z')]:
        rows.append({'type':'response_item','timestamp':timestamp,'payload':{'type':'function_call','call_id':call,'name':'exec','arguments':'{}'}})
    source.write_text('\n'.join(json.dumps(row) for row in rows))
    original=trace._run_context;calls=[]
    def once(workdir):calls.append(workdir);return original(workdir)
    monkeypatch.setattr(trace,'_run_context',once)
    assert trace.ingest_transcript(tmp_path,source)=={'seen':2,'written':2}
    spans=list(trace._iter_trace_rows(tmp_path/trace.TRACE_PATH))
    assert [row['attributes']['build_loop.run_id'] for row in spans]==[None,'run-B']
    assert all(row['attributes']['session.id']=='session-B' for row in spans)
    assert len(calls)==1


def test_historical_transcript_retains_session_without_active_run(tmp_path):
    state=tmp_path/'.build-loop/state.json';state.parent.mkdir()
    state.write_text(json.dumps({'execution':{'build_loop_id':'run-B','started_at':'2026-10-19T12:00:00Z','current_session_id':'session-B'}}))
    source=tmp_path/'history.jsonl'
    source.write_text('\n'.join(json.dumps(row) for row in [
        {'type':'session_meta','payload':{'id':'session-A'}},
        {'type':'response_item','timestamp':'2026-10-18T12:00:00Z','payload':{'type':'function_call','call_id':'old','name':'exec','arguments':'{}'}}]))
    assert trace.ingest_transcript(tmp_path,source)['written']==1
    span=next(trace._iter_trace_rows(tmp_path/trace.TRACE_PATH))
    assert span['attributes']['session.id']=='session-A'
    assert span['attributes']['build_loop.run_id'] is None


def test_late_terminal_error_is_recorded_once_after_observed_call(tmp_path):
    source=tmp_path/'session.jsonl'
    call=_line('assistant',[{'type':'tool_use','id':'late','name':'Bash','input':{}}],'2026-10-19T12:00:00Z')
    source.write_text(call+'\n')
    assert trace.ingest_transcript(tmp_path,source)['written']==1
    first=next(trace._iter_trace_rows(tmp_path/trace.TRACE_PATH))
    assert first['event']=='observed'
    assert first['status']['code']=='UNSET'
    assert trace.ingest_transcript(tmp_path,source)['written']==0
    source.write_text(call+'\n'+_line('user',[{'type':'tool_result','tool_use_id':'late','is_error':True,'content':'429 limit'}],'2026-10-19T12:00:01Z')+'\n')
    assert trace.ingest_transcript(tmp_path,source)['written']==1
    assert trace.ingest_transcript(tmp_path,source)['written']==0
    rows=list(trace._iter_trace_rows(tmp_path/trace.TRACE_PATH))
    assert [row['event'] for row in rows]==['observed','reconciled']
    assert rows[0]['span_id']==rows[1]['span_id']
    assert trace.summarize(tmp_path)['tool_calls']==1
    assert trace.summarize(tmp_path)['tool_errors']==1
    assert trace.summarize(tmp_path)['provider_429s']==1


def test_explicit_empty_terminal_result_is_completed(tmp_path):
    source=tmp_path/'session.jsonl'
    source.write_text(_line('assistant',[{'type':'tool_use','id':'empty','name':'Bash','input':{}}],'2026-10-19T12:00:00Z')+'\n'+_line('user',[{'type':'tool_result','tool_use_id':'empty','content':''}],'2026-10-19T12:00:01Z'))
    trace.ingest_transcript(tmp_path,source)
    row=next(trace._iter_trace_rows(tmp_path/trace.TRACE_PATH))
    assert row['event']=='reconciled'
    assert row['status']['code']=='OK'



def test_hook_with_fifo_state_stays_unattributed_without_blocking(tmp_path):
    import os,subprocess,sys
    state=tmp_path/'.build-loop/state.json';state.parent.mkdir();os.mkfifo(state)
    code=f"import sys;sys.path.insert(0,{str(Path(__file__).resolve().parent)!r});from pathlib import Path;import tool_trace as t;s=t.record_hook(Path({str(tmp_path)!r}),'start',{{'tool_name':'Bash','session_id':'s','timestamp':'2026-10-19T12:00:00Z'}});assert s['attributes']['build_loop.run_id'] is None"
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=2)
    assert result.returncode==0,result.stderr


def test_reingest_deduplicates_rotated_legacy_ordinal_spans(tmp_path):
    source=tmp_path/'session.jsonl'
    source.write_text(_line('user','hello','2026-10-19T12:00:00Z')+'\n'+_line('assistant',[{'type':'tool_use','id':'call','name':'Bash','input':{}}],'2026-10-19T12:00:01Z')+'\n'+_line('user',[{'type':'tool_result','tool_use_id':'call','content':'ok'}],'2026-10-19T12:00:02Z'))
    old=trace.build_span(workdir=tmp_path,session_id='session',tool_name='Bash',tool_use_id='call',phase='reconciled',ordinal=1)
    old['attributes'].pop('gen_ai.tool.call.id',None)
    old['attributes'].pop('build_loop.tool.call_id_sha256',None)
    rotated=tmp_path/trace.TRACE_PATH.with_suffix('.jsonl.1')
    rotated.parent.mkdir(parents=True)
    rotated.write_text(json.dumps(old)+'\n')
    assert trace.ingest_transcript(tmp_path,source)['written']==0


def test_failed_ingestion_has_a_visible_bounded_receipt(tmp_path,monkeypatch):
    import pytest,transcript_adapter as adapter
    source=tmp_path/'oversized.jsonl';source.write_text('x'*100)
    monkeypatch.setattr(adapter,'MAX_SOURCE_BYTES',50)
    with pytest.raises(adapter.TranscriptLimitError):trace.ingest_transcript(tmp_path,source)
    receipt=tmp_path/'.build-loop/telemetry/tool-traces.coverage.json'
    assert receipt.exists()
    data=json.loads(receipt.read_text())
    assert data['receipts'][-1]['status']=='skipped'
    assert data['receipts'][-1]['reason']=='input_limit'


def test_semantic_dedup_ignores_span_hash_and_tolerates_corrupt_rotated_shapes(tmp_path):
    source=tmp_path/'session.jsonl'
    source.write_text(_line('assistant',[{'type':'tool_use','id':'call','name':'Bash','input':{}}],'2026-10-19T12:00:01Z')+'\n'+_line('user',[{'type':'tool_result','tool_use_id':'call','content':'ok'}],'2026-10-19T12:00:02Z'))
    span=trace.build_span(workdir=tmp_path,session_id='session',tool_name='Bash',tool_use_id='call',phase='reconciled')
    span['span_id']='arbitrary-legacy-hash'
    path=tmp_path/trace.TRACE_PATH.with_suffix('.jsonl.2');path.parent.mkdir(parents=True)
    shapes=[[],{'attributes':[],'event':'reconciled'}, {'span_id':[],'event':'reconciled','attributes':{'session.id':'session','gen_ai.tool.name':'Bash'}}, {**span,'status':[]},span]
    path.write_text('broken json\n'+'\n'.join(json.dumps(row) for row in shapes))
    assert trace.ingest_transcript(tmp_path,source)['written']==0
    receipt=json.loads((tmp_path/'.build-loop/telemetry/tool-traces.coverage.json').read_text())['receipts'][-1]
    assert receipt['history']['invalid_records']==5
    assert receipt['history']['older_call_identity']=='unavailable'


def test_history_budget_failure_precedes_any_trace_publication(tmp_path,monkeypatch):
    import pytest,transcript_adapter as adapter
    source=tmp_path/'session.jsonl'
    source.write_text(_line('assistant',[{'type':'tool_use','id':'new','name':'Bash','input':{}}],'2026-10-19T12:00:01Z'))
    old=trace.build_span(workdir=tmp_path,session_id='other',tool_name='Bash',tool_use_id='old',phase='observed')
    trace.append_span(tmp_path,old)
    path=tmp_path/trace.TRACE_PATH;before=path.read_bytes()
    monkeypatch.setattr(trace,'MAX_HISTORY_BYTES',1)
    with pytest.raises(adapter.TranscriptLimitError):trace.ingest_transcript(tmp_path,source)
    assert path.read_bytes()==before
    row=json.loads((tmp_path/'.build-loop/telemetry/tool-traces.coverage.json').read_text())['receipts'][-1]
    assert row['status']=='skipped'
    assert row['history']['aggregate_read_budget_bytes']==1


def test_receipts_retain_only_latest_bounded_source_failures(tmp_path,monkeypatch):
    import pytest
    monkeypatch.setattr(trace,'MAX_COVERAGE_RECEIPTS',3)
    for index in range(5):
        source=tmp_path/f'source-{index}.jsonl'
        source.write_text(json.dumps({'sessionId':'actual','type':'user','message':{'content':'hello'}}))
        with pytest.raises(ValueError):trace.ingest_transcript(tmp_path,source,session_id='other')
    path=tmp_path/'.build-loop/telemetry/tool-traces.coverage.json'
    assert path.stat().st_size<=trace.MAX_COVERAGE_BYTES
    rows=json.loads(path.read_text())['receipts']
    assert len(rows)==3 and all(row['status']=='skipped' for row in rows)
    assert rows[0]['source_locator'].endswith('source-2.jsonl')
    assert trace.summarize(tmp_path)['coverage']['ingestion']['receipts']==rows


def test_call_count_budget_rejects_source_before_writes(tmp_path,monkeypatch):
    import pytest,transcript_adapter as adapter
    source=tmp_path/'source.jsonl'
    source.write_text(_line('assistant',[{'type':'tool_use','id':str(index),'name':'Bash','input':{}} for index in range(2)],'2026-10-19T12:00:00Z'))
    monkeypatch.setattr(trace,'MAX_INGEST_CALLS',1)
    with pytest.raises(adapter.TranscriptLimitError):trace.ingest_transcript(tmp_path,source)
    assert not (tmp_path/trace.TRACE_PATH).exists()


def test_actual_cli_rejection_keeps_fail_open_exit_and_source_receipt(tmp_path):
    import subprocess,sys
    from transcript_adapter import MAX_SOURCE_BYTES
    source=tmp_path/'oversized.jsonl'
    with source.open('wb') as handle:handle.truncate(MAX_SOURCE_BYTES+1)
    result=subprocess.run([sys.executable,str(Path(trace.__file__).resolve()),'--workdir',str(tmp_path),'ingest','--transcript',str(source)],input='{}',capture_output=True,text=True,timeout=5)
    assert result.returncode==0
    row=json.loads((tmp_path/'.build-loop/telemetry/tool-traces.coverage.json').read_text())['receipts'][-1]
    assert row['status']=='skipped' and row['reason']=='input_limit'
    assert not (tmp_path/trace.TRACE_PATH).exists()


def test_concurrent_reconciliation_shares_semantic_writer_lock(tmp_path):
    import subprocess,sys
    source=tmp_path/'session.jsonl'
    source.write_text(_line('assistant',[{'type':'tool_use','id':'call','name':'Bash','input':{}}],'2026-10-19T12:00:01Z')+'\n'+_line('user',[{'type':'tool_result','tool_use_id':'call','content':'ok'}],'2026-10-19T12:00:02Z'))
    command=[sys.executable,str(Path(trace.__file__).resolve()),'--workdir',str(tmp_path),'ingest','--transcript',str(source)]
    processes=[subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    for process in processes:
        stdout,stderr=process.communicate(timeout=5)
        assert process.returncode==0,stderr
    assert len(list(trace._iter_trace_rows(tmp_path/trace.TRACE_PATH)))==1


def test_unknown_reconciled_status_is_not_a_success_or_completed_call(tmp_path):
    path=tmp_path/'ledger.jsonl'
    for status in ({},{'code':'UNSET'},{'code':'UNKNOWN'},{'code':[]},{'code':{}},{'code':1},[],None):
        path.write_text(json.dumps({'event':'reconciled','status':status,'attributes':{'gen_ai.tool.name':'Bash'}})+'\n')
        result=trace.summarize_path(path)
        assert result['tool_calls']==0
        assert result['coverage']['invalid_trace_records']==1
    path.write_text(json.dumps({'event':'observed','status':{'code':'UNSET'},'attributes':{'gen_ai.tool.name':'Bash'}})+'\n')
    result=trace.summarize_path(path)
    assert result['tool_calls']==0 and result['coverage']['invalid_trace_records']==0


def test_normalized_leases_replace_original_source_failure_receipt(tmp_path):
    import pytest
    import transcript_adapter as adapter
    source = tmp_path / 'original.jsonl'
    source.write_text(json.dumps({'type':'attachment'}))
    with pytest.raises(adapter.TranscriptReadError):
        trace.ingest_transcript(tmp_path, source)
    assert trace.ingestion_incomplete(trace.summarize(tmp_path))
    source.write_text(_line('assistant', [{'type':'tool_use', 'id':'one', 'name':'Bash', 'input':{}}], '2026-10-19T12:00:01Z'))
    for index in range(3):
        lease = tmp_path / f'normalized-lease-{index}.jsonl'
        adapter.normalize_to_file(source, lease)
        trace.ingest_transcript(tmp_path, lease)
    summary = trace.summarize(tmp_path)
    rows = summary['coverage']['ingestion']['receipts']
    assert len(rows) == 1
    assert rows[0]['source_locator'] == str(source)
    assert rows[0]['status'] == 'ingested'
    assert not trace.ingestion_incomplete(summary)


def test_explicit_source_locator_handles_empty_normalized_stream(tmp_path):
    normalized = tmp_path / 'empty-normalized.jsonl'
    normalized.write_text('')
    original = tmp_path / 'original.jsonl'
    trace.ingest_transcript(tmp_path, normalized, source_locator=str(original))
    rows = trace.summarize(tmp_path)['coverage']['ingestion']['receipts']
    assert rows[0]['source_locator'] == str(original)


def test_incomplete_coverage_preserves_run_and_window_boundaries():
    summary = {'coverage': {'run_filter':'ours', 'recent_cutoff_unix':100,
                           'ingestion':{'status':'observed_sources_only', 'receipts':[
                               {'status':'skipped', 'run_id':'peer', 'updated_at_unix':200},
                               {'status':'skipped', 'run_id':'ours', 'updated_at_unix':50}]}}}
    assert not trace.ingestion_incomplete(summary)
    summary['coverage']['ingestion']['receipts'].append({'status':'skipped', 'run_id':'ours', 'updated_at_unix':200})
    assert trace.ingestion_incomplete(summary)


def test_summary_counts_errors_in_retained_rotations(tmp_path):
    span = trace.build_span(workdir=tmp_path, session_id='s', tool_name='Bash',
                            tool_use_id='one', phase='reconciled', is_error=True)
    path = tmp_path / trace.TRACE_PATH
    path.parent.mkdir(parents=True)
    path.with_suffix('.jsonl.1').write_text(json.dumps(span) + '\n')
    summary = trace.summarize(tmp_path)
    assert summary['tool_calls'] == 1
    assert summary['tool_errors'] == 1


def test_old_invalid_trace_diagnostic_does_not_poison_recent_window(tmp_path):
    import os, time
    path = tmp_path / trace.TRACE_PATH
    path.parent.mkdir(parents=True)
    old = path.with_suffix('.jsonl.1')
    old.write_text('{truncated\n')
    old_time = time.time() - 3600
    os.utime(old, (old_time, old_time))
    current = trace.build_span(workdir=tmp_path, session_id='s', tool_name='Bash', tool_use_id='current', phase='reconciled')
    path.write_text(json.dumps(current) + '\n')
    recent = trace.summarize(tmp_path, recent_seconds=60)
    assert recent['coverage']['invalid_trace_records'] == 1
    assert not trace.ingestion_incomplete(recent)
    assert trace.ingestion_incomplete(trace.summarize(tmp_path))
    # A recently written invalid line has no trustworthy timestamp/run: fail safe.
    with path.open('a') as handle:
        handle.write('{recently truncated\n')
    assert trace.ingestion_incomplete(trace.summarize(tmp_path, recent_seconds=60))


def test_invalid_span_with_other_run_or_old_timestamp_is_diagnostic_only(tmp_path):
    import time
    path = tmp_path / trace.TRACE_PATH
    path.parent.mkdir(parents=True)
    row = {'event':'reconciled','status':{'code':'INVALID'},
           'end_time_unix_nano':time.time_ns(),
           'attributes':{'build_loop.run_id':'peer','gen_ai.tool.name':'Bash'}}
    path.write_text(json.dumps(row) + '\n')
    assert not trace.ingestion_incomplete(trace.summarize(tmp_path, 'ours', recent_seconds=60))
    row['attributes']['build_loop.run_id'] = 'ours'
    row['end_time_unix_nano'] = time.time_ns() - 3600 * 1_000_000_000
    path.write_text(json.dumps(row) + '\n')
    assert not trace.ingestion_incomplete(trace.summarize(tmp_path, 'ours', recent_seconds=60))
    row['end_time_unix_nano'] = time.time_ns()
    path.write_text(json.dumps(row) + '\n')
    assert trace.ingestion_incomplete(trace.summarize(tmp_path, 'ours', recent_seconds=60))


def test_malformed_run_identity_cannot_hide_current_failure(tmp_path):
    import time
    path = tmp_path / trace.TRACE_PATH
    path.parent.mkdir(parents=True)
    for identity in ([], {}, False, 0, '', '   '):
        row = {'event':'reconciled','status':{'code':'INVALID'},
               'end_time_unix_nano':time.time_ns(),
               'attributes':{'build_loop.run_id':identity,'gen_ai.tool.name':'Bash'}}
        path.write_text(json.dumps(row) + '\n')
        summary = trace.summarize(tmp_path, 'ours', recent_seconds=60)
        assert summary['coverage']['invalid_trace_records_in_window'] == 1
        assert trace.ingestion_incomplete(summary)
        assert trace.ingestion_incomplete({'coverage':{'run_filter':'ours',
            'ingestion':{'receipts':[{'run_id':identity,'status':'skipped'}]}}})
    assert not trace.ingestion_incomplete({'coverage':{'run_filter':'ours',
        'ingestion':{'receipts':[{'run_id':'peer','status':'skipped'}]}}})


def test_malformed_receipt_timestamp_cannot_hide_failure(tmp_path):
    import time
    path = tmp_path / trace.TRACE_PATH
    path.parent.mkdir(parents=True)
    coverage = trace._coverage_path(path)
    for timestamp in (False, True, None, "0", [], {}):
        coverage.write_text(json.dumps({"receipts": [{"run_id": "ours",
            "status": "skipped", "updated_at_unix": timestamp}]}))
        assert trace.ingestion_incomplete(trace.summarize(tmp_path, "ours", recent_seconds=60))
    coverage.write_text(json.dumps({"receipts": [{"run_id": "ours",
        "status": "skipped", "updated_at_unix": int(time.time()) - 3600}]}))
    assert not trace.ingestion_incomplete(trace.summarize(tmp_path, "ours", recent_seconds=60))
