#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Background scan worker — invoked by both hook scripts.

Acquires a non-blocking ``fcntl.flock`` on
``.build-loop/architecture/.scan.lock`` and runs scan + acp + mark-fresh.

Exits silently if the lock is held; never raises.

Intended to be called as::

    nohup python3 hooks/_arch_scan_bg.py --workdir <path> </dev/null \\
        >/dev/null 2>&1 &

Why a separate file: putting the Python payload in a heredoc inside a shell
hook conflicts with the `</dev/null` redirection used to fully detach the
backgrounded process — the second stdin redirection wins, and the heredoc is
never delivered to the interpreter.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import time
import subprocess
import sys
from pathlib import Path


_CANCELLATION_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
_defer_cancel = False
_cancel_requested = False


def _stop_group(proc: subprocess.Popen) -> None:
    """Stop this command and any descendants before releasing the scan lock."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    else:
        time.sleep(0.05)
        # The group can remain after its leader exits; always escalate the
        # owned group, not just the Popen child.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def run_command(cmd: list[str], *, timeout: float, cwd: str | None = None,
                env: dict | None = None, capture_output: bool = False,
                text: bool = False, check: bool = False) -> subprocess.CompletedProcess:
    """Run one bounded command in its own session and clean up its whole group."""
    global _defer_cancel
    proc = None
    # Defer the Python cancellation handler until Popen has registered the
    # child. Do not block OS signals: children would inherit that mask.
    _defer_cancel = True
    try:
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
                stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
                text=text, start_new_session=True,
            )
        finally:
            _defer_cancel = False
        if _cancel_requested:
            raise SystemExit(0)
        stdout, stderr = proc.communicate(timeout=timeout)
        result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        if proc is not None:
            _defer_cancel = True
            try:
                _stop_group(proc)
            finally:
                _defer_cancel = False
            if _cancel_requested:
                raise SystemExit(0)


def _cancel(*_args) -> None:
    global _cancel_requested
    _cancel_requested = True
    if not _defer_cancel:
        raise SystemExit(0)


def main(argv: list | None = None) -> int:
    global _cancel_requested
    _cancel_requested = False
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", required=True)
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    lockpath = workdir / ".build-loop" / "architecture" / ".scan.lock"
    # The freshness script ships with the plugin; a project copy is only a fallback.
    script = Path(__file__).resolve().parent.parent / "scripts" / "architecture_freshness.py"
    if not script.is_file():
        script = workdir / "scripts" / "architecture_freshness.py"

    try:
        lockpath.parent.mkdir(parents=True, exist_ok=True)
        lf = open(lockpath, "a+")
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return 0  # another scan is in flight; bail silently

    previous_handlers = {sig: signal.signal(sig, _cancel) for sig in _CANCELLATION_SIGNALS}
    try:
        # Capture after taking the scan lock and before reading source. A later
        # edit may mark stale while this worker owns the scan lock; completing
        # this older scan must not erase that invalidation.
        try:
            observed = run_command(
                [sys.executable, str(script), "--generation", "--workdir", str(workdir)],
                capture_output=True, text=True, check=True, timeout=10,
            )
            generation = int(observed.stdout.strip())
            if generation < 0:
                return 1
        except (OSError, ValueError, subprocess.SubprocessError):
            return 1
        env = os.environ.copy()
        for cmd in (
            ["uv", "run", "python", "-m", "build_loop.architecture", "scan", "--incremental"],
            ["uv", "run", "python", "-m", "build_loop.architecture", "acp"],
        ):
            try:
                result = run_command(
                    cmd,
                    cwd=str(workdir),
                    env=env,
                    check=False,
                    timeout=120,
                )
                if result.returncode != 0:
                    return 1
            except (OSError, subprocess.SubprocessError):
                return 1

        if script.exists():
            try:
                run_command(
                    [sys.executable, str(script), "--mark-fresh", "--if-generation", str(generation),
                     "--workdir", str(workdir)],
                    check=False,
                    timeout=10,
                )
            except (OSError, subprocess.SubprocessError):
                pass
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            lf.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
