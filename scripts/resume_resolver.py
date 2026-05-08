#!/usr/bin/env python3
"""M3 — Resume resolver for /build-loop:run --resume.

Validates an existing .build-loop/state.json.execution block, computes the
remaining work list (queued + in-flight that didn't return), and runs the
concurrent-modification check on already-completed chunks.

Inputs (CLI):
  --workdir            project root (contains .build-loop/)
  --resume-arg         literal run_id, or 'latest', or '' (no --resume present)
  --staleness-minutes  threshold for the heartbeat-staleness path (default 5)

Output (stdout, JSON):
  {
    "decision": "resume" | "fresh" | "prompt_user" | "abort",
    "reason": "<human-readable>",
    "run_id": "<resolved-or-null>",
    "remaining_chunks": [{chunk_id, files, prior_status_if_any}],
    "iterate_attempt": <int>,
    "concurrent_modifications": [{chunk_id, files}],
    "execution_block": {<copy>},
    "envelopes": {chunk_id: [<envelope>, ...]}
  }

Exit codes: 0 success / 1 validation error (incompatible schema, no run, etc.)
            2 filesystem error
Zero deps. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EXPECTED_SCHEMA_VERSION = 1


def _load_state(workdir: Path) -> dict | None:
    p = workdir / ".build-loop" / "state.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_envelopes(workdir: Path, run_id: str) -> dict[str, list[dict]]:
    base = workdir / ".build-loop" / "subagent-results" / run_id
    out: dict[str, list[dict]] = {}
    if not base.exists():
        return out
    for fp in sorted(base.iterdir()):
        if not fp.name.endswith(".json"):
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        cid = data.get("chunk_id")
        if not cid:
            continue
        out.setdefault(cid, []).append(data)
    for cid in out:
        out[cid].sort(key=lambda e: e.get("attempt", 0))
    return out


def _parse_iso(s: str) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _resolve_latest(state: dict, now: datetime, staleness_minutes: int) -> tuple[str | None, str]:
    """Return (run_id, reason) for --resume latest. None if no resumable run."""
    execution = state.get("execution")
    if not isinstance(execution, dict):
        return None, "no execution block in state.json"
    if execution.get("phase") == "report":
        return None, "last run completed cleanly (phase=report)"
    last = _parse_iso(execution.get("last_heartbeat_at", ""))
    if last is None:
        return None, "execution.last_heartbeat_at is missing or unparseable"
    age = now - last
    if age < timedelta(minutes=staleness_minutes):
        return None, f"latest run heartbeat is fresh ({age.total_seconds():.0f}s old; threshold {staleness_minutes*60}s)"
    return execution.get("run_id"), f"latest run heartbeat is {age.total_seconds():.0f}s old"


def _files_for_chunk(execution: dict, chunk_id: str) -> list[str]:
    return list(execution.get("file_ownership", {}).get(chunk_id, []))


def _git_unstaged_files(workdir: Path) -> set[str]:
    """Files with unstaged or untracked modifications, relative to workdir."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(workdir), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    files: set[str] = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        # porcelain: "XY filename" — XY is two-char status; rest is path
        path = line[3:].strip()
        # rename has " -> " separator
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        # strip surrounding quotes git may add
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        files.add(path)
    return files


def _file_mtime(workdir: Path, rel: str) -> datetime | None:
    p = workdir / rel
    if not p.exists():
        return None
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _detect_concurrent_modifications(
    workdir: Path,
    execution: dict,
) -> list[dict]:
    """Walk completed_chunks; flag any whose owned files have changed since completed_at.

    Two signals (either triggers):
      1. file appears in `git status --porcelain` (unstaged/untracked)
      2. file mtime > completed_at
    """
    git_dirty = _git_unstaged_files(workdir)
    flagged: list[dict] = []
    for entry in execution.get("completed_chunks", []):
        cid = entry.get("chunk_id")
        if not cid or entry.get("status") != "fixed":
            continue
        completed_at = _parse_iso(entry.get("completed_at", ""))
        owned = _files_for_chunk(execution, cid)
        modified: list[str] = []
        for rel in owned:
            if rel in git_dirty:
                modified.append(rel)
                continue
            if completed_at is not None:
                mtime = _file_mtime(workdir, rel)
                if mtime is not None and mtime > completed_at + timedelta(seconds=2):
                    modified.append(rel)
        if modified:
            flagged.append({"chunk_id": cid, "files": modified})
    return flagged


def _compute_remaining(
    execution: dict,
    envelopes: dict[str, list[dict]],
    concurrent_mods: list[dict],
) -> list[dict]:
    """remaining = queued + in_flight (no envelope or non-fixed envelope) + concurrent-mod-demoted."""
    flagged_ids = {m["chunk_id"] for m in concurrent_mods}
    remaining: list[dict] = []

    for cid in execution.get("queued_chunks", []):
        remaining.append({
            "chunk_id": cid,
            "files": _files_for_chunk(execution, cid),
            "prior_status": None,
            "reason": "queued",
        })

    for cid in execution.get("in_flight_chunks", []):
        envs = envelopes.get(cid, [])
        latest_status = envs[-1].get("status") if envs else None
        if latest_status == "fixed":
            continue
        remaining.append({
            "chunk_id": cid,
            "files": _files_for_chunk(execution, cid),
            "prior_status": latest_status,
            "reason": "in_flight_no_clean_return",
        })

    for entry in execution.get("completed_chunks", []):
        cid = entry.get("chunk_id")
        if cid in flagged_ids:
            remaining.append({
                "chunk_id": cid,
                "files": _files_for_chunk(execution, cid),
                "prior_status": "concurrent_modification_detected",
                "reason": "completed_then_hand_modified",
            })
    return remaining


def resolve(
    workdir: Path,
    resume_arg: str,
    *,
    staleness_minutes: int = 5,
    now: datetime | None = None,
) -> dict:
    """Top-level resolver. Returns the decision envelope (see module docstring)."""
    now = now or datetime.now(timezone.utc)
    state = _load_state(workdir)
    if state is None:
        return {
            "decision": "fresh" if not resume_arg else "abort",
            "reason": "no .build-loop/state.json present",
            "run_id": None,
            "remaining_chunks": [],
            "iterate_attempt": 0,
            "concurrent_modifications": [],
            "execution_block": None,
            "envelopes": {},
        }

    execution = state.get("execution") if isinstance(state, dict) else None

    # No --resume: surface heartbeat staleness check (M4 primary signal).
    if not resume_arg:
        if not isinstance(execution, dict) or execution.get("phase") == "report":
            return {
                "decision": "fresh", "reason": "no incomplete run", "run_id": None,
                "remaining_chunks": [], "iterate_attempt": 0,
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
            }
        last = _parse_iso(execution.get("last_heartbeat_at", ""))
        if last is None or (now - last) < timedelta(minutes=staleness_minutes):
            return {
                "decision": "fresh", "reason": "no stale heartbeat detected", "run_id": None,
                "remaining_chunks": [], "iterate_attempt": 0,
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
            }
        return {
            "decision": "prompt_user",
            "reason": f"incomplete build detected (run_id={execution.get('run_id')}, "
                      f"last heartbeat {(now - last).total_seconds()/60:.1f} min ago); "
                      f"resume with --resume {execution.get('run_id')} or start fresh",
            "run_id": execution.get("run_id"),
            "remaining_chunks": [],  # caller re-runs us with the literal run_id to compute
            "iterate_attempt": int(execution.get("iterate_attempt", 0)),
            "concurrent_modifications": [],
            "execution_block": execution,
            "envelopes": {},
        }

    # --resume present.
    if resume_arg == "latest":
        run_id, reason = _resolve_latest(state, now, staleness_minutes)
        if run_id is None:
            return {
                "decision": "abort", "reason": f"--resume latest: {reason}", "run_id": None,
                "remaining_chunks": [], "iterate_attempt": 0,
                "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
            }
        resume_arg = run_id

    if not isinstance(execution, dict):
        return {
            "decision": "abort", "reason": "no execution block to resume from",
            "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": None, "envelopes": {},
        }
    if execution.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        return {
            "decision": "abort",
            "reason": f"incompatible schema_version {execution.get('schema_version')!r} "
                      f"(expected {EXPECTED_SCHEMA_VERSION}); upgrade build-loop or start fresh",
            "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
        }
    if execution.get("run_id") != resume_arg:
        return {
            "decision": "abort",
            "reason": f"--resume {resume_arg!r} does not match active run_id {execution.get('run_id')!r}",
            "run_id": None, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
        }
    if execution.get("phase") == "report":
        return {
            "decision": "abort", "reason": "run is already complete (phase=report); nothing to resume",
            "run_id": resume_arg, "remaining_chunks": [], "iterate_attempt": 0,
            "concurrent_modifications": [], "execution_block": execution, "envelopes": {},
        }

    envelopes = _load_envelopes(workdir, resume_arg)
    concurrent_mods = _detect_concurrent_modifications(workdir, execution)
    remaining = _compute_remaining(execution, envelopes, concurrent_mods)
    return {
        "decision": "resume",
        "reason": f"resuming {resume_arg} at phase={execution.get('phase')} "
                  f"with {len(remaining)} chunk(s) remaining",
        "run_id": resume_arg,
        "remaining_chunks": remaining,
        "iterate_attempt": int(execution.get("iterate_attempt", 0)),
        "concurrent_modifications": concurrent_mods,
        "execution_block": execution,
        "envelopes": envelopes,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Resume resolver for /build-loop:run --resume (M3).")
    p.add_argument("--workdir", required=True)
    p.add_argument("--resume-arg", default="", help="Literal run_id, 'latest', or '' for no-resume staleness check")
    p.add_argument("--staleness-minutes", type=int, default=5)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        env = resolve(Path(args.workdir).resolve(), args.resume_arg, staleness_minutes=args.staleness_minutes)
    except OSError as e:
        print(f"filesystem error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(env, indent=2))
    return 0 if env["decision"] in {"resume", "fresh", "prompt_user"} else 1


if __name__ == "__main__":
    sys.exit(main())
