#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Validate + stamp a project's canonical CURRENT.md status file against repo HEAD.
#   application: review-g-status
#   status: active
"""status_refresh.py — keep a project's canonical CURRENT.md status file honest.

The per-project status file lives at
``<memory-root>/projects/<slug>/status/CURRENT.md`` and carries ``as_of_commit``
+ ``last_verified_at``. After a build lands commits, ``as_of_commit`` falls behind
repo HEAD and the file is STALE. This script:

  * **detects** staleness (``as_of_commit`` vs the project repo's HEAD),
  * **reports** the file's embedded "Validation evidence" commands (it NEVER
    executes them — running shell parsed out of a markdown file is an injection
    risk; the caller runs them),
  * with ``--stamp`` **rewrites** ``as_of_commit`` + ``last_verified_at`` to HEAD/now
    (use only after the content was re-verified at that HEAD).

v1 deliberately validates/stamps an EXISTING CURRENT.md — it does not generate one.
Stdlib-only, fail-soft: a non-git workdir, a missing file, or any unexpected error
yields a structured ``ok: false`` payload, never a crash.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _paths import memory_store_root  # type: ignore  # noqa: E402
from project_resolver import resolve_project  # type: ignore  # noqa: E402

_AS_OF_RE = re.compile(r"\*\*as_of_commit:\*\*\s*`([0-9a-fA-F]+)`")
_VERIFIED_RE = re.compile(r"\*\*last_verified_at:\*\*\s*(\S+)")


def current_md_path(workdir: Path, memory_root: Path | None, project: str | None) -> Path:
    root = memory_root or memory_store_root()
    slug = project or resolve_project(workdir)
    return root / "projects" / slug / "status" / "CURRENT.md"


def repo_head(workdir: Path) -> str | None:
    """Short HEAD sha of the PROJECT repo (the repo CURRENT.md describes)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--short", "HEAD"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except (subprocess.SubprocessError, OSError):
        return None


def parse_status(text: str) -> dict[str, str | None]:
    as_of = _AS_OF_RE.search(text)
    verified = _VERIFIED_RE.search(text)
    return {
        "as_of_commit": as_of.group(1) if as_of else None,
        "last_verified_at": verified.group(1) if verified else None,
    }


def validation_commands(text: str) -> list[str]:
    """Extract the fenced commands under a '## Validation evidence' heading (report-only)."""
    lines = text.splitlines()
    cmds: list[str] = []
    in_section = False
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.lower().startswith("## validation")
            continue
        if not in_section:
            continue
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and stripped and not stripped.startswith("#"):
            cmds.append(stripped)
    return cmds


def stamp_text(text: str, head: str, today: str) -> str:
    text = _AS_OF_RE.sub(f"**as_of_commit:** `{head}`", text, count=1)
    text = _VERIFIED_RE.sub(f"**last_verified_at:** {today}", text, count=1)
    return text


def refresh(
    *,
    workdir: Path,
    memory_root: Path | None = None,
    project: str | None = None,
    stamp: bool = False,
    today: str | None = None,
) -> dict[str, Any]:
    wd = workdir.expanduser().resolve()
    path = current_md_path(wd, memory_root, project)
    if not path.is_file():
        return {
            "ok": False,
            "reason": "no_status_file",
            "expected_path": str(path),
            "hint": "Seed status/CURRENT.md before status_refresh can validate it (v1 does not generate).",
        }
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "reason": "unreadable", "path": str(path), "error": str(exc)}

    parsed = parse_status(text)
    head = repo_head(wd)
    as_of = parsed["as_of_commit"]
    stale = bool(head and as_of and head != as_of)
    result: dict[str, Any] = {
        "ok": True,
        "path": str(path),
        "as_of_commit": as_of,
        "head": head,
        "stale": stale,
        "last_verified_at": parsed["last_verified_at"],
        "validation_commands": validation_commands(text),
        "stamped": False,
    }
    if head is None:
        result["head_warning"] = "could not resolve project repo HEAD (non-git workdir?)"
    if stamp and head:
        stamp_today = today or _utc_now()
        try:
            path.write_text(stamp_text(text, head, stamp_today), encoding="utf-8")
            result["stamped"] = True
            result["stale"] = False
            result["as_of_commit"] = head
            result["last_verified_at"] = stamp_today
        except OSError as exc:
            result["stamp_error"] = str(exc)
    return result


def _utc_now() -> str:
    # Late import keeps the module importable where datetime stubbing is in play.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=".", help="The PROJECT repo CURRENT.md describes.")
    parser.add_argument("--memory-root")
    parser.add_argument("--project", help="Override the resolved slug.")
    parser.add_argument("--stamp", action="store_true", help="Rewrite as_of_commit + last_verified_at to HEAD/now.")
    parser.add_argument("--today", help="Override the stamp timestamp (testing).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = refresh(
        workdir=Path(args.workdir),
        memory_root=Path(args.memory_root).expanduser().resolve() if args.memory_root else None,
        project=args.project,
        stamp=args.stamp,
        today=args.today,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif not payload.get("ok"):
        print(f"status: {payload.get('reason')} ({payload.get('expected_path', payload.get('path',''))})")
    elif payload["stamped"]:
        print(f"stamped CURRENT.md -> as_of_commit {payload['as_of_commit']} @ {payload['last_verified_at']}")
    elif payload["stale"]:
        print(f"STALE: CURRENT.md as_of {payload['as_of_commit']} but HEAD is {payload['head']} — re-verify then --stamp")
    else:
        print(f"current: CURRENT.md as_of {payload['as_of_commit']} matches HEAD")
    # Exit 0 always (advisory/fail-soft); callers read the JSON for the verdict.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
