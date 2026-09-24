#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""tool_state_paths.py — attribute `.build-loop/`/`.rally/` to the session
that created them, so a final report never calls tool state "pre-existing".

WHY
---
Build-loop's SessionStart hooks create ``.build-loop/`` and ``.rally/`` (and
install git hooks) within the first seconds of a session. Downstream agents
then see those paths as untracked in ``git status`` and — with no memory of
having just created them — describe them as "pre-existing untracked folders
I left alone". This module records what the hooks created THIS session so a
later ``report`` call can attribute correctly.

Design
------
``snapshot`` runs early (from a SessionStart hook, in parallel with siblings
that may also create these dirs) and records, per ``TOOL_DIR``, whether it
was ``absent``, ``new`` (created within ``GRACE_SECONDS`` of the snapshot —
covering the sibling-hook race), or ``pre-existing`` (older than that). It
also snapshots git hook files so a later-installed hook (e.g.
``session-start-git-hooks.sh`` running after this one) is still attributable.

Only dirs classified ``absent``/``new`` AND not already tracked by git are
added to ``.git/info/exclude`` — local-only and never affects tracked files.
Per ``gitignore(5)``, a repo ``.gitignore`` pattern takes precedence over
``$GIT_DIR/info/exclude``, so a ``.gitignore`` negation such as
``!/.build-loop/`` (e.g. from ``backlog.py adopt``) still re-includes the
path even after this script excludes it locally. A pre-existing dir is left
alone because the user may intend to commit it (rally treats
``.rally/log/`` as committable).

State is stored under ``<git-common-dir>/build-loop/tool-state/`` — the git
directory, not the working tree — so nothing here is ever visible to
``git status`` or subject to accidental commit.

CLI
---
    tool_state_paths.py snapshot --workdir W [--session-id S] [--now EPOCH]
                                  [--emit-context] [--no-exclude]
    tool_state_paths.py report   --workdir W [--session-id S] [--json]

Both subcommands exit 0 unconditionally (this is hook-adjacent tooling;
failures must never break a session or a report).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

TOOL_DIRS: tuple[str, ...] = (".build-loop", ".rally")
GRACE_SECONDS = 30
_MAX_WALK_ENTRIES = 5000
_SESSION_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

def _git(workdir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True, text=True, check=False,
    )


def _git_resolved_path(workdir: Path, *rev_parse_args: str) -> Path | None:
    """Resolve a ``git rev-parse`` path output to an absolute Path, or None.

    Shared by every git-dir lookup below (``--git-common-dir``,
    ``--git-path hooks``, ``--git-path info/exclude``) — git may print a
    relative path, so this always re-anchors it at ``workdir`` and resolves.
    """
    try:
        cp = _git(workdir, "rev-parse", *rev_parse_args)
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    out = cp.stdout.strip()
    if not out:
        return None
    p = Path(out)
    if not p.is_absolute():
        p = workdir / p
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return None


def _git_common_dir(workdir: Path) -> Path | None:
    """Absolute path to the repo's real (worktree-shared) git dir, or None."""
    return _git_resolved_path(workdir, "--git-common-dir")


def _git_hooks_dir(workdir: Path) -> Path | None:
    return _git_resolved_path(workdir, "--git-path", "hooks")


def _git_info_exclude_path(workdir: Path) -> Path | None:
    return _git_resolved_path(workdir, "--git-path", "info/exclude")


def _is_git_repo(workdir: Path) -> bool:
    try:
        cp = _git(workdir, "rev-parse", "--git-dir")
    except OSError:
        return False
    return cp.returncode == 0


def _tracked_files_under(workdir: Path, rel_dir: str) -> bool:
    """True when git already tracks at least one file under rel_dir."""
    try:
        cp = _git(workdir, "ls-files", "--", rel_dir)
    except OSError:
        return False
    if cp.returncode != 0:
        return False
    return bool(cp.stdout.strip())


# ---------------------------------------------------------------------------
# "oldest evidence" of a directory
# ---------------------------------------------------------------------------

def _oldest_evidence(path: Path, floor: float) -> float | None:
    """Best-effort earliest creation-ish timestamp for ``path``.

    Prefers ``st_birthtime`` (macOS) on the directory itself. Falls back to
    the minimum mtime across the directory and a bounded walk of its
    entries — good enough to distinguish "created this session" from
    "existed already" without an expensive full walk. Early-exits once a
    timestamp older than ``floor`` is found (we only need "is this older
    than the grace window", not the true minimum).
    """
    try:
        st = path.stat()
    except OSError:
        return None
    birth = getattr(st, "st_birthtime", None)
    if birth is not None:
        return float(birth)

    best = st.st_mtime
    if best < floor:
        return best
    seen = 0
    try:
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                seen += 1
                if seen > _MAX_WALK_ENTRIES:
                    return best
                try:
                    mt = os.stat(os.path.join(root, name)).st_mtime
                except OSError:
                    continue
                if mt < best:
                    best = mt
                if best < floor:
                    return best
    except OSError:
        pass
    return best


def _dir_status(path: Path, t0: float) -> str:
    """Return 'absent' | 'new' | 'pre-existing' for a TOOL_DIR at snapshot time."""
    if not path.is_dir():
        return "absent"
    evidence = _oldest_evidence(path, t0 - GRACE_SECONDS)
    if evidence is None:
        return "new"
    return "new" if evidence >= (t0 - GRACE_SECONDS) else "pre-existing"


# ---------------------------------------------------------------------------
# state file paths
# ---------------------------------------------------------------------------

def _sanitize_session_id(session_id: str) -> str:
    cleaned = _SESSION_ID_SAFE_RE.sub("_", session_id.strip())
    return cleaned or "default"


def _state_dir(workdir: Path) -> Path | None:
    common = _git_common_dir(workdir)
    if common is None:
        return None
    return common / "build-loop" / "tool-state"


def _state_path(workdir: Path, session_id: str) -> Path | None:
    d = _state_dir(workdir)
    if d is None:
        return None
    return d / f"{_sanitize_session_id(session_id)}.json"


def _latest_path(workdir: Path) -> Path | None:
    d = _state_dir(workdir)
    if d is None:
        return None
    return d / "latest.json"


def _session_id_from_stdin() -> str | None:
    if sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.read(65536)
    except (OSError, ValueError):
        return None
    if not raw or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("session_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

def _existing_exclude_lines(exclude_path: Path) -> set[str]:
    if not exclude_path.is_file():
        return set()
    try:
        return {ln.rstrip("\n") for ln in exclude_path.read_text(encoding="utf-8").splitlines()}
    except OSError:
        return set()


def _exclude_variants(dirname: str) -> tuple[str, ...]:
    return (f"/{dirname}/", f"{dirname}/", f"/{dirname}", dirname)


def _append_to_exclude(exclude_path: Path, dirnames: list[str]) -> list[str]:
    """Append `/<dir>/` lines for dirnames not already present. Returns appended."""
    if not dirnames:
        return []
    existing = _existing_exclude_lines(exclude_path)
    to_add = [d for d in dirnames if not any(v in existing for v in _exclude_variants(d))]
    if not to_add:
        return []
    try:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        with exclude_path.open("a", encoding="utf-8") as fh:
            current = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
            if current and not current.endswith("\n"):
                fh.write("\n")
            fh.write("# build-loop: tool state created by build-loop/rally hooks (local only)\n")
            for d in to_add:
                fh.write(f"/{d}/\n")
    except OSError:
        return []
    return to_add


_HOOK_MARKER_RE = re.compile(r"build-loop|rally", re.IGNORECASE)


def _hook_has_marker(path: Path) -> bool:
    """True when the hook file's first 64KB names build-loop or rally.

    Timing alone (mtime within the grace window) is not evidence build-loop
    wrote the hook — a human or another tool could install a hook at the
    same moment. Only credit hooks that self-identify as ours.
    """
    try:
        with path.open("rb") as fh:
            chunk = fh.read(65536)
    except OSError:
        return False
    try:
        text = chunk.decode("utf-8", errors="ignore")
    except (UnicodeDecodeError, LookupError):
        return False
    return bool(_HOOK_MARKER_RE.search(text))


def _snapshot_hooks(workdir: Path, t0: float) -> dict[str, dict[str, Any]]:
    hooks_dir = _git_hooks_dir(workdir)
    result: dict[str, dict[str, Any]] = {}
    if hooks_dir is None or not hooks_dir.is_dir():
        return result
    try:
        entries = list(hooks_dir.iterdir())
    except OSError:
        return result
    for entry in entries:
        if not entry.is_file() or entry.name.endswith(".sample"):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        result[entry.name] = {
            "mtime": mtime,
            "installed_this_session": (
                mtime >= (t0 - GRACE_SECONDS) and _hook_has_marker(entry)
            ),
        }
    return result


def cmd_snapshot(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    try:
        if not _is_git_repo(workdir):
            return 0

        t0 = float(args.now) if args.now is not None else time.time()
        session_id = args.session_id or _session_id_from_stdin() or "default"

        dirs: dict[str, dict[str, Any]] = {}
        for name in TOOL_DIRS:
            path = workdir / name
            status = _dir_status(path, t0)
            dirs[name] = {"status": status}

        hooks = _snapshot_hooks(workdir, t0)

        excluded: list[str] = []
        if not args.no_exclude:
            exclude_path = _git_info_exclude_path(workdir)
            if exclude_path is not None:
                candidates = [
                    name for name in TOOL_DIRS
                    if dirs[name]["status"] in ("absent", "new")
                    and not _tracked_files_under(workdir, name)
                ]
                excluded = _append_to_exclude(exclude_path, candidates)

        state = {
            "session_id": session_id,
            "snapshot_at": t0,
            "dirs": dirs,
            "hooks": hooks,
            "excluded": excluded,
        }

        state_path = _state_path(workdir, session_id)
        latest_path = _latest_path(workdir)
        for target in (state_path, latest_path):
            if target is None:
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            except OSError:
                pass

        if args.emit_context:
            _maybe_emit_context(dirs, hooks, excluded)
        return 0
    except Exception:  # noqa: BLE001 - hook path must never raise
        return 0


def _maybe_emit_context(
    dirs: dict[str, dict[str, Any]],
    hooks: dict[str, dict[str, Any]],
    excluded: list[str],
) -> None:
    new_or_absent = [n for n, d in dirs.items() if d["status"] in ("new", "absent")]
    installed_hooks = [n for n, h in hooks.items() if h.get("installed_this_session")]
    if not new_or_absent and not installed_hooks:
        return

    lines: list[str] = []
    for name, d in dirs.items():
        if d["status"] == "new":
            lines.append(f"- `{name}/` was created by build-loop's hooks this session.")
        elif d["status"] == "absent":
            lines.append(
                f"- `{name}/` did not exist before this session — if it appears, "
                f"build-loop/rally hooks created it."
            )
    if installed_hooks:
        lines.append(
            "- git hooks installed this session: " + ", ".join(sorted(installed_hooks))
        )
    if excluded:
        lines.append(
            "- added to .git/info/exclude (local-only): "
            + ", ".join(f"/{n}/" for n in excluded)
        )
    lines.append(
        "Treat these as tool state: omit them from reports or say "
        "\"created by build-loop's hooks\"; never call them pre-existing."
    )
    message = "\n".join(lines)
    payload = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": message}}
    print(json.dumps(payload))


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _load_state(workdir: Path, session_id: str | None) -> dict[str, Any] | None:
    if session_id:
        path = _state_path(workdir, session_id)
        if path is not None and path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
    latest = _latest_path(workdir)
    if latest is not None and latest.is_file():
        try:
            return json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def cmd_report(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    entries: list[dict[str, Any]] = []
    session_id_out = args.session_id or "default"
    snapshot_at: float | None = None

    try:
        state = _load_state(workdir, args.session_id)
        if state is not None:
            session_id_out = state.get("session_id", session_id_out)
            snapshot_at = state.get("snapshot_at")
        dirs_state = state.get("dirs", {}) if state is not None else {}
        excluded_names = state.get("excluded") or [] if state is not None else []

        for name in TOOL_DIRS:
            path = workdir / name
            if not path.is_dir():
                continue
            status = dirs_state.get(name, {}).get("status")
            if status in ("absent", "new"):
                attribution = "created by build-loop's hooks this session"
            elif status == "pre-existing":
                attribution = "pre-existing"
            else:
                attribution = "tool state (build-loop/rally); creation time unknown"
            entries.append({
                "path": f"{name}/", "kind": "dir",
                "attribution": attribution, "excluded": name in excluded_names,
            })

        # Hooks require a prior snapshot to know install timing — with no
        # state, we cannot distinguish "installed this session" from
        # "installed long ago", so hooks are skipped entirely (dirs above
        # still degrade gracefully to "creation time unknown").
        if state is not None:
            hooks_state = state.get("hooks", {})
            hooks_dir = _git_hooks_dir(workdir)
            if hooks_dir is not None and hooks_dir.is_dir():
                try:
                    current_hooks = [
                        e for e in hooks_dir.iterdir()
                        if e.is_file() and not e.name.endswith(".sample")
                    ]
                except OSError:
                    current_hooks = []
                for entry in current_hooks:
                    prior = hooks_state.get(entry.name)
                    # Absent at snapshot time (installed afterward, e.g. by a
                    # sibling SessionStart hook that runs later) or already
                    # flagged installed_this_session at snapshot time. Either
                    # way, the marker check is mandatory — timing alone is
                    # not evidence build-loop/rally wrote the hook.
                    if prior is None:
                        installed_this_session = _hook_has_marker(entry)
                    else:
                        installed_this_session = (
                            bool(prior.get("installed_this_session"))
                            and _hook_has_marker(entry)
                        )
                    if installed_this_session:
                        entries.append({
                            "path": entry.name, "kind": "git-hook",
                            "attribution": "installed by build-loop's hooks this session",
                            "excluded": False,
                        })
    except Exception:  # noqa: BLE001
        pass

    if args.json:
        print(json.dumps({
            "paths": entries, "session_id": session_id_out, "snapshot_at": snapshot_at,
        }, indent=2))
    else:
        if not entries:
            print("(no build-loop/rally tool state found)")
        for e in entries:
            suffix = " (tool state; excluded via .git/info/exclude)" if e["excluded"] else ""
            print(f"- `{e['path']}` — {e['attribution']}{suffix}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Record tool-state dir/hook status for this session")
    snap.add_argument("--workdir", required=True)
    snap.add_argument("--session-id", default=None)
    snap.add_argument("--now", type=float, default=None, help="Override epoch time (tests)")
    snap.add_argument("--emit-context", action="store_true")
    snap.add_argument("--no-exclude", action="store_true")
    snap.set_defaults(func=cmd_snapshot)

    rep = sub.add_parser("report", help="Report attribution for current tool-state paths")
    rep.add_argument("--workdir", required=True)
    rep.add_argument("--session-id", default=None)
    rep.add_argument("--json", action="store_true")
    rep.set_defaults(func=cmd_report)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
