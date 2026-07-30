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

Peer-held store (2026-07-11): when the canonical store is busy/peer-held
(``promotion_queue.store_busy``), the default ``on_busy="queue"`` ENQUEUES the
milestone into the consumer repo's ``.build-loop/pending-promotions/`` (drained
at the next closeout / SessionStart sweep) instead of silently skipping. The
drain path passes ``on_busy="skip"`` so it never re-queues into the queue it is
draining.

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
import memory_update_ledger as mul  # type: ignore  # noqa: E402
import promotion_queue  # type: ignore  # noqa: E402

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
    # Build the path directly, applying the same safety validation _paths uses.
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


def append_milestone(
    *,
    workdir: str,
    summary: str,
    project: str | None = None,
    commit: str | None = None,
    run_id: str | None = None,
    memory_root: str | None = None,
    on_busy: str = "queue",
) -> dict:
    """Append one milestone record. Returns a result dict; never raises.

    ``on_busy``:
      * ``"queue"`` (default): when the store is peer-held, enqueue into
        ``.build-loop/pending-promotions/`` and return ``{queued: true}``.
      * ``"skip"``: legacy skip-on-busy (used by the drain path so it can not
        re-queue into the queue it is draining).
    """
    workdir = os.path.expanduser(workdir)
    memory_root_path = Path(os.path.expanduser(memory_root or DEFAULT_MEMORY_ROOT))

    # Resolve slug — fail-soft on bad slug
    try:
        slug = _resolve_slug(workdir, project)
    except ValueError as exc:
        return {"appended": False, "reason": f"invalid slug: {exc}"}

    # Resolve commit sha
    if not commit:
        commit = _git_head(workdir)
    if not commit:
        return {"appended": False, "reason": "could not resolve git HEAD (not a git repo or git unavailable)"}

    # Peer-held store → queue instead of skip (unless the drain path asked to skip).
    if on_busy == "queue" and promotion_queue.store_busy(memory_root_path):
        env = promotion_queue.enqueue(
            workdir,
            kind="milestone",
            payload={
                "summary": summary,
                "commit": commit,
                "project": slug,
                "memory_root": str(memory_root_path),
            },
            reason="store peer-held — milestone queued for next closeout drain",
            run_id=run_id,
        )
        return {"appended": False, "queued": env.get("queued", False),
                "reason": env.get("reason"), "queue_id": env.get("id")}

    repo_name = Path(workdir).resolve().name
    milestone_path = _milestones_path(memory_root_path, slug)

    # Fail-soft on unwritable memory root
    try:
        milestone_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"appended": False, "reason": f"cannot create milestone dir: {exc}"}

    try:
        with LockedFile(milestone_path):
            # Idempotency check: skip if last line matches commit+summary
            last = _last_line(milestone_path)
            if last and last.get("commit") == commit and last.get("summary") == summary:
                return {"appended": False, "path": str(milestone_path), "line": json.dumps(last)}

            record: dict = {
                "ts": _iso_utc(),
                "commit": commit,
                "repo": repo_name,
                "summary": summary,
                "run_id": run_id,
            }
            line = json.dumps(record, separators=(",", ":"))

            with milestone_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())

        try:
            mul.append_update(
                memory_root=memory_root_path,
                project=slug,
                lane="milestones",
                action="append",
                path=milestone_path,
                writer="append_milestone.py",
                run_id=run_id,
                source_workdir=workdir,
                source_commit=commit,
                memory_id=f"{slug}:milestone",
                summary=summary,
                metadata={"repo": repo_name},
            )
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: memory_update_ledger append failed: {exc}", file=sys.stderr)

        return {"appended": True, "path": str(milestone_path), "line": line}

    except TimeoutError as exc:
        # A lock timeout is a transient busy condition; queue rather than drop
        # (unless the drain path asked to skip).
        if on_busy == "queue":
            env = promotion_queue.enqueue(
                workdir,
                kind="milestone",
                payload={"summary": summary, "commit": commit, "project": slug,
                         "memory_root": str(memory_root_path)},
                reason=f"lock timeout ({exc}) — milestone queued",
                run_id=run_id,
            )
            return {"appended": False, "queued": env.get("queued", False),
                    "reason": env.get("reason"), "queue_id": env.get("id")}
        return {"appended": False, "reason": f"lock timeout: {exc}"}
    except OSError as exc:
        return {"appended": False, "reason": f"filesystem error: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"appended": False, "reason": f"unexpected error: {exc}"}


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
    parser.add_argument("--on-busy", choices=("queue", "skip"), default="queue",
                        help="Peer-held store behavior: queue (default) or skip.")
    parser.add_argument("--json", action="store_true", help="(no-op; output is always JSON)")
    args = parser.parse_args(argv)

    result = append_milestone(
        workdir=args.workdir,
        summary=args.summary,
        project=args.project,
        commit=args.commit,
        run_id=args.run_id,
        memory_root=args.memory_root,
        on_busy=args.on_busy,
    )
    _output(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
