#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point hook helpers used by the shell hook wrappers.

The shell files in ``hooks/`` are compatibility entrypoints. The behavior
lives here so the future agent-rally-point plugin can carry one namespaced
implementation instead of inline Python snippets embedded in build-loop
hook scripts.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = _HERE.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from rally_point import actor_identity, channel_paths, checkpoint, presence, revision
    from rally_point.backend_adapter import (
        acknowledge,
        audit_next,
        native_room_summary,
        resolve_context,
        room_snapshot,
        write_presence as write_backend_presence,
    )
except ImportError:
    from . import actor_identity, channel_paths, checkpoint, presence, revision
    from .backend_adapter import (
        acknowledge,
        audit_next,
        native_room_summary,
        resolve_context,
        room_snapshot,
        write_presence as write_backend_presence,
    )


# Throttle window — skip presence write when a fresh record for THIS
# session/tool/repo room already exists within this many seconds. Cheap
# stat+read, no flock. Single env override for tests; 60s default.
_PRE_EDIT_THROTTLE_SECONDS = 60.0
_NATIVE_THROTTLE_RETENTION_SECONDS = 24 * 60 * 60
_NATIVE_THROTTLE_MAX_ENTRIES = 128


def _throttle_seconds() -> float:
    raw = os.environ.get("BUILD_LOOP_RALLY_PRE_EDIT_THROTTLE_SECONDS")
    if not raw:
        return _PRE_EDIT_THROTTLE_SECONDS
    try:
        v = float(raw)
        return v if v >= 0 else _PRE_EDIT_THROTTLE_SECONDS
    except (TypeError, ValueError):
        return _PRE_EDIT_THROTTLE_SECONDS


def _is_git_repo(path: Path) -> bool:
    """Return True iff ``path`` is inside a git repo.

    Pure boolean — no slug derivation. Used as the SessionStart guard to
    avoid misregistering presence in a home / non-repo launch dir.
    Best-effort; any error returns False (fail-closed for the write side).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


# Match a leading `cd <path> && ...` / `cd <path>; ...` / `cd <path>\n...`.
# Captures `<path>` (which may be quoted). re.match() anchors at string start,
# so re.MULTILINE is a no-op here — omitted to avoid misleading readers into
# thinking mid-string `cd` lines are matched.
_CD_PREFIX_RE = re.compile(
    r"""^\s*cd\s+(?P<path>'(?:[^']|\\')*'|"(?:[^"\\]|\\.)*"|\S+)\s*(?:&&|;|\n|$)""",
)


def _extract_cd_target(command: str) -> str | None:
    """Return the path argument of a leading ``cd <path>`` in ``command``.

    Returns ``None`` when the command does not start with ``cd``. Strips
    matching single/double quotes. ``shlex`` is used to honour escapes.
    """
    if not command:
        return None
    m = _CD_PREFIX_RE.match(command)
    if not m:
        return None
    raw = m.group("path")
    try:
        parts = shlex.split(raw)
    except ValueError:
        return raw.strip("'\"") or None
    if not parts:
        return None
    return parts[0]


def _git_toplevel(path: Path) -> Path | None:
    """Return git toplevel for ``path`` or ``None`` if not in a repo.

    Falls back to the canonical repo root (worktree-collapsed) when the
    discovery bridge would otherwise canonicalize. We use the toplevel of
    the operative directory so a worktree's own checkout joins its own
    canonical channel (the bridge collapses worktree → main inside
    ``resolve``).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if not out:
        return None
    return Path(out)


def resolve_operative_repo(
    workdir: Path,
    *,
    file_path: str | None = None,
    command: str | None = None,
) -> Path | None:
    """Resolve the OPERATIVE repo for a tool-use event.

    Resolution order (MECE):
        1. ``file_path`` (Edit/Write): git-toplevel of the file's parent
           dir (the file itself need not exist yet — its parent does).
        2. ``command`` (Bash) starting with ``cd <path>``: git-toplevel of
           the cd target.
        3. ``command`` (Bash) without a leading cd: git-toplevel of
           ``workdir`` (the session's reported cwd).
        4. Fallback: git-toplevel of ``workdir``.

    Returns ``None`` when no resolution lands in a git repo. Never raises.
    """
    candidates: list[Path] = []
    if file_path:
        fp = Path(os.path.expanduser(file_path))
        if not fp.is_absolute():
            fp = (workdir / fp)
        # The file may not exist yet (Write creates new files), but the
        # parent dir always does for a valid edit target.
        try:
            parent = fp.parent.resolve()
        except (OSError, RuntimeError):
            parent = fp.parent
        candidates.append(parent)
    if command:
        target = _extract_cd_target(command)
        if target:
            tp = Path(os.path.expanduser(target))
            if not tp.is_absolute():
                tp = (workdir / tp)
            candidates.append(tp)
    candidates.append(workdir)

    for cand in candidates:
        try:
            cand_resolved = cand if cand.exists() else cand
        except OSError:
            continue
        top = _git_toplevel(cand_resolved)
        if top is not None:
            return top
    return None


def _heartbeat_session_id(slug: str) -> str:
    """Return a stable local-fallback id for the shared advisory heartbeat.

    Stable per host+slug (NO PID component). Each tool-use fires a fresh
    ``python3`` subprocess with a different PID, so a PID in the id would
    yield a different presence file every invocation and defeat the
    throttle. Native Rally does not use this shared id because its status,
    claims, and handoffs require one actor per live session.
    """
    host = socket.gethostname() or "host"
    safe_host = "".join(c if c.isalnum() or c in "-_." else "-" for c in host)[:40]
    return f"claude-code-hb-{slug.replace('/', '_')}-{safe_host}"


def _fresh_presence_exists(
    channel_dir: Path, session_id: str, *, throttle_s: float, now: float
) -> bool:
    """Return True iff ``session_id`` has a presence record fresher than
    ``throttle_s``.

    Cheap stat+read — NO flock. Reads the single ``sessions/<id>.json``
    file; missing file or any parse failure → not fresh. The throttle
    window is the only thing this check covers; staleness reaping is the
    SessionStart reaper's job.
    """
    p = channel_dir / "sessions" / f"{session_id}.json"
    try:
        st = p.stat()
    except (FileNotFoundError, OSError):
        return False
    # mtime is updated atomically on every ``_atomic_write`` in presence.py.
    age = now - st.st_mtime
    return age < throttle_s


def _native_throttle_path(workdir: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id).strip("._") or "unknown"
    return workdir / ".build-loop" / "rally-hook-throttle" / f"{safe}.stamp"


def _fresh_native_throttle(
    workdir: Path,
    session_id: str,
    *,
    throttle_s: float,
    now: float,
) -> bool:
    try:
        return now - _native_throttle_path(workdir, session_id).stat().st_mtime < throttle_s
    except (FileNotFoundError, OSError):
        return False


def _touch_native_throttle(workdir: Path, session_id: str, *, now: float) -> None:
    path = _native_throttle_path(workdir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    os.utime(path, (now, now))
    _prune_native_throttles(
        workdir,
        current_session_id=session_id,
        throttle_s=_throttle_seconds(),
        now=now,
    )


def _prune_native_throttles(
    workdir: Path,
    *,
    current_session_id: str,
    throttle_s: float,
    now: float,
) -> None:
    """Bound stale per-session hook stamps without deleting live throttles."""
    directory = _native_throttle_path(workdir, current_session_id).parent
    current = _native_throttle_path(workdir, current_session_id)
    try:
        entries = [path for path in directory.glob("*.stamp") if path.is_file()]
    except OSError:
        return

    fresh_cutoff = now - max(0.0, throttle_s)
    retention_cutoff = now - max(
        _NATIVE_THROTTLE_RETENTION_SECONDS,
        max(0.0, throttle_s) * 4,
    )
    observed: list[tuple[Path, float]] = []
    for path in entries:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if path != current and mtime < retention_cutoff:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        observed.append((path, mtime))

    overflow = len(observed) - _NATIVE_THROTTLE_MAX_ENTRIES
    if overflow <= 0:
        return
    removable = sorted(
        (
            (path, mtime)
            for path, mtime in observed
            if path != current and mtime <= fresh_cutoff
        ),
        key=lambda item: (item[1], item[0].name),
    )
    for path, _mtime in removable[:overflow]:
        try:
            path.unlink()
        except OSError:
            pass


def _session_start_id(slug: str) -> str:
    return f"sessionstart-{slug.replace('/', '_')}"


def _surface_coordination_refusal(context, operation: str) -> None:
    """Expose a loud refusal without consulting Build Loop private state."""
    envelope = context.envelope
    reason = envelope.refusal_reason or "no coordination authority is available"
    remedy = envelope.refusal_remedy or "restore coordination, then retry"
    print(
        f"rally: {operation} refused: {reason}; remedy: {remedy}",
        file=sys.stderr,
    )


def _resolve_existing_channel(workdir: Path) -> tuple[str, Path] | None:
    context = resolve_context(workdir)
    if context.native or context.envelope.backend != "build-loop-local":
        return None
    channel_dir = context.local_channel_dir
    if not channel_dir.exists():
        return None
    return context.envelope.app_slug, channel_dir


def session_start_restore(
    workdir: Path,
    *,
    verbose: bool = True,
    tool: str = "claude_code",
    session_id: str | None = None,
) -> int:
    context = resolve_context(workdir)
    if context.native:
        native_identity = actor_identity.resolve_identity(tool, session_id)
        result = room_snapshot(
            context,
            actor=native_identity.native_tool,
            readers=True,
        )
        if not result.ok or not verbose:
            return 0
        summary = native_room_summary(result)
        squads = summary.get("squads") if isinstance(summary, dict) else []
        peers = [
            item for item in (squads if isinstance(squads, list) else [])
            if isinstance(item, dict)
            and item.get("tool") != native_identity.native_tool
            and item.get("status") == "active"
        ]
        if peers:
            print(
                f"Rally Point: {context.envelope.app_slug} - "
                f"{len(peers)} live peer(s)"
            )
        return 0
    if context.envelope.backend != "build-loop-local":
        _surface_coordination_refusal(context, "session checkpoint read")
        return 0
    resolved = _resolve_existing_channel(workdir)
    if resolved is None:
        return 0
    slug, channel_dir = resolved
    env = checkpoint.checkpoint_read(
        channel_dir,
        session_id=_session_start_id(slug),
        my_files=[],
    )
    if not verbose or not env.get("changed"):
        return 0
    bits = [f"{len(env.get('new_changes', []))} change(s)"]
    peers = len(env.get("active_peers", []))
    if peers:
        bits.append(f"{peers} live peer(s)")
    reactions = {r.get("type") for r in env.get("reactions", [])}
    if "reinstall" in reactions:
        bits.append("dep-change: reinstall")
    if "re-baseline" in reactions:
        bits.append("arch changed: re-baseline")
    if "soft-claim" in reactions:
        bits.append("peer owns files (warning)")
    print(f"Rally Point: {slug} - " + "; ".join(bits))
    return 0


def session_start_advance(
    workdir: Path,
    *,
    tool: str = "claude_code",
    session_id: str | None = None,
) -> int:
    context = resolve_context(workdir)
    if context.native:
        native_identity = actor_identity.resolve_identity(tool, session_id)
        acknowledge(
            context,
            tool=native_identity.native_tool,
            session_id=native_identity.session_id,
        )
        return 0
    if context.envelope.backend != "build-loop-local":
        _surface_coordination_refusal(context, "session checkpoint advance")
        return 0
    resolved = _resolve_existing_channel(workdir)
    if resolved is None:
        return 0
    slug, channel_dir = resolved
    checkpoint.checkpoint_read(
        channel_dir,
        session_id=_session_start_id(slug),
        my_files=[],
    )
    return 0


def _rally_quiet() -> bool:
    """Return True when the user has suppressed rally status lines."""
    return os.environ.get("BUILD_LOOP_RALLY_QUIET", "0") == "1"


def pre_edit_hint(
    workdir: Path,
    *,
    tool: str = "claude_code",
    session_id: str | None = None,
) -> int:
    context = resolve_context(workdir)
    if context.native:
        native_identity = actor_identity.resolve_identity(tool, session_id)
        result = audit_next(context, tool=native_identity.native_tool)
        if result.ok and not _rally_quiet():
            data = result.payload.get("data") if isinstance(result.payload, dict) else {}
            next_data = data.get("next") if isinstance(data, dict) else {}
            actionable = next_data.get("actionable") if isinstance(next_data, dict) else None
            if actionable:
                print(f"rally: native coordination pending ({context.envelope.app_slug})")
        return 0
    if context.envelope.backend != "build-loop-local":
        _surface_coordination_refusal(context, "pre-tool coordination read")
        return 0
    resolved = _resolve_existing_channel(workdir)
    if resolved is None:
        return 0
    slug, channel_dir = resolved
    current = revision.read_revision(channel_dir)
    session_id = _session_start_id(slug)
    seen = presence.get_cursor(channel_dir, session_id).get("revision", 0)
    if current > seen and not _rally_quiet():
        print(f"rally: peer ahead → checkpoint ({slug} rev {seen}→{current})")
    return 0


def pre_edit_join(
    workdir: Path,
    *,
    file_path: str | None = None,
    command: str | None = None,
    tool: str = "claude_code",
    session_id: str | None = None,
    model: str = "unknown",
    now: float | None = None,
) -> int:
    """Per-tool-use throttled re-join of the operative repo's Rally room.

    Codex-parity: SessionStart misregisters (or no-ops) when the agent
    launches outside the repo it actually edits. This per-tool-use re-join
    resolves the operative repo from the event payload and registers /
    refreshes presence in THAT repo's ``.rally`` room — but only when a
    fresh presence record does not already exist (throttle), so the hot
    path stays a cheap stat+read.

    Contract:
        * Never blocks, never raises (fire-and-forget; advisory).
        * No lock on the hot path. A presence stat fresher than the
          throttle window short-circuits.
        * No-op when the operative repo cannot be resolved (Bash with no
          ``cd`` target outside a repo; Edit with a file_path outside any
          repo; etc.).
        * No-op when the channel cannot be created or written (graceful
          absence — empty errors, ``exit 0``).

    The function does NOT print on the success path; it is meant to be
    invisible. ``pre_edit_hint`` retains its one-line revision-advanced
    surface, called separately.
    """
    op = resolve_operative_repo(workdir, file_path=file_path, command=command)
    if op is None:
        return 0
    try:
        context = resolve_context(op)
        slug = context.envelope.app_slug
    except Exception:
        return 0
    if not slug or slug == "_unscoped":
        return 0
    if context.native:
        native_identity = actor_identity.resolve_identity(tool, session_id)
        resolved_session_id = native_identity.session_id
        t = now if now is not None else time.time()
        if _fresh_native_throttle(
            op,
            resolved_session_id,
            throttle_s=_throttle_seconds(),
            now=t,
        ):
            return 0
        try:
            result = write_backend_presence(
                context,
                session_id=resolved_session_id,
                tool=native_identity.native_tool,
                local_session_id=resolved_session_id,
                local_tool=native_identity.base_tool,
                model=model,
                run_id="pre-edit",
                app_slug=slug,
                phase="pre-edit-join",
                files_in_flight=[],
                cwd=str(op),
                tier="executing",
            )
        except Exception:
            return 0
        if result.ok:
            try:
                _touch_native_throttle(op, resolved_session_id, now=t)
            except Exception:
                pass
            if not _rally_quiet():
                print(f"rally: joined {slug} room")
        return 0

    if context.envelope.backend != "build-loop-local":
        _surface_coordination_refusal(context, "pre-tool presence join")
        return 0

    # Local fallback owns its own apps-root channel.
    session_id = _heartbeat_session_id(slug)
    try:
        channel_dir = context.local_channel_dir
        channel_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return 0
    t = now if now is not None else time.time()
    if _fresh_presence_exists(
        channel_dir, session_id, throttle_s=_throttle_seconds(), now=t
    ):
        return 0
    try:
        written = presence.write_presence(
            channel_dir,
            session_id=session_id,
            tool=tool,
            model=model,
            run_id="pre-edit",
            app_slug=slug,
            phase="pre-edit-join",
            files_in_flight=[],
            cwd=str(op),
        )
    except Exception:
        return 0
    if not written:
        return 0
    # Print a concise status line on every real write (not throttled).
    # Gate behind BUILD_LOOP_RALLY_QUIET=1 to suppress if desired.
    if not _rally_quiet():
        print(f"rally: joined {slug} room")
    return 0


def session_start_safe(
    workdir: Path,
    *,
    verbose: bool = True,
    tool: str = "claude_code",
    session_id: str | None = None,
) -> int:
    """SessionStart restore wrapper that no-ops outside a git repo.

    Codex-parity: when ``CLAUDE_PROJECT_DIR`` is unset and the launch cwd
    is not a git repo (e.g. ``$HOME``), the old probe wrote presence to
    the WRONG room. The per-tool-use re-join (pre_edit_join) now recovers
    the operative repo; this guard ensures SessionStart does not
    misregister in the home/_unscoped room first.

    Returns 0 (no-op) when ``workdir`` is not a git repo. Otherwise
    delegates to ``session_start_restore``.
    """
    if not _is_git_repo(workdir):
        return 0
    return session_start_restore(
        workdir,
        verbose=verbose,
        tool=tool,
        session_id=session_id,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="subcommand", required=True)
    for name in (
        "session-start-restore",
        "session-start-advance",
        "session-start-safe",
        "pre-edit",
    ):
        sp = sub.add_parser(name)
        sp.add_argument("--workdir", default=".")
        if name.startswith("session-start"):
            sp.add_argument("--tool", default="claude_code")
            sp.add_argument("--session-id", default=None)
        if name in ("session-start-restore", "session-start-safe"):
            sp.add_argument("--verbose", action="store_true")
        if name == "pre-edit":
            sp.add_argument("--file-path", default=None, dest="file_path")
            # ``--command`` carries the Bash payload. Use ``dest="bash_command"``
            # so it does not shadow the subparser ``dest="subcommand"``.
            sp.add_argument("--command", default=None, dest="bash_command")
            sp.add_argument("--tool", default="claude_code")
            sp.add_argument("--session-id", default=None)
            sp.add_argument("--model", default="unknown")
            sp.add_argument(
                "--skip-join",
                action="store_true",
                help="Skip the throttled operative-repo join (legacy-hint only).",
            )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # NOTE: do NOT resolve workdir for ``session-start-safe`` — we need
    # the raw value to decide whether the launch cwd is a git repo. The
    # other subcommands resolve to a canonical absolute path.
    raw_workdir = Path(args.workdir).expanduser()
    workdir = raw_workdir if not raw_workdir.exists() else raw_workdir.resolve()
    try:
        if args.subcommand == "session-start-restore":
            return session_start_restore(
                workdir,
                verbose=args.verbose,
                tool=args.tool,
                session_id=args.session_id,
            )
        if args.subcommand == "session-start-advance":
            return session_start_advance(
                workdir,
                tool=args.tool,
                session_id=args.session_id,
            )
        if args.subcommand == "session-start-safe":
            return session_start_safe(
                workdir,
                verbose=args.verbose,
                tool=args.tool,
                session_id=args.session_id,
            )
        if args.subcommand == "pre-edit":
            # Legacy revision-advanced hint (workdir's existing channel).
            try:
                pre_edit_hint(
                    workdir,
                    tool=args.tool,
                    session_id=args.session_id,
                )
            except Exception:
                pass
            # New: throttled operative-repo join.
            if not args.skip_join:
                pre_edit_join(
                    workdir,
                    file_path=args.file_path,
                    command=args.bash_command,
                    tool=args.tool,
                    session_id=args.session_id,
                    model=args.model,
                )
            return 0
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
