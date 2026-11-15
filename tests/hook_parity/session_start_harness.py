# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Differential harness for the SessionStart hooks.

The oracle (``legacy``) runs the five shell hook commands one after another,
each through ``/bin/sh -c`` under its host timeout, as registered before the
native path existed. ``bl`` runs ``bl hook session-start`` once. Both run on
the same stdin, environment and throwaway project. Compared:

- stdout: ``bl`` must print exactly ``merge(<the five legacy stdouts>)``
  (scripts/session_start_hook_command.py), which keeps their order;
- exit status;
- every file left under the world root (paths, and contents outside the
  Rally room and HOME, whose records carry clocks and pids);
- the worker processes started: argv, the environment the workers read, and
  the stdin handed to the tool-state worker.

A stub ``python3`` sits first on ``PATH``. It appends one record per call and
then runs the real interpreter, except for the detached background workers
(session probe, cursor advance, peer collision, architecture scan), which
are recorded and not run. ``HARNESS_SLOW`` / ``HARNESS_FAIL`` name a worker
script to delay past its deadline or to fail after partial output.
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
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import session_start_hook_command as registration  # noqa: E402

BL = Path(os.environ.get("BL_BIN") or REPO_ROOT / "target" / "release" / "bl")
REAL_PYTHON = os.path.realpath(sys.executable)
SESSION = "11111111-2222-3333-4444-555555555555"
# Calls the shell hooks make only to compute a value bl computes itself.
HELPERS = ("-", "-c", "actor_identity.py", "codex_hook_trust_check.py", "session_start_hook_command.py")
# The shortest step deadline is 1.2 s (architecture) and 1.5 s elsewhere.
SLOW_SECONDS = 1.1
RECORDED_ONLY = ("session_probe.py", "peer_collision.py", "_arch_scan_bg.py")
TOOLS = ("bash", "sh", "git", "head", "dirname", "cat", "nohup", "sleep", "mv", "rm", "mkdir")

STUB = r"""#!/bin/sh
dir="__SPAWNS__"
base="${1##*/}"
stdin=-
case "$base" in tool_state_paths.py) stdin="$dir/stdin.$$"; /bin/cat > "$stdin";; esac
rec="$base"
for a in "$@"; do rec="$rec$(printf '\037')$a"; done
printf '%s\036%s\036%s\036%s\035' "$rec" "${WORKDIR-<unset>}" "${BUILD_LOOP_RALLY_HOOK_DEADLINE_SECONDS-<unset>}" "$stdin" >> "$dir/log"
case "$base" in session_probe.py|peer_collision.py|_arch_scan_bg.py) exit 0;; esac
case "$base:$2" in hooks.py:session-start-advance) exit 0;; esac
if [ -n "${HARNESS_SLOW:-}" ] && [ "$base" = "$HARNESS_SLOW" ]; then /bin/sleep "${HARNESS_SLOW_SECONDS:-6}"; fi
if [ -n "${HARNESS_FAIL:-}" ] && [ "$base" = "$HARNESS_FAIL" ]; then printf 'partial'; exit 3; fi
if [ "$stdin" != - ]; then exec __PYTHON__ "$@" < "$stdin"; fi
exec __PYTHON__ "$@"
"""


def event(**extra) -> bytes:
    data = {"session_id": SESSION, "hook_event_name": "SessionStart", "source": "startup"}
    data.update(extra)
    return json.dumps(data).encode()


@dataclass
class Case:
    name: str
    stdin: bytes = field(default_factory=event)
    # "project" (git + .build-loop), "plain" (git only), "nogit", "worktree"
    kind: str = "project"
    env: dict[str, str | None] = field(default_factory=dict)
    # False: CLAUDE_PROJECT_DIR is unset and the project is the cwd.
    project_dir_set: bool = True
    rally: bool = True  # a `rally` binary on PATH (a native Rally context)
    cache_age: float | None = None  # discovery cache record age; None = no record
    path: str = "full"  # "full" | "no-python" | "no-perl"
    setup: Callable[["World"], None] | None = None
    # True: bl hands the event to the five shell hooks.
    handoff: bool = False
    # True: the case is about a deadline, so a slow run is the point.
    timing: bool = False
    expect: Callable[["Snapshot"], None] | None = None


@dataclass
class World:
    root: Path
    proj: Path
    env: dict[str, str]
    cwd: Path
    spawns: Path
    slowest: float = 0.0


@dataclass
class Snapshot:
    rc: int
    stdout: bytes
    parts: list[bytes]
    files: dict[str, str]
    spawns: list[tuple]
    background: list[str]
    elapsed: float
    # A step ran long enough that a deadline may have cut it (machine load).
    slow: bool = False
    stderr: bytes = b""
    trust_python_calls: int = 0

    def comparable(self) -> dict:
        return {"rc": self.rc, "stdout": self.stdout, "files": self.files, "spawns": self.spawns,
                "background": self.background}


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                    "-c", "user.email=h@example.invalid", "-c", "user.name=harness", *args],
                   cwd=cwd, check=True, capture_output=True)


def build_plugin(root: Path, state: str) -> Path:
    """A plugin root with the repo's hooks, scripts and digest and its own
    ``bin/native``: "absent", "stale" (sidecar differs), "fresh" or "off"."""
    plugin = root / "plugin"
    plugin.mkdir()
    for name in ("hooks", "scripts", "crates"):
        (plugin / name).symlink_to(REPO_ROOT / name)
    if state != "absent":
        native = plugin / "bin" / "native"
        native.mkdir(parents=True)
        shutil.copy2(BL, native / "bl")
        digest = (REPO_ROOT / "crates" / "SOURCES.digest").read_text().splitlines()[0]
        (native / "bl.sources").write_text(("0" * 64 if state == "stale" else digest) + "\n")
    return plugin


def build_world(root: Path, case: Case, plugin: str | None = None) -> World:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    root = root.resolve()
    for d in ("bin", "home", "tmp", "spawns", "cache"):
        (root / d).mkdir()
    proj = root / "proj"
    if case.kind == "worktree":
        main = root / "main"
        main.mkdir()
        _git("init", "-q", ".", cwd=main)
        _git("commit", "-q", "--allow-empty", "-m", "init", cwd=main)
        _git("worktree", "add", "-q", str(proj), "-b", "linked", cwd=main)
    else:
        proj.mkdir()
        if case.kind != "nogit":
            _git("init", "-q", ".", cwd=proj)
    if case.kind in ("project", "worktree"):
        (proj / ".build-loop").mkdir()
        (proj / ".build-loop" / "state.json").write_text('{"execution": {}}\n')
    spawns = root / "spawns"
    bindir = root / "bin"
    stub = bindir / "python3"
    if case.path != "no-python":
        stub.write_text(STUB.replace("__SPAWNS__", str(spawns)).replace("__PYTHON__", REAL_PYTHON))
        stub.chmod(0o755)
    if case.rally:
        rally = bindir / "rally"
        rally.write_text("#!/bin/sh\nprintf 'rally: repo-local coordination room\\n\\nUsage:\\n"
                         "  rally enter --tool <tool>\\n  rally say <kind>\\n  rally whoami\\n'\n")
        rally.chmod(0o755)
    if case.path == "full":
        path = f"{bindir}:/usr/bin:/bin"
    else:
        # Only the listed tools: no system python3 (or perl) to fall back on.
        tools = TOOLS + (("perl",) if case.path == "no-python" else ())
        for tool in tools:
            target = shutil.which(tool, path="/usr/bin:/bin")
            if target:
                (bindir / tool).symlink_to(target)
        path = str(bindir)
    env = {
        "PATH": path,
        "HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"),
        "LANG": "en_US.UTF-8",
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
        "BUILD_LOOP_RALLY_DISCOVERY_CACHE_DIR": str(root / "cache"),
        "BUILD_LOOP_DISABLE_BINARY_FETCH": "1",
        "BUILD_LOOP_DISABLE_SIBLING_RALLY": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    if case.project_dir_set:
        env["CLAUDE_PROJECT_DIR"] = str(proj)
    for k, v in case.env.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v.replace("{root}", str(root))
    if plugin:
        env["CLAUDE_PLUGIN_ROOT"] = str(build_plugin(root, plugin))
        if plugin == "off":
            env["BUILD_LOOP_NATIVE_HOOKS"] = "off"
    world = World(root, proj, env, proj if not case.project_dir_set else root / "tmp", spawns)
    if case.cache_age is not None:
        _write_cache(world, case.cache_age)
    if case.setup:
        case.setup(world)
    return world


def _write_cache(world: World, age: float) -> None:
    binary = str(world.root / "bin" / "rally")
    key_dir = os.path.realpath(world.proj)
    key = subprocess.run(
        [REAL_PYTHON, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         "from rally_point import discovery_bridge as d;"
         "print(d._disk_cache_key(sys.argv[2], sys.argv[3]))",
         str(REPO_ROOT / "scripts"), key_dir, binary],
        env=world.env, check=True, capture_output=True, text=True,
    ).stdout.strip()
    record = {
        "schema": "build-loop.rally-discovery-cache.v1", "key": key, "cached_at": time.time() - age,
        "resolved_via": "repo-local-rally-cli",
        "raw": {"channel_dir": f"{key_dir}/.rally", "app_slug": "proj-slug", "repo_id": "proj-slug",
                "protocol_version": "1.0", "rally_binary": binary},
    }
    (world.root / "cache" / f"{key}.json").write_text(json.dumps(record))


_VOLATILE = [
    (re.compile(r"proc-\d+"), "proc-N"),
    (re.compile(r"cache/[0-9a-f]{64}"), "cache/<key>"),
    (re.compile(r'("snapshot_at": )[0-9.e+]+'), r"\1<T>"),
    (re.compile(r'("(?:mtime|installed_at|first_seen)": )[0-9.e+]+'), r"\1<T>"),
]


def _norm(text: str, root: Path) -> str:
    text = text.replace(str(root), "<root>")
    for pattern, repl in _VOLATILE:
        text = pattern.sub(repl, text)
    return text


def snapshot_files(world: World) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(world.root):
        rel_dir = Path(dirpath).relative_to(world.root)
        top = rel_dir.parts[0] if rel_dir.parts else ""
        dirnames[:] = sorted(
            d for d in dirnames
            if not (rel_dir == Path(".") and d in ("bin", "spawns", "plugin")) and d != "__pycache__"
            # Inside a git dir only build-loop's own state is of interest.
            and not (rel_dir.name == ".git" and d != "build-loop")
        )
        if rel_dir.name == ".git":
            filenames = []
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = _norm(str(path.relative_to(world.root)), world.root)
            presence_only = top in ("home", "cache", "tmp") or ".rally" in rel_dir.parts
            if presence_only or path.is_symlink():
                out[rel] = "present"
            else:
                text = path.read_bytes().decode("utf-8", "replace")
                if "tool-state" in rel_dir.parts and name.endswith(".json"):
                    text = json.dumps(json.loads(text), sort_keys=True)
                out[rel] = _norm(text, world.root)
    return out


def _stub_running(world: World) -> bool:
    out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
    return any(str(world.root) in line for line in out.splitlines())


def collect_spawns(world: World, wait: float = 15.0) -> tuple[list[tuple], list[str]]:
    """Worker records (sorted; helpers dropped) and the background stage's
    script names in start order."""
    log = world.spawns / "log"
    deadline = time.time() + wait
    time.sleep(0.2)
    last = -1
    while time.time() < deadline:
        size = log.stat().st_size if log.exists() else 0
        if size == last and not _stub_running(world):
            break
        last = size
        time.sleep(0.15)
    records: list[tuple] = []
    background: list[str] = []
    for line in (log.read_text() if log.exists() else "").split("\x1d")[:-1]:
        argv_field, workdir, deadline_env, stdin = line.split("\x1e")
        base, *argv = argv_field.split("\x1f")
        if base == "tool_state_paths.py" or base in HELPERS or (argv and argv[0] in HELPERS):
            continue
        argv = [_norm(a, world.root) for a in argv]
        for i, a in enumerate(argv[:-1]):
            if a == "--watch-parent-pid":
                argv[i + 1] = "<pid>"
        stdin_bytes = Path(stdin).read_bytes().decode("utf-8", "replace") if stdin != "-" else "-"
        records.append((tuple(argv), _norm(workdir, world.root), deadline_env, stdin_bytes))
        if base in RECORDED_ONLY[:2] or "session-start-advance" in argv:
            background.append(base)
    return sorted(records), background


def run_legacy(world: World, stdin: bytes, env: dict[str, str], commands=None) -> tuple[int, list[bytes]]:
    """The five hook commands in registration order, as the host runs each:
    a timed-out hook's output is discarded."""
    parts: list[bytes] = []
    rc = 0
    world.slowest = 0.0
    for _name, command, timeout, _group in commands or registration.LEGACY:
        began = time.time()
        try:
            proc = subprocess.run(["/bin/sh", "-c", command], input=stdin, env=env, cwd=str(world.cwd),
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
            parts.append(proc.stdout)
            rc = rc or proc.returncode
        except subprocess.TimeoutExpired:
            parts.append(b"")
        world.slowest = max(world.slowest, time.time() - began)
    return rc, parts


def run(impl: str, case: Case, root: Path, *, merge=registration.merge, argv: list[str] | None = None,
        extra_env: dict[str, str] | None = None, plugin: str | None = None) -> Snapshot:
    """``legacy``: the five old commands. ``registered``: every SessionStart
    command the generator registers, as the host would run them. ``bl``: the
    binary alone."""
    world = build_world(root, case, plugin)
    env = {**world.env, **(extra_env or {})}
    started = time.time()
    if impl == "legacy":
        rc, parts = run_legacy(world, case.stdin, env)
        stdout, stderr = merge(parts), b""
    elif impl == "registered":
        commands = [("", h["command"], h["timeout"], 0) for g in registration.groups() for h in g["hooks"]]
        rc, parts = run_legacy(world, case.stdin, env, commands)
        stdout, stderr = merge(parts), b""
    elif impl == "bl":
        env["BL_TRACE_FALLBACK"] = "1"
        proc = subprocess.run(argv or [str(BL), "hook", "session-start", "--root", str(REPO_ROOT)],
                              input=case.stdin, env=env, cwd=str(world.cwd), capture_output=True, timeout=30)
        rc, parts, stdout, stderr = proc.returncode, [], proc.stdout, proc.stderr
        world.slowest = time.time() - started
    else:
        raise ValueError(impl)
    elapsed = time.time() - started
    spawns, background = collect_spawns(world)
    stdout = _norm(stdout.decode("utf-8", "replace"), world.root).encode()
    log = world.spawns / "log"
    trust_calls = sum(line.startswith("codex_hook_trust_check.py\x1f")
                      for line in (log.read_text() if log.exists() else "").split("\x1d"))
    return Snapshot(rc, stdout, parts, snapshot_files(world), spawns, background, elapsed,
                    slow=world.slowest >= SLOW_SECONDS, stderr=stderr, trust_python_calls=trust_calls)


def run_stable(impl: str, case: Case, root: Path, attempts: int = 4, **kwargs) -> Snapshot:
    """``run``, repeated while a step ran slowly enough for a deadline to
    cut it. Deadline cases run once. The caller skips on a still-slow result."""
    snap = run(impl, case, root, **kwargs)
    for _ in range(attempts - 1):
        if case.timing or not snap.slow:
            break
        snap = run(impl, case, root, **kwargs)
    return snap
