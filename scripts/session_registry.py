#!/usr/bin/env python3
"""Track active build-loop sessions across processes / terminals / coding hosts.

Multiple build-loop sessions can run concurrently in different terminals and
hosts (Claude Code, Codex, Gemini CLI, etc.) and may target the same project.
Without coordination, two sessions modifying the same files race to commit
and clobber each other's work. This registry is the cross-session presence
signal that lets each session see the others and surface collisions BEFORE
the race lands in `.git/`.

Storage:
  ~/.build-loop/sessions/<run_id>.json        — active session presence
  ~/.build-loop/sessions/dead/<run_id>.json   — post-completion or stale

Each presence file:
  {
    "run_id": "run_<UTC>_<hash>",
    "host": "claude_code" | "codex" | "gemini" | "other",
    "workdir": "/abs/path/to/project",
    "workdir_git_remote": "<remote url>" | null,
    "pid": <int>,
    "phase": "assess" | "plan" | "execute" | "review" | "iterate" | "report",
    "started_at": "ISO8601 UTC",
    "last_heartbeat_at": "ISO8601 UTC",
    "files_owned": ["<rel>", ...],   # populated mid-Phase-3 by orchestrator
    "high_frequency_mode": false      # auto-set when collision risk == HIGH
  }

Collision tiers (returned by `check_collision()`):
  LOW       — different workdir
  MEDIUM    — same workdir, different phases (e.g. one assessing, one executing)
  HIGH      — same workdir AND both in execute/iterate
  CRITICAL  — same workdir AND files_owned overlap during concurrent execute

Behavior contract (callers — typically agents/build-orchestrator.md):
  Interactive host (Claude Code with AskUserQuestion available):
    LOW/MEDIUM  → log + proceed
    HIGH        → AskUserQuestion proceed/abort/queue
    CRITICAL    → hard-stop with surfaced message
  Headless host (Codex, cron):
    LOW         → log + proceed
    MEDIUM      → log + proceed
    HIGH        → log + proceed + set high_frequency_mode=True (caller bumps
                  heartbeat cadence to every 30s vs 5min default)
    CRITICAL    → write SAFE-STOP-collision-<peer>.md sentinel to
                  <workdir>/.build-loop/ and exit non-zero

Stale sweep:
  Files with last_heartbeat_at older than --staleness-minutes (default 5) are
  moved to dead/ on every register/scan call. Caps active dir size.

Atomicity:
  Each presence file is owned by one process. Atomic write via tmpfile +
  os.replace. No locking needed — the file IS the lock.

Stdlib only. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SESSIONS_DIR = Path.home() / ".build-loop" / "sessions"
DEFAULT_STALENESS_MINUTES = 5

VALID_HOSTS = frozenset({"claude_code", "codex", "gemini", "other"})
VALID_PHASES = frozenset({
    "assess", "plan", "execute", "review", "iterate", "report",
})
EXECUTE_PHASES = frozenset({"execute", "iterate"})

COLLISION_LOW = "LOW"
COLLISION_MEDIUM = "MEDIUM"
COLLISION_HIGH = "HIGH"
COLLISION_CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def iso_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(stamp: str) -> datetime | None:
    """Parse 'YYYY-MM-DDTHH:MM:SSZ' or with offset. Return None on failure."""
    try:
        # Replace trailing 'Z' for fromisoformat compatibility in 3.11.
        normalized = stamp.replace("Z", "+00:00") if stamp.endswith("Z") else stamp
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _ensure_dirs(sessions_dir: Path) -> None:
    (sessions_dir / "dead").mkdir(parents=True, exist_ok=True)


def _presence_path(sessions_dir: Path, run_id: str) -> Path:
    return sessions_dir / f"{_safe_id(run_id)}.json"


def _safe_id(run_id: str) -> str:
    """Defensive: strip path separators and weird chars. The run_id format
    is already constrained (`run_<UTC>_<hex>`) but we never trust input."""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", run_id)[:128] or "unknown"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _detect_git_remote(workdir: Path) -> str | None:
    """Best-effort `git remote get-url origin`. None on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# ---------------------------------------------------------------------------
# Stale sweep
# ---------------------------------------------------------------------------


def _is_stale(payload: dict, staleness_minutes: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    heartbeat = parse_iso(payload.get("last_heartbeat_at", ""))
    if heartbeat is None:
        return True  # no heartbeat = stale by definition
    age_seconds = (now - heartbeat).total_seconds()
    return age_seconds > staleness_minutes * 60


def sweep_stale(
    sessions_dir: Path,
    staleness_minutes: int = DEFAULT_STALENESS_MINUTES,
    now: datetime | None = None,
) -> list[str]:
    """Move stale presence files to dead/. Return list of moved run_ids."""
    _ensure_dirs(sessions_dir)
    moved: list[str] = []
    for path in sessions_dir.glob("*.json"):
        if path.parent.name == "dead":
            continue
        payload = _read_json(path)
        if payload is None:
            continue
        if _is_stale(payload, staleness_minutes, now):
            dead_path = sessions_dir / "dead" / path.name
            try:
                shutil.move(str(path), str(dead_path))
                moved.append(payload.get("run_id", path.stem))
            except OSError:
                pass
    return moved


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def register(
    sessions_dir: Path,
    run_id: str,
    host: str,
    workdir: str,
    pid: int,
    phase: str = "assess",
    files_owned: list[str] | None = None,
    high_frequency_mode: bool = False,
) -> Path:
    """Write a presence file. Returns the path. Idempotent: re-registering
    the same run_id overwrites the previous file (same atomic-write path)."""
    _ensure_dirs(sessions_dir)
    if host not in VALID_HOSTS:
        raise ValueError(f"host must be one of {sorted(VALID_HOSTS)}; got {host!r}")
    if phase not in VALID_PHASES:
        raise ValueError(f"phase must be one of {sorted(VALID_PHASES)}; got {phase!r}")
    workdir_abs = str(Path(workdir).resolve())
    payload = {
        "run_id": run_id,
        "host": host,
        "workdir": workdir_abs,
        "workdir_git_remote": _detect_git_remote(Path(workdir_abs)),
        "pid": int(pid),
        "phase": phase,
        "started_at": iso_utc(),
        "last_heartbeat_at": iso_utc(),
        "files_owned": list(files_owned or []),
        "high_frequency_mode": bool(high_frequency_mode),
    }
    path = _presence_path(sessions_dir, run_id)
    _atomic_write_json(path, payload)
    sweep_stale(sessions_dir)  # opportunistic
    return path


def heartbeat(
    sessions_dir: Path,
    run_id: str,
    phase: str | None = None,
    files_owned: list[str] | None = None,
    high_frequency_mode: bool | None = None,
) -> bool:
    """Refresh last_heartbeat_at. Optionally update phase, files_owned, or
    high_frequency_mode. Returns True if the file existed and was updated;
    False if no presence file existed for run_id (caller should re-register).
    """
    path = _presence_path(sessions_dir, run_id)
    payload = _read_json(path)
    if payload is None:
        return False
    if phase is not None:
        if phase not in VALID_PHASES:
            raise ValueError(f"phase must be one of {sorted(VALID_PHASES)}; got {phase!r}")
        payload["phase"] = phase
    if files_owned is not None:
        payload["files_owned"] = list(files_owned)
    if high_frequency_mode is not None:
        payload["high_frequency_mode"] = bool(high_frequency_mode)
    payload["last_heartbeat_at"] = iso_utc()
    _atomic_write_json(path, payload)
    return True


def unregister(sessions_dir: Path, run_id: str) -> bool:
    """Move presence to dead/. Returns True if moved; False if not found."""
    _ensure_dirs(sessions_dir)
    path = _presence_path(sessions_dir, run_id)
    if not path.exists():
        return False
    dead_path = sessions_dir / "dead" / path.name
    try:
        shutil.move(str(path), str(dead_path))
        return True
    except OSError:
        return False


def scan_active(
    sessions_dir: Path,
    staleness_minutes: int = DEFAULT_STALENESS_MINUTES,
    exclude_run_id: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Return live presence payloads. Sweeps stale entries as a side effect."""
    _ensure_dirs(sessions_dir)
    sweep_stale(sessions_dir, staleness_minutes, now)
    out: list[dict] = []
    for path in sessions_dir.glob("*.json"):
        if path.parent.name == "dead":
            continue
        payload = _read_json(path)
        if payload is None:
            continue
        if exclude_run_id and payload.get("run_id") == exclude_run_id:
            continue
        if _is_stale(payload, staleness_minutes, now):
            continue
        out.append(payload)
    return out


def check_collision(
    sessions_dir: Path,
    workdir: str,
    run_id: str,
    phase: str,
    files_owned: list[str] | None = None,
    staleness_minutes: int = DEFAULT_STALENESS_MINUTES,
) -> dict:
    """Compare this session against active peers; return collision tier + peers.

    Return shape:
      {
        "tier": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
        "peers": [<peer_payload>, ...],   # all live peers
        "same_workdir": [...],            # subset that match workdir
        "execute_collisions": [...],      # subset that overlap in execute/iterate
        "files_overlap": {                # peer_run_id → list of overlapping files
            "<peer_run_id>": ["<rel>", ...]
        }
      }
    """
    workdir_abs = str(Path(workdir).resolve())
    my_files = set(files_owned or [])
    peers = scan_active(sessions_dir, staleness_minutes, exclude_run_id=run_id)

    same_workdir = [p for p in peers if p.get("workdir") == workdir_abs]
    execute_collisions = [
        p for p in same_workdir
        if p.get("phase") in EXECUTE_PHASES and phase in EXECUTE_PHASES
    ]
    files_overlap: dict[str, list[str]] = {}
    for peer in execute_collisions:
        peer_files = set(peer.get("files_owned") or [])
        overlap = sorted(my_files & peer_files)
        if overlap:
            files_overlap[peer.get("run_id", "?")] = overlap

    if files_overlap:
        tier = COLLISION_CRITICAL
    elif execute_collisions:
        tier = COLLISION_HIGH
    elif same_workdir:
        tier = COLLISION_MEDIUM
    else:
        tier = COLLISION_LOW

    return {
        "tier": tier,
        "peers": peers,
        "same_workdir": same_workdir,
        "execute_collisions": execute_collisions,
        "files_overlap": files_overlap,
    }


def write_safe_stop_sentinel(workdir: Path, peer_run_id: str, reason: str) -> Path:
    """Write a SAFE-STOP-collision-<peer>.md file inside <workdir>/.build-loop/.

    Used in headless-host CRITICAL handling. The next session entering Phase 1
    Assess at this workdir reads .build-loop/SAFE-STOP-*.md files and surfaces
    them to the user before doing anything else.
    """
    bl_dir = workdir / ".build-loop"
    bl_dir.mkdir(parents=True, exist_ok=True)
    safe_id = _safe_id(peer_run_id)
    path = bl_dir / f"SAFE-STOP-collision-{safe_id}.md"
    body = (
        f"# Build-loop SAFE-STOP — collision with {peer_run_id}\n\n"
        f"_Detected {iso_utc()}_\n\n"
        f"This session detected a CRITICAL collision (overlapping `files_owned` "
        f"during concurrent execute/iterate) with peer session **{peer_run_id}** "
        f"and stopped before modifying the working tree.\n\n"
        f"## Reason\n\n{reason}\n\n"
        f"## What to do\n\n"
        f"1. Confirm the peer session is still active (`python3 ${{CLAUDE_PLUGIN_ROOT}}/scripts/session_registry.py scan --json`).\n"
        f"2. If the peer is stale or crashed, remove the corresponding presence file "
        f"(`~/.build-loop/sessions/<run_id>.json`) and remove this sentinel.\n"
        f"3. If the peer is live, decide which run wins and stop the other.\n"
        f"4. Once resolved, delete this file: `rm {path}`\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_register(args: argparse.Namespace) -> int:
    try:
        register(
            Path(args.sessions_dir),
            run_id=args.run_id,
            host=args.host,
            workdir=args.workdir,
            pid=args.pid,
            phase=args.phase,
            files_owned=args.files_owned.split(",") if args.files_owned else None,
            high_frequency_mode=args.high_frequency_mode,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def _cli_heartbeat(args: argparse.Namespace) -> int:
    ok = heartbeat(
        Path(args.sessions_dir),
        run_id=args.run_id,
        phase=args.phase,
        files_owned=args.files_owned.split(",") if args.files_owned else None,
        high_frequency_mode=args.high_frequency_mode,
    )
    if not ok:
        print(f"ERROR: no presence file for run_id={args.run_id}", file=sys.stderr)
        return 1
    return 0


def _cli_unregister(args: argparse.Namespace) -> int:
    ok = unregister(Path(args.sessions_dir), args.run_id)
    if not ok:
        print(f"WARN: no presence file for run_id={args.run_id}", file=sys.stderr)
    return 0


def _cli_scan(args: argparse.Namespace) -> int:
    peers = scan_active(
        Path(args.sessions_dir),
        staleness_minutes=args.staleness_minutes,
        exclude_run_id=args.exclude_run_id,
    )
    if args.json:
        json.dump(peers, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    else:
        if not peers:
            print("(no active sessions)")
        for p in peers:
            print(
                f"{p.get('run_id')} | host={p.get('host')} | "
                f"workdir={p.get('workdir')} | phase={p.get('phase')} | "
                f"heartbeat={p.get('last_heartbeat_at')}"
            )
    return 0


def _cli_check(args: argparse.Namespace) -> int:
    result = check_collision(
        Path(args.sessions_dir),
        workdir=args.workdir,
        run_id=args.run_id,
        phase=args.phase,
        files_owned=args.files_owned.split(",") if args.files_owned else None,
        staleness_minutes=args.staleness_minutes,
    )
    if args.json:
        json.dump(result, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(f"tier: {result['tier']}")
        print(f"peers_total: {len(result['peers'])}")
        print(f"same_workdir: {len(result['same_workdir'])}")
        print(f"execute_collisions: {len(result['execute_collisions'])}")
        if result["files_overlap"]:
            for peer_id, files in result["files_overlap"].items():
                print(f"  CRITICAL overlap with {peer_id}: {files}")
    # Exit code: 0=LOW, 1=MEDIUM, 2=HIGH, 3=CRITICAL — lets shell callers
    # branch on tier without parsing JSON.
    return {
        COLLISION_LOW: 0,
        COLLISION_MEDIUM: 1,
        COLLISION_HIGH: 2,
        COLLISION_CRITICAL: 3,
    }.get(result["tier"], 0)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--sessions-dir",
        default=str(DEFAULT_SESSIONS_DIR),
        help="Override default ~/.build-loop/sessions/ (testing).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register", help="Write a new presence file")
    r.add_argument("--run-id", required=True)
    r.add_argument("--host", required=True, choices=sorted(VALID_HOSTS))
    r.add_argument("--workdir", required=True)
    r.add_argument("--pid", type=int, required=True)
    r.add_argument("--phase", default="assess", choices=sorted(VALID_PHASES))
    r.add_argument("--files-owned", default=None, help="Comma-separated relative paths")
    r.add_argument("--high-frequency-mode", action="store_true")

    h = sub.add_parser("heartbeat", help="Refresh last_heartbeat_at")
    h.add_argument("--run-id", required=True)
    h.add_argument("--phase", default=None, choices=sorted(VALID_PHASES))
    h.add_argument("--files-owned", default=None)
    h.add_argument(
        "--high-frequency-mode",
        default=None,
        type=lambda s: s.lower() in ("1", "true", "yes", "on"),
        help="Set true/false; omit to leave unchanged",
    )

    u = sub.add_parser("unregister", help="Move presence to dead/")
    u.add_argument("--run-id", required=True)

    s = sub.add_parser("scan", help="List active sessions")
    s.add_argument("--staleness-minutes", type=int, default=DEFAULT_STALENESS_MINUTES)
    s.add_argument("--exclude-run-id", default=None)
    s.add_argument("--json", action="store_true")

    c = sub.add_parser("check", help="Check collision tier for current session")
    c.add_argument("--run-id", required=True)
    c.add_argument("--workdir", required=True)
    c.add_argument("--phase", required=True, choices=sorted(VALID_PHASES))
    c.add_argument("--files-owned", default=None)
    c.add_argument("--staleness-minutes", type=int, default=DEFAULT_STALENESS_MINUTES)
    c.add_argument("--json", action="store_true")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dispatch = {
        "register": _cli_register,
        "heartbeat": _cli_heartbeat,
        "unregister": _cli_unregister,
        "scan": _cli_scan,
        "check": _cli_check,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
