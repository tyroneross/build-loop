"""The registered startup command must return JSON within the host deadline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def startup(tmp_path: Path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    plugin = tmp_path / "plugin"
    hook_dir = plugin / "hooks"
    pkg = plugin / "scripts/rally_point"
    hook_dir.mkdir(parents=True)
    pkg.mkdir(parents=True)
    for name in ["session-start-rally-point.sh", "_session_start_lib.sh"]:
        shutil.copy2(ROOT / "hooks" / name, hook_dir / name)
    shutil.copy2(ROOT / "scripts/rally_point/actor_identity.py", pkg / "actor_identity.py")
    # Keep real argument parsing, host identity, deadline and native renderer.
    # Substitute only room discovery/read; no test writes to the user's ledger.
    (pkg / "hooks.py").write_text(textwrap.dedent('''
        import json, os, sys, time
        from pathlib import Path
        from types import SimpleNamespace
        if sys.argv[1] != "session-start-safe":
            sys.exit(0)
        if os.environ.get("HOOK_TEST_PARTIAL"):
            print('{"hookSpecificOutput":', end="", flush=True)
            if os.environ.get("HOOK_TEST_PARTIAL_EXIT"):
                sys.exit(0)
            time.sleep(5)
        sys.path.insert(0, os.environ["HOOK_TEST_SOURCE_SCRIPTS"])
        from rally_point import hooks
        from rally_point.backend_adapter import NativeResult
        context = SimpleNamespace(native=True, envelope=SimpleNamespace(app_slug="fixture"))
        hooks.resolve_context = lambda _: context
        def room_snapshot(context, *, actor, readers):
            Path(os.environ["HOOK_TEST_ACTOR_LOG"]).write_text(actor)
            squads = [] if os.environ.get("HOOK_TEST_NO_PEERS") else [{"tool": "other:peer", "status": "active"}]
            return NativeResult("ok", payload={"data": {"room": {"squads": squads}}}, backend="rally")
        hooks.room_snapshot = room_snapshot
        sys.exit(hooks.main(sys.argv[1:]))
    '''))
    (pkg / "session_probe.py").write_text('import json, os, sys\nfrom pathlib import Path\nPath(os.environ["HOOK_TEST_PROBE_LOG"]).write_text(json.dumps(sys.argv[1:]))\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rally = bin_dir / "rally"
    rally.write_text("#!/bin/sh\nprintf 'rally 0.2.8\\n'\n")
    rally.chmod(0o755)
    python = bin_dir / "python3"
    python.write_text("#!/bin/sh\n" + 'if [ -n "${HOOK_TEST_SLOW_PYTHON:-}" ]; then /bin/sleep 1.1; fi\n' + "exec " + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "CLAUDE_", "RALLY_", "BUILD_LOOP_"))}
    env.update({
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "CLAUDE_PLUGIN_ROOT": str(plugin),
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOOK_TEST_SOURCE_SCRIPTS": str(ROOT / "scripts"),
        "HOOK_TEST_ACTOR_LOG": str(tmp_path / "actor"),
        "HOOK_TEST_PROBE_LOG": str(tmp_path / "probe"),
    })
    registered = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["SessionStart"]
    command = next(h["command"] for group in registered for h in group["hooks"] if "session-start-rally-point.sh" in h["command"])
    payload = json.dumps({"session_id": "session-a", "hook_event_name": "SessionStart"}, indent=2)
    return command, payload, env


def run_startup(startup, **overrides):
    command, payload, env = startup
    started = time.monotonic()
    proc = subprocess.run(["/bin/bash", "-c", command], input=payload, capture_output=True, text=True, env=dict(env, **overrides), timeout=3)
    return proc, time.monotonic() - started


@pytest.mark.parametrize("host", ["codex", "claude_code"])
def test_registered_command_returns_one_session_start_envelope(startup, host):
    overrides = {"CODEX_THREAD_ID": "session-a"} if host == "codex" else {"CLAUDE_SESSION_ID": "session-a"}
    proc, elapsed = run_startup(startup, **overrides)
    assert proc.returncode == 0
    assert elapsed < 2
    assert json.loads(proc.stdout) == {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "Rally Point: fixture - 1 live peer(s)"}}
    assert Path(startup[2]["HOOK_TEST_ACTOR_LOG"]).read_text() == f"{host}:session-a"


def test_empty_room_returns_valid_noop(startup):
    proc, _ = run_startup(startup, HOOK_TEST_NO_PEERS="1")
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


@pytest.mark.parametrize("module", ["JSON/PP", "POSIX", "Time/HiRes"])
def test_unavailable_supervisor_runtime_returns_valid_noop(startup, module):
    _, _, env = startup
    modules = Path(env["CLAUDE_PLUGIN_ROOT"]) / "missing-perl-modules"
    shadow = modules / (module + ".pm")
    shadow.parent.mkdir(parents=True)
    shadow.write_text('die "fixture runtime unavailable";\n')
    proc, _ = run_startup(startup, PERL5LIB=str(modules))
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}
    assert not Path(env["HOOK_TEST_ACTOR_LOG"]).exists()


def test_explicit_host_wins_over_inherited_session_environment(startup):
    proc, _ = run_startup(startup, CODEX_THREAD_ID="parent-codex", CLAUDE_SESSION_ID="session-a", BUILD_LOOP_RALLY_TOOL="claude_code")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert Path(startup[2]["HOOK_TEST_ACTOR_LOG"]).read_text() == "claude_code:session-a"


def test_supervisor_preserves_parent_for_identity_and_watch(startup):
    command, _, env = startup
    # Exec avoids introducing the registration shell as another parent.
    proc = subprocess.run(["/bin/bash", "-c", "exec " + command.split(" 2>/dev/null;")[0]], input="{}", capture_output=True, text=True, env=env, timeout=3)
    assert proc.returncode == 0
    assert Path(env["HOOK_TEST_ACTOR_LOG"]).read_text() == f"claude_code:claude_code-proc-{os.getpid()}"
    probe = Path(env["HOOK_TEST_PROBE_LOG"])
    deadline = time.monotonic() + 2
    while not probe.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    args = json.loads(probe.read_text())
    assert args[args.index("--watch-parent-pid") + 1] == str(os.getpid())


def test_manual_foreground_diagnosis_retains_inline_background_work(startup):
    _, _, env = startup
    probe = Path(env["CLAUDE_PLUGIN_ROOT"]) / "scripts/rally_point/session_probe.py"
    probe.write_text("import time\ntime.sleep(0.3)\n" + probe.read_text())
    proc, elapsed = run_startup(startup, BUILD_LOOP_RALLY_SESSION_START_FOREGROUND="1", BUILD_LOOP_RALLY_START_DEADLINE_MS="100")
    assert proc.returncode == 0
    assert elapsed >= 0.3
    assert json.loads(proc.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert Path(env["HOOK_TEST_PROBE_LOG"]).exists()


def test_slow_interpreter_startup_is_inside_deadline(startup):
    proc, elapsed = run_startup(startup, HOOK_TEST_SLOW_PYTHON="1")
    assert proc.returncode == 0
    assert elapsed < 2
    assert json.loads(proc.stdout) == {}
    assert not Path(startup[2]["HOOK_TEST_ACTOR_LOG"]).exists()


def test_deadline_cannot_be_extended_past_host_budget(startup):
    proc, elapsed = run_startup(startup, HOOK_TEST_SLOW_PYTHON="1", BUILD_LOOP_RALLY_START_DEADLINE_MS="999999")
    assert proc.returncode == 0
    assert elapsed < 2
    assert json.loads(proc.stdout) == {}


def test_partial_child_output_is_discarded_on_deadline(startup):
    proc, elapsed = run_startup(startup, HOOK_TEST_PARTIAL="1", BUILD_LOOP_RALLY_START_DEADLINE_MS="400")
    assert proc.returncode == 0
    assert elapsed < 2
    assert json.loads(proc.stdout) == {}


def test_partial_output_from_completed_child_is_discarded(startup):
    proc, elapsed = run_startup(startup, HOOK_TEST_PARTIAL="1", HOOK_TEST_PARTIAL_EXIT="1")
    assert proc.returncode == 0
    assert elapsed < 2
    assert json.loads(proc.stdout) == {}


def test_completed_child_output_is_drained_after_scheduler_pause(startup):
    _, _, env = startup
    plugin = Path(env["CLAUDE_PLUGIN_ROOT"])
    hook = plugin / "hooks/session-start-rally-point.sh"
    # Model preemption after a select timeout while the child writes and exits.
    select_call = "my $n = select(my $rout = $rin, undef, undef, $left < 0.02 ? $left : 0.02);"
    source = hook.read_text()
    assert select_call in source
    hook.write_text(source.replace(select_call, select_call + " Time::HiRes::sleep(0.35) if $n == 0;"))
    helper = plugin / "scripts/rally_point/hooks.py"
    helper.write_text(helper.read_text().replace("from rally_point import hooks", "time.sleep(0.10)\nfrom rally_point import hooks"))
    proc, elapsed = run_startup(startup)
    assert proc.returncode == 0
    assert elapsed < 2
    assert json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] == "Rally Point: fixture - 1 live peer(s)"


def test_stdin_without_eof_is_inside_deadline(startup):
    command, payload, env = startup
    proc = subprocess.Popen(["/bin/bash", "-c", command], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(env, BUILD_LOOP_RALLY_START_DEADLINE_MS="300"))
    try:
        proc.stdin.write(payload)
        proc.stdin.flush()
        assert proc.wait(timeout=2) == 0
        assert json.loads(proc.stdout.read()) == {}
    finally:
        proc.stdin.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=2)
        proc.stdout.close()
        proc.stderr.close()
