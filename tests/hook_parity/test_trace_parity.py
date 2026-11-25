# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Parity cases for the tool-trace hook (``scripts/tool_trace.py hook``).

``test_python_contract`` pins each case's outcome on the Python hook alone
(the oracle). ``test_bl_matches_python`` runs ``bl hook trace`` on the same
case and requires identical exit status, stdout, stderr and files.
Supported UTF-8 host events retain strict Python parity. Explicit unsupported
input and Python-only decoding settings have separate native contract tests;
no trace event is handed to Python.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

from tests.hook_parity.failure_evidence import save_failure, without_diagnostics

from tests.hook_parity import trace_harness as h
from tests.hook_parity.trace_harness import Case, payload

SESSION = "sess-1"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _silent(snap):
    assert (snap.rc, snap.stdout, snap.stderr) == (0, b"", b""), snap


def nothing(snap):
    _silent(snap)
    assert snap.new_lines == []


def span(*, tool: str, call: str = "", session: str = SESSION, event: str = "end",
         status: str | None = None, attrs: dict | None = None):
    """Exactly one new line with these identity fields and attributes."""
    def check(snap):
        _silent(snap)
        assert len(snap.new_lines) == 1, snap.files
        line = snap.new_lines[0]
        code = status or {"end": "OK", "error": "ERROR"}.get(event, "UNSET")
        assert line["schema"] == "build-loop.otel-tool-span.v1"
        assert line["trace_id"] == sha(session)[:32]
        assert line["span_id"] == sha(f"{session}|{call}|{tool}|0")[:16]
        assert line["name"] == f"execute_tool {tool}"
        assert (line["kind"], line["event"], line["status"]) == ("INTERNAL", event, {"code": code})
        assert (line["parent_span_id"], line["end_time_unix_nano"], line["duration_ms"]) == (None, None, None)
        a = line["attributes"]
        assert a["gen_ai.tool.name"] == tool
        assert a["session.id"] == session
        assert a["build_loop.tool.call_id_sha256"] == sha(call)
        assert a["build_loop.tool.phase"] == event
        assert a["gen_ai.operation.name"] == "execute_tool"
        for k, v in (attrs or {}).items():
            assert a.get(k, "<absent>") == v, (k, a.get(k, "<absent>"))
    return check


def raw_lines_canonical(snap):
    """Every new trace line is ``json.dumps(sort_keys, separators=(',',':'))``:
    key order, separators and ASCII escaping are the byte contract."""
    for rel, body in snap.files.items():
        if rel.endswith("tool-traces.jsonl") and '{"attributes":' in body:
            text = body.split(":", 2)[2]
            for line in text.splitlines():
                if line.startswith('{"attributes":'):
                    parsed = json.loads(line.replace("<T>", "0"))
                    canon = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
                    assert canon.replace('"start_time_unix_nano":0', '"start_time_unix_nano":<T>') == line
            assert text.endswith("\n")


def all_of(*checks):
    def check(snap):
        for c in checks:
            c(snap)
    return check


def claude(event: str, tool: str = "Bash", **extra) -> bytes:
    body = {"session_id": SESSION, "transcript_path": "/x/t.jsonl", "cwd": "/x",
            "hook_event_name": event, "tool_name": tool,
            "tool_input": {"command": "ls -la", "description": "List files"},
            "tool_use_id": "toolu_01"}
    body.update(extra)
    return json.dumps(body).encode()


def codex(response, **extra) -> bytes:
    body = {"session_id": SESSION, "turn_id": "turn-1", "hook_event_name": "PostToolUse",
            "tool_name": "shell", "tool_input": {"command": ["bash", "-lc", "ls"]},
            "tool_use_id": "call_1", "tool_response": response}
    body.update(extra)
    return json.dumps(body).encode()


def deep(n: int):
    v: object = "leaf"
    for _ in range(n):
        v = {"k": v}
    return v


def _active_state(session: str = SESSION) -> bytes:
    return json.dumps({"execution": {"run_id": "bl-run-1", "started_at": "2026-01-01T00:00:00Z",
                                     "current_session_id": session}}).encode()


def _setup_rotation(size: int, rotated: tuple[int, ...] = ()):
    def setup(world):
        h.prefill(world.proj / h.TRACE, size)
        for i in rotated:
            h.prefill(world.proj / f"{h.TRACE}.{i}", 2000 + i)
    return setup


def _chmod_telemetry(mode: int, files: dict[str, int] | None = None):
    def setup(world):
        tel = world.proj / h.TELEMETRY
        tel.mkdir(parents=True, exist_ok=True)
        for name, size in (files or {}).items():
            h.prefill(tel / name, size)
        tel.chmod(mode)
    return setup


def _telemetry_is_file(world):
    (world.proj / h.TELEMETRY).write_text("not a dir")


NON_ASCII = {"command": "echo 'héllo 😀 日本' \u007f\u0001\t\"q\" \\ end", "ключ": "значение"}
BIG = "x" * 120_000
RAW = json.dumps({"tool_name": "Edit", "session_id": SESSION, "tool_input": NON_ASCII},
                 ensure_ascii=False).encode()

CASES = [
    # --- host payload shapes, each phase ---------------------------------
    Case("claude_pre_start", claude("PreToolUse"), phase="start",
         expect=span(tool="Bash", call="toolu_01", event="start", attrs={
             "gen_ai.tool.call.arguments.keys": ["command", "description"],
             "gen_ai.tool.call.arguments.preview": '{"command":"ls -la","description":"List files"}',
             "gen_ai.tool.call.arguments.truncated": False,
             "gen_ai.tool.call.result.sha256": "<absent>",
             "build_loop.run_id": None})),
    Case("claude_post_end", claude("PostToolUse", tool_response={
        "stdout": "a\nb", "stderr": "", "interrupted": False, "isImage": False}),
         expect=span(tool="Bash", call="toolu_01", attrs={
             "gen_ai.tool.call.result.keys": ["interrupted", "isImage", "stderr", "stdout"]})),
    Case("claude_failure_error", claude("PostToolUseFailure", error="Command failed: exit 1",
                                        is_interrupt=False), phase="error",
         expect=span(tool="Bash", call="toolu_01", event="error")),
    Case("claude_end_exit_code_stays_end", claude("PostToolUse", tool_response={"exit_code": 1}),
         expect=span(tool="Bash", call="toolu_01")),
    Case("codex_pre_start", codex(None), phase="start",
         expect=span(tool="shell", call="call_1", event="start")),
    Case("codex_post_ok_json_string", codex(json.dumps({"output": "hello", "metadata": {"exit_code": 0}})),
         expect=span(tool="shell", call="call_1")),
    Case("codex_post_exit_code", codex({"exit_code": 2, "output": "x"}),
         expect=span(tool="shell", call="call_1", event="error")),
    Case("codex_post_error_key", codex(None, tool_result={"error": "boom"}) .replace(
        b'"tool_response": null, ', b""),
         expect=span(tool="shell", call="call_1", event="error")),
    Case("codex_post_is_error", codex({"is_error": True}),
         expect=span(tool="shell", call="call_1", event="error")),
    Case("codex_post_failed_text", codex("Process exited with code 1"),
         expect=span(tool="shell", call="call_1", event="error")),
    Case("codex_post_exit_zero_text", codex("Exit code: 0\nOutput: fine"),
         expect=span(tool="shell", call="call_1")),
    Case("codex_post_nan_string", codex('{"exit_code": NaN}'),
         expect=span(tool="shell", call="call_1", event="error")),
    Case("codex_post_plain_text", codex("total 0\nREADME.md"),
         expect=span(tool="shell", call="call_1")),
    # --- field aliases and types -----------------------------------------
    Case("tool_key_alias", payload(tool="Read", tool_call_id="c1", session_id=SESSION),
         expect=span(tool="Read", call="c1")),
    Case("duplicate_keys_last_wins", b'{"tool_name":"A","tool_name":"B","session_id":"sess-1"}',
         expect=span(tool="B")),
    Case("tool_name_non_string", payload(tool_name=5, session_id=SESSION),
         expect=span(tool="5")),
    Case("session_from_env", payload(tool_name="Grep"), env={"CLAUDE_SESSION_ID": "env-sess"},
         expect=span(tool="Grep", session="env-sess")),
    Case("session_unknown", payload(tool_name="Grep"),
         expect=span(tool="Grep", session="unknown")),
    Case("session_non_string", payload(tool_name="Grep", session_id=7),
         expect=span(tool="Grep", session="7")),
    Case("is_error_truthy_on_end", payload(tool_name="Grep", session_id=SESSION, is_error=1),
         expect=span(tool="Grep", status="ERROR")),
    Case("tool_response_null_shadows_result",
         payload(tool_name="Grep", session_id=SESSION, tool_response=None, tool_result={"a": 1}),
         expect=span(tool="Grep", attrs={"gen_ai.tool.call.result.sha256": "<absent>"})),
    Case("tool_input_scalar", payload(tool_name="Grep", session_id=SESSION, tool_input="pattern"),
         expect=span(tool="Grep", attrs={"gen_ai.tool.call.arguments.preview": '"pattern"',
                                         "gen_ai.tool.call.arguments.keys": "<absent>"})),
    Case("ints_bools_nulls", payload(tool_name="Grep", session_id=SESSION,
                                     tool_input={"n": -42, "m": 0, "b": True, "z": None,
                                                 "big": 18446744073709551615}),
         expect=span(tool="Grep", attrs={"gen_ai.tool.call.arguments.preview":
                                         '{"b":true,"big":18446744073709551615,"m":0,"n":-42,"z":null}'})),
    Case("float_input", payload(tool_name="Grep", session_id=SESSION, tool_input={"t": 1.5}),
         expect=span(tool="Grep", attrs={"gen_ai.tool.call.arguments.preview": '{"t":1.5}'})),
    Case("huge_int_input", b'{"tool_name":"Grep","session_id":"sess-1","tool_input":{"n":123456789012345678901234567890}}',
         expect=span(tool="Grep", attrs={
             "gen_ai.tool.call.arguments.preview": '{"n":123456789012345678901234567890}'})),
    Case("timestamp_attaches_run", payload(tool_name="Grep", session_id=SESSION,
                                           timestamp="2026-06-01T00:00:00Z"),
         state=_active_state(), start_ns=1_780_272_000_000_000_000,
         expect=span(tool="Grep", attrs={"build_loop.run_id": "bl-run-1"})),
    Case("timestamp_falsy", payload(tool_name="Grep", session_id=SESSION, timestamp=""),
         state=_active_state(), expect=span(tool="Grep", attrs={"build_loop.run_id": None})),
    Case("active_run_without_timestamp", payload(tool_name="Grep", session_id=SESSION),
         state=_active_state(), expect=span(tool="Grep", attrs={"build_loop.run_id": None})),
    # --- content: escaping, bounds, redaction ----------------------------
    Case("non_ascii_input", payload(tool_name="Edit✓", session_id=SESSION, tool_use_id="ü-1",
                                    tool_input=NON_ASCII),
         expect=all_of(span(tool="Edit✓", call="ü-1"), raw_lines_canonical)),
    Case("long_input", payload(tool_name="Write", session_id=SESSION, tool_input={"content": BIG}),
         expect=span(tool="Write", attrs={"gen_ai.tool.call.arguments.truncated": True,
                                          "gen_ai.tool.call.arguments.bytes": len(BIG) + 14})),
    Case("long_non_ascii_preview_cut", payload(tool_name="Write", session_id=SESSION,
                                               tool_input={"content": "é😀" * 400}),
         expect=span(tool="Write", attrs={"gen_ai.tool.call.arguments.truncated": True})),
    Case("long_list_truncated", payload(tool_name="Grep", session_id=SESSION,
                                        tool_input={"l": list(range(80))}),
         expect=span(tool="Grep", attrs={"gen_ai.tool.call.arguments.preview":
                                         '{"l":' + json.dumps(list(range(50)), separators=(",", ":")) + "}"})),
    Case("many_keys", payload(tool_name="Grep", session_id=SESSION,
                              tool_input={f"k{i:02d}": i for i in range(70)}),
         expect=span(tool="Grep", attrs={"gen_ai.tool.call.arguments.keys": [f"k{i:02d}" for i in range(50)]})),
    Case("sensitive_keys", payload(tool_name="WebFetch", session_id=SESSION,
                                   tool_input={"api_key": "abc", "headers": {"X-Auth-Token": {"n": 1}},
                                               "PrivateKey": [1], "url": "https://e.x"}),
         expect=span(tool="WebFetch", attrs={"gen_ai.tool.call.arguments.preview":
             '{"PrivateKey":"[REDACTED]","api_key":"[REDACTED]","headers":{"X-Auth-Token":"[REDACTED]"},'
             '"url":"https://e.x"}'})),
    Case("sensitive_value", payload(tool_name="Bash", session_id=SESSION,
                                    tool_input={"command": "curl -H 'Authorization: Bearer abc123' x"}),
         expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.preview":
                                                       '{"command":"curl -H \'Authorization: [REDACTED]\' x"}'})),
    Case("sk_value", payload(tool_name="Bash", session_id=SESSION, tool_input={"c": "use sk-abcdefghijkl"}),
         expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.preview": '{"c":"use [REDACTED]"}'})),
    Case("case_fold_char", payload(tool_name="Bash", session_id=SESSION, tool_input={"c": "ſecret=1"}),
         expect=span(tool="Bash")),
    Case("redaction_nested_unicode", payload(tool_name="Bash", session_id=SESSION,
         tool_input={"prİvate_Key": {"nested": "fixture-private"}, "list": [
             {"command": "API KEY = 'fixture-inline'"},
             "BEARER\u001cfixture-bearer", "ſecret=fixture-secret"]}),
         expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.preview":
             '{"list":[{"command":"API KEY = [REDACTED]"},"BEARER\\u001c[REDACTED]",'
             '"ſecret=[REDACTED]"],"prİvate_Key":"[REDACTED]"}'})),
    Case("redaction_token_boundaries", payload(tool_name="Bash", session_id=SESSION,
         tool_input={"c": "éghp_abcdefgh ghp_abcdefghé ghp_abcdefgh- ghp_abcdefgh́ "
                          "sk-project-abcdefgh-xyzé sk_abcdefg sk_abcdefgh _ghp_abcdefgh"}),
         expect=span(tool="Bash")),
    Case("redaction_quotes_and_punctuation", payload(tool_name="Bash", session_id=SESSION,
         tool_input={"c": 'password="a b"; TOKEN=\'c,d\', api-key: bearer abc ] '
                          'x_token=abc; passwd=unterminated\"quoted secret="" cookie=1'}),
         expect=span(tool="Bash")),
    Case("redaction_long_unicode_preview", payload(tool_name="Bash", session_id=SESSION,
         tool_input={"c": "😀" * 490 + " token=fixture-secret " + "é" * 1000}),
         expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.truncated": True})),
    Case("redaction_list_limit", payload(tool_name="Bash", session_id=SESSION,
         tool_input=["token=fixture-secret"] * 50 + ["Bearer fixture-outside-limit"]),
         expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.truncated": True})),
    Case("redaction_long_identifier", payload(tool_name="Bash", session_id=SESSION,
         tool_input="x" + "sk_aaaaaaaa" * 20000 + " Bearer fixture-secret"),
         expect=span(tool="Bash", attrs={
             "gen_ai.tool.call.arguments.sha256": sha(json.dumps(
                 "x" + "sk_aaaaaaaa" * 20000 + " Bearer [REDACTED]")),
             "gen_ai.tool.call.arguments.truncated": True})),
    Case("redaction_embedded_bearer", payload(tool_name="Bash", session_id=SESSION,
         tool_input="xsk_aaaabearer fixture-secret"),
         expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.preview":
             '"xsk_aaaabearer [REDACTED]"'})),
    Case("redaction_github_token_families", payload(tool_name="Bash", session_id=SESSION,
         tool_input=[f"{prefix}_abcdefgh" for prefix in ("gho", "ghp", "ghs", "ghu", "ghr")]),
         expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.preview":
             json.dumps(["[REDACTED]"] * 5, separators=(",", ":"))})),
    Case("redaction_sensitive_object_keys", payload(tool_name="Bash", session_id=SESSION,
         tool_input={"gho_abcdefgh": {"ghu_abcdefgh": "safe"},
                     "normal": [{"ghs_abcdefgh": "safe"}], "token=fixture-key-secret": "hidden"}),
         expect=span(tool="Bash", attrs={
             "gen_ai.tool.call.arguments.keys": ["[REDACTED]", "normal", "token=[REDACTED]"],
             "gen_ai.tool.call.arguments.preview":
                 '{"[REDACTED]":{"[REDACTED]":"safe"},"normal":[{"[REDACTED]":"safe"}],'
                 '"token=[REDACTED]":"[REDACTED]"}'})),
    Case("redaction_key_collision", payload(tool_name="Bash", session_id=SESSION,
         tool_input={"ghp_abcdefgh": "last original key", "gho_abcdefgh": "first original key"}),
         expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.keys": ["[REDACTED]"],
             "gen_ai.tool.call.arguments.preview": '{"[REDACTED]":"last original key"}'})),
    Case("redaction_native_without_tmpdir", payload(tool_name="Bash", session_id=SESSION,
         tool_input="token=fixture-secret"), env={"TMPDIR": "/nonexistent-dir"},
         expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.preview": '"token=[REDACTED]"'})),
    Case("codex_unicode_failure_text", codex("ſcript\u001cfailed: 2"),
         expect=span(tool="shell", call="call_1", event="error")),
    Case("deep_payload", payload(tool_name="Grep", session_id=SESSION, tool_input=deep(150)),
         expect=span(tool="Grep")),
    # --- numbers serde cannot hold inside a Codex JSON string (F2) ---------
    Case("codex_returncode_overflow", codex('{"returncode": 1e999}'),
         expect=span(tool="shell", call="call_1", event="error")),
    Case("codex_error_overflow", codex('{"error": 1e999}'),
         expect=span(tool="shell", call="call_1", event="error")),
    Case("codex_status_code_400_digits", codex('{"status_code": 1' + "0" * 400 + "}"),
         expect=span(tool="shell", call="call_1", event="error")),
    # --- hand-off with an unwritable TMPDIR (F3) ---------------------------
    Case("handoff_with_unwritable_tmpdir", payload(tool_name="Bash", session_id=SESSION,
                                                   tool_input={"command": "echo token=abc", "fraction": 1.5}),
         env={"TMPDIR": "/nonexistent-dir"}, expect=span(tool="Bash")),
    # --- stdin encoding Python would use (F4); RAW is non-ASCII on stdin ----
    Case("latin1_locale_non_ascii", RAW,
         env={"LC_ALL": "en_US.ISO8859-1"}),
    Case("latin1_locale_ascii", claude("PostToolUse"), env={"LC_ALL": "en_US.ISO8859-1"},
         expect=span(tool="Bash", call="toolu_01")),
    Case("lc_ctype_latin1_non_ascii", RAW,
         env={"LC_CTYPE": "en_US.ISO8859-1"}),
    Case("lc_all_utf8_over_latin1_lang", RAW,
         env={"LC_ALL": "en_US.UTF-8", "LANG": "en_US.ISO8859-1"}, expect=span(tool="Edit")),
    Case("ioencoding_latin1_non_ascii", RAW,
         env={"PYTHONIOENCODING": "latin-1"}),
    Case("ioencoding_utf16_ascii", claude("PostToolUse"), env={"PYTHONIOENCODING": "utf-16"}),
    Case("ioencoding_utf8_non_ascii", RAW,
         env={"PYTHONIOENCODING": "UTF-8:strict", "LC_ALL": "en_US.ISO8859-1"}, expect=span(tool="Edit")),
    Case("c_locale_non_ascii", RAW,
         env={"LANG": "C"}, expect=span(tool="Edit")),
    Case("utf8_mode_off_c_locale_non_ascii", RAW,
         env={"LANG": "C", "PYTHONUTF8": "0"}, expect=span(tool="Edit")),  # C.UTF-8 coercion; bl hands off conservatively
    Case("unknown_locale_non_ascii", RAW, env={"LANG": "xx_XX.ISO8859-1"},
         expect=span(tool="Edit")),  # Python falls back to C (UTF-8); bl hands off conservatively
    Case("utf8_mode_on_latin1_locale", RAW,
         env={"PYTHONUTF8": "1", "LC_ALL": "en_US.ISO8859-1"}, expect=span(tool="Edit")),
    Case("utf8_mode_invalid_value", claude("PostToolUse"), env={"PYTHONUTF8": "2"}),
    # --- malformed stdin --------------------------------------------------
    Case("empty_stdin", b"", expect=nothing),
    Case("whitespace_stdin", b" \n\t\r ", expect=nothing),
    Case("no_tool_name", payload(session_id=SESSION, tool_input={"a": 1}), expect=nothing),
    Case("non_object_stdin", b"[1, 2]", expect=nothing),
    Case("truncated_stdin", b'{"tool_name": "Bash", ', expect=nothing),
    Case("malformed_stdin", b"{not json", expect=nothing),
    Case("invalid_utf8", b'\xff{"tool_name":"Bash"}', expect=nothing),
    Case("bom_stdin", b'\xef\xbb\xbf{"tool_name":"Bash"}', expect=nothing),
    # --- state.json -------------------------------------------------------
    Case("state_missing", claude("PostToolUse"), state=None, expect=span(tool="Bash", call="toolu_01")),
    Case("state_malformed", claude("PostToolUse"), state=b"{oops", expect=span(tool="Bash", call="toolu_01")),
    Case("state_is_directory", claude("PostToolUse"), state="dir", expect=span(tool="Bash", call="toolu_01")),
    Case("state_deeply_nested", claude("PostToolUse"), state=b"[" * 300 + b"]" * 300,
         expect=span(tool="Bash", call="toolu_01")),
    # --- workdir ----------------------------------------------------------
    Case("project_without_build_loop", claude("PostToolUse"), workdir="bare",
         expect=span(tool="Bash", call="toolu_01")),
    Case("workdir_missing", claude("PostToolUse"), workdir="missing", expect=span(tool="Bash", call="toolu_01")),
    Case("workdir_empty_uses_cwd", claude("PostToolUse"), workdir="empty",
         expect=span(tool="Bash", call="toolu_01")),
    Case("telemetry_is_a_file", claude("PostToolUse"), setup=_telemetry_is_file, expect=nothing),
    # --- rotation ---------------------------------------------------------
    Case("rotation_below_limit", claude("PostToolUse"), setup=_setup_rotation(h.MAX_FILE_BYTES - 1),
         expect=span(tool="Bash", call="toolu_01")),
    Case("rotation_at_limit", claude("PostToolUse"), setup=_setup_rotation(h.MAX_FILE_BYTES, (1, 2)),
         expect=span(tool="Bash", call="toolu_01")),
    Case("rotation_gap_in_rotations", claude("PostToolUse"), setup=_setup_rotation(h.MAX_FILE_BYTES + 5, (2,)),
         expect=span(tool="Bash", call="toolu_01")),
    # --- locking and permissions -----------------------------------------
    Case("lock_held_past_timeout", claude("PostToolUse"), lock_hold=1.8, expect=nothing),
    Case("lock_held_briefly", claude("PostToolUse"), lock_hold=0.3,
         expect=span(tool="Bash", call="toolu_01")),
    Case("telemetry_read_only_empty", claude("PostToolUse"), setup=_chmod_telemetry(0o555), expect=nothing),
    Case("telemetry_read_only_existing_files", claude("PostToolUse"),
         setup=_chmod_telemetry(0o555, {"tool-traces.jsonl": 1500, "tool-traces.jsonl.lock": 0}),
         expect=span(tool="Bash", call="toolu_01")),
    Case("telemetry_read_only_needs_rotation", claude("PostToolUse"),
         setup=_chmod_telemetry(0o555, {"tool-traces.jsonl": h.MAX_FILE_BYTES, "tool-traces.jsonl.lock": 0}),
         expect=nothing),
]


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve() / "w"


# These inputs are outside the native host contract. No partial record or Python
# subprocess is permitted; diagnostics identify the skipped category without data.
UNSUPPORTED = {
    "codex_post_nan_string": "unsupported_json",
    "codex_returncode_overflow": "unsupported_json",
    "codex_error_overflow": "unsupported_json",
    "tool_name_non_string": "unsupported_metadata",
    "session_non_string": "unsupported_metadata",
    "truncated_stdin": "unsupported_json",
    "malformed_stdin": "unsupported_json",
    "invalid_utf8": "unsupported_encoding",
    "bom_stdin": "unsupported_json",
}
# Python runtime locale overrides do not alter the native UTF-8 wire format.
ENCODING_OVERRIDES = {
    "latin1_locale_non_ascii", "lc_ctype_latin1_non_ascii",
    "ioencoding_latin1_non_ascii", "ioencoding_utf16_ascii",
    "utf8_mode_off_c_locale_non_ascii", "unknown_locale_non_ascii",
    "utf8_mode_invalid_value",
}
PARITY_CASES = [c for c in CASES if c.name not in UNSUPPORTED and c.name not in ENCODING_OVERRIDES]


def test_case_names_unique():
    assert len({c.name for c in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_python_contract(case, root):
    snap = h.run("python", case, root)
    raw_lines_canonical(snap)
    if case.expect:
        case.expect(snap)
    if case.lock_hold and case.lock_hold > 1.2:
        assert snap.elapsed >= 1.0


def _compare(case: Case, root: Path):
    oracle = h.run("python", case, root)
    raw_native = h.run("bl", case, root, extra_env={"BL_TRACE_EXIT": "1"})
    native = without_diagnostics(raw_native)
    if not (native.comparable() == oracle.comparable()):
        evidence = save_failure(root, case.name, h.BL, oracle, raw_native)
        pytest.fail(f"Differential mismatch; full snapshots: {evidence}", pytrace=False)
    if case.expect:
        case.expect(native)


needs_bl = pytest.mark.skipif(not h.BL.exists(), reason="bl not built (cargo build --release)")


@needs_bl
@pytest.mark.parametrize("case", PARITY_CASES, ids=[c.name for c in PARITY_CASES])
def test_bl_matches_python(case, root):
    _compare(case, root)


@needs_bl
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_bl_never_hands_trace_events_to_python(case, root):
    raw = h.run("bl", case, root, extra_env={"BL_TRACE_FALLBACK": "1", "BL_TRACE_EXIT": "1"})
    assert b"bl: fallback:" not in raw.stderr
    snap = without_diagnostics(raw)
    if case.name in UNSUPPORTED:
        assert ("reason=" + UNSUPPORTED[case.name]).encode() in raw.stderr, raw.stderr
        nothing(snap)
    else:
        assert b"unsupported_" not in raw.stderr, raw.stderr
        if case.name in ENCODING_OVERRIDES:
            check = span(tool="Bash", call="toolu_01") if case.stdin != RAW else span(tool="Edit")
            check(snap)
            if case.stdin == RAW:
                expected = json.dumps(NON_ASCII, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                assert snap.new_lines[0]["attributes"]["gen_ai.tool.call.arguments.sha256"] == sha(expected)
        elif case.expect:
            case.expect(snap)


@needs_bl
def test_native_redaction_hashes_full_sanitized_content_before_preview(root):
    value = {"authorization": "fixture-auth", "nested": ["Bearer fixture-bearer", "secret='fixture-space'"],
             "content": "😀" * 490 + " token=fixture-tail " + "é" * 1000}
    sanitized = {"authorization": "[REDACTED]", "nested": ["Bearer [REDACTED]", "secret=[REDACTED]"],
                 "content": "😀" * 490 + " token=[REDACTED] " + "é" * 1000}
    case = Case("native_redaction_full_hash", payload(tool_name="Bash", session_id=SESSION, tool_input=value))
    snap = h.run("bl", case, root, extra_env={"BL_TRACE_FALLBACK": "1"})
    encoded = json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    span(tool="Bash", attrs={
        "gen_ai.tool.call.arguments.sha256": sha(encoded),
        "gen_ai.tool.call.arguments.bytes": len(encoded.encode()),
        "gen_ai.tool.call.arguments.preview": encoded[:512],
        "gen_ai.tool.call.arguments.truncated": True,
    })(snap)
    assert all("fixture-" not in body for body in snap.files.values())


# --- concurrency and readers ---------------------------------------------
def _event(i: int) -> bytes:
    return claude("PostToolUse", tool_use_id=f"call-{i:03d}", tool_response={"stdout": f"line {i}"})


@needs_bl
def test_concurrent_mixed_appends_lose_and_interleave_nothing(root):
    """200 hooks in concurrent mixed batches across a rotation: every event
    lands exactly once as one whole line, and rotation preserves the old file.

    Both writers deliberately drop events when their bounded lock wait expires.
    Ten active hooks exercise shared-flock contention without delaying stdin
    until 200 processes have started and exhausting those fail-open budgets.
    Separate held-lock cases cover the intended timeout behavior.
    """
    case = Case("concurrency")
    world = h.build_world(root, case)
    trace = world.proj / h.TRACE
    h.prefill(trace, h.MAX_FILE_BYTES - 60_000)
    batch_size = 10
    for start in range(0, 200, batch_size):
        procs = []
        for i in range(start, start + batch_size):
            impl = "python" if i % 2 else "bl"
            procs.append(subprocess.Popen(h.argv_for(impl, Case("c", phase="end"), world), stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=world.env,
                                          cwd=str(world.cwd)))
        for i, p in enumerate(procs, start=start):
            p.stdin.write(_event(i))
            p.stdin.close()
        for p in procs:
            assert p.wait(timeout=60) == 0
            assert (p.stdout.read(), p.stderr.read()) == (b"", b"")
    seen: list[str] = []
    prefill_lines = 0
    for path in (trace, Path(f"{trace}.1"), Path(f"{trace}.2")):
        if not path.exists():
            continue
        data = path.read_bytes()
        assert data.endswith(b"\n")
        for line in data.splitlines():
            if b'"event":"prefill"' in line or not line.startswith(b"{"):
                prefill_lines += 1
                continue
            seen.append(json.loads(line)["attributes"]["build_loop.tool.call_id_sha256"])
    assert Path(f"{trace}.1").exists(), "the run never crossed the rotation boundary"
    assert sorted(seen) == sorted(sha(f"call-{i:03d}") for i in range(200))
    assert prefill_lines > 0


@needs_bl
def test_summarize_reads_mixed_files_like_all_python(tmp_path):
    """tool_trace.py summarize gives the same answer for a file written by
    Python alone and one written alternately by Python and bl."""
    events = [("start", claude("PreToolUse", tool_use_id=f"u{i}")) for i in range(3)]
    events += [("end", claude("PostToolUse", tool_use_id="u0", tool_response={"stdout": "ok"})),
               ("error", claude("PostToolUseFailure", tool_use_id="u1", error="exit 1")),
               ("end", codex({"exit_code": 4}))]
    out = {}
    files = {}
    for label in ("python", "mixed"):
        world = h.build_world(tmp_path / label, Case(label))
        for i, (phase, stdin) in enumerate(events):
            impl = "bl" if label == "mixed" and i % 2 else "python"
            proc = subprocess.run(h.argv_for(impl, Case("e", phase=phase), world), input=stdin, env=world.env,
                                  cwd=str(world.cwd), capture_output=True, timeout=30)
            assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
        summary = subprocess.run(["python3", str(h.TOOL_TRACE), "--workdir", str(world.proj), "summarize"],
                                 env=world.env, capture_output=True, timeout=30)
        assert summary.returncode == 0, summary.stderr
        out[label] = summary.stdout
        files[label] = h.TIME_FIELD.sub(b"T", (world.proj / h.TRACE).read_bytes())
    assert files["mixed"] == files["python"]
    assert out["mixed"] == out["python"]
    assert json.loads(out["python"])["coverage"]["invalid_trace_records"] == 0


# --- hooks.json commands ---------------------------------------------------
# The command hooks/hooks.json registered before the native path existed; the
# registered command must behave exactly like it whenever bl cannot run.
INLINE = ('if [ -f "${CLAUDE_PROJECT_DIR:-$PWD}/.build-loop/state.json" ]; then python3 '
          '"${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/tool_trace.py" --workdir "$CLAUDE_PROJECT_DIR" '
          'hook --phase {phase} >/dev/null 2>&1 || true; fi')
DIGEST = "a" * 64
sys.path.insert(0, str(h.REPO_ROOT / "scripts"))
import trace_hook_command  # noqa: E402

sys.path.pop(0)


def _registered(phase: str) -> str:
    """The trace command hooks/hooks.json registers for ``phase``, read from the file."""
    entry = trace_hook_command.trace_hooks(json.loads((h.REPO_ROOT / "hooks" / "hooks.json").read_text()))[phase]
    assert entry["async"] is True and entry["timeout"] == 1, entry
    return entry["command"]


def test_launcher_hooks_json_matches_template():
    proc = subprocess.run([sys.executable, str(h.REPO_ROOT / "scripts" / "trace_hook_command.py"), "--check"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    commands = {p: _registered(p) for p in ("start", "end", "error")}
    assert len({c.replace(f"--phase {p} ", "--phase P ") for p, c in commands.items()}) == 1
    for p, c in commands.items():
        assert c.startswith('if [ -f "${CLAUDE_PROJECT_DIR:-$PWD}/.build-loop/state.json" ]; then')
        assert INLINE.replace("{phase}", p).split("then ", 1)[1].removesuffix(" fi") in c


def _plugin(tmp_path: Path, binary: str, *, built: str | None = DIGEST, tree: str | None = DIGEST) -> Path:
    """A throwaway plugin root: the real scripts/ (linked) and ``binary``:
    "real" (this build's bl), "absent", or a stand-in from ``STANDINS``."""
    root = tmp_path / "plugin"
    native = root / "bin" / "native"
    native.mkdir(parents=True)
    (root / "scripts").symlink_to(h.REPO_ROOT / "scripts")
    bl = native / "bl"
    if binary == "real":
        shutil.copy2(h.BL, bl)
    elif binary == "directory":
        bl.mkdir()
    elif binary != "absent":
        bl.write_bytes(STANDINS[binary][0])
        bl.chmod(STANDINS[binary][1])
    if built is not None:
        (native / "bl.sources").write_text(built + "\n")
    if tree is not None:
        (native / "sources.current").write_text(tree + "\n# comment\n")
    return root


def _stub(code: int) -> bytes:
    return f'#!/bin/sh\n: > "$BL_STUB_MARK"\nexit {code}\n'.encode()


STANDINS = {
    "stub0": (_stub(0), 0o755),
    "stub75": (_stub(75), 0o755),
    "stub127": (_stub(127), 0o755),
    "stub1": (_stub(1), 0o755),
    "stub_killed": (b'#!/bin/sh\n: > "$BL_STUB_MARK"\nkill -9 $$\n', 0o755),
    "garbage": (b"\x00not a program", 0o755),          # sh: exit 126
    "bad_interpreter": (b"#!/nonexistent/interp\n", 0o755),  # sh: exit 126
    "not_executable": (_stub(0), 0o644),
}
LAUNCHER_CASES = [c for c in CASES if c.name in {
    "claude_pre_start", "claude_failure_error", "codex_post_exit_code", "sensitive_value",
    "malformed_stdin", "empty_stdin", "project_without_build_loop", "state_missing",
    "workdir_empty_uses_cwd", "non_ascii_input"}]
# binary, sidecar digest, env, whether the stand-in should have been started
FALLBACK_CONDITIONS = {
    "binary_absent": ("absent", DIGEST, {}, False),
    "sidecar_stale": ("stub0", "b" * 64, {}, False),
    "sidecar_missing": ("stub0", None, {}, False),
    "native_off": ("stub0", DIGEST, {"BUILD_LOOP_NATIVE_HOOKS": "off"}, False),
    "not_executable": ("not_executable", DIGEST, {}, False),
    "unrunnable_126_garbage": ("garbage", DIGEST, {}, False),
    "unrunnable_126_bad_interpreter": ("bad_interpreter", DIGEST, {}, False),
    "unrunnable_126_directory": ("directory", DIGEST, {}, False),
    "bl_exit_75": ("stub75", DIGEST, {}, True),
    "bl_exit_127": ("stub127", DIGEST, {}, True),
    "bl_exit_1": ("stub1", DIGEST, {}, True),
    "bl_killed_sigkill": ("stub_killed", DIGEST, {}, True),
}


def _hook(command: str, case: Case, root: Path, plugin: Path, **env) -> h.Snapshot:
    """Run ``command`` the way the host does: ``/bin/sh -c`` with the hook env."""
    return h.run("hook", case, root, argv=["/bin/sh", "-c", command.replace("{phase}", case.phase)],
                 extra_env={"CLAUDE_PLUGIN_ROOT": str(plugin), **env})


@pytest.mark.parametrize("cond", list(FALLBACK_CONDITIONS))
@pytest.mark.parametrize("case", LAUNCHER_CASES, ids=[c.name for c in LAUNCHER_CASES])
def test_launcher_without_usable_binary_matches_inline(case, cond, root, tmp_path):
    binary, built, env, started = FALLBACK_CONDITIONS[cond]
    plugin = _plugin(tmp_path, binary, built=built)
    mark = tmp_path / "stub-ran"
    old = _hook(INLINE, case, root, plugin, BL_STUB_MARK=str(mark), **env)
    assert not mark.exists()
    new = _hook(_registered(case.phase), case, root, plugin, BL_STUB_MARK=str(mark), **env)
    state_exists = case.workdir in ("proj", "empty") and case.state is not None
    assert mark.exists() == (started and state_exists), "guard started a binary it should have refused, or vice versa"
    assert new.comparable() == old.comparable()


@needs_bl
@pytest.mark.parametrize("case", PARITY_CASES, ids=[c.name for c in PARITY_CASES])
def test_launcher_with_fresh_binary_matches_inline(case, root, tmp_path):
    plugin = _plugin(tmp_path, "real")
    old = _hook(INLINE, case, root, plugin)
    new = _hook(_registered(case.phase), case, root, plugin)
    if new.comparable() != old.comparable():
        evidence = save_failure(root, case.name, plugin / "bin/native/bl", old, new)
        pytest.fail(f"Launcher mismatch; full snapshots: {evidence}", pytrace=False)


@needs_bl
@pytest.mark.parametrize("case", [c for c in CASES if c.native and c.name in {
    "claude_post_end", "codex_post_exit_code", "non_ascii_input", "rotation_at_limit"}])
def test_launcher_runs_fresh_binary_natively(case, root, tmp_path):
    """With no python3 on PATH only bl can write the line."""
    plugin = _plugin(tmp_path, "real")
    snap = _hook(_registered(case.phase), case, root, plugin, PATH="/bin")
    case.expect(snap)


def test_launcher_late_fallback_never_duplicates(root, tmp_path):
    """A binary that read stdin to EOF, appended its line and then died
    (here SIGKILL) leaves exactly that one line: the Python fallback the
    command then runs reads an empty stdin and writes nothing. Real bl reads
    stdin to EOF before its only append, so this is the worst case."""
    plugin = _plugin(tmp_path, "absent")
    bl = plugin / "bin" / "native" / "bl"
    bl.write_text('#!/bin/sh\ncat >/dev/null\nd="$CLAUDE_PROJECT_DIR/.build-loop/telemetry"\nmkdir -p "$d"\n'
                  'printf \'{"stub":1}\\n\' >> "$d/tool-traces.jsonl"\nkill -9 $$\n')
    bl.chmod(0o755)
    case = next(c for c in CASES if c.name == "claude_post_end")
    snap = _hook(_registered(case.phase), case, root, plugin)
    assert (snap.rc, snap.stdout, snap.stderr) == (0, b"", b"")
    body = snap.files["proj/.build-loop/telemetry/tool-traces.jsonl"]
    assert body.endswith(':{"stub":1}\n'), body


@needs_bl
@pytest.mark.parametrize("timestamp", [
    "2026-06-01T00:00:00Z", "2026-05-31T17:00:00-07:00",
    "2026-06-01T05:30:00.123456+05:30", "2026-06-01T00:00:00.123456789Z",
    "1900-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z",
])
def test_native_timestamp_precision_matches_persisted_python_time(timestamp, root):
    import datetime
    ns = int(datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1e9)
    case = Case("timestamp_precision", payload(tool_name="Grep", session_id=SESSION,
                timestamp=timestamp), start_ns=ns, state=_active_state())
    _compare(case, root)


@needs_bl
@pytest.mark.parametrize("change,session,expected", [
    ({}, SESSION, "bl-run-1"),
    ({"current_session_id": "resumed", "started_by_session_id": SESSION}, SESSION, "bl-run-1"),
    ({"current_session_id": "other"}, SESSION, None),
    ({"started_at": "2026-06-01T00:00:00.000001Z"}, SESSION, None),
    ({"started_at": "20260601T000000Z", "ended_at": "2026-06-01T00:00:00Z"}, SESSION, "bl-run-1"),
    ({"ended_at": "2026-05-31T23:59:59.999999Z"}, SESSION, None),
    ({"ended_at": "invalid"}, SESSION, None),
    ({"ended_at": "2025-01-01T00:00:00Z"}, SESSION, None),
    ({"build_loop_id": "conflicting"}, SESSION, None),
    ({"run_id": None}, SESSION, None),
])
def test_native_temporal_ownership_never_borrows_another_run(change, session, expected, root):
    state = json.loads(_active_state())
    state["execution"].update(change)
    state["runs"] = [{"run_id": "completed-history", "session_id": session}]
    case = Case("temporal_ownership", payload(tool_name="Grep", session_id=session,
                timestamp="2026-06-01T00:00:00Z"), state=json.dumps(state).encode(),
                start_ns=1_780_272_000_000_000_000,
                expect=span(tool="Grep", session=session, attrs={"build_loop.run_id": expected}))
    _compare(case, root)


@needs_bl
@pytest.mark.parametrize("levels,accepted", [(254, True), (255, True), (256, False), (1000, False)])
def test_native_real_input_depth_bound_is_safe(levels, accepted, root):
    # Root adds one container. Construct bytes directly; Python recursion is not
    # part of this native boundary test and no event payload appears in diagnostics.
    raw = b'{"tool_name":"Grep","session_id":"sess-1","tool_input":' + b'[' * levels + b'0' + b']' * levels + b'}'
    case = Case("depth_boundary", raw)
    result = h.run("bl", case, root, extra_env={"BL_TRACE_EXIT": "1"})
    snap = without_diagnostics(result)
    if accepted:
        span(tool="Grep")(snap)
        assert b"reason=append_ok" in result.stderr
    else:
        nothing(snap)
        assert b"reason=unsupported_limits" in result.stderr
    assert b"bl: fallback:" not in result.stderr


@needs_bl
@pytest.mark.parametrize("raw,reason", [
    (b'{"tool_name":"Bash","tool_input":1e999}', "unsupported_number"),
    (b'{"tool_name":"Bash","tool_input":NaN}', "unsupported_json"),
    (b'{"tool_name":"Bash","tool_input":"\\ud800"}', "unsupported_json"),
])
def test_native_unsupported_input_is_silent_by_default_and_countable(raw, reason, root):
    case = Case("unsupported_contract", raw)
    nothing(h.run("bl", case, root))
    diagnosed = h.run("bl", case, root, extra_env={"BL_TRACE_EXIT": "1"})
    nothing(without_diagnostics(diagnosed))
    assert ("reason=" + reason).encode() in diagnosed.stderr
    assert b"bl: fallback:" not in diagnosed.stderr


@needs_bl
def test_native_numeric_fingerprints_remain_compatible_with_shared_reader(root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(h.REPO_ROOT / "scripts"))
    import tool_trace
    case = Case("numeric_reader", payload(tool_name="Grep", session_id=SESSION,
                timestamp="2026-06-01T00:00:00Z", tool_input={"float": 1e-7, "negative_zero": -0.0,
                "huge": 10**400, "nested": [1.5, "token=fixture-hidden"]}),
                start_ns=1_780_272_000_000_000_000, state=_active_state())
    legacy = h.run("python", case, root)
    native = h.run("bl", case, root)
    assert native.comparable() == legacy.comparable()
    path = tmp_path / "reader.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in legacy.new_lines + native.new_lines))
    diagnostics = {"invalid_records": 0}
    rows = list(tool_trace._iter_trace_rows(path, diagnostics=diagnostics))
    assert len(rows) == 2 and diagnostics["invalid_records"] == 0
    assert rows[0]["attributes"]["gen_ai.tool.call.arguments.sha256"] == rows[1]["attributes"]["gen_ai.tool.call.arguments.sha256"]
    assert all(row["attributes"]["build_loop.run_id"] == "bl-run-1" for row in rows)
    assert "fixture-hidden" not in path.read_text()
    # Live hook records remain observed hook spans, never counted as reconciled
    # tool calls merely because the writer changed language.
    summary = tool_trace.summarize_path(path, run_id="bl-run-1")
    assert summary["tool_calls"] == 0
    assert summary["coverage"]["invalid_trace_records"] == 0


@needs_bl
@pytest.mark.parametrize("value", [
    {"$serde_json::private::Number": "1"},
    {"$serde_json::private::Number": "secret=fixture-hidden", "safe": [1.5]},
    [{"$serde_json::private::Number": "1e999"}, 10**400],
])
def test_native_arbitrary_precision_cannot_reinterpret_real_object_keys(value, root):
    _compare(Case("reserved_number_key", payload(tool_name="Grep", session_id=SESSION, tool_input=value)), root)


@needs_bl
@pytest.mark.parametrize("literal", ["-0", "0", "-0.0", "1e-999", "-1e-999", "1e23", "1e-4", "1e-5", "1e16", "1e15", "1.7976931348623157e308", "5e-324"])
def test_native_numeric_lexemes_keep_python_fingerprint(literal, root):
    raw = b'{"tool_name":"Grep","session_id":"sess-1","tool_input":' + literal.encode() + b'}'
    _compare(Case("numeric_lexeme", raw), root)


@needs_bl
def test_native_attribution_ignores_unrelated_large_numbers_in_state(root):
    state = json.loads(_active_state())
    state["unrelated"] = 10**400
    state["execution"]["irrelevant"] = {"$serde_json::private::Number": "1", "huge": 10**400}
    case = Case("state_large_numbers", payload(tool_name="Grep", session_id=SESSION, timestamp="2026-06-01T00:00:00Z"),
                state=json.dumps(state).encode(), start_ns=1_780_272_000_000_000_000,
                expect=span(tool="Grep", attrs={"build_loop.run_id": "bl-run-1"}))
    _compare(case, root)


@needs_bl
@pytest.mark.parametrize("unrelated", [b'1e999', b'-1e999', b'{"nested":[1e999]}'])
def test_native_state_numeric_range_does_not_change_shared_reader_attribution(unrelated, root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(h.REPO_ROOT / "scripts"))
    import tool_trace
    state = _active_state()[:-1] + b',"unrelated":' + unrelated + b'}'
    case = Case("state_numeric_range", payload(tool_name="Grep", session_id=SESSION,
                timestamp="2026-06-01T00:00:00Z"), state=state,
                start_ns=1_780_272_000_000_000_000,
                expect=span(tool="Grep", attrs={"build_loop.run_id": "bl-run-1"}))
    _compare(case, root)
    native = h.run("bl", case, root)
    path = tmp_path / "state-range-reader.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in native.new_lines))
    diagnostics = {"invalid_records": 0}
    rows = list(tool_trace._iter_trace_rows(path, diagnostics=diagnostics))
    assert diagnostics["invalid_records"] == 0 and len(rows) == 1
    assert rows[0]["attributes"]["build_loop.run_id"] == "bl-run-1"
    assert rows[0]["start_time_unix_nano"] == case.start_ns


@needs_bl
@pytest.mark.parametrize("value", [
    {"$serde_json::private::RawValue": "[]"},
    {"$serde_json::private::RawValue": '{"file_path":"/other/x.py"}', "safe": True},
    {"nested": [{"$serde_json::private::RawValue": "secret=fixture-hidden"}]},
])
def test_native_literal_raw_value_keys_are_ordinary_data(value, root):
    _compare(Case("literal_raw_key", payload(tool_name="Grep", session_id=SESSION, tool_input=value)), root)


@needs_bl
def test_native_large_integer_extension_is_readable_without_python_digit_limit(root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(h.REPO_ROOT / "scripts"))
    import tool_trace
    digits = b"9" * 5000  # Deliberately beyond CPython's default4300-digit decoder guard.
    case = Case("large_integer_extension", b'{"tool_name":"Grep","session_id":"sess-1","tool_input":' + digits + b'}')
    result = h.run("bl", case, root)
    span(tool="Grep")(result)
    attrs = result.new_lines[0]["attributes"]
    assert attrs["gen_ai.tool.call.arguments.sha256"] == hashlib.sha256(digits).hexdigest()
    assert attrs["gen_ai.tool.call.arguments.preview"] == "9" * 512
    assert attrs["gen_ai.tool.call.arguments.bytes"] == 5000
    assert attrs["gen_ai.tool.call.arguments.truncated"] is True
    path = tmp_path / "large-integer-reader.jsonl"
    path.write_text(json.dumps(result.new_lines[0]) + "\n")
    diagnostics = {"invalid_records": 0}
    assert len(list(tool_trace._iter_trace_rows(path, diagnostics=diagnostics))) == 1
    assert diagnostics["invalid_records"] == 0


@needs_bl
@pytest.mark.parametrize("timestamp", ["not-time", "0000-01-01T00:00:00Z",
    "2026-06-01T00:00:00z", "2026-06-30T23:59:60Z", "2026-06-01", 123, True, [1], {"secret": "fixture-hidden"}])
def test_native_unusable_timestamp_preserves_event_without_run(timestamp, root):
    case = Case("uncertain_time", payload(tool_name="Grep", session_id=SESSION,
        timestamp=timestamp, tool_input={"safe": "context"}), state=_active_state())
    for env in ({}, {"BL_TRACE_EXIT": "1"}):
        result = h.run("bl", case, root, extra_env=env)
        snap = without_diagnostics(result)
        span(tool="Grep", attrs={"build_loop.run_id": None})(snap)
        # Harness only normalizes timestamps inside the measured invocation.
        # A guessed epoch or historical timestamp remains nonzero and fails.
        assert snap.new_lines[0]["start_time_unix_nano"] == 0
        assert "context" in snap.new_lines[0]["attributes"]["gen_ai.tool.call.arguments.preview"]
        if env:
            assert result.stderr.count(b"reason=timestamp_unavailable") == 1
            assert b"reason=append_ok" in result.stderr
            assert b"fixture-hidden" not in result.stderr
        else:
            assert result.stderr == b""


@needs_bl
@pytest.mark.parametrize("prefix,run_id", [(b"\xef\xbb\xbf", "bl-run-1"),
    (b"\xef\xbb\xbf" * 2, None)])
def test_native_saved_state_bom_preserves_safe_attribution(prefix, run_id, root):
    case = Case("state_bom", payload(tool_name="Grep", session_id=SESSION,
        timestamp="2026-06-01T00:00:00Z"), state=prefix + _active_state(),
        start_ns=1_780_272_000_000_000_000,
        expect=span(tool="Grep", attrs={"build_loop.run_id": run_id}))
    _compare(case, root)
