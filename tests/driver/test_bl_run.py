# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""`bl run` against a scripted agent: the plan, a whole turn with gate
decisions and trace lines, and that no process is left behind on a
deadline, an interrupt or a failure. No model and no network."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BL = Path(os.environ.get("BL_BIN") or REPO / "target" / "release" / "bl")
FAKE = Path(__file__).with_name("fake_acp_agent.py")

pytestmark = pytest.mark.skipif(not BL.exists(), reason="bl not built (cargo build --release)")


def _profile(tmp: Path, **overrides) -> Path:
    profile = {
        "schema": "build-loop.profile.v1",
        "name": "fake",
        "agent": {"host": "claude_code", "command": sys.executable, "args": [str(FAKE)], "model_env": "ANTHROPIC_MODEL", "mode": "default", "session_meta": {"fake": {"lean": True}}},
        "sign_in": {"mode": "subscription", "unset_env": ["ANTHROPIC_API_KEY"]},
        "models": {"default": {"segment": "agentic_execution", "tier": "T3"}, "lookup": {"segment": "generative_reasoning", "tier": "T4"}},
        "instructions": {"method": True, "append": ["PROFILE-LINE"]},
        "policy": "slice1-experimental",
        "deadline_seconds": 60,
    }
    profile.update(overrides)
    path = tmp / "fake.json"
    path.write_text(json.dumps(profile))
    return path


def _env(tmp: Path, mode: str = "ok") -> dict[str, str]:
    env = dict(os.environ, FAKE_AGENT_MODE=mode, FAKE_AGENT_PIDFILE=str(tmp / "pids"), FAKE_AGENT_LOG=str(tmp / "agent.log"), BL_ROOT=str(REPO))
    env["ANTHROPIC_API_KEY"] = "must-not-reach-the-agent"
    return env


def _project(tmp: Path) -> Path:
    project = tmp / "project"
    project.mkdir()
    (project / "a.txt").write_text("hello\n")
    return project


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _assert_no_process_left(tmp: Path) -> None:
    pids = [int(p) for p in (tmp / "pids").read_text().split()]
    deadline = time.time() + 5
    while time.time() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.05)
    assert not any(_alive(p) for p in pids), f"left behind: {[p for p in pids if _alive(p)]}"


def test_dry_run_prints_the_plan_and_starts_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cp = subprocess.run([str(BL), "run", "--profile", str(_profile(tmp_path)), "--dry-run", "--json", "--cwd", str(project)], capture_output=True, text=True, env=_env(tmp_path))
    assert cp.returncode == 0, cp.stderr
    plan = json.loads(cp.stdout)
    assert plan["model"] == "sonnet" and plan["policy"] == "slice1-experimental" and plan["experimental"] is True
    assert plan["agent_command"] == [sys.executable, str(FAKE)]
    assert plan["instruction_chars"] > len("PROFILE-LINE")
    assert not (tmp_path / "pids").exists()
    lookup = subprocess.run([str(BL), "run", "--profile", str(_profile(tmp_path)), "--dry-run", "--json", "--work", "lookup", "--cwd", str(project)], capture_output=True, text=True, env=_env(tmp_path))
    assert json.loads(lookup.stdout)["model"] == "haiku"


def test_a_turn_streams_text_gates_requests_and_traces(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cp = subprocess.run([str(BL), "run", "--profile", str(_profile(tmp_path)), "--cwd", str(project), "do the thing"], capture_output=True, text=True, env=_env(tmp_path))
    assert cp.returncode == 0, cp.stderr
    # The model went in at spawn, the mode by set_config_option, and the
    # policy allowed the read and the status, refused the recursive delete.
    assert cp.stdout.strip() == "model=sonnet mode=default allowed=true,true,false"
    notice = cp.stderr.splitlines()[0]
    assert "EXPERIMENTAL" in notice and "slice1-experimental" in notice and "not a safety gate" in notice
    assert "'rm -rf planted_dir'" not in cp.stderr and '"rm -rf planted_dir" -> refuse (recursive-delete)' in cp.stderr
    assert '"git status --short" -> allow (read-only-command)' in cp.stderr
    _assert_no_process_left(tmp_path)

    seen = [json.loads(line) for line in (tmp_path / "agent.log").read_text().splitlines()]
    new = next(m for m in seen if m.get("method") == "session/new")["params"]
    assert new["cwd"] == str(project.resolve()) and new["_meta"] == {"fake": {"lean": True}}
    prompt = new["systemPrompt"]
    assert prompt["mode"] == "append" and "build-loop method" in prompt["content"][0]["text"] and prompt["content"][0]["text"].endswith("PROFILE-LINE")
    assert [m["params"]["configId"] for m in seen if m.get("method") == "session/set_config_option"] == ["mode"]
    replies = {m["id"]: m["result"]["outcome"] for m in seen if str(m.get("id", "")).startswith("perm-")}
    assert replies == {"perm-1": {"outcome": "selected", "optionId": "allow-once"}, "perm-2": {"outcome": "selected", "optionId": "allow-once"}, "perm-3": {"outcome": "selected", "optionId": "reject"}}

    lines = [json.loads(line) for line in (project / ".build-loop/telemetry/tool-traces.jsonl").read_text().splitlines()]
    assert all(line["schema"] == "build-loop.otel-tool-span.v1" for line in lines)
    events = [(line["attributes"]["gen_ai.tool.name"], line["event"]) for line in lines]
    assert events == [("Read", "start"), ("Read", "end"), ("Bash", "start"), ("Bash", "end"), ("Bash", "start"), ("Bash", "error"), ("Glob", "start"), ("Glob", "end")]


def test_json_summary_records_requests_and_the_unasked_call(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cp = subprocess.run([str(BL), "run", "--profile", str(_profile(tmp_path)), "--model", "haiku", "--json", "--cwd", str(project), "x"], capture_output=True, text=True, env=_env(tmp_path))
    assert cp.returncode == 0, cp.stderr
    summary = json.loads(cp.stdout)
    assert summary["ok"] and summary["agent_model"] == "haiku" and summary["mode"] == "default" and summary["dropped_lines"] == 1
    assert summary["ignored_requests"] == ["_fake/unknown"]
    tools = {t["id"]: t for t in summary["tools"]}
    assert [(t["requested"], t["decision"], t["status"]) for t in (tools["call-read"], tools["call-rm"])] == [(True, "allow", "completed"), (True, "refuse", "failed")]
    assert all(isinstance(tools[c]["gate_us"], int) for c in ("call-read", "call-status", "call-rm"))
    # The call the agent never asked about is visible, and marked as such.
    assert (tools["call-unasked"]["requested"], tools["call-unasked"]["ran_unasked"], tools["call-unasked"]["decision"]) == (False, True, None)
    # --summary writes the same record to a file and keeps stdout for the text.
    side = tmp_path / "side.json"
    cp = subprocess.run([str(BL), "run", "--profile", str(_profile(tmp_path)), "--summary", str(side), "--cwd", str(project), "x"], capture_output=True, text=True, env=_env(tmp_path))
    assert cp.stdout.startswith("model=sonnet") and json.loads(side.read_text())["tools"][0]["id"] == "call-read"
    stages = summary["stages"]
    assert stages["spawned_ms"] <= stages["initialized_ms"] <= stages["session_ms"] <= stages["configured_ms"] <= stages["first_text_ms"] <= stages["done_ms"]


def test_deadline_kills_the_agent_group(tmp_path: Path) -> None:
    project = _project(tmp_path)
    started = time.time()
    cp = subprocess.run([str(BL), "run", "--profile", str(_profile(tmp_path, deadline_seconds=2)), "--cwd", str(project), "x"], capture_output=True, text=True, env=_env(tmp_path, "hang"))
    assert cp.returncode == 124 and "deadline of 2s expired" in cp.stderr
    assert time.time() - started < 30
    _assert_no_process_left(tmp_path)


@pytest.mark.parametrize("sig,code", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_interrupt_kills_the_agent_group(tmp_path: Path, sig: int, code: int) -> None:
    project = _project(tmp_path)
    proc = subprocess.Popen([str(BL), "run", "--profile", str(_profile(tmp_path)), "--cwd", str(project), "x"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(tmp_path, "hang"))
    deadline = time.time() + 30
    while time.time() < deadline and "session/prompt" not in ((tmp_path / "agent.log").read_text() if (tmp_path / "agent.log").exists() else ""):
        time.sleep(0.05)
    proc.send_signal(sig)
    assert proc.wait(timeout=10) == code
    _assert_no_process_left(tmp_path)


def test_failures_exit_non_zero_with_one_line_and_leave_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run = lambda mode, *extra: subprocess.run([str(BL), "run", "--profile", str(_profile(tmp_path)), "--cwd", str(project), *extra, "x"], capture_output=True, text=True, env=_env(tmp_path, mode))

    refused = run("refusal")
    assert refused.returncode == 1 and refused.stderr.strip().splitlines()[-1] == "bl run: turn stopped: refusal"
    _assert_no_process_left(tmp_path)

    unknown_model = run("ok", "--model", "gpt-6.1-sol")
    assert unknown_model.returncode == 1 and "agent does not offer model" in unknown_model.stderr.splitlines()[-1]
    _assert_no_process_left(tmp_path)

    no_capability = run("no-prompt-cap")
    assert no_capability.returncode == 1 and "does not accept appended instructions" in no_capability.stderr
    _assert_no_process_left(tmp_path)

    missing = subprocess.run([str(BL), "run", "--profile", str(_profile(tmp_path, agent={"host": "claude_code", "command": "/nonexistent/agent"})), "--cwd", str(project), "x"], capture_output=True, text=True, env=_env(tmp_path))
    assert missing.returncode == 1 and "cannot start agent" in missing.stderr


def test_usage_and_profile_errors_exit_2(tmp_path: Path) -> None:
    project = _project(tmp_path)
    for override, needle in (({"sign_in": {"mode": "api-key"}}, "reserved"), ({"policy": "strict"}, "does not have"), ({"skills": ["x"]}, "not built in this slice")):
        cp = subprocess.run([str(BL), "run", "--profile", str(_profile(tmp_path, **override)), "--cwd", str(project), "x"], capture_output=True, text=True, env=_env(tmp_path))
        assert cp.returncode == 2 and needle in cp.stderr, cp.stderr
    cp = subprocess.run([str(BL), "run", "--cwd", str(project), "x"], capture_output=True, text=True, env=_env(tmp_path))
    assert cp.returncode == 2 and "--profile is required" in cp.stderr


def test_the_api_key_variable_does_not_reach_the_agent(tmp_path: Path) -> None:
    project = _project(tmp_path)
    probe = tmp_path / "probe.py"
    probe.write_text("import os, sys\nopen(sys.argv[1], 'w').write(str('ANTHROPIC_API_KEY' in os.environ))\n")
    profile = _profile(tmp_path, agent={"host": "claude_code", "command": sys.executable, "args": [str(probe), str(tmp_path / "seen")]})
    subprocess.run([str(BL), "run", "--profile", str(profile), "--cwd", str(project), "x"], capture_output=True, text=True, env=_env(tmp_path))
    assert (tmp_path / "seen").read_text() == "False"
