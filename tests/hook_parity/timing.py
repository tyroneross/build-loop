# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Time the pre-edit hook: shell path vs launcher->bl vs bl alone.

Usage: python3 -m tests.hook_parity.timing [--runs N] [--json OUT]

Each run resets the throttle stamp (fresh: 5 s old; stale: 120 s old) in a
temp git repo whose discovery cache is fresh, then times one invocation.
Implementations are interleaved run by run so they share the machine load.
``python3`` on PATH is a symlink to the real interpreter (no wrapper cost);
children (the detached join) really run, identically for every variant.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from tests.hook_parity import harness
from tests.hook_parity.harness import Case


def load() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=15)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    root = Path(tempfile.mkdtemp()).resolve() / "w"
    case = Case("timing", cache={"repo": 5.0})
    world = harness.build_world(root, case)
    py = root / "bin" / "python3"
    py.unlink()
    py.symlink_to(harness.REAL_PYTHON)
    stamp = world.paths["repo"] / ".build-loop" / "rally-hook-throttle" / "sess-parity.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()
    stdin = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": f"{world.paths['repo']}/a.py"}}).encode()
    variants = {
        "shell": (["bash", str(harness.HOOK)], {"BUILD_LOOP_NATIVE_HOOKS": "off"}),
        "launcher_bl": (["bash", str(harness.HOOK)], {}),
        "bl_direct": ([str(harness.REPO_ROOT / "bin" / "native" / "bl"), "hook", "pre-edit"], {}),
    }
    results: dict = {"load_before": load(), "runs": args.runs, "paths": {}}
    for path_name, age in (("fresh_stamp", 5.0), ("stale_stamp", 120.0)):
        samples: dict[str, list[float]] = {k: [] for k in variants}
        outputs: dict[str, set] = {k: set() for k in variants}
        for _ in range(args.runs):
            for name, (argv, extra) in variants.items():
                t = time.time() - age
                os.utime(stamp, (t, t))
                env = {**world.env, **extra}
                start = time.perf_counter()
                proc = subprocess.run(argv, input=stdin, env=env, capture_output=True, cwd=str(world.paths["nogit"]))
                samples[name].append((time.perf_counter() - start) * 1000)
                outputs[name].add((proc.returncode, proc.stdout))
        results["paths"][path_name] = {
            name: {
                "median_ms": statistics.median(v),
                "max_ms": max(v),
                "min_ms": min(v),
                "samples_ms": [round(x, 1) for x in v],
                "outputs": sorted(repr(o) for o in outputs[name]),
            }
            for name, v in samples.items()
        }
        shell = statistics.median(samples["shell"])
        for name in ("launcher_bl", "bl_direct"):
            results["paths"][path_name][name]["speedup_vs_shell"] = shell / statistics.median(samples[name])
    results["load_after"] = load()
    text = json.dumps(results, indent=2)
    if args.json:
        Path(args.json).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
