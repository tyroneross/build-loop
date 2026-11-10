# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Time the SessionStart hooks through their registered commands.

    python3 -m tests.hook_parity.session_start_timing [runs] [room]

With ``room`` the project already has a ``.rally/`` directory, so ``bl`` does
not hold the Rally step back until the tool-state step is done.

Interleaves, per round: the five legacy commands started together (how a
host runs them today), the same five one after another, the six registered
commands started together with a fresh binary (how a host runs them now),
``bl hook session-start`` alone, and each Python worker on its own. Uses the
real interpreter (no recording stub) in a throwaway git project that already
has ``.build-loop/`` and ``.rally/``. Prints medians, worst cases and the
load average.
"""
from __future__ import annotations

import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from tests.hook_parity import session_start_harness as h


def main(runs: int, room: bool = False) -> None:
    root = Path(tempfile.mkdtemp(prefix="bl-session-start-timing-")).resolve()
    case = h.Case("timing")
    world = h.build_world(root, case, plugin="fresh")
    plugin = world.env["CLAUDE_PLUGIN_ROOT"]
    if room:
        (world.proj / ".rally").mkdir()
    python_dir = os.path.dirname(h.REAL_PYTHON)
    (root / "bin" / "python3").unlink()
    env = {**world.env, "PATH": f"{root / 'bin'}:{python_dir}:/usr/bin:/bin",
           "BUILD_LOOP_RALLY_POINT_SKIP_WATCH": "1"}
    off = {**env, "BUILD_LOOP_NATIVE_HOOKS": "off"}
    legacy = [c for _n, c, _t, _g in h.registration.LEGACY]
    registered = [x["command"] for g in h.registration.groups() for x in g["hooks"]]
    proj = str(world.proj)
    workers = {
        "tool_state_paths.py": ["python3", f"{plugin}/scripts/tool_state_paths.py", "snapshot", "--workdir", proj,
                                "--no-exclude", "--emit-context"],
        "hooks.py session-start-safe": ["python3", f"{plugin}/scripts/rally_point/hooks.py", "session-start-safe",
                                        "--workdir", proj, "--verbose", "--tool", "claude_code",
                                        "--session-id", h.SESSION],
        "stop_closeout.py": ["python3", f"{plugin}/scripts/stop_closeout.py", "--workdir", proj,
                             "--mode", "session-start", "--hook"],
        "codex_hook_trust_check.py": ["python3", f"{plugin}/scripts/codex_hook_trust_check.py", "--workdir", proj,
                                      "--quiet-unknown"],
        "python3 -c pass": ["python3", "-c", "pass"],
    }

    def together(commands, environment) -> float:
        started = time.perf_counter()
        procs = [subprocess.Popen(["/bin/sh", "-c", c], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=environment, cwd=str(world.cwd)) for c in commands]
        for p in procs:
            try:
                p.stdin.write(case.stdin)
                p.stdin.close()
            except BrokenPipeError:
                pass  # a guarded entry that exits without reading its event
        for p in procs:
            p.stdout.read()
            p.wait()
        return (time.perf_counter() - started) * 1000

    def one(argv, environment) -> float:
        started = time.perf_counter()
        subprocess.run(argv, input=case.stdin, env=environment, cwd=str(world.cwd), capture_output=True)
        return (time.perf_counter() - started) * 1000

    # One untimed pass creates .rally/ and warms caches.
    together(legacy, off)
    time.sleep(1)
    rows: dict[str, list[float]] = {}
    loads = []
    for _ in range(runs):
        loads.append(os.getloadavg()[0])
        rows.setdefault("five shell hooks, started together (host, before)", []).append(together(legacy, off))
        time.sleep(0.3)
        rows.setdefault("six registered commands, fresh binary (host, now)", []).append(together(registered, env))
        time.sleep(0.3)
        rows.setdefault("six registered commands, binary off (fallback)", []).append(together(registered, off))
        time.sleep(0.3)
        rows.setdefault("five shell hooks, one after another", []).append(
            sum(one(["/bin/sh", "-c", c], off) for c in legacy))
        time.sleep(0.3)
        rows.setdefault("bl hook session-start alone", []).append(one(["/bin/sh", "-c", h.registration.NATIVE], env))
        time.sleep(0.3)
        for name, argv in workers.items():
            rows.setdefault(f"  worker: {name}", []).append(one(argv, env))
    print(f"project {'with' if room else 'without'} .rally/")
    print(f"runs {runs}; load average min {min(loads):.0f} median {statistics.median(loads):.0f} max {max(loads):.0f}")
    print(f"{'':54} median   worst   (ms)")
    for name, values in rows.items():
        print(f"{name:54} {statistics.median(values):6.0f}  {max(values):6.0f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15, "room" in sys.argv[2:])
