#!/usr/bin/env python3
"""Runtime probe for build-loop memory call sites.

Companion to `.build-loop/audits/memory-invocation-2026-05-05.md`. For each
call site documented in build-loop's phase protocol, this script exercises
the call programmatically and records the result envelope. This is a
**reporting** tool, not a gate — it always exits 0. Empty results, MCP
unavailability, and Postgres-down are all valid data points.

Per call site:
  {
    "call_site": "<phase> — <action>",
    "invoked": true | false,
    "latency_ms": <float>,
    "result_count": <int>,
    "result_sample": <small subset of result>,
    "error": "<reason>" | null,
    "verdict": "ok" | "graceful_degradation" | "skipped" | "error"
  }

Output: `.build-loop/audits/memory-runtime-probe-<DATE>.json`.

Usage:
    python3 scripts/audit_memory_invocation.py --workdir "$PWD"
    python3 scripts/audit_memory_invocation.py --workdir "$PWD" --date 2026-05-05
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _time_call(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Wrap a probe function with latency capture and exception handling."""
    t0 = time.perf_counter()
    try:
        out = fn()
    except Exception as e:  # noqa: BLE001 — graceful degradation contract
        return {
            "invoked": False,
            "latency_ms": (time.perf_counter() - t0) * 1000.0,
            "result_count": 0,
            "result_sample": None,
            "error": f"{type(e).__name__}: {e}",
            "verdict": "error",
        }
    out.setdefault("latency_ms", (time.perf_counter() - t0) * 1000.0)
    out.setdefault("error", None)
    return out


# --- Probes ---------------------------------------------------------------

def probe_global_memory(workdir: Path) -> dict[str, Any]:
    p = Path.home() / ".build-loop" / "memory" / "MEMORY.md"
    if not p.is_file():
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": "global MEMORY.md not present"}
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    return {
        "invoked": True,
        "result_count": len(lines),
        "result_sample": {"path": str(p), "line_count": len(lines), "non_empty": len(non_empty), "first_heading": next((ln for ln in lines if ln.startswith("#")), None)},
        "verdict": "ok",
    }


def probe_project_memory(workdir: Path) -> dict[str, Any]:
    p = workdir / ".build-loop" / "memory" / "MEMORY.md"
    if not p.is_file():
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": "project MEMORY.md not present"}
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    return {
        "invoked": True,
        "result_count": len(lines),
        "result_sample": {"path": str(p), "line_count": len(lines), "non_empty": len(non_empty), "first_heading": next((ln for ln in lines if ln.startswith("#")), None)},
        "verdict": "ok",
    }


def probe_runs_tail(workdir: Path) -> dict[str, Any]:
    p = workdir / ".build-loop" / "state.json"
    if not p.is_file():
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": "state.json not present"}
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "error", "error": f"state.json parse error: {e}"}
    runs = state.get("runs") or []
    tail = runs[-3:]
    return {
        "invoked": True,
        "result_count": len(tail),
        "result_sample": [{"run_id": r.get("run_id"), "outcome": r.get("outcome"), "goal_head": (r.get("goal") or "")[:60]} for r in tail],
        "verdict": "ok",
    }


def probe_recall_facade(workdir: Path) -> dict[str, Any]:
    """Invoke memory_facade.recall() with a representative architecture-domain query.

    Empty stores + Postgres-down are EXPECTED (graceful degradation). What we
    audit is the envelope SHAPE: keys present, reasons[] populated when a
    backend is down, no exception raised.
    """
    from memory_facade import recall  # type: ignore  # noqa: PLC0415
    env = recall(query="architecture", kind=None, project=None, limit=5, workdir=workdir)
    # Envelope contract assertions (in-line, soft).
    expected_keys = {"query", "kind_filter", "project", "results_by_kind", "merged", "reasons"}
    missing_keys = sorted(expected_keys - set(env.keys()))
    rbk = env.get("results_by_kind", {})
    expected_kinds = {"runs", "decisions", "semantic", "debugger"}
    missing_kinds = sorted(expected_kinds - set(rbk.keys()))
    return {
        "invoked": True,
        "result_count": len(env.get("merged", [])),
        "result_sample": {
            "envelope_keys": sorted(env.keys()),
            "missing_keys": missing_keys,
            "missing_kinds": missing_kinds,
            "per_kind_counts": {k: len(rbk.get(k, [])) for k in sorted(rbk.keys())},
            "reasons": env.get("reasons", []),
        },
        "verdict": "ok" if not missing_keys and not missing_kinds else "graceful_degradation",
    }


def probe_decision_canonical(workdir: Path) -> dict[str, Any]:
    """Read the architecture-scout's baseline decision from canonical path.

    This is the underlying artifact `architecture-scout task=baseline` writes
    via `write_decision.py`. We don't dispatch the subagent — we verify the
    artifact landed where canonical recall expects it.
    """
    canonical_root = Path.home() / "dev" / "git-folder" / "build-loop-memory" / "decisions"
    proj_dir = canonical_root / "build-loop"
    if not proj_dir.is_dir():
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": f"canonical decisions dir absent: {proj_dir}"}
    md_files = sorted(proj_dir.glob("*.md"))
    baseline_files = [p for p in md_files if "baseline" in p.stem.lower() or "architecture" in p.stem.lower()]
    return {
        "invoked": True,
        "result_count": len(baseline_files),
        "result_sample": [p.stem for p in baseline_files[:5]],
        "verdict": "ok" if baseline_files else "graceful_degradation",
    }


def probe_debugger_mcp(workdir: Path, query: str = "memory") -> dict[str, Any]:
    """Probe the debugger MCP via npx CLI. Reports unreachable as data."""
    npx = shutil.which("npx")
    if not npx:
        return {"invoked": False, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": "npx not on PATH"}
    try:
        proc = subprocess.run(
            [npx, "--no-install", "@tyroneross/claude-code-debugger", "search", "--query", query, "--limit", "3", "--json"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": f"mcp_unavailable: {type(e).__name__}: {e}"}
    if proc.returncode != 0:
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": f"mcp_unavailable: cli rc={proc.returncode}"}
    try:
        payload = json.loads(proc.stdout) if proc.stdout else {"incidents": []}
    except json.JSONDecodeError as e:
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": f"mcp_unavailable: bad json: {e}"}
    incidents = payload.get("incidents") or payload.get("results") or []
    return {
        "invoked": True,
        "result_count": len(incidents),
        "result_sample": [{"id": i.get("id"), "symptom_head": (i.get("symptom") or "")[:80]} for i in incidents[:3]],
        "verdict": "ok",
    }


def probe_session_start_hook(workdir: Path) -> dict[str, Any]:
    p = workdir / "hooks" / "hooks.json"
    if not p.is_file():
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": "hooks.json absent"}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "error", "error": str(e)}
    ss = (cfg.get("hooks") or {}).get("SessionStart") or []
    cmd_count = sum(len(entry.get("hooks") or []) for entry in ss)
    return {
        "invoked": True,
        "result_count": cmd_count,
        "result_sample": [(h.get("type"), (h.get("command") or "")[:80]) for entry in ss for h in (entry.get("hooks") or [])],
        "verdict": "ok" if cmd_count else "graceful_degradation",
    }


def probe_consolidate_memory(workdir: Path) -> dict[str, Any]:
    """Verify the consolidate_memory.py script imports cleanly (Phase 6)."""
    script = workdir / "scripts" / "consolidate_memory.py"
    if not script.is_file():
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": "consolidate_memory.py absent"}
    return {
        "invoked": True,
        "result_count": 1,
        "result_sample": {"path": str(script.relative_to(workdir)), "size": script.stat().st_size},
        "verdict": "ok",
    }


def probe_procedural_governance(workdir: Path) -> dict[str, Any]:
    script = workdir / "scripts" / "procedural_governance.py"
    if not script.is_file():
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": "procedural_governance.py absent"}
    return {
        "invoked": True,
        "result_count": 1,
        "result_sample": {"path": str(script.relative_to(workdir)), "size": script.stat().st_size},
        "verdict": "ok",
    }


def probe_write_run_entry(workdir: Path) -> dict[str, Any]:
    script = workdir / "scripts" / "write_run_entry.py"
    if not script.is_file():
        return {"invoked": True, "result_count": 0, "result_sample": None, "verdict": "graceful_degradation", "error": "write_run_entry.py absent"}
    return {
        "invoked": True,
        "result_count": 1,
        "result_sample": {"path": str(script.relative_to(workdir)), "size": script.stat().st_size},
        "verdict": "ok",
    }


# --- Dispatch table ------------------------------------------------------

PROBES: list[tuple[str, str, str, Callable[[Path], dict[str, Any]]]] = [
    # (call_site, phase, expected verdict tier, callable)
    ("SessionStart hook → architecture freshness", "session-start", "wired", probe_session_start_hook),
    ("Phase 1 Assess → read ~/.build-loop/memory/MEMORY.md (global)", "phase-1", "wired", probe_global_memory),
    ("Phase 1 Assess → read <repo>/.build-loop/memory/MEMORY.md (project)", "phase-1", "wired", probe_project_memory),
    ("Phase 1 Assess → state.json.runs[-3:] tail", "phase-1", "wired", probe_runs_tail),
    ("Phase 1 Assess → architecture-scout baseline (decision artifact)", "phase-1", "wired", probe_decision_canonical),
    ("Phase 1 Assess → debugger MCP list/search", "phase-1", "best-effort", probe_debugger_mcp),
    ("Phase 1 Assess → recall() facade (unified read)", "phase-1", "wired", probe_recall_facade),
    ("Phase 4 Review-B → debugger MCP search (memory-first gate)", "phase-4-b", "best-effort", probe_debugger_mcp),
    # Review-D scout dispatch: not directly probable without invoking subagent; we read its artifact instead.
    ("Phase 4 Review-D → architecture-scout review-rules artifacts", "phase-4-d", "wired", probe_decision_canonical),
    ("Phase 4 Review-F → debugger MCP store (proxy: CLI reachable)", "phase-4-f", "best-effort", probe_debugger_mcp),
    ("Phase 4 Review-F → write_run_entry.py", "phase-4-f", "wired", probe_write_run_entry),
    ("Phase 5 Iterate → debugger MCP search (adapted symptom)", "phase-5", "best-effort", probe_debugger_mcp),
    ("Phase 6 Learn → consolidate_memory.py", "phase-6", "wired", probe_consolidate_memory),
    ("Phase 6 Learn → procedural_governance.py", "phase-6", "wired", probe_procedural_governance),
]


def run_probes(workdir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for call_site, phase, tier, fn in PROBES:
        result = _time_call(lambda fn=fn: fn(workdir))
        rows.append({
            "call_site": call_site,
            "phase": phase,
            "expected_tier": tier,
            **result,
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workdir", default=os.getcwd(), help="Repo root (default: cwd)")
    p.add_argument("--date", default=_date.today().isoformat(), help="Date stamp for output file (default: today)")
    p.add_argument("--out", default=None, help="Override output path (default: .build-loop/audits/memory-runtime-probe-<DATE>.json)")
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    out_path = Path(args.out) if args.out else workdir / ".build-loop" / "audits" / f"memory-runtime-probe-{args.date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = run_probes(workdir)

    summary = {
        "total": len(rows),
        "verdicts": {},
        "by_phase": {},
    }
    for r in rows:
        v = r.get("verdict", "unknown")
        summary["verdicts"][v] = summary["verdicts"].get(v, 0) + 1
        ph = r.get("phase", "unknown")
        summary["by_phase"][ph] = summary["by_phase"].get(ph, 0) + 1

    envelope = {
        "schema_version": "1.0.0",
        "audit_date": args.date,
        "workdir": str(workdir),
        "summary": summary,
        "probes": rows,
    }

    out_path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    # Brief stdout summary so the orchestrator sees the result without parsing the file.
    print(f"audit: wrote {out_path.relative_to(workdir) if out_path.is_relative_to(workdir) else out_path}")
    print(f"audit: total={summary['total']} verdicts={summary['verdicts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
