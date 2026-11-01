"""Helpers for tests of hooks/pre-edit-architecture.sh.

The hook returns immediately and does its work in a detached runner
(hooks/_arch_pre_edit_bg.py), so tests poll for effects instead of asserting
right after the hook exits. A stub ``uv`` that sleeps keeps the scan running
long enough that the stale flag stays observable (the real scan marks fresh).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable


def stub_uv_env(tmp_path: Path, sleep_s: float = 0.6) -> dict:
    bindir = tmp_path / "_stubbin"
    bindir.mkdir(exist_ok=True)
    uv = bindir / "uv"
    uv.write_text(f"#!/bin/sh\nsleep {sleep_s}\n", encoding="utf-8")
    uv.chmod(0o755)
    # The hook spills its payload to $TMPDIR/bl-pre-edit.*; the runner deletes
    # it once it has read it, so wait_idle can see that the runner started.
    spill = tmp_path / "_tmp"
    spill.mkdir(exist_ok=True)
    return {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path), "TMPDIR": str(spill),
            "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}


def wait_for(pred: Callable[[], bool], timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def _busy(tmp_path: Path) -> bool:
    out = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    key = str(tmp_path)
    return any(key in line and ("_arch_pre_edit_bg" in line or "_arch_scan_bg" in line
                                or "_stubbin/uv" in line or "sleep 0.6" in line)
               for line in out.splitlines())


def wait_idle(tmp_path: Path, timeout: float = 15.0) -> None:
    """Block until the hook's detached runner and scan worker have exited.

    First wait for the runner to consume the spilled payload: before the
    background subshell execs python, ps shows only the hook's bash line, so
    a process check alone can pass before the runner has done anything.
    """
    spill = tmp_path / "_tmp"
    assert wait_for(lambda: not any(spill.glob("bl-pre-edit.*")), timeout), \
        "runner never consumed the hook payload"
    assert wait_for(lambda: not _busy(tmp_path), timeout), "runner/scan still running"
