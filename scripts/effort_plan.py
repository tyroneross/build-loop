#!/usr/bin/env python3
"""Measured effort estimates for a build-loop plan.

Phase 2 calls `plan` with the plan's chunks. Each chunk gets a typical and
slow-case estimate from transcript-miner's effort lookup (past jobs timed by
the active minutes their agent session spent). The script then lays the chunks
into dispatch waves from their dependencies and reports:

  - the critical path (the wall-clock floor if each wave runs in parallel)
  - the serial total (what a single agent doing everything would take)
  - coordination notes: low-confidence estimates, lopsided waves, and an
    integration checkpoint after every wave that runs chunks in parallel

`--record` stores a prediction (with the orchestrator's gut estimate, made
BEFORE the lookup) so `close` at Report can score both against elapsed wall-clock minutes.
Predictions and immutable results belong to the exact Build Loop run; the
external effort tool is queried read-only and its prediction ledger is untouched.

Degrades, never blocks: if the effort tool or its store is missing, it prints
`status: unavailable` with the reason and exits 0.

Chunk file (JSON list):
  [{"id": "c1", "platform": "python", "kind": "feature", "lines": 200,
    "depends_on": []}, ...]
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
import os
import subprocess
import sys
import time
from pathlib import Path

EFFORT_SCRIPT = Path(os.environ.get(
    "BUILD_LOOP_EFFORT_SCRIPT",
    str(Path.home() / "dev" / "git-folder" / "transcript-miner" / "scripts" / "effort.py")))
STATE_FILE = Path(".build-loop") / "runs"
LOW_N = 5
LOPSIDED = 3.0  # longest chunk in a wave at least this many times the shortest


def run_effort(args: list[str]) -> dict | None:
    if not EFFORT_SCRIPT.exists():
        return None
    try:
        proc = subprocess.run([sys.executable, str(EFFORT_SCRIPT), *args],
                              capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        result = json.loads(proc.stdout)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def lookup(chunk: dict) -> dict | None:
    args = ["lookup", "--json"]
    for key in ("platform", "kind"):
        if chunk.get(key):
            args += [f"--{key}", str(chunk[key])]
    if chunk.get("lines"):
        args += ["--lines", str(int(chunk["lines"]))]
    return run_effort(args)


def waves(chunks: list[dict]) -> list[list[str]]:
    """Topological levels: wave N holds chunks whose dependencies all sit in
    earlier waves. Raises ValueError on a cycle or an unknown dependency."""
    ids = {c["id"] for c in chunks}
    deps = {c["id"]: set(c.get("depends_on") or []) for c in chunks}
    for cid, d in deps.items():
        unknown = d - ids
        if unknown:
            raise ValueError(f"chunk {cid} depends on unknown {sorted(unknown)}")
    placed: set[str] = set()
    out: list[list[str]] = []
    while len(placed) < len(ids):
        ready = sorted(cid for cid in ids - placed if deps[cid] <= placed)
        if not ready:
            raise ValueError(f"dependency cycle among {sorted(ids - placed)}")
        out.append(ready)
        placed |= set(ready)
    return out


def critical_path(chunks: list[dict], est: dict[str, float]) -> tuple[float, list[str]]:
    """Longest dependency chain by estimated minutes."""
    by_id = {c["id"]: c for c in chunks}
    memo: dict[str, tuple[float, list[str]]] = {}

    def best(cid: str) -> tuple[float, list[str]]:
        if cid not in memo:
            prev = [best(d) for d in by_id[cid].get("depends_on") or []]
            base = max(prev, key=lambda p: p[0]) if prev else (0.0, [])
            memo[cid] = (base[0] + est.get(cid, 0.0), base[1] + [cid])
        return memo[cid]

    return max((best(c["id"]) for c in chunks), key=lambda p: p[0], default=(0.0, []))


def valid_estimate(value):
    return (isinstance(value, dict) and type(value.get("n")) is int and value["n"] > 0
            and isinstance(value.get("scope"), str)
            and all(type(value.get(k)) in (int, float) and math.isfinite(value[k]) and value[k] >= 0
                    for k in ("typical_min", "slow_min")))


def publish_once(path, data):
    """Publish fully written JSON without replacing another writer's result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_plan(chunks: list[dict], lookups: dict[str, dict | None]) -> dict:
    missing = [c["id"] for c in chunks if not valid_estimate(lookups.get(c["id"]))]
    if missing:
        return {"status": "unavailable", "reason": "missing measured estimates: " + ", ".join(missing), "waves": waves(chunks)}
    typ = {cid: (r or {}).get("typical_min", 0.0) for cid, r in lookups.items()}
    slow = {cid: (r or {}).get("slow_min", 0.0) for cid, r in lookups.items()}
    layered = waves(chunks)
    crit_min, crit_ids = critical_path(chunks, typ)
    crit_slow, _ = critical_path(chunks, slow)
    notes: list[str] = []
    for cid, r in lookups.items():
        if not r or not r.get("n"):
            notes.append(f"{cid}: no past jobs match; estimate unknown, size it by hand")
        elif r["n"] < LOW_N or not r["scope"].startswith("platform"):
            notes.append(f"{cid}: low confidence (n={r['n']}, matched on {r['scope']})")
    for i, wave in enumerate(layered, 1):
        if len(wave) < 2:
            continue
        times = sorted((typ[c], c) for c in wave if typ[c])
        if len(times) >= 2 and times[0][0] and times[-1][0] / times[0][0] >= LOPSIDED:
            notes.append(f"wave {i}: {times[-1][1]} (~{times[-1][0]} min) is "
                         f"{times[-1][0] / times[0][0]:.0f}x {times[0][1]}; dispatch it first "
                         "or split it so the wave finishes together")
        notes.append(f"wave {i}: {len(wave)} chunks run in parallel; integrate and run "
                     "tests before wave " + str(i + 1) if i < len(layered)
                     else f"wave {i}: {len(wave)} chunks run in parallel; integrate and run tests before Review")
    return {
        "status": "ok",
        "chunks": [{"id": c["id"], "typical_min": typ[c["id"]], "slow_min": slow[c["id"]],
                    "n": (lookups[c["id"]] or {}).get("n", 0),
                    "scope": (lookups[c["id"]] or {}).get("scope")} for c in chunks],
        "waves": layered,
        "critical_path": {"ids": crit_ids, "typical_min": round(crit_min, 1),
                          "slow_min": round(crit_slow, 1)},
        "serial_total": {"typical_min": round(sum(typ.values()), 1),
                         "slow_min": round(sum(slow.values()), 1)},
        "notes": notes,
    }


def render(plan: dict) -> str:
    if plan["status"] != "ok":
        return f"status: {plan['status']} — {plan.get('reason', '')}"
    cp, st = plan["critical_path"], plan["serial_total"]
    lines = [f"critical path {cp['typical_min']} min (slow {cp['slow_min']}): "
             f"{' -> '.join(cp['ids'])}",
             f"serial total {st['typical_min']} min (slow {st['slow_min']})"]
    for i, w in enumerate(plan["waves"], 1):
        lines.append(f"wave {i}: {', '.join(w)}")
    for c in plan["chunks"]:
        lines.append(f"  {c['id']}: {c['typical_min']} min (slow {c['slow_min']}, n={c['n']})")
    lines += [f"note: {n}" for n in plan["notes"]]
    return "\n".join(lines)


def _cmd_plan(args) -> int:
    chunks = json.loads(Path(args.chunks).read_text())
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("chunks must be a nonempty list")
    ids = []
    for chunk in chunks:
        if not isinstance(chunk, dict) or not isinstance(chunk.get("id"), str) or not chunk["id"]:
            raise ValueError("each chunk requires a nonempty id")
        ids.append(chunk["id"])
        if "lines" in chunk and (type(chunk["lines"]) is not int or chunk["lines"] < 0):
            raise ValueError("chunk lines must be a nonnegative integer")
        deps = chunk.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ValueError("depends_on must be a list of ids")
    if len(set(ids)) != len(ids):
        raise ValueError("chunk ids must be unique")
    waves(chunks)
    if not EFFORT_SCRIPT.exists():
        plan = {"status": "unavailable", "reason": f"effort tool not found at {EFFORT_SCRIPT}"}
    else:
        lookups = {c["id"]: lookup(c) for c in chunks}
        if all(not valid_estimate(r) for r in lookups.values()):
            plan = {"status": "unavailable",
                    "reason": "effort lookup failed; run `effort.py index` to build the store"}
        else:
            try:
                plan = build_plan(chunks, lookups)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
    if plan["status"] == "ok" and args.record:
        if args.gut is None:
            print("error: --record needs --gut (your estimate made BEFORE this lookup)",
                  file=sys.stderr)
            return 2
        if not args.run_id or Path(args.run_id).name != args.run_id or args.run_id in (".", ".."):
            print("error: --record needs a single-component --run-id", file=sys.stderr)
            return 2
        state = Path(args.workdir) / STATE_FILE / args.run_id / "effort.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        try:
            publish_once(state, {"run_id": args.run_id, "recorded_at": time.time(),
                                 "gut_min": args.gut, "plan": plan})
        except FileExistsError:
            print("warning: keeping this run's first prediction", file=sys.stderr)
    print(json.dumps(plan, indent=2) if args.json else render(plan))
    return 0


def _cmd_close(args) -> int:
    if not args.run_id or Path(args.run_id).name != args.run_id or args.run_id in (".", ".."):
        print("status: nothing to close (no valid --run-id)")
        return 0
    state = Path(args.workdir) / STATE_FILE / args.run_id / "effort.json"
    if not state.exists():
        print("status: nothing to close (no recorded prediction for this run)")
        return 0
    receipt = state.with_name("effort-result.json")
    if receipt.exists():
        print(json.dumps(json.loads(receipt.read_text())))
        return 0
    data = json.loads(state.read_text())
    actual = max(0.0, (time.time() - data["recorded_at"]) / 60)
    lookup_min = data["plan"]["critical_path"]["typical_min"]
    result = {"run_id": args.run_id, "actual_min": actual, "gut_min": data["gut_min"],
              "lookup_min": lookup_min, "estimate_kind": "critical_path",
              "gut_ratio": data["gut_min"] / actual if actual else None,
              "lookup_ratio": lookup_min / actual if actual else None}
    try:
        publish_once(receipt, result)
    except FileExistsError:
        result = json.loads(receipt.read_text())
    print(json.dumps(result))
    return 0


def _degrade(fn, args):
    try:
        return fn(args)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "unavailable", "reason": str(exc)}))
        return 0


def cmd_plan(args):
    return _degrade(_cmd_plan, args)


def cmd_close(args):
    return _degrade(_cmd_close, args)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="estimate chunks, waves, critical path")
    p.add_argument("--chunks", required=True, help="JSON list of chunks")
    p.add_argument("--workdir", default=".")
    p.add_argument("--run-id", help="exact Build Loop run identity")
    p.add_argument("--record", action="store_true", help="store a prediction to score at Report")
    p.add_argument("--gut", type=float, help="orchestrator's estimate in minutes, made first")
    p.add_argument("--job", help="one-line description for the ledger")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("close", help="score the recorded prediction against the actual")
    p.add_argument("--workdir", default=".")
    p.add_argument("--run-id", help="exact Build Loop run identity")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_close)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
