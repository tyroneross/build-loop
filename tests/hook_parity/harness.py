# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Differential harness for the Rally pre-edit hook.

Runs ``hooks/pre-edit-rally-point.sh`` (forced onto the shell path) and the
native ``bl hook pre-edit`` on the same stdin, environment and temp git repos,
then compares exit status, stdout, stderr, the files each run left behind and
the argv of every child process it started.

Child processes are captured, never run: a stub ``python3`` sits first on
``PATH``. It passes the hook's own ``hooks.py pre-edit`` call through to the
real interpreter (with ``PYTHONEXECUTABLE`` pointing back at the stub, so
``sys.executable`` children also hit it) and records every other call.
A stub ``rally`` prints Rally's usage text for the binary probe.
"""
from __future__ import annotations

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
HOOK = REPO_ROOT / "hooks" / "pre-edit-rally-point.sh"
BL = Path(os.environ.get("BL_BIN") or REPO_ROOT / "target" / "release" / "bl")
REAL_PYTHON = os.path.realpath(sys.executable)
FIXED_STUB_MTIME = 1_000_000_000

RALLY_USAGE = (
    "rally: repo-local coordination room\\n\\nUsage:\\n"
    "  rally enter --tool <tool>\\n  rally say <kind>\\n  rally whoami\\n"
)


@dataclass
class Case:
    name: str
    stdin: bytes = b""
    env: dict[str, str] = field(default_factory=dict)
    # workdir: "repo", "other", "nogit", "missing" (CLAUDE_PROJECT_DIR)
    workdir: str = "repo"
    # cache entries: {"repo": age_seconds, ...}; slug override via cache_slug
    cache: dict[str, float] = field(default_factory=dict)
    cache_slug: dict[str, str] = field(default_factory=dict)
    # stamps: {"<dir label>/<name>": age_seconds}
    stamps: dict[str, float] = field(default_factory=dict)
    locks: dict[str, float] = field(default_factory=dict)
    rally: str = "ok"  # ok | none | slow
    # Unsupervised runs skip the shell's 1.5 s deadline so a loaded machine
    # still produces a comparable oracle; deadline cases set this True.
    supervised: bool = False
    expect: Callable[["Snapshot", "World"], None] | None = None


@dataclass
class World:
    root: Path
    paths: dict[str, Path]
    env: dict[str, str]
    keys: dict[str, str]
    preset: dict[str, float]
    spawn_dir: Path
    binary: str


@dataclass
class Snapshot:
    rc: int
    stdout: bytes
    stderr: bytes
    files: dict[str, str]
    spawns: list[tuple[str, ...]]
    elapsed: float

    def comparable(self) -> dict:
        return {
            "rc": self.rc,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "files": self.files,
            "spawns": self.spawns,
        }


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)
    os.utime(path, (FIXED_STUB_MTIME, FIXED_STUB_MTIME))


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def build_world(root: Path, case: Case) -> World:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    root = root.resolve()
    paths = {
        "repo": root / "repo",
        "other": root / "other",
        "nogit": root / "nogit",
        "missing": root / "does-not-exist",
    }
    _git_init(paths["repo"])
    _git_init(paths["other"])
    paths["nogit"].mkdir()
    (paths["repo"] / "sub").mkdir()
    bindir = root / "bin"
    bindir.mkdir()
    spawn_dir = root / "spawns"
    spawn_dir.mkdir()
    home = root / "home"
    home.mkdir()
    cache_dir = root / "cache"

    _write_exec(
        bindir / "python3",
        "#!/bin/sh\n"
        'if [ "$2" = pre-edit ]; then case "$1" in */scripts/rally_point/hooks.py)\n'
        f'  PYTHONEXECUTABLE="$0" exec {REAL_PYTHON} "$@";; esac; fi\n'
        f'out="{spawn_dir}/spawn.$$"\n'
        "printf '%s\\0' \"$0\" \"$@\" > \"$out.tmp\" && mv \"$out.tmp\" \"$out\"\n",
    )
    if case.rally != "none":
        sleep = "sleep 3\n" if case.rally == "slow" else ""
        _write_exec(bindir / "rally", f"#!/bin/sh\n{sleep}printf '{RALLY_USAGE}'\n")
    binary = str(bindir / "rally")

    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "BUILD_LOOP_RALLY_DISCOVERY_CACHE_DIR": str(cache_dir),
        "CLAUDE_PROJECT_DIR": str(paths[case.workdir]),
        "BUILD_LOOP_DISABLE_BINARY_FETCH": "1",
        "BUILD_LOOP_DISABLE_SIBLING_RALLY": "1",
        "CLAUDE_SESSION_ID": "sess-parity",
    }
    for k, v in case.env.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v.replace("{root}", str(root))

    keys: dict[str, str] = {}
    now = time.time()
    preset: dict[str, float] = {}
    for label, age in case.cache.items():
        key_dir = os.path.realpath(paths[label])
        key = subprocess.run(
            [REAL_PYTHON, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]);"
             "from rally_point import discovery_bridge as d;"
             "print(d._disk_cache_key(sys.argv[2], sys.argv[3]))",
             str(REPO_ROOT / "scripts"), key_dir, binary],
            env=env, check=True, capture_output=True, text=True,
        ).stdout.strip()
        keys[label] = key
        cache_dir.mkdir(parents=True, exist_ok=True)
        slug = case.cache_slug.get(label, f"{label}-slug")
        record = {
            "schema": "build-loop.rally-discovery-cache.v1",
            "key": key,
            "cached_at": now - age,
            "resolved_via": "repo-local-rally-cli",
            "raw": {
                "channel_dir": f"{key_dir}/.rally",
                "app_slug": slug,
                "repo_id": slug,
                "protocol_version": "1.0",
                "rally_binary": binary,
            },
        }
        cache_file = cache_dir / f"{key}.json"
        cache_file.write_text(json.dumps(record))
        preset[str(cache_file.relative_to(root))] = os.stat(cache_file).st_mtime
    for spec, age in case.locks.items():
        label, key_label = spec.split(":")
        key = keys.get(key_label) or _key_for(env, binary, paths[key_label])
        cache_dir.mkdir(parents=True, exist_ok=True)
        lock = cache_dir / f"{key}.refresh"
        lock.write_text("")
        os.utime(lock, (now - age, now - age))
        preset[str(lock.relative_to(root))] = now - age
    for spec, age in case.stamps.items():
        label, name = spec.split("/", 1)
        stamp = paths[label] / ".build-loop" / "rally-hook-throttle" / name
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text("")
        os.utime(stamp, (now - age, now - age))
        preset[str(stamp.relative_to(root))] = now - age
    return World(root, paths, env, keys, preset, spawn_dir, binary)


def _key_for(env: dict[str, str], binary: str, path: Path) -> str:
    return subprocess.run(
        [REAL_PYTHON, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         "from rally_point import discovery_bridge as d;"
         "print(d._disk_cache_key(sys.argv[2], sys.argv[3]))",
         str(REPO_ROOT / "scripts"), os.path.realpath(path), binary],
        env=env, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _norm(text: str) -> str:
    return re.sub(r"proc-(?!4242\b)\d+", "proc-N", text)


def snapshot_files(world: World, started: float) -> dict[str, str]:
    out: dict[str, str] = {}
    skip = {"bin", "spawns"}
    for dirpath, dirnames, filenames in os.walk(world.root):
        rel_dir = Path(dirpath).relative_to(world.root)
        dirnames[:] = [
            d for d in dirnames
            if not (rel_dir == Path(".") and d in skip) and d != ".git"
            and d != "__pycache__"
        ]
        for name in filenames:
            path = Path(dirpath) / name
            rel = str(path.relative_to(world.root))
            st = os.lstat(path)
            if rel in world.preset:
                state = "touched" if st.st_mtime > world.preset[rel] + 1 else "kept"
            else:
                state = "new"
            if rel.startswith("home/") or (rel.startswith("cache/") and rel.endswith(".json")):
                # Presence and cache records carry timestamps and pids.
                out[_norm(rel)] = state
            else:
                out[_norm(rel)] = f"{state}:{st.st_size}"
    return out


def _stubs_running(world: World) -> bool:
    stub = str(world.root / "bin" / "python3")
    out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
    return any(stub in line for line in out.splitlines())


def collect_spawns(world: World, wait: float = 10.0, settle: float = 0.2) -> list[tuple[str, ...]]:
    """Wait until no stub ``python3`` is still running, then read records.

    Both implementations return from spawn only after exec succeeded, so
    every child exists by the time the hook exits; a loaded machine can
    still take a while to let it write its record.
    """
    deadline = time.time() + wait
    time.sleep(settle)
    while time.time() < deadline and _stubs_running(world):
        time.sleep(0.05)
    last = sorted(p.name for p in world.spawn_dir.glob("spawn.*") if not p.name.endswith(".tmp"))
    spawns = []
    stub = str(world.root / "bin" / "python3")
    for name in last:
        parts = (world.spawn_dir / name).read_bytes().split(b"\0")[:-1]
        argv = [p.decode("utf-8", "replace") for p in parts]
        argv[0] = "<python-stub>" if argv[0] == stub else argv[0]
        spawns.append(tuple(_norm(a.replace(str(world.root), "<root>")) for a in argv))
    return sorted(spawns)


def run(impl: str, case: Case, root: Path) -> tuple[Snapshot, World]:
    world = build_world(root, case)
    env = dict(world.env)
    if impl == "shell":
        env["BUILD_LOOP_NATIVE_HOOKS"] = "off"
        argv = ["bash", str(HOOK)]
    elif impl == "bl":
        argv = [str(BL), "hook", "pre-edit"]
    elif impl == "launcher":
        argv = ["bash", str(HOOK)]
    else:
        raise ValueError(impl)
    if not case.supervised and impl != "launcher":
        # The supervisor's own re-entry marker: the hook body runs directly.
        env["BUILD_LOOP_BOUNDED_HOOK_CHILD"] = str(HOOK)
        env["BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS"] = "0"
    started = time.time()
    proc = subprocess.run(
        argv, input=case.stdin, env=env, cwd=str(world.root / "nogit"),
        capture_output=True, timeout=30,
    )
    elapsed = time.time() - started
    spawns = collect_spawns(world)
    snap = Snapshot(
        rc=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        files=snapshot_files(world, started),
        spawns=spawns,
        elapsed=elapsed,
    )
    return snap, world
