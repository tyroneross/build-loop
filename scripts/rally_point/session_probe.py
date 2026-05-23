#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point session probe — auto-invoke runtime for session entry.

``probe(workdir, tool, ...)`` is the single entry point. On session start
it: resolves repo identity, reads coordination state, writes presence,
posts a rally-start phase record, optionally launches a background watcher,
and returns a compact JSON envelope.

Design rules (R2-A spec):
- Read + publish + listen, not just read. Whoever starts first becomes
  visible; whoever arrives later sees them and coordinates.
- Fire-and-forget on all channel writes. Errors collected into ``errors[]``,
  never raised into the caller.
- Solo mode (no active peers, no coord file) MUST post kind=phase
  payload.phase=rally-start and write presence. It must NOT create a coord
  file (coordination_file=null). This satisfies the test_orchestrator_auto_invoke
  solo-mode contract (Codex retro §6).

CLI:
    python3 session_probe.py --workdir <path> --tool <tool-id> \\
        [--mode hook|interactive] [--start-watch] [--run-id <id>]
        [--model <model>] [--json]
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure the rally_point package directory is importable from both
# "python3 session_probe.py" and "from rally_point import session_probe" forms.
_HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = _HERE.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from rally_point import channel_paths, inbox, post as _post_mod, presence, rally
except ImportError:
    from . import channel_paths, inbox
    from . import post as _post_mod
    from . import presence, rally


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _short_random(n: int = 8) -> str:
    """Return a random hex token for session-id disambiguation (SEC-007).

    Uses ``secrets.token_hex`` (CSPRNG) rather than ``random.choices``:
    session ids are written into a shared multi-peer channel, and a
    predictable id makes collision/forgery against another session's
    presence record cheap. ``n`` is the byte count (default 8 → 16 hex
    chars), well clear of birthday-collision range for concurrent peers.
    """
    return secrets.token_hex(n)


def _utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _generate_session_id(tool: str) -> str:
    """Generate a session ID: ``<tool>-<short-random>-<utc-stamp>``."""
    safe_tool = tool.replace("_", "-").replace(" ", "-").lower()
    return f"{safe_tool}-{_short_random()}-{_utc_stamp()}"


def _read_coordination_file(channel_dir: Path) -> str | None:
    """Return the active coordination file path from rally/current.json, or None."""
    try:
        current = rally.read_current(channel_dir)
        if current is None:
            return None
        # Only consider it live if the run is active and recent (<24h)
        status = current.get("status", "active")
        if status == "closed":
            return None
        updated_at = current.get("updated_at")
        if updated_at is not None:
            try:
                age_s = time.time() - float(updated_at)
                if age_s > 86400:
                    return None  # stale pointer
            except (TypeError, ValueError):
                pass
        coord_file = current.get("coord_file")
        if coord_file and isinstance(coord_file, str):
            return coord_file
        return None
    except Exception:
        return None


def _run_status_subprocess(
    workdir: str,
    session_id: str,
    tool: str,
    errors: list,
) -> dict[str, Any]:
    """Run coordination_status.py as a subprocess and return its JSON output.

    Falls back to a minimal envelope on any failure (subprocess, parse error).
    """
    status_script = _SCRIPTS_DIR / "coordination_status.py"
    if not status_script.exists():
        errors.append("coordination_status.py not found")
        return {}
    try:
        result = subprocess.run(
            [
                sys.executable, str(status_script),
                "--workdir", str(workdir),
                "--session-id", session_id,
                "--tool", tool,
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        errors.append(f"coordination_status exit {result.returncode}: {result.stderr.strip()[:200]}")
    except subprocess.TimeoutExpired:
        errors.append("coordination_status timed out")
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        errors.append(f"coordination_status parse error: {exc}")
    return {}


def _launch_watcher(
    workdir: str,
    session_id: str,
    tool: str,
    slug: str,
    watcher_launcher: Any | None,
    errors: list,
) -> str | None:
    """Launch coordination_watch.py detached in the background.

    Returns the PID file path on success, None on failure.
    Uses ``watcher_launcher`` callable when provided (for test injection).
    Default uses ``subprocess`` with nohup + detach so the hook returns fast.
    """
    watch_script = _SCRIPTS_DIR / "coordination_watch.py"
    pid_dir = Path(channel_paths.apps_root()) / slug / "watchers"
    pid_file = pid_dir / f"{session_id}.json"

    if watcher_launcher is not None:
        # Dependency-injected launcher for tests
        try:
            pid = watcher_launcher(
                workdir=workdir,
                session_id=session_id,
                tool=tool,
                watch_script=str(watch_script),
            )
            pid_dir.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(
                json.dumps({"session_id": session_id, "tool": tool, "pid": pid}),
                encoding="utf-8",
            )
            return str(pid_file)
        except Exception as exc:
            errors.append(f"watcher launcher failed: {exc}")
            return None

    if not watch_script.exists():
        errors.append("coordination_watch.py not found; watcher not launched")
        return None

    try:
        pid_dir.mkdir(parents=True, exist_ok=True)
        log_path = pid_dir / f"{session_id}.log"
        # nohup + fully detached: double-fork via subprocess with close_fds
        proc = subprocess.Popen(
            [
                sys.executable, str(watch_script),
                "--workdir", str(workdir),
                "--session-id", session_id,
                "--tool", tool,
                "--baseline-current",
                "--jsonl",
            ],
            stdout=open(str(log_path), "w"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        pid_file.write_text(
            json.dumps({
                "session_id": session_id,
                "tool": tool,
                "pid": proc.pid,
                "log": str(log_path),
                "started_at": time.time(),
            }),
            encoding="utf-8",
        )
        return str(pid_file)
    except Exception as exc:
        errors.append(f"watcher launch failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def probe(
    workdir: str | Path,
    tool: str,
    *,
    mode: str = "interactive",
    start_watch: bool = False,
    model: str = "unknown",
    run_id: str | None = None,
    clock: Any | None = None,
    watcher_launcher: Any | None = None,
) -> dict[str, Any]:
    """Run the session probe and return a compact JSON envelope.

    Parameters
    ----------
    workdir:
        Project working directory. Used to resolve the app slug.
    tool:
        Tool identifier (e.g. "claude_code", "codex").
    mode:
        "interactive" (default) or "hook" (called from a SessionStart hook).
    start_watch:
        When True, launch coordination_watch.py in the background.
    model:
        Model identifier, included in presence + post records.
    run_id:
        Run identifier. Auto-generated from current timestamp if omitted.
    clock:
        Optional callable ``() -> float`` for test injection (replaces time.time).
    watcher_launcher:
        Optional callable for hermetic watcher-launch tests. Signature:
        ``(workdir, session_id, tool, watch_script) -> pid``.

    Returns
    -------
    dict with keys:
        status, active_peers, inbox_unread_count, inbox_unread_counts,
        watcher_started, coordination_file, session_id, slug, errors
    """
    errors: list[str] = []
    now = (clock or time.time)()
    workdir_path = Path(workdir).expanduser().resolve()
    effective_run_id = run_id or f"probe-{_utc_stamp()}"
    tool = tool or "unknown"

    # ------------------------------------------------------------------
    # Step 1: Resolve app identity
    # ------------------------------------------------------------------
    try:
        slug = channel_paths.app_slug(workdir_path)
    except Exception as exc:
        errors.append(f"slug resolution failed: {exc}")
        slug = "_unscoped"

    session_id = _generate_session_id(tool)

    # ------------------------------------------------------------------
    # Step 2: Read rally/current.json for live pointer
    # ------------------------------------------------------------------
    try:
        channel_dir = channel_paths.ensure_channel_dir(slug)
    except Exception as exc:
        errors.append(f"channel_dir creation failed: {exc}")
        return {
            "status": "error",
            "active_peers": [],
            "inbox_unread_count": 0,
            "inbox_unread_counts": {"direct": 0, "broadcast": 0, "total": 0},
            "watcher_started": False,
            "coordination_file": None,
            "session_id": session_id,
            "slug": slug,
            "errors": errors,
        }

    coordination_file: str | None = _read_coordination_file(channel_dir)

    # ------------------------------------------------------------------
    # Step 3: Read own inbox (direct + broadcast + total)
    # ------------------------------------------------------------------
    try:
        inbox_counts = inbox.unread_counts(channel_dir, tool)
    except Exception as exc:
        errors.append(f"inbox read failed: {exc}")
        inbox_counts = {"direct": 0, "broadcast": 0, "total": 0}

    # ------------------------------------------------------------------
    # Step 4: Write presence
    # ------------------------------------------------------------------
    try:
        presence.write_presence(
            channel_dir,
            session_id=session_id,
            tool=tool,
            model=model,
            run_id=effective_run_id,
            app_slug=slug,
            phase="rally-start",
            files_in_flight=[],
            cwd=workdir_path,
        )
    except Exception as exc:
        errors.append(f"presence write failed: {exc}")

    # ------------------------------------------------------------------
    # Step 5: Post kind=phase payload.phase=rally-start (the "announce" step)
    # ------------------------------------------------------------------
    try:
        _post_mod.post(
            channel_dir=channel_dir,
            kind="phase",
            tool=tool,
            model=model,
            run_id=effective_run_id,
            app_slug=slug,
            payload={
                "phase": "rally-start",
                "session_id": session_id,
                "tool": tool,
                "cwd": str(workdir_path),
                "started_at": now,
                "mode": mode,
                "scope": "session-entry",
                "run_id": effective_run_id,
            },
        )
    except Exception as exc:
        errors.append(f"post failed: {exc}")

    # ------------------------------------------------------------------
    # Step 6: Run coordination status read
    # ------------------------------------------------------------------
    status_envelope = _run_status_subprocess(
        workdir=str(workdir_path),
        session_id=session_id,
        tool=tool,
        errors=errors,
    )
    overall_status = status_envelope.get("status", "clear")
    active_peers = status_envelope.get("active_peers", [])

    # ------------------------------------------------------------------
    # Step 7: Optionally launch background watcher
    # ------------------------------------------------------------------
    watcher_started = False
    if start_watch:
        pid_file = _launch_watcher(
            workdir=str(workdir_path),
            session_id=session_id,
            tool=tool,
            slug=slug,
            watcher_launcher=watcher_launcher,
            errors=errors,
        )
        watcher_started = pid_file is not None

    # ------------------------------------------------------------------
    # Step 9: Return compact envelope
    # ------------------------------------------------------------------
    return {
        "status": overall_status,
        "active_peers": active_peers,
        "inbox_unread_count": inbox_counts.get("total", 0),
        "inbox_unread_counts": inbox_counts,
        "watcher_started": watcher_started,
        "coordination_file": coordination_file,
        "session_id": session_id,
        "slug": slug,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rally Point session probe — announce + listen on session entry."
    )
    p.add_argument("--workdir", default=".", help="Project working directory")
    p.add_argument("--tool", required=True, help="Tool identifier (e.g. claude_code)")
    p.add_argument(
        "--mode",
        default="interactive",
        choices=["hook", "interactive"],
        help="Invocation mode (hook = called from SessionStart hook)",
    )
    p.add_argument(
        "--start-watch",
        action="store_true",
        help="Launch coordination_watch.py as a detached background watcher",
    )
    p.add_argument("--run-id", default=None, help="Run identifier (auto-generated if omitted)")
    p.add_argument("--model", default="unknown", help="Model identifier")
    p.add_argument("--json", action="store_true", help="Print JSON envelope to stdout")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = probe(
        workdir=args.workdir,
        tool=args.tool,
        mode=args.mode,
        start_watch=args.start_watch,
        run_id=args.run_id,
        model=args.model,
    )
    if args.json or not sys.stdout.isatty():
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        peers = len(result.get("active_peers", []))
        status = result.get("status", "?")
        inbox_n = result.get("inbox_unread_count", 0)
        slug = result.get("slug", "?")
        print(
            f"Rally Point probe: {slug} — status={status} "
            f"peers={peers} inbox={inbox_n} "
            f"watch={'yes' if result.get('watcher_started') else 'no'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
