#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""prepush_judgment_gate.py — BLOCKING Frontier-judgment gate at the push boundary.

Closes learn-gates-01 (Option A/C): the Stop-hook judgment check
(``stop_closeout.py`` → ``judgment_gate``) can only WARN — a Stop hook cannot block
a push or dispatch an agent — so a stakes-gated SELF-MODIFYING run whose Frontier
judgment layer was skipped could still reach origin. This gate makes that bypass
structurally impossible: at pre-push, for a push that touches build-loop's own
enforcement surface (``scripts/``, ``agents/``, ``skills/build-loop/references/``),
it runs ``judgment_gate`` on THIS run and BLOCKS on a ``fail`` verdict.

Safe-by-construction (never wedges legitimate work):
  * Only self-modifying pushes are in scope; ordinary pushes always allow.
  * Enforcement is attributed to THIS run via ``temporal_membership`` — if the latest
    ``runs[]`` entry is not in the current push window, the gate ALLOWS (it refuses to
    judge a push against a stale/foreign run). Prerequisite E3 (per-run stakes read)
    is already fixed in ``judgment_gate``.
  * Fail-OPEN on any internal error; honors ``BUILDLOOP_JUDGMENT_GATE_SKIP=1`` and the
    shared ``BUILDLOOP_PUSH_HOLD_BYPASS=1`` emergency override (logged by the caller).

Contract mirrors the sibling gates:
    evaluate(repo, stdin_lines, env) -> {"action": "allow"|"block"|"bypass", "exit_code", "reason", ...}
    format_block_message(verdict) -> str
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SELF_MOD_PREFIXES = ("scripts/", "agents/", "skills/build-loop/references/")
_ZERO = "0" * 40


def _run(cmd: list[str], cwd: Path, timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 127, ""


def _pushed_files(repo: Path, stdin_lines: list[str]) -> set[str]:
    """Union of files changed across every ref being pushed."""
    files: set[str] = set()
    for line in stdin_lines or []:
        parts = line.split()
        if len(parts) < 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts[:4]
        if not local_sha or set(local_sha) == {"0"}:
            continue  # branch deletion
        if remote_sha and set(remote_sha) != {"0"}:
            rc, out = _run(["git", "diff", "--name-only", f"{remote_sha}..{local_sha}"], repo)
        else:
            # New ref: fall back to the tip commit's files (bounded, self-mod detection only).
            rc, out = _run(["git", "show", "--name-only", "--pretty=format:", local_sha], repo)
        if rc == 0:
            files.update(f for f in out.splitlines() if f.strip())
    return files


def _touches_self_mod(files: set[str]) -> bool:
    return any(f.startswith(_SELF_MOD_PREFIXES) for f in files)


def _judge(repo: Path, env: dict) -> dict | None:
    """Run judgment_gate on the current (in-window) run. None → nothing enforceable."""
    scripts_dir = repo / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import json as _json

    import judgment_gate  # type: ignore
    import temporal_membership as tm  # type: ignore

    state_path = repo / ".build-loop" / "state.json"
    try:
        state = _json.loads(state_path.read_text())
    except Exception:
        return None
    runs = state.get("runs") or []
    if not runs or not isinstance(runs[-1], dict):
        return None
    run = runs[-1]
    # Attribute to THIS push: the latest run's window must be recent (overlap now
    # within tolerance). A stale/foreign run is NOT judged against this push, so an
    # ordinary push is never wedged by an old fail record.
    now = datetime.now(timezone.utc)
    try:
        _start, end = tm.run_window(run)
        # end is None (open window) → treat as current; else require recency.
        if end is not None and (now - end) > timedelta(hours=24):
            return {"skip": True, "reason": "latest run not in push window — not judged"}
    except Exception:
        pass  # window unavailable → do not use it to skip enforcement
    ledger = repo / ".build-loop" / "agent-ledger.jsonl"
    result = judgment_gate.evaluate(state, ledger, run.get("run_id"),
                                    agent_tool_available=True, require_seats=False)
    result["run_id"] = run.get("run_id")
    return result


def evaluate(repo: Path, stdin_lines: list[str] | None = None, env: dict | None = None) -> dict:
    env = env if env is not None else os.environ
    for k in ("BUILDLOOP_JUDGMENT_GATE_SKIP", "BUILDLOOP_PUSH_HOLD_BYPASS"):
        if str(env.get(k, "")).strip().lower() in {"1", "true", "yes", "on"}:
            return {"action": "bypass", "exit_code": 0, "reason": f"judgment gate bypassed ({k})"}
    try:
        files = _pushed_files(repo, stdin_lines or [])
        if not _touches_self_mod(files):
            return {"action": "allow", "exit_code": 0, "reason": "not a self-modifying push"}
        result = _judge(repo, env)
        if result is None:
            return {"action": "allow", "exit_code": 0, "reason": "no run record to judge (fail-open)"}
        if result.get("skip"):
            return {"action": "allow", "exit_code": 0, "reason": result["reason"]}
        if result.get("verdict") == "fail":
            return {
                "action": "block",
                "exit_code": 1,
                "reason": result.get("summary") or "Frontier judgment layer skipped on a stakes-gated self-mod run",
                "run_id": result.get("run_id"),
                "findings": result.get("findings", []),
            }
        return {"action": "allow", "exit_code": 0,
                "reason": f"judgment verdict {result.get('verdict', '?')}"}
    except Exception as exc:  # noqa: BLE001 — never wedge a push on an internal error
        return {"action": "allow", "exit_code": 0, "reason": f"judgment gate internal error — allowing: {exc!r}"}


def format_block_message(verdict: dict) -> str:
    run_id = verdict.get("run_id", "(unknown)")
    reason = verdict.get("reason", "Frontier judgment layer skipped")
    return (
        "\n"
        "===============================================================\n"
        "  BUILD-LOOP JUDGMENT GATE — push BLOCKED\n"
        "===============================================================\n"
        "  This push modifies build-loop's own enforcement surface\n"
        "  (scripts/ | agents/ | skills/build-loop/references/) and the\n"
        "  Frontier (Fable) judgment layer was NOT dispatched for the run:\n"
        f"    run: {run_id}\n"
        f"    {reason}\n"
        "  ---------------------------------------------------------------\n"
        "  Dispatch the independent-auditor (and, on riskSurfaceChange, the\n"
        "  security-reviewer) for this run, record the verdict, then re-push.\n"
        "\n"
        "  EMERGENCY override (logged):\n"
        "    BUILDLOOP_JUDGMENT_GATE_SKIP=1 git push <remote> <branch>\n"
        "===============================================================\n"
    )
