#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the host-neutral transcript adapter.

Fixtures are synthetic: a real rollout carries the user's own prompts, and a
test corpus is the wrong place for them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import transcript_adapter as ta  # type: ignore  # noqa: E402


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


CODEX_ROWS = [
    {"timestamp": "2026-08-22T16:14:53Z", "type": "session_meta",
     "payload": {"session_id": "s1", "cwd": "/repos/demo"}},
    {"timestamp": "2026-08-22T16:15:00Z", "type": "event_msg",
     "payload": {"type": "user_message", "message": "add a retry to the fetch helper"}},
    # Same turn, API-level, wrapped in injected context. Must NOT be mined as intent.
    {"timestamp": "2026-08-22T16:15:00Z", "type": "response_item",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text",
                              "text": "<recommended_plugins>Airtable</recommended_plugins>"}]}},
    {"timestamp": "2026-08-22T16:15:01Z", "type": "response_item",
     "payload": {"type": "message", "role": "developer",
                 "content": [{"type": "input_text", "text": "Thread coordination preamble"}]}},
    {"timestamp": "2026-08-22T16:15:02Z", "type": "event_msg",
     "payload": {"type": "agent_message", "message": "I'll add the retry."}},
    {"timestamp": "2026-08-22T16:15:03Z", "type": "response_item",
     "payload": {"type": "custom_tool_call", "name": "exec",
                 "input": "{\"cmd\": \"pytest -q\"}"}},
    {"timestamp": "2026-08-22T16:15:04Z", "type": "response_item",
     "payload": {"type": "custom_tool_call_output",
                 "output": [{"type": "input_text", "text": "3 passed"}]}},
]

CLAUDE_ROWS = [
    {"type": "user", "sessionId": "abc",
     "message": {"role": "user", "content": "add a retry to the fetch helper"}},
    {"type": "assistant", "sessionId": "abc",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}]}},
]


def test_detects_each_host_by_structure(tmp_path: Path) -> None:
    assert ta.detect_format(_write(tmp_path / "cx.jsonl", CODEX_ROWS)) == ta.CODEX
    assert ta.detect_format(_write(tmp_path / "cl.jsonl", CLAUDE_ROWS)) == ta.CLAUDE


def test_claude_transcripts_pass_through_byte_identical(tmp_path: Path) -> None:
    """Wiring a consumer through the adapter must not change Claude behavior."""
    src = _write(tmp_path / "cl.jsonl", CLAUDE_ROWS)
    assert list(ta.iter_events(src)) == CLAUDE_ROWS


def test_codex_turns_normalize_to_the_claude_shape(tmp_path: Path) -> None:
    events = list(ta.iter_events(_write(tmp_path / "cx.jsonl", CODEX_ROWS)))
    kinds = [(e["type"], e["message"]["content"][0]["type"]) for e in events]
    assert kinds == [
        ("user", "text"),
        ("assistant", "text"),
        ("assistant", "tool_use"),
        ("user", "tool_result"),
    ]
    assert events[0]["message"]["content"][0]["text"] == "add a retry to the fetch helper"
    # `command` is synthesized from Codex's `cmd` so consumers that scan
    # input["command"] (git-commit detection, ritual mining) still see it.
    assert events[2]["message"]["content"][0]["input"] == {
        "cmd": "pytest -q", "command": "pytest -q",
    }
    assert events[3]["message"]["content"][0]["content"] == "3 passed"


def test_injected_context_and_developer_turns_are_not_mined_as_intent(tmp_path: Path) -> None:
    """Codex records each turn twice; the API-level copy carries injected text.

    Mining that copy would score '<recommended_plugins>' as something the user
    said, which is exactly what the miner must never do.
    """
    events = list(ta.iter_events(_write(tmp_path / "cx.jsonl", CODEX_ROWS)))
    blob = json.dumps(events)
    assert "recommended_plugins" not in blob
    assert "Thread coordination preamble" not in blob


def test_session_cwd_resolves_for_codex_and_is_absent_for_claude(tmp_path: Path) -> None:
    assert ta.session_cwd(_write(tmp_path / "cx.jsonl", CODEX_ROWS)) == "/repos/demo"
    assert ta.session_cwd(_write(tmp_path / "cl.jsonl", CLAUDE_ROWS)) is None


def test_malformed_lines_never_raise(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"type": "event_msg", "payload": {"type":"user_message","message":"ok"}}\n'
                    "not json at all\n"
                    "[]\n", encoding="utf-8")
    assert len(list(ta.iter_events(path))) == 1


def test_missing_file_yields_nothing_instead_of_raising(tmp_path: Path) -> None:
    assert list(ta.iter_events(tmp_path / "nope.jsonl")) == []
    assert ta.detect_format(tmp_path / "nope.jsonl") == ta.UNKNOWN


def test_normalize_to_file_round_trips(tmp_path: Path) -> None:
    src = _write(tmp_path / "cx.jsonl", CODEX_ROWS)
    dest = tmp_path / "norm.jsonl"
    assert ta.normalize_to_file(src, dest) == 4
    assert ta.detect_format(dest) == ta.CLAUDE


def test_command_key_is_synthesized_for_every_codex_payload_shape(tmp_path: Path) -> None:
    """session_is_trivial() and the ritual miner both read input["command"].

    Codex emits the command under "cmd" inside a JS program string, so without
    this mapping a Codex session that ran `git commit` scores as trivial.
    """
    rows = [
        {"type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec",
            "input": 'tools.exec_command({"cmd":"git commit -m wip"})'}},
    ]
    events = list(ta.iter_events(_write(tmp_path / "cx.jsonl", rows)))
    assert "git commit" in events[0]["message"]["content"][0]["input"]["command"]


def _rollout(root: Path, name: str, cwd: str, mtime: float) -> Path:
    import os

    day = root / "2026" / "08" / "22"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"rollout-{name}.jsonl"
    path.write_text(json.dumps(
        {"timestamp": "2026-08-22T16:14:53Z", "type": "session_meta",
         "payload": {"session_id": name, "cwd": cwd}}) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_finds_the_newest_rollout_for_a_repo(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    _rollout(root, "old", "/repos/demo", 1_000_000)
    newest = _rollout(root, "new", "/repos/demo", 2_000_000)
    _rollout(root, "other", "/repos/elsewhere", 3_000_000)
    assert ta.find_codex_transcript("/repos/demo", sessions_root=root) == newest


def test_repo_names_containing_spaces_resolve(tmp_path: Path) -> None:
    """Space-in-path repos were a real failure cluster for the promotion path."""
    root = tmp_path / "sessions"
    target = _rollout(root, "spaced", "/repos/My Repo Name", 1_000_000)
    assert ta.find_codex_transcript("/repos/My Repo Name", sessions_root=root) == target


def test_trailing_slash_does_not_prevent_a_match(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    target = _rollout(root, "s", "/repos/demo", 1_000_000)
    assert ta.find_codex_transcript("/repos/demo/", sessions_root=root) == target


def test_unknown_repo_and_missing_store_return_none(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    _rollout(root, "s", "/repos/demo", 1_000_000)
    assert ta.find_codex_transcript("/repos/nothing", sessions_root=root) is None
    assert ta.find_codex_transcript("/repos/demo", sessions_root=tmp_path / "gone") is None


def test_scan_is_bounded_so_an_unbounded_store_cannot_stall_a_hook(tmp_path: Path) -> None:
    """The rollout store grows without limit; a Stop hook must still return fast."""
    root = tmp_path / "sessions"
    for i in range(12):
        _rollout(root, f"noise{i}", "/repos/elsewhere", 9_000_000 + i)
    _rollout(root, "target", "/repos/demo", 1_000)
    assert ta.find_codex_transcript("/repos/demo", sessions_root=root, max_scan=5) is None
    assert ta.find_codex_transcript("/repos/demo", sessions_root=root, max_scan=50) is not None


def test_four_sweeps_share_one_normalization(tmp_path: Path) -> None:
    """Four Stop sweeps fire per session; four multi-MB copies is a leak."""
    src = _write(tmp_path / "cx.jsonl", CODEX_ROWS)
    cache = tmp_path / "cache"
    paths = {ta.normalize_cached(src, cache) for _ in range(4)}
    assert len(paths) == 1
    assert len(list(cache.glob("normalized-*.jsonl"))) == 1


def test_cache_refreshes_when_the_session_grows(tmp_path: Path) -> None:
    """A session is still being written at Stop time; a stale copy loses turns."""
    import os

    src = _write(tmp_path / "cx.jsonl", CODEX_ROWS)
    cache = tmp_path / "cache"
    before = ta.normalize_cached(src, cache).read_text().count("\n")
    _write(src, CODEX_ROWS + [
        {"type": "event_msg", "payload": {"type": "user_message", "message": "one more"}}])
    os.utime(src, (9_999_999_999, 9_999_999_999))
    assert ta.normalize_cached(src, cache).read_text().count("\n") == before + 1


def test_stale_cache_entries_are_reaped(tmp_path: Path) -> None:
    import os

    cache = tmp_path / "cache"
    cache.mkdir()
    old = cache / "normalized-deadbeefdeadbeef.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    os.utime(old, (1_000_000, 1_000_000))
    ta.normalize_cached(_write(tmp_path / "cx.jsonl", CODEX_ROWS), cache)
    assert not old.exists()


def test_partial_files_are_never_left_behind(tmp_path: Path) -> None:
    """Readers must see a complete file or none -- four sweeps race on this."""
    cache = tmp_path / "cache"
    ta.normalize_cached(_write(tmp_path / "cx.jsonl", CODEX_ROWS), cache)
    assert list(cache.glob("*.part")) == []


def test_empty_source_produces_no_cache_entry(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert ta.normalize_cached(empty, tmp_path / "cache") is None


def test_response_only_prose_preserves_ids_timestamps_errors_and_provenance(tmp_path):
    rows = [
        {"type":"session_meta", "payload":{"id":"session-one"}},
        {"type":"response_item", "timestamp":"2026-10-19T12:00:00Z", "payload":{
            "type":"message", "role":"user", "content":[{"type":"input_text","text":"Please fix parsing"}]}},
        {"type":"response_item", "timestamp":"2026-10-19T12:00:01Z", "payload":{
            "type":"function_call", "name":"exec", "call_id":"call-one", "arguments":"{}"}},
        {"type":"response_item", "timestamp":"2026-10-19T12:00:02Z", "payload":{
            "type":"function_call_output", "call_id":"call-one", "output":"Process exited with code 2"}},
    ]
    events = list(ta.iter_events(_write(tmp_path/'one.jsonl',rows),include_provenance=True))
    assert events[0]['message']['content'][0]['text']=='Please fix parsing'
    assert events[0]['timestamp']=='2026-10-19T12:00:00Z'
    assert events[0]['_source']['session_id']=='session-one'
    assert events[0]['_source']['record_index']==2
    assert events[1]['message']['content'][0]['id']=='call-one'
    assert events[2]['message']['content'][0]['tool_use_id']=='call-one'
    assert events[2]['message']['content'][0]['is_error'] is True


def test_prose_deduplicates_only_equivalent_representations(tmp_path):
    response = lambda text: {'type':'response_item','payload':{'type':'message','role':'user',
                                                'content':[{'type':'input_text','text':text}]}}
    ui = lambda text: {'type':'event_msg','payload':{'type':'user_message','message':text}}
    for rows in ([response('same'),ui('same')],[ui('same'),response('same')]):
        assert len(list(ta.iter_events(_write(tmp_path/'same.jsonl',rows))))==1
    rows=[response('first'),ui('second')]
    assert len(list(ta.iter_events(_write(tmp_path/'different.jsonl',rows))))==2


def test_cache_detects_same_size_rewrite_with_restored_mtime(tmp_path):
    import os
    src=_write(tmp_path/'source.jsonl',[{'type':'event_msg','payload':{'type':'user_message','message':'alpha'}}])
    before=src.stat()
    first=ta.normalize_cached(src,tmp_path/'cache')
    src.write_text(src.read_text().replace('alpha','bravo'))
    os.utime(src,ns=(before.st_atime_ns,before.st_mtime_ns))
    second=ta.normalize_cached(src,tmp_path/'cache')
    assert first!=second
    assert 'bravo' in second.read_text()


def test_concurrent_requests_normalize_once(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    src=_write(tmp_path/'source.jsonl',CODEX_ROWS)
    calls=[]
    original=ta.normalize_to_file
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(ta,'normalize_to_file',counted)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:ta.normalize_cached(src,tmp_path/'cache'),range(4)))
    assert len(set(results))==1 and results[0] is not None
    assert len(calls)==1


def test_cache_parser_revision_and_capacity(tmp_path,monkeypatch):
    src=_write(tmp_path/'source.jsonl',CODEX_ROWS)
    cache=tmp_path/'cache'
    first=ta.normalize_cached(src,cache)
    monkeypatch.setattr(ta,'PARSER_VERSION','test-new-version')
    second=ta.normalize_cached(src,cache)
    assert first!=second
    monkeypatch.setattr(ta,'CACHE_MAX_ENTRIES',2)
    for index in range(3):
        ta.normalize_cached(_write(tmp_path/f'{index}.jsonl',CODEX_ROWS),cache)
    assert len(list(cache.glob('normalized-*.jsonl')))<=2
    monkeypatch.setattr(ta,'CACHE_MAX_BYTES',second.stat().st_size if second.exists() else 2000)
    ta.normalize_cached(src,cache)
    assert sum(p.stat().st_size for p in cache.glob('normalized-*.jsonl'))<=ta.CACHE_MAX_BYTES


def test_changed_during_normalization_is_not_published(tmp_path,monkeypatch):
    src=_write(tmp_path/'source.jsonl',CODEX_ROWS)
    original=ta.normalize_to_file
    def changing(source,dest):
        count=original(source,dest)
        with source.open('a') as handle:handle.write('\n')
        return count
    monkeypatch.setattr(ta,'normalize_to_file',changing)
    cache=tmp_path/'cache'
    assert ta.normalize_cached(src,cache) is None
    assert not list(cache.glob('normalized-*.jsonl'))


def test_input_budget_is_explicit_and_publishes_no_partial_artifact(tmp_path,monkeypatch):
    import pytest
    src=_write(tmp_path/'source.jsonl',CODEX_ROWS)
    monkeypatch.setattr(ta,'MAX_RECORD_BYTES',10)
    with pytest.raises(ta.TranscriptLimitError):list(ta.iter_events(src))
    assert ta.normalize_cached(src,tmp_path/'cache') is None
    assert not list((tmp_path/'cache').glob('*.part'))


def test_midstream_read_failure_never_publishes_partial_normalization(tmp_path,monkeypatch):
    import io
    import pytest
    src=_write(tmp_path/'source.jsonl',CLAUDE_ROWS)
    content=src.read_bytes()
    from contextlib import contextmanager
    original=ta._open_regular_binary
    class BrokenReader(io.BytesIO):
        calls=0
        def readline(self,*args):
            self.calls+=1
            if self.calls>1:raise OSError('injected midstream read failure')
            return super().readline(*args)
    @contextmanager
    def failing_open(path):
        if path==src:
            with BrokenReader(content) as reader: yield reader
        else:
            with original(path) as reader: yield reader
    monkeypatch.setattr(ta,'_open_regular_binary',failing_open)
    dest=tmp_path/'normalized.jsonl'
    with pytest.raises(ta.TranscriptReadError):ta.normalize_to_file(src,dest)
    assert not dest.exists()
    cache=tmp_path/'cache'
    assert ta.normalize_cached(src,cache) is None
    assert not list(cache.glob('normalized-*.jsonl'))
    assert not list(cache.glob('*.part'))


def test_normalized_round_trip_preserves_source_and_records_container(tmp_path):
    src=_write(tmp_path/'source.jsonl',[{'type':'session_meta','payload':{'id':'source-session'}},
        {'type':'response_item','payload':{'type':'message','role':'user',
            'content':[{'type':'input_text','text':'Always use stable IDs'}]}}])
    first=list(ta.iter_events(src,include_provenance=True))[0]
    dest=tmp_path/'normalized.jsonl'
    ta.normalize_to_file(src,dest)
    reread=list(ta.iter_events(dest,include_provenance=True))[0]
    assert reread['_source']==first['_source']
    assert reread['_source']['host']=='codex'
    assert reread['_source']['record_index']==2
    assert reread['_container']['path']==str(dest.resolve())
    assert reread['_container']['record_index']==1


def test_named_pipe_is_rejected_before_blocking_open(tmp_path):
    import os,pytest
    source=tmp_path/'pipe';os.mkfifo(source)
    import transcript_adapter as adapter
    with pytest.raises(adapter.TranscriptReadError):
        list(adapter.iter_events(source,require_complete=True))
    assert adapter.normalize_cached(source,tmp_path/'cache') is None


def test_metadata_public_entries_return_promptly_for_fifo_in_child(tmp_path):
    import os, subprocess, sys
    source=tmp_path/'pipe';os.mkfifo(source)
    expressions = [
        "assert a.session_key(p) == 'pipe'",
        "assert a.detect_format(p) == a.UNKNOWN",
        "assert a.session_cwd(p) is None",
        "assert a.find_codex_transcript('/repo',p.parent) is None",
        "assert a.normalize_cached(p,p.parent/'cache') is None",
        "list(a.iter_events(p,require_complete=True))",
        "a.normalized_cache_path(p,p.parent/'cache')",
        "a.normalize_to_file(p,p.parent/'normalized.jsonl')",
    ]
    # Discovery must encounter the pipe too, not an empty candidate set.
    (tmp_path/'rollout-pipe.jsonl').symlink_to(source)
    for index, expression in enumerate(expressions):
        prefix = f"import sys;sys.path.insert(0,{str(Path(__file__).resolve().parent)!r});from pathlib import Path;import transcript_adapter as a;p=Path({str(source)!r})"
        code = (prefix + f"\ntry:\n {expression}\nexcept a.TranscriptReadError:\n pass\nelse:\n raise AssertionError('nonregular source was accepted')"
                if index >= 5 else prefix + "\n" + expression)
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=2)
        assert result.returncode == 0, (expression,result.stderr)


def test_metadata_record_and_source_limits_keep_fallback_contracts(tmp_path,monkeypatch):
    source=tmp_path/'rollout-too-large.jsonl'
    source.write_text('{"type":"session_meta","payload":{"id":"wrong","cwd":"/repo"},"padding":"'+'x'*1000+'"}\n')
    monkeypatch.setattr(ta,'MAX_RECORD_BYTES',128)
    for check in (lambda:ta.session_key(source)==source.stem,
                  lambda:ta.detect_format(source)==ta.UNKNOWN,
                  lambda:ta.session_cwd(source) is None,
                  lambda:ta.find_codex_transcript('/repo',tmp_path) is None):
        assert check()
    monkeypatch.setattr(ta,'MAX_RECORD_BYTES',2000)
    monkeypatch.setattr(ta,'MAX_SOURCE_BYTES',128)
    assert ta.session_key(source)==source.stem
    assert ta.detect_format(source)==ta.UNKNOWN
    assert ta.session_cwd(source) is None
    assert ta.find_codex_transcript('/repo',tmp_path) is None


def test_descriptor_verification_rejects_replacement_fifo_and_closes_fd(tmp_path,monkeypatch):
    import os,pytest
    source=tmp_path/'source.jsonl'
    source.write_text('{}\n')
    assert source.is_file()  # The old pathname precheck passes.
    original_open=os.open
    descriptors=[]
    def replace_before_open(path,flags,*args,**kwargs):
        if Path(path)==source:
            source.unlink();os.mkfifo(source)
        descriptor=original_open(path,flags,*args,**kwargs)
        descriptors.append(descriptor)
        return descriptor
    monkeypatch.setattr(ta.os,'open',replace_before_open)
    with pytest.raises(ta.TranscriptReadError):list(ta.iter_events(source,require_complete=True))
    with pytest.raises(OSError):os.fstat(descriptors[0])


def test_descriptor_is_closed_when_fstat_fails(tmp_path,monkeypatch):
    import os,pytest
    source=tmp_path/'source.jsonl';source.write_text('{}\n')
    original_open=os.open;original_fstat=os.fstat;descriptors=[]
    def capture(*args,**kwargs):
        descriptor=original_open(*args,**kwargs);descriptors.append(descriptor);return descriptor
    def failing_stat(descriptor):raise OSError('injected fstat failure')
    monkeypatch.setattr(ta.os,'open',capture)
    monkeypatch.setattr(ta.os,'fstat',failing_stat)
    with pytest.raises(OSError):
        with ta._open_regular_binary(source): pass
    with pytest.raises(OSError):original_fstat(descriptors[0])



def test_unavailable_nonblocking_support_fails_closed_before_open(tmp_path,monkeypatch):
    import pytest
    source=tmp_path/'source';source.write_text('{}\n')
    monkeypatch.delattr(ta.os,'O_NONBLOCK',raising=False)
    monkeypatch.setattr(ta.os,'open',lambda *args,**kwargs:pytest.fail('blocking fallback open'))
    with pytest.raises(ta.TranscriptReadError):
        with ta._open_regular_binary(source):pass


def test_codex_real_multiblock_status_and_bounded_headers(tmp_path):
    # Actual authorized rollout's structural shape: multiple text blocks each
    # contain one unified-exec JSON result, including a nonzero exit code.
    row={"type":"response_item","payload":{"type":"function_call_output","call_id":"actual-shape",
        "output":[{"type":"text","text":json.dumps({"chunk_id":"redacted","exit_code":1,"original_token_count":1,"output":"redacted","wall_time_seconds":0.1})},
                  {"type":"text","text":json.dumps({"chunk_id":"other","exit_code":0,"original_token_count":1,"output":"ok","wall_time_seconds":0.1})}]}}
    event=next(ta.iter_events(_write(tmp_path/'actual.jsonl',[row])))
    assert event['message']['content'][0]['is_error'] is True
    for output in [json.dumps({'output':'redacted','metadata':{'exit_code':2}}),
                   'Chunk ID: fixture\nWall time: 0.1 seconds\nProcess exited with code 2\nFinal output:\nredacted',
                   'Chunk ID: fixture\nWall time: 0.1 seconds\nExit code: 12\nOutput:\nredacted']:
        assert ta._codex_output_is_error(output)
    for output in ['Chunk ID: fixture\nWall time: 0.1 seconds\nProcess exited with code 0\nFinal output:\nProcess exited with code 2',
                   'source example\nProcess exited with code 2',
                   'Chunk ID: fixture\nFinal output:\nExit code: 2']:
        assert not ta._codex_output_is_error(output)


def test_original_source_locator_survives_cache_and_second_normalization(tmp_path):
    source=_write(tmp_path/'original.jsonl',[{'type':'session_meta','payload':{'session_id':'original-session'}},
        {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'Always keep provenance.'}]}}])
    cached=ta.normalize_cached(source,tmp_path/'cache')
    assert ta.session_key(source)=='original-session'
    first=next(ta.iter_events(cached,include_provenance=True))
    assert first['_source']['path']==str(source.resolve())
    second=tmp_path/'second.jsonl';ta.normalize_to_file(cached,second)
    assert next(ta.iter_events(second,include_provenance=True))['_source']==first['_source']


def test_user_instructions_context_excluded_without_losing_response_only_human(tmp_path):
    rows=[{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':text}]}}
          for text in ['<user_instructions>Always inject context.</user_instructions>','Please fix the parser.']]
    events=list(ta.iter_events(_write(tmp_path/'source.jsonl',rows)))
    assert [ta.human_text(r['message']['content']) for r in events]==['Please fix the parser.']


def test_lease_survives_competing_publication_and_budget_reap(tmp_path,monkeypatch):
    import os
    source=_write(tmp_path/'one.jsonl',CODEX_ROWS);cache=tmp_path/'cache'
    leased=ta.normalize_cached(source,cache)
    expected=leased.read_bytes()
    monkeypatch.setattr(ta,'CACHE_MAX_ENTRIES',1)
    other=_write(tmp_path/'two.jsonl',CLAUDE_ROWS)
    assert ta.normalize_cached(other,cache) is None  # readers own the entire cap
    assert leased.read_bytes()==expected
    assert ta._cache_usage(cache)[1]<=1
    leased.unlink()
    assert ta.normalize_cached(other,cache) is not None


def test_orphan_parts_reclaimed_but_live_writer_parts_retained(tmp_path):
    import os,time,subprocess,sys
    cache=tmp_path/'cache';cache.mkdir()
    child=subprocess.Popen([sys.executable,'-c','pass']);child.wait()
    dead=cache/f'.normalized-old.jsonl.{child.pid}.dead.part';dead.write_text('orphan')
    live=cache/f'.normalized-live.jsonl.{os.getpid()}.live.part';live.write_text('active')
    for p in (dead,live):os.utime(p,(time.time()-600,time.time()-600))
    ta._reap(cache)
    assert not dead.exists() and live.read_text()=='active'


def test_cache_lock_contention_is_bounded(tmp_path):
    import time
    from atomic_io import LockedFile
    source=_write(tmp_path/'source.jsonl',CODEX_ROWS);cache=tmp_path/'cache'
    with LockedFile(cache/'normalization'):
        before=time.monotonic()
        assert ta.normalize_cached(source,cache) is None
        assert time.monotonic()-before<1.5


def test_dead_worker_lease_is_reaped_without_touching_active_reader(tmp_path):
    import os,subprocess,sys
    source=_write(tmp_path/'source.jsonl',CODEX_ROWS);cache=tmp_path/'cache'
    active=ta.normalize_cached(source,cache,lease_owner_pid=os.getpid())
    child=subprocess.Popen([sys.executable,'-c','pass']);child.wait()
    dead=cache/f'.normalized-fixture.lease-{child.pid}-dead.jsonl'
    os.link(active,dead)
    ta._reap(cache)
    assert not dead.exists() and active.exists()
    active.unlink()


def test_structured_tool_result_and_malformed_blocks_keep_safe_status(tmp_path):
    rows=[{'type':'response_item','payload':{'type':'function_call_output','call_id':'dict',
           'output':{'output':'redacted','metadata':{'exit_code':3}}}},
          {'type':'response_item','payload':{'type':'function_call_output','call_id':'bad',
           'output':[{'type':'text','text':None},{'type':'image','text':'ERROR: unrelated image caption'}]}}]
    events=list(ta.iter_events(_write(tmp_path/'source.jsonl',rows)))
    assert [event['message']['content'][0]['is_error'] for event in events]==[True,False]


def test_killed_normalizer_parts_are_reclaimed_without_deleting_live_writer(tmp_path):
    import os,time,subprocess,sys
    cache=tmp_path/'cache';cache.mkdir()
    source=tmp_path/'source.jsonl';source.write_text('{}\n')
    ready=tmp_path/'ready'
    code=f"import sys,time;sys.path.insert(0,{str(Path(__file__).resolve().parent)!r});from pathlib import Path;import transcript_adapter as a\ndef records(*args,**kwargs):\n yield {{'type':'user','message':{{'role':'user','content':'fixture'}}}}\n Path({str(ready)!r}).touch()\n time.sleep(10)\na.iter_events=records\na.normalize_to_file(Path({str(source)!r}),Path({str(cache/'normalized-killed.jsonl')!r}))"
    child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+3
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert ready.exists()
        child.kill();child.wait(timeout=2)
        parts=list(cache.glob('.normalized-*.part'));assert len(parts)==1
        live=cache/f'.normalized-live.jsonl.{os.getpid()}.live.part';live.write_text('live')
        for part in [*parts,live]:os.utime(part,(time.time()-600,time.time()-600))
        ta._reap(cache)
        assert not parts[0].exists() and live.exists()
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=2)
        child.stderr.close()


def test_normalized_publication_respects_downstream_record_budget(tmp_path,monkeypatch):
    import pytest
    source=_write(tmp_path/'source.jsonl',[{'type':'user','message':{'role':'user','content':'fixture'}}])
    monkeypatch.setattr(ta,'MAX_RECORD_BYTES',source.stat().st_size+1)
    dest=tmp_path/'normalized.jsonl'
    with pytest.raises(ta.TranscriptLimitError):ta.normalize_to_file(source,dest)
    assert not dest.exists() and not list(tmp_path.glob('*.part'))


def test_complete_ingestion_rejects_unsupported_records_in_same_stream(tmp_path):
    import pytest
    unsupported = {'type':'future_host_turn','payload':{'message':'Always verify before commit'}}
    for rows in ([unsupported], [unsupported] + CODEX_ROWS, CODEX_ROWS + [unsupported],
                 [{'type':'session_meta','payload':{'id':'s'}} , unsupported],
                 CLAUDE_ROWS + [unsupported]):
        source = _write(tmp_path/'unsupported.jsonl', rows)
        with pytest.raises(ta.TranscriptReadError):
            list(ta.iter_events(source, require_complete=True))


def test_complete_ingestion_accepts_empty_and_known_metadata_prefixes(tmp_path):
    metadata = [{'type':'session_meta','payload':{'id':'s'}},
                {'type':'turn_context','payload':{}},
                {'type':'event_msg','payload':{'type':'token_count'}}]
    for rows in ([], metadata, [{'type':'file-history-snapshot','snapshot':{}}],
                 [{'type':'progress','data':{}}] + CLAUDE_ROWS,
                 metadata + CODEX_ROWS):
        source = _write(tmp_path/'supported.jsonl',rows)
        events = list(ta.iter_events(source,require_complete=True))
        if not rows or rows == metadata:
            assert events == []
        elif rows[-1].get('type') in {'assistant','response_item'}:
            assert events


def test_complete_reader_rejects_cross_host_tool_records(tmp_path):
    import pytest
    codex = [
        {"type": "session_meta", "payload": {"id": "codex-session"}},
        {"type": "response_item", "payload": {"type": "function_call", "call_id": "a", "name": "exec", "arguments": "{}"}},
    ]
    claude = [{"type": "assistant", "sessionId": "claude-session", "message": {
        "role": "assistant", "content": [{"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "true"}}]}}]
    for rows in (codex + claude, claude + codex):
        source = _write(tmp_path / "mixed.jsonl", rows)
        with pytest.raises(ta.TranscriptReadError, match="unsupported transcript records"):
            list(ta.iter_events(source, require_complete=True))
        projected = ta.normalize_cached(source, tmp_path / "cache")
        assert projected is not None
        with pytest.raises(ta.TranscriptReadError):
            list(ta.iter_events(projected, require_complete=True))


def test_complete_reader_rejects_unknown_nested_codex_records(tmp_path):
    import pytest
    for kind in ("response_item", "event_msg"):
        source = _write(tmp_path / "unknown.jsonl", [
            {"type": "session_meta", "payload": {"id": "session"}},
            {"type": kind, "payload": {"type": "local_shell_call", "call_id": "missing", "action": {"command": ["true"]}}},
        ])
        with pytest.raises(ta.TranscriptReadError, match="unsupported transcript records"):
            list(ta.iter_events(source, require_complete=True))
        assert ta.normalize_cached(source, tmp_path / "cache") is None


def test_complete_reader_accepts_supported_metadata_and_reasoning_prefixes(tmp_path):
    rows = [
        {"type": "file-history-snapshot"},
        {"type": "session_meta", "payload": {"id": "session"}},
        {"type": "turn_context", "payload": {"cwd": "/repo"}},
        {"type": "response_item", "payload": {"type": "reasoning", "summary": []}},
        {"type": "event_msg", "payload": {"type": "agent_reasoning", "text": "internal"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {}}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": []}},
        {"type": "response_item", "payload": {"type": "function_call", "call_id": "a", "name": "exec", "arguments": "{}"}},
    ]
    events = list(ta.iter_events(_write(tmp_path / "prefixes.jsonl", rows), require_complete=True))
    assert len(events) == 1
    assert events[0]["message"]["content"][0]["id"] == "a"


def test_discovery_json_retains_source_on_normalization_failure(tmp_path, monkeypatch, capsys):
    source = _write(tmp_path / 'rollout.jsonl', [
        {'type':'session_meta', 'payload':{'id':'session'}},
        {'type':'response_item', 'payload':{'type':'local_shell_call', 'call_id':'one'}}])
    monkeypatch.setattr(ta, 'find_codex_transcript', lambda cwd: source)
    args = ['--find-codex-session', str(tmp_path), '--cache-dir', str(tmp_path/'cache')]
    assert ta.main(args + ['--json']) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt == {'source_path':str(source), 'normalized_path':None,
                       'status':'normalization_unavailable'}
    assert ta.main(args) == 1
    assert capsys.readouterr().out == ''
    source.write_text(json.dumps({'type':'response_item', 'payload':{
        'type':'function_call', 'call_id':'one', 'name':'exec', 'arguments':'{}'}}) + '\n')
    assert ta.main(args + ['--json']) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['source_path'] == str(source)
    assert receipt['status'] == 'normalized'
    assert Path(receipt['normalized_path']).is_file()


def test_lenient_projection_preserves_prose_and_incomplete_coverage(tmp_path):
    import pytest
    source = _write(tmp_path / 'source.jsonl', [
        {'type':'session_meta','payload':{'id':'s','cwd':str(tmp_path)}},
        {'type':'event_msg','payload':{'type':'user_message','message':'Always preserve my decision.'}},
        {'type':'response_item','payload':{'type':'local_shell_call','call_id':'unknown'}},
        {'type':'event_msg','payload':{'type':'agent_message','message':'I will preserve this decision.'}},
    ])
    projected = ta.normalize_cached(source, tmp_path / 'cache')
    assert projected is not None
    assert 'Always preserve my decision.' in projected.read_text()
    assert len(list(ta.iter_events(projected))) == 2
    with pytest.raises(ta.TranscriptReadError):
        list(ta.iter_events(projected, require_complete=True))
    again = tmp_path / 'again.jsonl'
    ta.normalize_to_file(projected, again)
    with pytest.raises(ta.TranscriptReadError):
        list(ta.iter_events(again, require_complete=True))
    import tool_trace
    with pytest.raises(ta.TranscriptReadError):
        tool_trace.ingest_transcript(tmp_path, projected)
    assert tool_trace.ingestion_incomplete(tool_trace.summarize(tmp_path))
    # The actual SessionEnd entrypoint must receive the same usable prose.
    from hooks.session_end_retro_sweep import normalize_transcript
    retro_source, _ = normalize_transcript(source, tmp_path / 'retro-cache')
    assert retro_source != source
    assert 'Always preserve my decision.' in retro_source.read_text()


def test_lenient_normalization_keeps_missing_source_as_read_failure(tmp_path):
    import pytest
    destination = tmp_path / 'normalized.jsonl'
    with pytest.raises(ta.TranscriptReadError):
        ta.normalize_to_file(tmp_path / 'missing.jsonl', destination)
    assert not destination.exists()
