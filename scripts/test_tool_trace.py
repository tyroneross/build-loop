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
    source.write_text(_line("assistant", [{"type":"tool_use","id":"id","name":"Bash","input":{"command":"run"}}], "2026-08-10T00:00:00Z"))
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
