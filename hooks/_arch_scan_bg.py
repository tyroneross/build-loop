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
import subprocess
import sys
from pathlib import Path


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", required=True)
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    lockpath = workdir / ".build-loop" / "architecture" / ".scan.lock"
    script = workdir / "scripts" / "architecture_freshness.py"

    try:
        lockpath.parent.mkdir(parents=True, exist_ok=True)
        lf = open(lockpath, "a+")
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return 0  # another scan is in flight; bail silently

    try:
        env = os.environ.copy()
        for cmd in (
            ["uv", "run", "python", "-m", "build_loop.architecture", "scan", "--incremental"],
            ["uv", "run", "python", "-m", "build_loop.architecture", "acp"],
        ):
            try:
                subprocess.run(
                    cmd,
                    cwd=str(workdir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    check=False,
                    timeout=120,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        if script.exists():
            try:
                subprocess.run(
                    [sys.executable, str(script), "--mark-fresh", "--workdir", str(workdir)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            lf.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
