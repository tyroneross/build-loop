# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Differential harness for the tool-trace hook.

Runs ``python3 scripts/tool_trace.py --workdir W hook --phase P`` (the oracle)
and ``bl hook trace --workdir W --phase P`` on the same stdin, environment and
throwaway project, then compares exit status, stdout, stderr and every file
and directory left under the world root (bytes and permission bits).

Time is the one field that differs between runs. Each ``start_time_unix_nano``
an implementation writes is checked to lie inside that run's wall-clock window
and then replaced with ``<T>``; every other byte is compared as-is. Prefilled
trace lines carry the fixed value ``PRESET_NS``, which is left alone.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_TRACE = REPO_ROOT / "scripts" / "tool_trace.py"
BL = Path(os.environ.get("BL_BIN") or REPO_ROOT / "target" / "release" / "bl")
REAL_PYTHON = os.path.realpath(sys.executable)
MAX_FILE_BYTES = 10 * 1024 * 1024
PRESET_NS = 1_000_000_000
TIME_FIELD = re.compile(rb'"start_time_unix_nano":(\d+)')
TELEMETRY = ".build-loop/telemetry"
TRACE = f"{TELEMETRY}/tool-traces.jsonl"


@dataclass
class Case:
    name: str
    stdin: bytes = b""
    phase: str = "end"
    # True: bl handles the case itself. False: bl hands it to Python.
    native: bool = True
    env: dict[str, str | None] = field(default_factory=dict)
    # "proj" (has .build-loop/state.json), "bare" (no .build-loop),
    # "missing" (does not exist), "empty" (--workdir "" with cwd = proj)
    workdir: str = "proj"
    # state.json bytes; None leaves it absent; "dir" makes it a directory
    state: bytes | str | None = b'{"execution": {}}'
    setup: Callable[["World"], None] | None = None
    # Hold the trace lock in another process for this many seconds.
    lock_hold: float | None = None
    # A start time taken from the payload (not the clock); compared as-is.
    start_ns: int | None = None
    expect: Callable[["Snapshot"], None] | None = None


@dataclass
class World:
    root: Path
    proj: Path
    env: dict[str, str]
    workdir_arg: str
    cwd: Path


@dataclass
class Snapshot:
    rc: int
    stdout: bytes
    stderr: bytes
    files: dict[str, str]
    new_lines: list[dict]
    elapsed: float

    def comparable(self) -> dict:
        return {"rc": self.rc, "stdout": self.stdout, "stderr": self.stderr, "files": self.files}


def payload(**fields) -> bytes:
    return json.dumps(fields).encode()


def prefill(path: Path, size: int) -> None:
    """A trace file of exactly ``size`` bytes made of valid preset lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"event": "prefill", "start_time_unix_nano": PRESET_NS,
                       "pad": "x" * 900}, separators=(",", ":")).encode() + b"\n"
    count, rest = divmod(size, len(line))
    with path.open("wb") as f:
        f.write(line * count)
        f.write(b"x" * (rest - 1) + b"\n" if rest else b"")


def build_world(root: Path, case: Case) -> World:
    if root.exists():
        _unlock_tree(root)
        shutil.rmtree(root)
    root.mkdir(parents=True)
    root = root.resolve()
    proj = root / "proj"
    for d in ("bin", "home", "tmp", "cwd"):
        (root / d).mkdir()
    (root / "bin" / "python3").symlink_to(REAL_PYTHON)
    cwd = root / "cwd"
    if case.workdir in ("proj", "empty"):
        (proj / ".build-loop").mkdir(parents=True)
        if case.state == "dir":
            (proj / ".build-loop" / "state.json").mkdir()
        elif case.state is not None:
            (proj / ".build-loop" / "state.json").write_bytes(case.state)
    elif case.workdir == "bare":
        proj.mkdir()
    if case.workdir == "empty":
        cwd = proj
        workdir_arg = ""
    elif case.workdir == "missing":
        workdir_arg = str(root / "missing" / "deeper")
    else:
        workdir_arg = str(proj)
    env = {
        "PATH": f"{root / 'bin'}:/usr/bin:/bin",
        "HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"),
        "LANG": "en_US.UTF-8",
        "CLAUDE_PROJECT_DIR": workdir_arg,
        "CLAUDE_SESSION_ID": "",
    }
    for k, v in case.env.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    world = World(root, proj, env, workdir_arg, cwd)
    if case.setup:
        case.setup(world)
    return world


def _unlock_tree(root: Path) -> None:
    """Restore write permission so read-only cases can be cleaned up."""
    for dirpath, dirnames, _ in os.walk(root):
        for d in dirnames:
            try:
                os.chmod(os.path.join(dirpath, d), 0o755)
            except OSError:
                pass
    try:
        os.chmod(root, 0o755)
    except OSError:
        pass


def argv_for(impl: str, case: Case, world: World) -> list[str]:
    if impl == "python":
        return ["python3", str(TOOL_TRACE), "--workdir", world.workdir_arg, "hook", "--phase", case.phase]
    if impl == "bl":
        return [str(BL), "hook", "trace", "--workdir", world.workdir_arg, "--phase", case.phase]
    raise ValueError(impl)


def hold_lock(world: World, seconds: float) -> subprocess.Popen:
    lock = world.proj / TELEMETRY / "tool-traces.jsonl.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [REAL_PYTHON, "-c",
         "import fcntl, os, sys, time\n"
         "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)\n"
         "fcntl.flock(fd, fcntl.LOCK_EX)\n"
         "print('ready', flush=True)\n"
         "time.sleep(float(sys.argv[2]))\n",
         str(lock), str(seconds)],
        stdout=subprocess.PIPE, text=True,
    )
    assert proc.stdout.readline().strip() == "ready"
    return proc


def _mask(data: bytes, keep: set[int]) -> tuple[bytes, list[int]]:
    seen: list[int] = []

    def sub(m: re.Match) -> bytes:
        value = int(m.group(1))
        if value in keep:
            return m.group(0)
        seen.append(value)
        return b'"start_time_unix_nano":<T>'

    return TIME_FIELD.sub(sub, data), seen


def snapshot_files(world: World, window: tuple[int, int],
                   keep: set[int]) -> tuple[dict[str, str], list[dict]]:
    out: dict[str, str] = {}
    new_lines: list[dict] = []
    skip = {"bin", "home"}
    for dirpath, dirnames, filenames in os.walk(world.root):
        rel_dir = Path(dirpath).relative_to(world.root)
        dirnames[:] = sorted(d for d in dirnames if not (rel_dir == Path(".") and d in skip))
        for d in dirnames:
            st = os.lstat(Path(dirpath) / d)
            out[str(rel_dir / d) + "/"] = f"dir:{oct(st.st_mode & 0o7777)}"
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = str(path.relative_to(world.root))
            st = os.lstat(path)
            data = path.read_bytes()
            if "tool-traces.jsonl" in name:
                data, times = _mask(data, keep)
                for t in times:
                    assert window[0] <= t <= window[1], (rel, t, window)
                for line in data.splitlines():
                    if line.startswith(b'{"attributes":'):
                        new_lines.append(json.loads(line.replace(b"<T>", b"0")))
            body = data.decode("utf-8", "replace") if len(data) <= 65536 else (
                f"sha256:{hashlib.sha256(data).hexdigest()}")
            out[rel] = f"{oct(st.st_mode & 0o7777)}:{len(data)}:{body}"
    return out, new_lines


def run(impl: str, case: Case, root: Path, argv: list[str] | None = None,
        extra_env: dict[str, str] | None = None) -> Snapshot:
    world = build_world(root, case)
    env = {**world.env, **(extra_env or {})}
    holder = hold_lock(world, case.lock_hold) if case.lock_hold else None
    before = time.time_ns()
    started = time.time()
    try:
        proc = subprocess.run(argv or argv_for(impl, case, world), input=case.stdin, env=env,
                              cwd=str(world.cwd), capture_output=True, timeout=60)
    finally:
        if holder:
            holder.wait(timeout=30)
    elapsed = time.time() - started
    after = time.time_ns()
    try:
        keep = {PRESET_NS} | ({case.start_ns} if case.start_ns else set())
        files, new_lines = snapshot_files(world, (before, after), keep)
    finally:
        _unlock_tree(world.root)
    return Snapshot(proc.returncode, proc.stdout, proc.stderr, files, new_lines, elapsed)
