"""Architecture scan commands must finish before single-flight ownership ends."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "hooks/_arch_scan_bg.py"


def _worker():
    spec = importlib.util.spec_from_file_location("arch_scan_cleanup", WORKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path: Path, monkeypatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    pids = tmp_path / "pids.json"
    uv = bindir / "uv"
    uv.write_text(f'''#!{sys.executable}
import json, os, signal, time
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open({str(pids)!r}, 'w') as output:
        json.dump([os.getppid(), os.getpid()], output)
    while True:
        time.sleep(1)
else:
    while True:
        time.sleep(1)
''')
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    return pids


def _running(pid: int) -> bool:
    status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                            capture_output=True, text=True).stdout.strip()
    return bool(status) and not status.startswith("Z")


def _assert_stopped(pids: Path) -> None:
    assert pids.exists(), "fixture descendant never started"
    owned = json.loads(pids.read_text())
    deadline = time.monotonic() + 2
    while any(_running(pid) for pid in owned) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not any(_running(pid) for pid in owned)


def _cleanup(pids: Path) -> None:
    if pids.exists():
        for pid in json.loads(pids.read_text()):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _assert_lock_available(tmp_path: Path) -> None:
    with open(tmp_path / ".build-loop/architecture/.scan.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_timeout_kills_term_resistant_grandchild_before_unlock(tmp_path, monkeypatch):
    module = _worker()
    pids = _fixture(tmp_path, monkeypatch)
    run = module.run_command
    stop = module._stop_group
    cleanup_observed = []

    def short_command(cmd, **kwargs):
        if cmd[0] == "uv":
            kwargs["timeout"] = 0.5
        return run(cmd, **kwargs)

    def check_lock_during_cleanup(proc):
        if proc.args[0] == "uv":
            with pytest.raises(BlockingIOError):
                _assert_lock_available(tmp_path)
            cleanup_observed.append(True)
        stop(proc)
        if proc.args[0] == "uv":
            _assert_stopped(pids)

    monkeypatch.setattr(module, "run_command", short_command)
    monkeypatch.setattr(module, "_stop_group", check_lock_during_cleanup)
    try:
        started = time.monotonic()
        assert module.main(["--workdir", str(tmp_path)]) == 1
        assert time.monotonic() - started < 4
        assert cleanup_observed == [True]
        _assert_stopped(pids)
        _assert_lock_available(tmp_path)
    finally:
        _cleanup(pids)


def test_worker_cancellation_cleans_owned_command_group(tmp_path, monkeypatch):
    pids = _fixture(tmp_path, monkeypatch)
    proc = subprocess.Popen([sys.executable, str(WORKER), "--workdir", str(tmp_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not pids.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pids.exists(), "fixture scan never started"
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=3)
        assert proc.returncode == 0, stderr
        _assert_stopped(pids)
        _assert_lock_available(tmp_path)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=3)
        _cleanup(pids)


def test_exec_permission_failure_keeps_freshness_unpublished(tmp_path, monkeypatch):
    module = _worker()
    calls = []

    def denied_scan(cmd, **kwargs):
        calls.append(cmd)
        if "--generation" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="0\n", stderr="")
        raise PermissionError("fixture denied execution")

    monkeypatch.setattr(module, "run_command", denied_scan)
    assert module.main(["--workdir", str(tmp_path)]) == 1
    assert len(calls) == 2
    assert not any("--mark-fresh" in cmd for cmd in calls)
    _assert_lock_available(tmp_path)
