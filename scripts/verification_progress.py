"""Native verification supervision, with portable file logging when Rust is absent."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TAIL_BYTES = 8 * 1024 * 1024


class ProgressError(OSError):
    def __init__(self, message: str, progress: dict):
        super().__init__(message)
        self.progress = progress


class ProgressTimeout(subprocess.TimeoutExpired):
    def __init__(self, command: list[str], timeout: int, stdout: str, stderr: str, progress: dict):
        super().__init__(command, timeout, output=stdout.encode("utf-8"), stderr=stderr.encode("utf-8"))
        self.progress = progress


def native_runner() -> Path | None:
    compiler = shutil.which("rustc")
    if not compiler:
        return None
    source = HERE / "progress_runner.rs"
    version = subprocess.run([compiler, "-vV"], check=True, capture_output=True).stdout
    source_bytes = source.read_bytes()
    key = hashlib.sha256(source_bytes + version + platform.machine().encode()).hexdigest()
    cache = Path.home() / ".cache" / "build-loop" / "progress-runner" / key
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = cache / ("runner.exe" if platform.system() == "Windows" else "runner")
    if not binary.is_file():
        with tempfile.TemporaryDirectory(prefix="build-", dir=cache) as staging:
            snapshot = Path(staging) / "progress_runner.rs"
            snapshot.write_bytes(source_bytes)
            temporary = Path(staging) / binary.name
            subprocess.run([compiler, "--edition=2021", "-O", str(snapshot), "-o", str(temporary)], check=True, capture_output=True)
            os.replace(temporary, binary)
    return binary


def _tail(path: Path) -> str:
    with path.open("rb") as source:
        source.seek(max(0, path.stat().st_size - TAIL_BYTES))
        return source.read(TAIL_BYTES).decode("utf-8", errors="replace")


def run_logged(command: list[str], *, cwd: Path, timeout: int | None, directory: Path) -> tuple[subprocess.CompletedProcess, dict]:
    """Run once; never retry a failed native launch through the portable path."""
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="verify-", dir=directory))
    os.chmod(run_dir, 0o700)
    env = dict(os.environ, BUILD_LOOP_PROGRESS_DIR=str(run_dir), PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = str(HERE) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # pytest hooks are the test-identity authority; stdout parsing is not.
    command = [*command, "-p", "build_loop_progress"]
    info = {"directory": str(run_dir), "stdout": str(run_dir / "stdout.log"),
            "stderr": str(run_dir / "stderr.log"), "tests": str(run_dir / "pytest-status.json"),
            "test_events": str(run_dir / "pytest-events.jsonl"), "runner": str(run_dir / "runner-status.json")}
    print(f"self_mod_verify: progress logs: {run_dir}", file=sys.stderr, flush=True)
    try:
        try:
            binary = native_runner()
        except subprocess.CalledProcessError as error:
            raise OSError(f"Rust progress runner build failed: {error.stderr!r}; logs: {run_dir}") from error
        info["backend"] = "rust" if binary else "portable"
        if binary:
            native_command = [str(binary), "--log-dir", str(run_dir), "--cwd", str(cwd),
                              "--timeout-ms", str(int(timeout * 1000) if timeout else 0),
                              "--parent-pid", str(os.getpid()), "--", *command]
            process = subprocess.Popen(native_command, env=env)
            try:
                returncode = process.wait()
            except KeyboardInterrupt:
                # Give the Rust supervisor ownership of cancellation and group cleanup.
                process.send_signal(signal.SIGINT)
                process.wait()
                raise
            try:
                status = json.loads((run_dir / "runner-status.json").read_text())
            except (OSError, ValueError) as error:
                raise OSError(f"Native progress runner did not finish; logs: {run_dir}") from error
            if status.get("state") not in {"finished", "timed_out", "cancelled"} or status.get("exit_code") != returncode:
                raise OSError(f"Native progress runner failed; logs: {run_dir}")
            if status["state"] == "cancelled":
                raise OSError(f"Verification cancelled; logs: {run_dir}")
            timed_out = status["state"] == "timed_out"
        else:
            print("self_mod_verify: Rust compiler unavailable; portable output/test logs enabled, native heartbeat and process-group supervision unavailable", file=sys.stderr)
            with (run_dir / "stdout.log").open("xb") as stdout, (run_dir / "stderr.log").open("xb") as stderr:
                try:
                    result = subprocess.run(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
                    returncode, timed_out = result.returncode, False
                except subprocess.TimeoutExpired:
                    returncode, timed_out = 124, True
            (run_dir / "runner-status.json").write_text(json.dumps({"state": "timed_out" if timed_out else "finished", "exit_code": returncode, "backend": "portable", "process_group": False}))
        stdout = _tail(run_dir / "stdout.log")
        stderr = _tail(run_dir / "stderr.log")
        info["output_tail_truncated"] = any((run_dir / name).stat().st_size > TAIL_BYTES for name in ("stdout.log", "stderr.log"))
        if timed_out:
            if timeout is None:
                raise OSError("Native supervisor reported an unrequested deadline")
            raise ProgressTimeout(command, timeout, stdout, stderr, info)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr), info
    except OSError as error:
        raise ProgressError(str(error), info) from error
