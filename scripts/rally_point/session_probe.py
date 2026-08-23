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
import fcntl
import hashlib
import json
import math
import os
import re
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
    from rally_point import (
        actor_identity,
        channel_paths,
        hook_budget,
        inbox,
        post as _post_mod,
        presence,
        rally,
    )
    from rally_point.backend_adapter import (
        native_inbox_snapshot,
        recent as native_recent,
        resolve_context,
        room_snapshot,
        watcher_dir,
        write_presence as write_backend_presence,
    )
    from rally_point.discovery_bridge import resolve as _bridge_resolve
except ImportError:
    from . import actor_identity, channel_paths, hook_budget, inbox
    from . import post as _post_mod
    from . import presence, rally
    from .backend_adapter import (
        native_inbox_snapshot,
        recent as native_recent,
        resolve_context,
        room_snapshot,
        watcher_dir,
        write_presence as write_backend_presence,
    )
    from .discovery_bridge import resolve as _bridge_resolve


def _probe_inject_readiness(errors: list[str]) -> dict[str, bool | str]:
    """Best-effort pane-backend probe; never blocks session start."""
    try:
        from rally_point import inject_readiness
    except ImportError:
        try:
            from . import inject_readiness
        except ImportError as exc:
            errors.append(f"inject readiness unavailable: {exc}")
            return {
                "tmux": False,
                "ptyd_socket_live": False,
                "ptyd_bin": False,
                "inject_available": False,
                "recommended_backend": "handoff",
            }
    try:
        return inject_readiness.probe()
    except Exception as exc:  # noqa: BLE001 — session start must stay fail-open
        errors.append(f"inject readiness probe failed: {exc}")
        return {
            "tmux": False,
            "ptyd_socket_live": False,
            "ptyd_bin": False,
            "inject_available": False,
            "recommended_backend": "handoff",
        }


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


def _bounded_safe_id(value: str, *, max_length: int = 96) -> str:
    """Return a filesystem-safe, bounded id using Rally's tool sanitizer."""
    raw = str(value or "unknown").strip()
    cleaned = inbox._safe_tool(raw)
    if cleaned == raw and len(cleaned) <= max_length:
        return cleaned
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    prefix = cleaned[: max_length - len(digest) - 1].rstrip("._-") or "unknown"
    return f"{prefix}-{digest}"


def _watcher_artifact_path(pid_dir: Path, session_id: str, suffix: str) -> Path:
    """Build a watcher artifact path and prove it stays in ``pid_dir``."""
    root = Path(pid_dir).expanduser().resolve(strict=False)
    candidate = (root / f"{_bounded_safe_id(session_id)}{suffix}").resolve(
        strict=False
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("watcher artifact path escapes watcher directory") from exc
    if candidate.parent != root:
        raise ValueError("watcher artifact path must be a direct child")
    return candidate


def _utc_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _generate_session_id(tool: str) -> str:
    """Generate a session ID: ``<tool>-<short-random>-<utc-stamp>``."""
    safe_tool = _bounded_safe_id(tool.lower(), max_length=48)
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
            # Derive from the rally hook wall-clock budget so this inner timeout
            # can NEVER exceed the outer budget (was a fixed 5s under a 3s budget,
            # which guaranteed a fail-open whenever the probe was slow).
            timeout=hook_budget.inner_timeout_seconds(hook_budget.MARGIN_PARENT),
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
_WATCHER_LAUNCH_LOCK_TIMEOUT_SECONDS = 0.5
_MAX_SAFE_PID = (1 << 31) - 1


def _watcher_max_lifetime() -> float:
    """Return the configured watcher max-lifetime, env-overridable.

    Matches the parsing rules in watch._env_max_lifetime so launcher and
    watcher agree on the value persisted in the pid file.
    """
    raw = os.environ.get("BUILD_LOOP_WATCHER_MAX_LIFETIME_SECONDS")
    if raw is None:
        return _WATCHER_DEFAULT_MAX_LIFETIME_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _WATCHER_DEFAULT_MAX_LIFETIME_SECONDS
    if not math.isfinite(value) or value <= 0:
        return _WATCHER_DEFAULT_MAX_LIFETIME_SECONDS
    return value


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


def _process_identity(pid: int) -> dict[str, str] | None:
    """Return an OS start token and full command for ``pid`` when readable."""
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 1
        or pid > _MAX_SAFE_PID
    ):
        return None

    proc_dir = Path("/proc") / str(pid)
    try:
        stat_text = (proc_dir / "stat").read_text(encoding="utf-8")
        close_paren = stat_text.rfind(")")
        stat_tail = stat_text[close_paren + 2 :].split()
        start_ticks = stat_tail[19]
        command_parts = [
            part.decode("utf-8", errors="replace")
            for part in (proc_dir / "cmdline").read_bytes().split(b"\0")
            if part
        ]
        command = " ".join(command_parts)
        if close_paren > 0 and start_ticks and command:
            return {
                "start_token": f"proc:{start_ticks}",
                "command": command,
            }
    except (IndexError, OSError, ValueError):
        pass

    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "lstart=", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    parts = result.stdout.strip().split(maxsplit=5)
    if result.returncode != 0 or len(parts) != 6:
        return None
    return {
        "start_token": "ps:" + " ".join(parts[:5]),
        "command": parts[5],
    }


def _identity_describes_watcher(identity: Any, session_id: Any) -> bool:
    """Return whether identity names the expected watcher session."""
    if not isinstance(identity, dict) or not isinstance(session_id, str):
        return False
    start_token = identity.get("start_token")
    command = identity.get("command")
    if not isinstance(start_token, str) or not start_token:
        return False
    if not isinstance(command, str) or not command:
        return False
    if "coordination_watch.py" not in command:
        return False
    if re.search(
        rf"(?:^|\s)--session-id\s+{re.escape(session_id)}(?:\s|$)", command
    ) is None:
        return False
    return True


def _capture_stable_watcher_identity(
    pid: int, session_id: str, *, timeout: float = 0.25
) -> dict[str, str] | None:
    """Wait for two identical identity reads after the child finishes exec."""
    deadline = time.monotonic() + timeout
    previous: dict[str, str] | None = None
    while time.monotonic() < deadline:
        current = _process_identity(pid)
        if _identity_describes_watcher(current, session_id):
            if current == previous:
                return current
            previous = current
        else:
            previous = None
        time.sleep(0.01)
    return None


def _watcher_identity_matches(meta: dict[str, Any], pid: int) -> bool:
    """Prove ``pid`` is the exact watcher recorded by ``meta``."""
    stored = meta.get("process_identity")
    session_id = meta.get("session_id")
    if not _identity_describes_watcher(stored, session_id):
        return False
    current = _process_identity(pid)
    return bool(
        current
        and current.get("start_token") == stored.get("start_token")
        and current.get("command") == stored.get("command")
    )


def _terminate_watcher(
    pid: int, *, identity_check: Any | None = None
) -> tuple[bool, bool]:
    """SIGTERM with brief grace, then SIGKILL. Returns (sigtermed, sigkilled).

    Errors swallowed (best-effort; rally never-block charter). Caller decides
    whether to delete the pid file afterward. When supplied, ``identity_check``
    must pass immediately before each signal so PID reuse cannot redirect it.
    """
    sigtermed = False
    sigkilled = False
    if identity_check is not None:
        try:
            if not identity_check():
                return (False, False)
        except Exception:
            return (False, False)
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
        if identity_check is not None:
            try:
                if not identity_check():
                    return (sigtermed, sigkilled)
            except Exception:
                return (sigtermed, sigkilled)
        time.sleep(0.02)
    # Still alive after grace → SIGKILL.
    if identity_check is not None:
        try:
            if not identity_check():
                return (sigtermed, sigkilled)
        except Exception:
            return (sigtermed, sigkilled)
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
        except (OSError, UnicodeError, json.JSONDecodeError):
            # Unreadable record: delete it so it stops accumulating.
            _safe_delete_watcher_files(entry)
            stats["deleted_files"] += 1
            continue

        try:
            if not isinstance(meta, dict):
                raise TypeError("watcher metadata must be an object")
            pid_raw = meta.get("pid")
            started_at_raw = meta.get("started_at")
            if (
                isinstance(pid_raw, bool)
                or not isinstance(pid_raw, int)
                or pid_raw <= 1
                or pid_raw > _MAX_SAFE_PID
            ):
                raise ValueError("watcher pid must be an integer greater than one")
            if (
                isinstance(started_at_raw, bool)
                or not isinstance(started_at_raw, (int, float))
            ):
                raise ValueError("watcher started_at must be numeric")
            pid = pid_raw
            started_at = float(started_at_raw)
            if not math.isfinite(started_at) or started_at <= 0:
                raise ValueError("watcher started_at must be finite and positive")
            parent_pid = meta.get("parent_pid")
            if parent_pid is not None and (
                isinstance(parent_pid, bool)
                or not isinstance(parent_pid, int)
                or parent_pid <= 1
                or parent_pid > _MAX_SAFE_PID
            ):
                raise ValueError("watcher parent_pid must be a valid process id")
        except (TypeError, ValueError, OverflowError):
            # Valid JSON can still carry unusable metadata. Clean only this
            # entry and continue so a bad record cannot shield later leaks.
            _safe_delete_watcher_files(entry)
            stats["deleted_files"] += 1
            continue

        should_terminate = False
        # (A) Watcher process itself dead → just delete the file.
        if pid > 0 and not _pid_alive(pid):
            _safe_delete_watcher_files(entry)
            stats["deleted_files"] += 1
            continue
        # (B) Parent (launcher) dead → terminate watcher.
        if parent_pid is not None and not _pid_alive(parent_pid):
            should_terminate = True
        # (C) Over absolute lifetime → terminate watcher.
        if (
            not should_terminate
            and started_at > 0
            and (now - started_at) > max_lifetime
        ):
            should_terminate = True

        if should_terminate and pid > 0:
            if not _watcher_identity_matches(meta, pid):
                # A reused PID can belong to an unrelated process. Legacy or
                # unverifiable records are safe to delete, never safe to signal.
                _safe_delete_watcher_files(entry)
                stats["deleted_files"] += 1
                continue
            sigtermed, sigkilled = _terminate_watcher(
                pid,
                identity_check=lambda: _watcher_identity_matches(meta, pid),
            )
            if sigtermed:
                stats["sigtermed"] += 1
            if sigkilled:
                stats["sigkilled"] += 1
            if not _pid_alive(pid):
                _safe_delete_watcher_files(entry)
                stats["deleted_files"] += 1
                continue
            current_identity = _process_identity(pid)
            stored_identity = meta.get("process_identity")
            if (
                isinstance(current_identity, dict)
                and isinstance(stored_identity, dict)
                and (
                    current_identity.get("start_token")
                    != stored_identity.get("start_token")
                    or current_identity.get("command")
                    != stored_identity.get("command")
                )
            ):
                # The recorded PID now provably names another process. Drop
                # stale metadata, but never signal the replacement process.
                _safe_delete_watcher_files(entry)
                stats["deleted_files"] += 1
            # Otherwise the watcher is still alive or its identity is
            # unreadable. Preserve metadata so a later sweep can retry; an
            # untracked live watcher is worse than a retained stale record.
    return stats


def _safe_delete_watcher_files(pid_file: Path) -> None:
    """Delete ``pid_file`` and its sibling ``.log``; swallow errors."""
    for path in (pid_file, pid_file.with_suffix(".log")):
        try:
            path.unlink()
        except (FileNotFoundError, OSError):
            pass


def _spawned_watcher_is_dead(proc: Any) -> bool:
    try:
        return proc.poll() is not None
    except (ChildProcessError, OSError):
        return False


def _terminate_and_reap_spawned_watcher(proc: Any) -> bool:
    """Terminate/reap a spawned child and return only proven-dead success."""
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=0.2)
        return True
    except subprocess.TimeoutExpired:
        pass
    except (ChildProcessError, OSError):
        if _spawned_watcher_is_dead(proc):
            return True
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=0.2)
        return True
    except (subprocess.TimeoutExpired, ChildProcessError, OSError):
        return _spawned_watcher_is_dead(proc)


def _acquire_watcher_launch_lock(pid_dir: Path) -> int | None:
    """Serialize all watcher launches in one directory with a fixed sidecar."""
    pid_dir.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(pid_dir / ".launch.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + _WATCHER_LAUNCH_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_fd
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                os.close(lock_fd)
                raise
            if time.monotonic() >= deadline:
                os.close(lock_fd)
                return None
            time.sleep(0.01)


def _release_watcher_launch_lock(lock_fd: int) -> None:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(lock_fd)


def _atomic_write_watcher_artifact(path: Path, data: str) -> None:
    """Publish watcher metadata/snapshots without exposing partial contents."""
    temp = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            encoded = data.encode("utf-8")
            offset = 0
            while offset < len(encoded):
                written = os.write(fd, encoded[offset:])
                if written <= 0:
                    raise OSError("zero-byte watcher artifact write")
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _existing_watcher_state(pid_file: Path, session_id: str) -> str:
    """Return absent, live, or unsafe for one same-session metadata record."""
    try:
        if pid_file.is_symlink():
            return "unsafe"
        meta = json.loads(pid_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "absent"
    except (OSError, UnicodeError, json.JSONDecodeError):
        _safe_delete_watcher_files(pid_file)
        return "absent"
    if not isinstance(meta, dict) or meta.get("session_id") != session_id:
        return "unsafe"
    pid = meta.get("pid")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 1
        or pid > _MAX_SAFE_PID
    ):
        _safe_delete_watcher_files(pid_file)
        return "absent"
    if not _pid_alive(pid):
        _safe_delete_watcher_files(pid_file)
        return "absent"
    if _watcher_identity_matches(meta, pid):
        return "live"
    # A live but unverifiable PID must never be replaced by a duplicate or
    # signaled as if it were ours. A later reaper can retry identity checks.
    return "unsafe"


def _private_watcher_dir(workdir: Path | str, slug: str) -> Path:
    """Resolve Build Loop-owned watcher metadata without touching Rally."""
    workdir_path = Path(workdir)
    try:
        return watcher_dir(resolve_context(workdir_path))
    except Exception:
        # Discovery is best-effort, but resolver failure must not regress to
        # the historical basename-only namespace.  The direct fallback path
        # is still canonical-repository-identity keyed.
        return channel_paths.fallback_channel_dir(
            workdir_path, slug
        ) / "watchers"


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

    Watcher PID files are Build Loop process metadata, so they always live
    under the identity-keyed private fallback channel's ``watchers/``
    directory.  Healthy standalone Rally rooms never receive Build Loop
    sidecars.
    """
    watch_script = _SCRIPTS_DIR / "coordination_watch.py"
    # Capture launcher PID BEFORE any subprocess work. Once we Popen with
    # start_new_session=True the child is reparented to init the moment we
    # exit — by then our pid is gone, so capture must happen here.
    effective_parent_pid = parent_pid if parent_pid is not None else os.getpid()
    effective_session_id = _bounded_safe_id(session_id)
    max_lifetime = _watcher_max_lifetime()
    pid_dir = _private_watcher_dir(workdir, slug)
    pid_file = _watcher_artifact_path(pid_dir, effective_session_id, ".json")
    pid_dir = pid_file.parent
    try:
        pid_dir.mkdir(parents=True, exist_ok=True)
        lock_fd = _acquire_watcher_launch_lock(pid_dir)
    except Exception as exc:
        errors.append(f"watcher launch lock failed: {exc}")
        return None
    if lock_fd is None:
        errors.append("watcher launch lock timed out")
        return None

    proc = None
    spawned_metadata: dict[str, Any] | None = None
    try:
        existing = _existing_watcher_state(pid_file, effective_session_id)
        if existing == "live":
            return str(pid_file)
        if existing == "unsafe":
            errors.append(
                "same-session watcher metadata names a live unverifiable process; "
                "duplicate launch refused"
            )
            return None

        if watcher_launcher is not None:
            # Dependency-injected launcher for tests.
            try:
                pid = watcher_launcher(
                    workdir=workdir,
                    session_id=effective_session_id,
                    tool=tool,
                    watch_script=str(watch_script),
                    parent_pid=effective_parent_pid,
                    max_lifetime_seconds=max_lifetime,
                )
            except TypeError:
                # Backward-compat: launchers written before C2 take only the
                # original kwargs. Injection returns a PID, not a process
                # handle, so cleanup ownership remains with the injector.
                pid = watcher_launcher(
                    workdir=workdir,
                    session_id=effective_session_id,
                    tool=tool,
                    watch_script=str(watch_script),
                )
            _atomic_write_watcher_artifact(
                pid_file,
                json.dumps({
                    "session_id": effective_session_id,
                    "tool": tool,
                    "pid": pid,
                    "parent_pid": effective_parent_pid,
                    "started_at": time.time(),
                    "max_lifetime_seconds": max_lifetime,
                    "process_identity": _process_identity(pid),
                }),
            )
            return str(pid_file)

        if not watch_script.exists():
            errors.append("coordination_watch.py not found; watcher not launched")
            return None

        log_path = _watcher_artifact_path(pid_dir, effective_session_id, ".log")
        _atomic_write_watcher_artifact(log_path, "")
        # Fully detached, but no append-only stream: the child atomically
        # replaces one bounded latest-event snapshot at ``log_path``.
        proc = subprocess.Popen(
            [
                sys.executable, str(watch_script),
                "--workdir", str(workdir),
                "--session-id", effective_session_id,
                "--tool", tool,
                "--baseline-current",
                "--jsonl",
                "--snapshot-file", str(log_path),
                "--parent-pid", str(effective_parent_pid),
                "--max-lifetime-seconds", str(max_lifetime),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        process_identity = _capture_stable_watcher_identity(
            proc.pid, effective_session_id
        )
        if not process_identity:
            raise RuntimeError("spawned watcher process identity could not be verified")
        spawned_metadata = {
            "session_id": effective_session_id,
            "tool": tool,
            "pid": proc.pid,
            "parent_pid": effective_parent_pid,
            "log": str(log_path),
            "started_at": time.time(),
            "max_lifetime_seconds": max_lifetime,
            "process_identity": process_identity,
        }
        _atomic_write_watcher_artifact(pid_file, json.dumps(spawned_metadata))
        return str(pid_file)
    except Exception as exc:
        proven_dead = proc is None
        if proc is not None:
            proven_dead = _terminate_and_reap_spawned_watcher(proc)
        if proven_dead:
            _safe_delete_watcher_files(pid_file)
        elif spawned_metadata is not None:
            # Keep an identity-verified retry record whenever cleanup could
            # not prove the detached child dead. The next reaper can safely
            # retry; deleting it here would create an untracked orphan.
            retry_metadata = {
                **spawned_metadata,
                "launch_state": "metadata_persist_failed_cleanup_unconfirmed",
                "launch_error": str(exc)[:240],
            }
            try:
                _atomic_write_watcher_artifact(
                    pid_file, json.dumps(retry_metadata)
                )
            except Exception as retry_exc:
                errors.append(
                    "watcher retry metadata persistence failed; log preserved: "
                    f"{retry_exc}"
                )
        errors.append(f"watcher launch failed: {exc}")
        return None
    finally:
        _release_watcher_launch_lock(lock_fd)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def probe(
    workdir: str | Path,
    tool: str,
    *,
    session_id: str | None = None,
    mode: str = "interactive",
    start_watch: bool = False,
    watch_parent_pid: int | None = None,
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
        User-facing host family (e.g. "claude_code", "codex") or an
        explicitly session-qualified Rally actor.
    session_id:
        Stable host session key. When omitted, resolves from the shared host
        environment contract in ``actor_identity``.
    mode:
        "interactive" (default) or "hook" (called from a SessionStart hook).
    start_watch:
        When True, launch coordination_watch.py in the background.
    watch_parent_pid:
        Optional long-lived host PID for watcher liveness. Hook callers should
        pass their shell parent PID so the watcher is not tied to the short-lived
        probe process.
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
    requested_session_id = session_id
    identity = actor_identity.resolve_identity(tool, requested_session_id)
    base_tool = identity.base_tool
    local_session_id = (
        str(requested_session_id).strip()
        if requested_session_id is not None and str(requested_session_id).strip()
        else identity.session_id
    )
    session_id = identity.session_id
    inject_readiness = _probe_inject_readiness(errors)

    # ------------------------------------------------------------------
    # Step 1: Resolve app identity + channel via the shared bridge (β1)
    # ------------------------------------------------------------------
    try:
        context = resolve_context(workdir_path)
        envelope = context.envelope
        slug = envelope.app_slug
        channel_dir = Path(envelope.channel_dir)
        capability_level = envelope.capability_level
        resolved_via = envelope.resolved_via
        if envelope.backend == "build-loop-local":
            channel_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        errors.append(f"channel resolution failed: {exc}")
        slug = "_unscoped"
        return {
            "status": "error",
            "active_peers": [],
            "inbox_unread_count": 0,
            "inbox_unread_counts": {"direct": 0, "broadcast": 0, "total": 0},
            "inbox_latest_messages": [],
            "watcher_started": False,
            "coordination_file": None,
            "session_id": session_id,
            "tool": base_tool,
            "rally_tool": identity.native_tool,
            "slug": slug,
            "capability_level": "unavailable",
            "resolved_via": None,
            "inject_readiness": inject_readiness,
            "errors": errors,
        }

    coordination_tool = identity.native_tool if context.native else base_tool

    if not context.native and envelope.backend != "build-loop-local":
        reason = envelope.refusal_reason or "no coordination authority is available"
        remedy = envelope.refusal_remedy or "restore coordination, then retry"
        errors.append(f"coordination refused: {reason}; remedy={remedy}")
        return {
            "status": "warn",
            "active_peers": [],
            "inbox_unread_count": 0,
            "inbox_unread_counts": {"direct": 0, "broadcast": 0, "total": 0},
            "inbox_latest_messages": [],
            "inbox_coverage_incomplete": True,
            "inbox_coverage": {
                "repo_recent_available": False,
                "reasons": ["coordination_refused"],
            },
            "coordination_write_failed": True,
            "coordination_refused": True,
            "coordination_unavailable": envelope.coordination_unavailable,
            "reason": reason,
            "remedy": remedy,
            "watcher_started": False,
            "coordination_file": None,
            "session_id": session_id,
            "tool": base_tool,
            "rally_tool": identity.native_tool,
            "slug": slug,
            "capability_level": capability_level,
            "resolved_via": resolved_via,
            "backend": envelope.backend,
            "transport": envelope.transport,
            "inject_readiness": inject_readiness,
            "errors": errors,
        }

    coordination_file: str | None = (
        None if context.native else _read_coordination_file(context.local_channel_dir)
    )
    inbox_coverage_incomplete = False
    inbox_coverage: dict[str, Any] = {"reasons": []}

    # ------------------------------------------------------------------
    # Step 3: Read own inbox (direct + broadcast + total)
    # ------------------------------------------------------------------
    try:
        if context.native:
            native_room = room_snapshot(context, actor=coordination_tool, readers=True)
            if not native_room.ok:
                raise RuntimeError(native_room.reason or "native room read failed")
            native_inbox = native_inbox_snapshot(
                native_room,
                tool=coordination_tool,
                recent_result=native_recent(context, limit=500),
            )
            inbox_counts = native_inbox["counts"]
            inbox_latest_messages = native_inbox["latest"]
            inbox_coverage_incomplete = bool(
                native_inbox.get("coverage_incomplete")
            )
            inbox_coverage = dict(native_inbox.get("coverage") or {})
            if inbox_coverage_incomplete:
                errors.append(
                    "native inbox coverage incomplete: "
                    + ",".join(inbox_coverage.get("reasons") or ["unknown"])
                )
        else:
            inbox_counts = inbox.unread_counts(context.local_channel_dir, base_tool)
            inbox_latest_messages = inbox.latest_message_summaries(
                context.local_channel_dir, tool=base_tool, limit=3
            )
    except Exception as exc:
        errors.append(f"inbox read failed: {exc}")
        inbox_counts = {"direct": 0, "broadcast": 0, "total": 0}
        inbox_latest_messages = []

    # ------------------------------------------------------------------
    # Step 4: Write presence
    # ------------------------------------------------------------------
    presence_result = None
    required_coordination_write_failed = False
    try:
        presence_result = write_backend_presence(
            context,
            session_id=session_id,
            tool=coordination_tool,
            local_session_id=local_session_id,
            local_tool=base_tool,
            model=model,
            run_id=effective_run_id,
            app_slug=slug,
            phase="rally-start",
            files_in_flight=[],
            cwd=workdir_path,
        )
        if not presence_result.ok:
            raise RuntimeError(presence_result.reason or presence_result.status)
    except Exception as exc:
        required_coordination_write_failed = True
        errors.append(f"presence write failed: {exc}")

    # ------------------------------------------------------------------
    # Step 5: Post kind=phase payload.phase=rally-start (the "announce" step)
    # ------------------------------------------------------------------
    phase_outcome: dict[str, Any] = {}
    try:
        phase_revision = _post_mod.post(
            channel_dir=channel_dir,
            kind="phase",
            tool=coordination_tool,
            model=model,
            run_id=effective_run_id,
            app_slug=slug,
            payload={
                "phase": "rally-start",
                "session_id": session_id,
                "tool": base_tool,
                "rally_tool": coordination_tool,
                "cwd": str(workdir_path),
                "started_at": now,
                "mode": mode,
                "scope": "session-entry",
                "run_id": effective_run_id,
                "capability_level": capability_level,
                "resolved_via": resolved_via,
                "inject_readiness": inject_readiness,
            },
            workdir=workdir_path,
            outcome=phase_outcome,
            local_tool=base_tool,
            local_session_id=local_session_id,
        )
        if (
            type(phase_revision) is not int
            or phase_revision <= 0
            or phase_outcome.get("status") != "posted"
        ):
            required_coordination_write_failed = True
            detail = phase_outcome.get("reason") or phase_outcome.get("status") or "unknown"
            remedy = phase_outcome.get("remedy")
            errors.append(
                f"rally-start post not committed: {detail}"
                + (f"; remedy={remedy}" if remedy else "")
            )
    except Exception as exc:
        required_coordination_write_failed = True
        errors.append(f"post failed: {exc}")

    # ------------------------------------------------------------------
    # Step 6: Run coordination status read
    # ------------------------------------------------------------------
    status_envelope = _run_status_subprocess(
        workdir=str(workdir_path),
        session_id=session_id,
        tool=coordination_tool,
        errors=errors,
    )
    overall_status = status_envelope.get("status", "clear")
    if (
        inbox_coverage_incomplete or required_coordination_write_failed
    ) and overall_status == "clear":
        overall_status = "warn"
    active_peers = status_envelope.get("active_peers", [])

    # ------------------------------------------------------------------
    # Step 7: Optionally reap stale watchers, then launch background watcher.
    # Reaping happens BEFORE launching so a leak from a prior session is
    # cleaned up before this session adds its own watcher to the directory.
    # Best-effort: a reaper failure never blocks the new watcher.
    # ------------------------------------------------------------------
    watcher_started = False
    if start_watch:
        pid_dir = _private_watcher_dir(workdir_path, slug)
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
            tool=coordination_tool,
            slug=slug,
            watcher_launcher=watcher_launcher,
            errors=errors,
            parent_pid=watch_parent_pid,
        )
        watcher_started = pid_file is not None

    # ------------------------------------------------------------------
    # Step 9: Return compact envelope
    # ------------------------------------------------------------------
    actual_backend = (
        phase_outcome.get("backend")
        or (presence_result.backend if presence_result is not None else None)
        or envelope.backend
    )
    actual_transport = (
        phase_outcome.get("transport")
        or (presence_result.transport if presence_result is not None else None)
        or envelope.transport
    )
    actual_resolved_via = (
        "build-loop-internal"
        if actual_backend == "build-loop-local"
        else resolved_via
    )
    return {
        "status": overall_status,
        "active_peers": active_peers,
        "inbox_unread_count": inbox_counts.get("total", 0),
        "inbox_unread_counts": inbox_counts,
        "inbox_latest_messages": inbox_latest_messages,
        "inbox_coverage_incomplete": inbox_coverage_incomplete,
        "inbox_coverage": inbox_coverage,
        "coordination_write_failed": required_coordination_write_failed,
        "watcher_started": watcher_started,
        "coordination_file": coordination_file,
        "session_id": session_id,
        "tool": base_tool,
        "rally_tool": coordination_tool,
        "slug": slug,
        "capability_level": capability_level,
        "resolved_via": actual_resolved_via,
        "backend": actual_backend,
        "transport": actual_transport,
        "inject_readiness": inject_readiness,
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
        "--session-id",
        default=None,
        help="Stable host session key (otherwise resolved from host environment)",
    )
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
    p.add_argument(
        "--watch-parent-pid",
        type=int,
        default=None,
        help=(
            "Long-lived host PID for watcher liveness. SessionStart hooks should "
            "pass their shell parent PID so the watcher outlives session_probe.py "
            "but still exits with the host."
        ),
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
        session_id=args.session_id,
        mode=args.mode,
        start_watch=args.start_watch,
        watch_parent_pid=args.watch_parent_pid,
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
