# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Parity cases for the tool-trace hook (``scripts/tool_trace.py hook``).

``test_python_contract`` pins each case's outcome on the Python hook alone
(the oracle). ``test_bl_matches_python`` runs ``bl hook trace`` on the same
case and requires identical exit status, stdout, stderr and files.
``test_bl_handles_or_hands_off`` pins which cases ``bl`` handles itself and
which it hands to Python, so a regression to "always hand off" cannot pass
the comparison vacuously.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

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
    Case("codex_post_failed_text", codex("Process exited with code 1"), native=False,
         expect=span(tool="shell", call="call_1", event="error")),
    Case("codex_post_exit_zero_text", codex("Exit code: 0\nOutput: fine"), native=False,
         expect=span(tool="shell", call="call_1")),
    Case("codex_post_nan_string", codex('{"exit_code": NaN}'), native=False,
         expect=span(tool="shell", call="call_1", event="error")),
    Case("codex_post_plain_text", codex("total 0\nREADME.md"),
         expect=span(tool="shell", call="call_1")),
    # --- field aliases and types -----------------------------------------
    Case("tool_key_alias", payload(tool="Read", tool_call_id="c1", session_id=SESSION),
         expect=span(tool="Read", call="c1")),
    Case("duplicate_keys_last_wins", b'{"tool_name":"A","tool_name":"B","session_id":"sess-1"}',
         expect=span(tool="B")),
    Case("tool_name_non_string", payload(tool_name=5, session_id=SESSION), native=False,
         expect=span(tool="5")),
    Case("session_from_env", payload(tool_name="Grep"), env={"CLAUDE_SESSION_ID": "env-sess"},
         expect=span(tool="Grep", session="env-sess")),
    Case("session_unknown", payload(tool_name="Grep"),
         expect=span(tool="Grep", session="unknown")),
    Case("session_non_string", payload(tool_name="Grep", session_id=7), native=False,
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
         native=False, expect=span(tool="Grep", attrs={"gen_ai.tool.call.arguments.preview": '{"t":1.5}'})),
    Case("huge_int_input", b'{"tool_name":"Grep","session_id":"sess-1","tool_input":{"n":123456789012345678901234567890}}',
         native=False, expect=span(tool="Grep", attrs={
             "gen_ai.tool.call.arguments.preview": '{"n":123456789012345678901234567890}'})),
    Case("timestamp_attaches_run", payload(tool_name="Grep", session_id=SESSION,
                                           timestamp="2026-06-01T00:00:00Z"),
         state=_active_state(), native=False, start_ns=1_780_272_000_000_000_000,
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
         native=False, expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.preview":
                                                       '{"command":"curl -H \'Authorization: [REDACTED]\' x"}'})),
    Case("sk_value", payload(tool_name="Bash", session_id=SESSION, tool_input={"c": "use sk-abcdefghijkl"}),
         native=False, expect=span(tool="Bash", attrs={"gen_ai.tool.call.arguments.preview": '{"c":"use [REDACTED]"}'})),
    Case("case_fold_char", payload(tool_name="Bash", session_id=SESSION, tool_input={"c": "ſecret=1"}),
         native=False, expect=span(tool="Bash")),
    Case("deep_payload", payload(tool_name="Grep", session_id=SESSION, tool_input=deep(150)),
         native=False, expect=span(tool="Grep")),
    # --- malformed stdin --------------------------------------------------
    Case("empty_stdin", b"", expect=nothing),
    Case("whitespace_stdin", b" \n\t\r ", expect=nothing),
    Case("no_tool_name", payload(session_id=SESSION, tool_input={"a": 1}), expect=nothing),
    Case("non_object_stdin", b"[1, 2]", expect=nothing),
    Case("truncated_stdin", b'{"tool_name": "Bash", ', expect=nothing),
    Case("malformed_stdin", b"{not json", native=False, expect=nothing),
    Case("invalid_utf8", b'\xff{"tool_name":"Bash"}', native=False, expect=nothing),
    Case("bom_stdin", b'\xef\xbb\xbf{"tool_name":"Bash"}', native=False, expect=nothing),
    # --- state.json -------------------------------------------------------
    Case("state_missing", claude("PostToolUse"), state=None, expect=span(tool="Bash", call="toolu_01")),
    Case("state_malformed", claude("PostToolUse"), state=b"{oops", expect=span(tool="Bash", call="toolu_01")),
    Case("state_is_directory", claude("PostToolUse"), state="dir", expect=span(tool="Bash", call="toolu_01")),
    Case("state_deeply_nested", claude("PostToolUse"), state=b"[" * 300 + b"]" * 300, native=False,
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
    native = h.run("bl", case, root)
    assert native.comparable() == oracle.comparable()
    if case.expect:
        case.expect(native)


needs_bl = pytest.mark.skipif(not h.BL.exists(), reason="bl not built (cargo build --release)")


@needs_bl
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_bl_matches_python(case, root):
    _compare(case, root)


@needs_bl
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_bl_handles_or_hands_off(case, root):
    snap = h.run("bl", case, root, extra_env={"BL_TRACE_FALLBACK": "1"})
    handed_off = snap.stderr.startswith(b"bl: fallback:")
    assert handed_off == (not case.native), snap.stderr


# --- concurrency and readers ---------------------------------------------
def _event(i: int) -> bytes:
    return claude("PostToolUse", tool_use_id=f"call-{i:03d}", tool_response={"stdout": f"line {i}"})


@needs_bl
def test_concurrent_mixed_appends_lose_and_interleave_nothing(root):
    """200 hooks at once, alternating Python and bl, across a rotation: every
    event lands exactly once as one whole line, and the rotation moved the
    pre-existing file intact (both writers rotate under the same flock)."""
    case = Case("concurrency")
    world = h.build_world(root, case)
    trace = world.proj / h.TRACE
    h.prefill(trace, h.MAX_FILE_BYTES - 60_000)
    procs = []
    for i in range(200):
        impl = "python" if i % 2 else "bl"
        procs.append(subprocess.Popen(h.argv_for(impl, Case("c", phase="end"), world), stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=world.env,
                                      cwd=str(world.cwd)))
    for i, p in enumerate(procs):
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


# --- launcher -------------------------------------------------------------
# The command hooks/hooks.json registered before hooks/tool-trace.sh existed;
# the launcher must behave exactly like it whenever the binary cannot run.
INLINE = ('if [ -f "${CLAUDE_PROJECT_DIR:-$PWD}/.build-loop/state.json" ]; then python3 '
          '"${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/tool_trace.py" --workdir "$CLAUDE_PROJECT_DIR" '
          'hook --phase {phase} >/dev/null 2>&1 || true; fi')
DIGEST = "a" * 64


def _registered(phase: str) -> str:
    """The trace command hooks/hooks.json registers for ``phase``."""
    data = json.loads((h.REPO_ROOT / "hooks" / "hooks.json").read_text())
    event = {"start": "PreToolUse", "end": "PostToolUse", "error": "PostToolUseFailure"}[phase]
    found = [hk for group in data["hooks"][event] for hk in group["hooks"] if "tool-trace.sh" in hk["command"]]
    assert len(found) == 1 and found[0]["async"] is True and found[0]["timeout"] == 1, found
    return found[0]["command"]


def test_launcher_registrations_keep_guard_and_phase():
    for phase in ("start", "end", "error"):
        assert _registered(phase) == INLINE.replace("{phase}", phase).replace(
            'python3 "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/scripts/tool_trace.py" --workdir "$CLAUDE_PROJECT_DIR" hook --phase',
            'bash "${CLAUDE_PLUGIN_ROOT:-$CLAUDE_PROJECT_DIR}/hooks/tool-trace.sh"')


def _plugin(tmp_path: Path, binary: str, *, built: str | None = DIGEST, tree: str | None = DIGEST) -> Path:
    """A throwaway plugin root: the real launcher, the real scripts/ (linked)
    and ``binary``: "real" (this build's bl), "stub" (records that it ran),
    "noexec" (not runnable) or "absent"."""
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    shutil.copy2(h.LAUNCHER, root / "hooks" / "tool-trace.sh")
    (root / "scripts").symlink_to(h.REPO_ROOT / "scripts")
    native = root / "bin" / "native"
    native.mkdir(parents=True)
    if binary == "real":
        shutil.copy2(h.BL, native / "bl")
    elif binary == "stub":
        (native / "bl").write_text('#!/bin/sh\n: > "$BL_STUB_MARK"\nexit 0\n')
        (native / "bl").chmod(0o755)
    elif binary == "noexec":
        (native / "bl").write_bytes(b"\x00not a program")
        (native / "bl").chmod(0o755)
    if built is not None:
        (native / "bl.sources").write_text(built + "\n")
    if tree is not None:
        (root / "crates").mkdir()
        (root / "crates" / "SOURCES.digest").write_text(tree + "\n# comment\n")
    return root


LAUNCHER_CASES = [c for c in CASES if c.name in {
    "claude_pre_start", "claude_failure_error", "codex_post_exit_code", "sensitive_value",
    "malformed_stdin", "empty_stdin", "project_without_build_loop", "state_missing",
    "workdir_empty_uses_cwd", "non_ascii_input"}]
FALLBACK_CONDITIONS = {
    "binary_absent": ("absent", DIGEST, {}),
    "sidecar_stale": ("stub", "b" * 64, {}),
    "sidecar_missing": ("stub", None, {}),
    "native_off": ("stub", DIGEST, {"BUILD_LOOP_NATIVE_HOOKS": "off"}),
    "binary_cannot_run": ("noexec", DIGEST, {}),
}


def _hook(command: str, case: Case, root: Path, plugin: Path, **env) -> h.Snapshot:
    return h.run("hook", case, root, argv=["sh", "-c", command.replace("{phase}", case.phase)],
                 extra_env={"CLAUDE_PLUGIN_ROOT": str(plugin), **env})


@pytest.mark.parametrize("cond", list(FALLBACK_CONDITIONS))
@pytest.mark.parametrize("case", LAUNCHER_CASES, ids=[c.name for c in LAUNCHER_CASES])
def test_launcher_without_usable_binary_matches_inline(case, cond, root, tmp_path):
    binary, built, env = FALLBACK_CONDITIONS[cond]
    plugin = _plugin(tmp_path, binary, built=built)
    mark = tmp_path / "stub-ran"
    old = _hook(INLINE, case, root, plugin, BL_STUB_MARK=str(mark), **env)
    new = _hook(_registered(case.phase), case, root, plugin, BL_STUB_MARK=str(mark), **env)
    assert not mark.exists(), "the guard ran a binary it should have refused"
    assert new.comparable() == old.comparable()


@needs_bl
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_launcher_with_fresh_binary_matches_inline(case, root, tmp_path):
    plugin = _plugin(tmp_path, "real")
    old = _hook(INLINE, case, root, plugin)
    new = _hook(_registered(case.phase), case, root, plugin)
    assert new.comparable() == old.comparable()


@needs_bl
@pytest.mark.parametrize("case", [c for c in CASES if c.native and c.name in {
    "claude_post_end", "codex_post_exit_code", "non_ascii_input", "rotation_at_limit"}])
def test_launcher_runs_fresh_binary_natively(case, root, tmp_path):
    """With no python3 on PATH only bl can write the line."""
    plugin = _plugin(tmp_path, "real")
    snap = _hook(_registered(case.phase), case, root, plugin, PATH="/bin")
    case.expect(snap)
