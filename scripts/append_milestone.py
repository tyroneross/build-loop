#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Append one milestone record to a project's append-only milestones.jsonl log.

Called at Review-G to stamp what shipped and the repo HEAD sha. The log is
never rewritten or truncated — append-only by construction, which prevents
rewrite-drift (the failure mode where "current state" files rot because
writers overwrite without reading first).

CONTRACT (frozen — sibling staleness-check reads this):
  Append-only JSONL at:
    <memory-root>/projects/<slug>/milestones.jsonl
  Each line:
    {"ts": iso8601, "commit": <repo HEAD sha>, "repo": <dir name>,
     "summary": <what shipped>, "run_id": <id|null>}

Concurrency: fcntl.flock(LOCK_EX) on a sidecar .lock file — same pattern
as atomic_io.LockedFile used by memory_index.py and write_run_entry.py.

Idempotency: if the last line already has the same commit AND summary, skip
with appended=false.

Fail-soft: non-git workdir, unwritable memory-root, or any unexpected error
produces {"appended": false, "reason": "..."} on stdout and exits 0.

Stdlib only. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _paths import derive_slug_from_cwd, project_root, _safe_project_tag  # type: ignore  # noqa: E402
from atomic_io import LockedFile  # type: ignore  # noqa: E402

DEFAULT_MEMORY_ROOT = "~/dev/git-folder/build-loop-memory"
MILESTONES_FILENAME = "milestones.jsonl"


def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_head(workdir: str) -> str | None:
    """Return HEAD sha for workdir, or None if not a git repo / git unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _resolve_slug(workdir: str, project_arg: str | None) -> str:
    if project_arg:
        # Validate user-supplied slug (each segment)
        for seg in project_arg.split("/"):
            _safe_project_tag(seg)
        return project_arg
    return derive_slug_from_cwd(Path(workdir))


def _milestones_path(memory_root: Path, slug: str) -> Path:
    proj_dir = project_root.__module__ and None  # just compute manually to avoid side-effects
    # Reuse _paths.project_root logic: <memory_root>/projects/<slug>/
    # But project_root() reads env vars for memory_store_root; we have an explicit root.
    # Build the path directly, applying the same safety validation.
    parts = slug.split("/")
    for part in parts:
        _safe_project_tag(part)
    proj_dir_path = memory_root / "projects" / Path(*parts)
    return proj_dir_path / MILESTONES_FILENAME


def _last_line(path: Path) -> dict | None:
    """Return parsed last non-empty line of a JSONL file, or None."""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            last: dict | None = None
            for raw in f:
                raw = raw.strip()
                if raw:
                    try:
                        last = json.loads(raw)
                    except json.JSONDecodeError:
                        last = None
        return last
    except OSError:
        return None


def _output(data: dict) -> None:
    print(json.dumps(data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append an append-only milestone record to milestones.jsonl"
    )
    parser.add_argument("--workdir", required=True, help="Path to the repo working directory")
    parser.add_argument("--summary", required=True, help="What shipped this run")
    parser.add_argument("--project", default=None, help="Slug override (default: derive from --workdir)")
    parser.add_argument("--commit", default=None, help="Commit sha override (default: git rev-parse HEAD)")
    parser.add_argument("--run-id", default=None, dest="run_id", help="build-loop run ID")
    parser.add_argument("--memory-root", default=DEFAULT_MEMORY_ROOT, dest="memory_root",
                        help="Override build-loop-memory root path")
    parser.add_argument("--json", action="store_true", help="(no-op; output is always JSON)")
    args = parser.parse_args(argv)

    workdir = os.path.expanduser(args.workdir)
    memory_root = Path(os.path.expanduser(args.memory_root))

    # Resolve slug — fail-soft on bad slug
    try:
        slug = _resolve_slug(workdir, args.project)
    except ValueError as exc:
        _output({"appended": False, "reason": f"invalid slug: {exc}"})
        return 0

    # Resolve commit sha
    commit = args.commit
    if not commit:
        commit = _git_head(workdir)
    if not commit:
        _output({"appended": False, "reason": "could not resolve git HEAD (not a git repo or git unavailable)"})
        return 0

    repo_name = Path(workdir).resolve().name

    milestone_path = _milestones_path(memory_root, slug)

    # Fail-soft on unwritable memory root
    try:
        milestone_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _output({"appended": False, "reason": f"cannot create milestone dir: {exc}"})
        return 0

    lock_path = milestone_path.with_suffix(milestone_path.suffix + ".lock")

    try:
        with LockedFile(milestone_path):
            # Idempotency check: skip if last line matches commit+summary
            last = _last_line(milestone_path)
            if last and last.get("commit") == commit and last.get("summary") == args.summary:
                _output({
                    "appended": False,
                    "path": str(milestone_path),
                    "line": json.dumps(last),
                })
                return 0

            record: dict = {
                "ts": _iso_utc(),
                "commit": commit,
                "repo": repo_name,
                "summary": args.summary,
                "run_id": args.run_id,
            }
            line = json.dumps(record, separators=(",", ":"))

            with milestone_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())

        _output({
            "appended": True,
            "path": str(milestone_path),
            "line": line,
        })
        return 0

    except TimeoutError as exc:
        _output({"appended": False, "reason": f"lock timeout: {exc}"})
        return 0
    except OSError as exc:
        _output({"appended": False, "reason": f"filesystem error: {exc}"})
        return 0
    except Exception as exc:  # noqa: BLE001
        _output({"appended": False, "reason": f"unexpected error: {exc}"})
        return 0


if __name__ == "__main__":
    sys.exit(main())
