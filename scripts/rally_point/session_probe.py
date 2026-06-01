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
import errno
import json
import os
import secrets
import signal
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
    from rally_point.discovery_bridge import resolve as _bridge_resolve
except ImportError:
    from . import channel_paths, inbox
    from . import post as _post_mod
    from . import presence, rally
    from .discovery_bridge import resolve as _bridge_resolve


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


# Match watch.py's default lifetime (single source of truth at the CLI layer).
# Kept local for clarity in the persisted pid-file metadata.
_WATCHER_DEFAULT_MAX_LIFETIME_SECONDS = 14400.0  # 4h


def _watcher_max_lifetime() -> float:
    """Return the configured watcher max-lifetime, env-overridable.

    Matches the parsing rules in watch._env_max_lifetime so launcher and
    watcher agree on the value persisted in the pid file.
    """
    raw = os.environ.get("BUILD_LOOP_WATCHER_MAX_LIFETIME_SECONDS")
    if raw is None:
        return _WATCHER_DEFAULT_MAX_LIFETIME_SECONDS
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _WATCHER_DEFAULT_MAX_LIFETIME_SECONDS


def _pid_alive(pid: int) -> bool:
    """Return True iff signalable pid is alive. EPERM means alive (other uid)."""
    if pid <= 1:
        # Treat <=1 as 'unknown but do not act'; the reaper relies on
        # parent_pid being a real session pid, never 0/1.
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH


def _terminate_watcher(pid: int) -> tuple[bool, bool]:
    """SIGTERM with brief grace, then SIGKILL. Returns (sigtermed, sigkilled).

    Errors swallowed (best-effort; rally never-block charter). Caller decides
    whether to delete the pid file afterward.
    """
    sigtermed = False
    sigkilled = False
    try:
        os.kill(pid, signal.SIGTERM)
        sigtermed = True
    except (ProcessLookupError, PermissionError):
        return (False, False)
    except OSError:
        return (False, False)
    # Brief grace; the watcher polls every 3s by default but a SIGTERM
    # interrupts the sleep on POSIX.
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return (sigtermed, sigkilled)
        time.sleep(0.02)
    # Still alive after grace → SIGKILL.
    try:
        os.kill(pid, signal.SIGKILL)
        sigkilled = True
    except (ProcessLookupError, PermissionError, OSError):
        pass
    return (sigtermed, sigkilled)


def _reap_stale_watchers(
    pid_dir: Path, now: float, max_lifetime: float
) -> dict[str, int]:
    """Sweep ``pid_dir/*.json`` and reap watchers whose owner is gone, whose
    own pid is dead, or whose started_at is older than ``max_lifetime``.

    For each file:
        * If process at ``pid`` is dead → delete json (and matching .log).
        * If recorded ``parent_pid`` is set and dead → SIGTERM → 0.2s grace
          → SIGKILL → delete json + log.
        * If ``now - started_at > max_lifetime`` → same SIGTERM/SIGKILL/delete.
        * Otherwise leave the file alone.

    Returns ``{"scanned": N, "deleted_files": N, "sigtermed": N, "sigkilled": N}``.
    All exceptions swallowed; the reaper must NEVER block coordination
    (build-loop-memory feedback_close_out_stops_the_watcher.md / rally
    never-block charter).
    """
    stats = {"scanned": 0, "deleted_files": 0, "sigtermed": 0, "sigkilled": 0}
    if not pid_dir.exists():
        return stats
    try:
        entries = sorted(pid_dir.glob("*.json"))
    except OSError:
        return stats
    for entry in entries:
        stats["scanned"] += 1
        try:
            meta = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Unreadable record: delete it so it stops accumulating.
            _safe_delete_watcher_files(entry)
            stats["deleted_files"] += 1
            continue

        pid = int(meta.get("pid", 0) or 0)
        parent_pid = meta.get("parent_pid")
        started_at = float(meta.get("started_at", 0) or 0)

        should_terminate = False
        reason = ""
        # (A) Watcher process itself dead → just delete the file.
        if pid > 0 and not _pid_alive(pid):
            _safe_delete_watcher_files(entry)
            stats["deleted_files"] += 1
            continue
        # (B) Parent (launcher) dead → terminate watcher.
        if parent_pid is not None:
            try:
                parent_pid_int = int(parent_pid)
            except (TypeError, ValueError):
                parent_pid_int = 0
            if parent_pid_int > 1 and not _pid_alive(parent_pid_int):
                should_terminate = True
                reason = "parent-dead"
        # (C) Over absolute lifetime → terminate watcher.
        if (
            not should_terminate
            and started_at > 0
            and (now - started_at) > max_lifetime
        ):
            should_terminate = True
            reason = "over-lifetime"

        if should_terminate and pid > 0:
            sigtermed, sigkilled = _terminate_watcher(pid)
            if sigtermed:
                stats["sigtermed"] += 1
            if sigkilled:
                stats["sigkilled"] += 1
            _safe_delete_watcher_files(entry)
            stats["deleted_files"] += 1
    return stats


def _safe_delete_watcher_files(pid_file: Path) -> None:
    """Delete ``pid_file`` and its sibling ``.log``; swallow errors."""
    for path in (pid_file, pid_file.with_suffix(".log")):
        try:
            path.unlink()
        except (FileNotFoundError, OSError):
            pass


def _launch_watcher(
    workdir: str,
    session_id: str,
    tool: str,
    slug: str,
    watcher_launcher: Any | None,
    errors: list,
    parent_pid: int | None = None,
) -> str | None:
    """Launch coordination_watch.py detached in the background.

    Returns the PID file path on success, None on failure.
    Uses ``watcher_launcher`` callable when provided (for test injection).
    Default uses ``subprocess`` with nohup + detach so the hook returns fast.

    ``parent_pid`` (optional, default ``os.getpid()``) is captured BEFORE the
    Popen call and threaded to the child via ``--parent-pid``. This closes
    the race where the hook process exits during child Python startup and the
    watcher's ``os.getppid()`` already reads 1 by the time main runs
    (build-loop-memory lessons/2026-05-31-coordination-process-leak.md fix
    iteration 2). The pid file persists ``parent_pid``, ``started_at``, and
    ``max_lifetime_seconds`` so the reaper can audit dead-parent / over-age
    watchers on the next SessionStart.

    Watcher PID files live under ``<channel_dir>/watchers/`` — the
    channel dir is resolved via the discovery bridge so canonical/legacy
    policy is honoured (no direct ``channel_paths.apps_root()`` write).
    """
    watch_script = _SCRIPTS_DIR / "coordination_watch.py"
    # Capture launcher PID BEFORE any subprocess work. Once we Popen with
    # start_new_session=True the child is reparented to init the moment we
    # exit — by then our pid is gone, so capture must happen here.
    effective_parent_pid = parent_pid if parent_pid is not None else os.getpid()
    max_lifetime = _watcher_max_lifetime()
    try:
        envelope = _bridge_resolve(Path(workdir))
        pid_dir = Path(envelope.channel_dir) / "watchers"
    except Exception:
        # Bridge resolution is best-effort; fall back to the legacy
        # apps_root() path so a single resolver failure doesn't suppress
        # the watcher.
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
                parent_pid=effective_parent_pid,
                max_lifetime_seconds=max_lifetime,
            )
            pid_dir.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(
                json.dumps({
                    "session_id": session_id,
                    "tool": tool,
                    "pid": pid,
                    "parent_pid": effective_parent_pid,
                    "started_at": time.time(),
                    "max_lifetime_seconds": max_lifetime,
                }),
                encoding="utf-8",
            )
            return str(pid_file)
        except TypeError:
            # Backward-compat: tests written before C2 take only the
            # original kwargs. Retry without the new kwargs and persist the
            # metadata anyway so reap-stale audits the file correctly.
            try:
                pid = watcher_launcher(
                    workdir=workdir,
                    session_id=session_id,
                    tool=tool,
                    watch_script=str(watch_script),
                )
                pid_dir.mkdir(parents=True, exist_ok=True)
                pid_file.write_text(
                    json.dumps({
                        "session_id": session_id,
                        "tool": tool,
                        "pid": pid,
                        "parent_pid": effective_parent_pid,
                        "started_at": time.time(),
                        "max_lifetime_seconds": max_lifetime,
                    }),
                    encoding="utf-8",
                )
                return str(pid_file)
            except Exception as exc:
                errors.append(f"watcher launcher failed: {exc}")
                return None
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
                "--parent-pid", str(effective_parent_pid),
                "--max-lifetime-seconds", str(max_lifetime),
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
                "parent_pid": effective_parent_pid,
                "log": str(log_path),
                "started_at": time.time(),
                "max_lifetime_seconds": max_lifetime,
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
    # Step 1: Resolve app identity + channel via the shared bridge (β1)
    # ------------------------------------------------------------------
    try:
        envelope = _bridge_resolve(workdir_path)
        slug = envelope.app_slug
        channel_dir = Path(envelope.channel_dir)
        if envelope.resolved_via == "build-loop-internal":
            channel_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        errors.append(f"channel resolution failed: {exc}")
        slug = "_unscoped"
        session_id = _generate_session_id(tool)
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

    session_id = _generate_session_id(tool)

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
    # Step 7: Optionally reap stale watchers, then launch background watcher.
    # Reaping happens BEFORE launching so a leak from a prior session is
    # cleaned up before this session adds its own watcher to the directory.
    # Best-effort: a reaper failure never blocks the new watcher.
    # ------------------------------------------------------------------
    watcher_started = False
    if start_watch:
        try:
            envelope_for_pid = _bridge_resolve(workdir_path)
            pid_dir = Path(envelope_for_pid.channel_dir) / "watchers"
        except Exception:
            pid_dir = Path(channel_paths.apps_root()) / slug / "watchers"
        try:
            _reap_stale_watchers(
                pid_dir=pid_dir,
                now=now,
                max_lifetime=_watcher_max_lifetime(),
            )
        except Exception as exc:
            errors.append(f"reap-stale failed: {exc}")
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
