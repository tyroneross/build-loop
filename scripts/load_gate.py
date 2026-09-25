#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""load_gate.py — keep parallel build/perf work off an already-loaded machine.

WHY
---
Parallel multi-agent builds dispatched concurrently (Phase 3 fan-out) can each
launch their own compiler/test invocation on the SAME machine with no shared
awareness of the others. Observed (2026-09): several concurrent builds pushed
the 1-minute load average to 64 while antivirus File Shield sat at ~225% CPU,
so every perf measurement taken during that window was noise, not signal, and
every build competed for the same handful of cores instead of running serially
behind each other.

This script is the one gate: serialize competing builds behind a single
exclusive lock, cap the job count each build asks for, and refuse (rather than
silently record) a perf measurement taken while the machine was already hot.

VERBS
-----
    load_gate.py run [--jobs N] [--nice NICE] [--lock PATH]
                      [--wait-seconds S] -- <cmd...>
        Take the exclusive build lock (blocking up to --wait-seconds), run
        <cmd...> under `nice -n NICE` with a capped parallel job count
        exported via env (CARGO_BUILD_JOBS, MAKEFLAGS=-jN,
        CMAKE_BUILD_PARALLEL_LEVEL), and return the command's own exit code.
        Swift/Xcode do not read a job-count env var — callers building with
        `swift build` / `xcodebuild` must pass `-jobs N` (Xcode) or
        `--jobs N` (SwiftPM) themselves in <cmd...>; this script only sets the
        env vars the other toolchains honor.
        Exit 75 (EX_TEMPFAIL) if the lock cannot be acquired within
        --wait-seconds — never silently proceeds unlocked.

    load_gate.py check-measure [--max-load N] [--json]
        Exit 0 when the 1-minute load average is below --max-load, else exit 1
        with the load in the message. --json prints
        {load1, load5, load15, max_load, ok}.

    load_gate.py record --metric NAME --value FLOAT [--unit UNIT]
                         [--file PATH] [--max-load N] [--force]
        Append one JSONL row {metric, value, unit, load1, load5, load15,
        max_load, ts} to --file. REFUSES (exit 1, nothing written) when
        load1 >= --max-load unless --force, in which case the row also
        carries `"load_gate": "overridden"` so a later reader can tell the
        measurement was taken hot.

LOCK
----
An exclusive `fcntl.flock` on a sidecar lockfile at --lock (default
~/.build-loop/locks/build.lock). Blocking with a bounded poll loop rather than
a bare blocking flock so the wait is interruptible and has a deadline; the
lock is process-scoped (released automatically on process exit even on a
crash, since flock ties to the open fd).

Stdlib only. POSIX (fcntl) — matches the rest of build-loop's lock primitives
(scripts/atomic_io.py::LockedFile), which is intentionally NOT reused here:
that lock always targets a sidecar next to a data file being written; this
lock targets one fixed machine-wide path regardless of what is being built,
so sharing the class would only add an unused `target` concept.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOCK_PATH = Path.home() / ".build-loop" / "locks" / "build.lock"
DEFAULT_WAIT_SECONDS = 1800.0
DEFAULT_JOBS = 4
DEFAULT_NICE = 15
DEFAULT_MAX_LOAD = 15.0
DEFAULT_PERF_FILE = Path(".build-loop") / "perf-measurements.jsonl"

#: EX_TEMPFAIL (BSD sysexits.h) — a transient condition, not a usage error.
EXIT_LOCK_TIMEOUT = 75

_POLL_INTERVAL_S = 0.2


def get_loadavg() -> tuple[float, float, float]:
    """Thin wrapper over os.getloadavg() — the sole seam tests monkeypatch.

    Keeping every load read in this repo behind one function means a test can
    swap in fixed values without depending on, or waiting on, real machine
    load.
    """
    return os.getloadavg()


def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BuildLock:
    """Exclusive fcntl.flock on a sidecar lockfile, blocking up to wait_seconds.

    Raises TimeoutError if the lock is still held by someone else once the
    deadline passes. Always releases on __exit__, including on exception.
    """

    def __init__(self, lock_path: Path, wait_seconds: float) -> None:
        self.lock_path = lock_path
        self.wait_seconds = wait_seconds
        self._fd: int | None = None

    def __enter__(self) -> "BuildLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise TimeoutError(
                        f"build lock busy: could not acquire {self.lock_path} "
                        f"within {self.wait_seconds:.0f}s — another build is "
                        "holding it"
                    )
                time.sleep(_POLL_INTERVAL_S)

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def _jobs_env(jobs: int) -> dict[str, str]:
    """Base environment plus the parallel-job caps every non-Swift toolchain reads."""
    env = dict(os.environ)
    env["CARGO_BUILD_JOBS"] = str(jobs)
    env["MAKEFLAGS"] = f"-j{jobs}"
    env["CMAKE_BUILD_PARALLEL_LEVEL"] = str(jobs)
    return env


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

HELD_ENV = "LOAD_GATE_HELD"


def _returncode(proc: subprocess.CompletedProcess) -> int:
    """Shell convention for a signal death (128+N), not sys.exit's 256-N wrap."""
    return 128 - proc.returncode if proc.returncode < 0 else proc.returncode


def cmd_run(args: argparse.Namespace) -> int:
    if not args.command:
        print("load_gate run: no command given — pass '-- <cmd...>'", file=sys.stderr)
        return 2

    argv = ["nice", "-n", str(args.nice), *args.command]
    if os.environ.get(HELD_ENV) == "1":
        # Nested `load_gate run` inside a gated command: the outer call already
        # holds the machine-wide lock, so waiting on it would deadlock until the
        # timeout (independent-auditor f8, 2026-09-25).
        return _returncode(subprocess.run(argv, env=_jobs_env(args.jobs), shell=False))
    try:
        with BuildLock(args.lock, args.wait_seconds):
            env = _jobs_env(args.jobs)
            env[HELD_ENV] = "1"
            return _returncode(subprocess.run(argv, env=env, shell=False))
    except TimeoutError as exc:
        print(f"load_gate run: {exc}", file=sys.stderr)
        return EXIT_LOCK_TIMEOUT


# ---------------------------------------------------------------------------
# check-measure
# ---------------------------------------------------------------------------

def cmd_check_measure(args: argparse.Namespace) -> int:
    load1, load5, load15 = get_loadavg()
    ok = load1 < args.max_load
    if args.json_output:
        print(json.dumps({
            "load1": load1,
            "load5": load5,
            "load15": load15,
            "max_load": args.max_load,
            "ok": ok,
        }))
    elif ok:
        print(f"load ok: load1={load1:.2f} < max {args.max_load:g}")
    else:
        print(f"load too high: load1={load1:.2f} >= max {args.max_load:g}", file=sys.stderr)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

def cmd_record(args: argparse.Namespace) -> int:
    load1, load5, load15 = get_loadavg()
    gated = load1 >= args.max_load
    if gated and not args.force:
        print(
            f"load_gate record: refusing — load1={load1:.2f} >= max "
            f"{args.max_load:g}; pass --force to record anyway (the row will "
            "carry load_gate: overridden)",
            file=sys.stderr,
        )
        return 1

    row: dict[str, Any] = {
        "metric": args.metric,
        "value": args.value,
        "unit": args.unit,
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "max_load": args.max_load,
        "ts": _iso_utc(),
    }
    if gated and args.force:
        row["load_gate"] = "overridden"

    args.file.parent.mkdir(parents=True, exist_ok=True)
    with args.file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    print(json.dumps(row))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _split_command(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first literal '--': (own args, trailing command).

    Done by hand rather than via argparse.REMAINDER because REMAINDER's
    interaction with subparsers and a leading '--' is inconsistent across
    Python versions; a plain list split is unambiguous.
    """
    if "--" in argv:
        idx = argv.index("--")
        return argv[:idx], argv[idx + 1:]
    return argv, []


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Serialize competing builds behind one lock and gate perf "
        "measurements on machine load."
    )
    sub = p.add_subparsers(dest="verb", required=True)

    run_p = sub.add_parser("run", help="Run a command under the build lock + job cap.")
    run_p.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    run_p.add_argument("--nice", type=int, default=DEFAULT_NICE)
    run_p.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    run_p.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT_SECONDS)
    run_p.set_defaults(func=cmd_run)

    check_p = sub.add_parser("check-measure", help="Exit 0 iff load1 < --max-load.")
    check_p.add_argument("--max-load", type=float, default=DEFAULT_MAX_LOAD, dest="max_load")
    check_p.add_argument("--json", action="store_true", dest="json_output")
    check_p.set_defaults(func=cmd_check_measure)

    record_p = sub.add_parser("record", help="Append a load-gated perf measurement row.")
    record_p.add_argument("--metric", required=True)
    record_p.add_argument("--value", type=float, required=True)
    record_p.add_argument("--unit", default="ms")
    record_p.add_argument("--file", type=Path, default=DEFAULT_PERF_FILE)
    record_p.add_argument("--max-load", type=float, default=DEFAULT_MAX_LOAD, dest="max_load")
    record_p.add_argument("--force", action="store_true")
    record_p.set_defaults(func=cmd_record)

    return p


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    own_args, command = _split_command(raw)
    args = _build_parser().parse_args(own_args)
    args.command = command
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
