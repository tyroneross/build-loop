# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Wall-clock timing of the tool-trace hook: Python vs ``bl``, each through
the command the host runs.

``python``: the former inline hooks.json command (state.json test, then
``python3 scripts/tool_trace.py``). ``hooks_json_bl``: the registered command
(the guarded hooks.json command with a fresh ``bl``). ``bl_direct``: the binary
alone. Runs are interleaved so load drifts hit every variant alike; each run
appends one line to the same project, and the script asserts that each run
wrote exactly one line.

    python3 -m tests.hook_parity.trace_timing --runs 30 [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from tests.hook_parity import trace_harness as h
from tests.hook_parity.test_trace_parity import INLINE, _plugin, _registered, claude


def load() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp()).resolve()
    world = h.build_world(tmp / "w", h.Case("timing"))
    plugin = _plugin(tmp, "real")
    env = {**world.env, "CLAUDE_PLUGIN_ROOT": str(plugin)}
    stdin = claude("PostToolUse", tool_response={"stdout": "ok\n", "stderr": ""})
    variants = {
        "python": ["/bin/sh", "-c", INLINE.replace("{phase}", "end")],
        "hooks_json_bl": ["/bin/sh", "-c", _registered("end")],
        "bl_direct": [str(plugin / "bin" / "native" / "bl"), "hook", "trace",
                      "--workdir", world.workdir_arg, "--phase", "end"],
    }
    trace = world.proj / h.TRACE
    samples: dict[str, list[float]] = {k: [] for k in variants}
    results: dict = {"load_before": load(), "runs": args.runs}
    for _ in range(args.runs):
        for name, argv in variants.items():
            lines = trace.read_bytes().count(b"\n") if trace.exists() else 0
            start = time.perf_counter()
            proc = subprocess.run(argv, input=stdin, env=env, capture_output=True, cwd=str(world.cwd))
            samples[name].append((time.perf_counter() - start) * 1000)
            assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b""), (name, proc)
            assert trace.read_bytes().count(b"\n") == lines + 1, name
    results["load_after"] = load()
    results["variants"] = {
        name: {"median_ms": round(statistics.median(v), 1), "max_ms": round(max(v), 1),
               "min_ms": round(min(v), 1), "samples_ms": [round(x, 1) for x in v]}
        for name, v in samples.items()
    }
    py = statistics.median(samples["python"])
    for name in ("hooks_json_bl", "bl_direct"):
        results["variants"][name]["speedup_vs_python"] = round(py / statistics.median(samples[name]), 1)
    text = json.dumps(results, indent=2)
    if args.json:
        Path(args.json).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
