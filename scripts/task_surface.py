#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Produce the canonical active task view from existing build-loop surfaces.
#   application: planning
#   status: active
"""Derived active task surface for build-loop."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _paths import memory_store_root  # type: ignore  # noqa: E402
from project_resolver import resolve_project  # type: ignore  # noqa: E402

ACTIVE_DIRS = [
    ("ux_queue", ".build-loop/ux-queue", "active-iterate", 30),
    ("issues", ".build-loop/issues", "open-issue", 40),
    ("followup", ".build-loop/followup", "deferred-active", 50),
    ("backlog", ".build-loop/backlog", "repo-backlog", 60),
]
PROPOSAL_DIR = ("proposals", ".build-loop/proposals", "candidate", 90)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _markdown_title(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
            if stripped.startswith("name:"):
                return stripped.split(":", 1)[1].strip()
    except OSError:
        pass
    return path.stem.replace("-", " ")


def _unchecked_items(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- [ ]"):
            items.append(stripped[5:].strip())
    return items


def _item(
    *,
    surface: str,
    lifecycle: str,
    priority: int,
    title: str,
    path: str,
    item_id: str,
) -> dict[str, Any]:
    return {
        "surface": surface,
        "lifecycle": lifecycle,
        "priority": priority,
        "title": title,
        "path": path,
        "id": item_id,
    }


def execution_items(workdir: Path) -> list[dict[str, Any]]:
    state = _read_json(workdir / ".build-loop" / "state.json")
    execution = state.get("execution") if isinstance(state.get("execution"), dict) else {}
    per_commit = state.get("perCommit") if isinstance(state.get("perCommit"), dict) else {}
    rows: list[dict[str, Any]] = []
    for key, lifecycle, priority in (
        ("in_flight_chunks", "in-flight", 10),
        ("queued_chunks", "queued", 20),
        ("in_flight", "in-flight", 10),
        ("queued", "queued", 20),
    ):
        values = execution.get(key) or per_commit.get(key) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            rows.append(
                _item(
                    surface=f"state.{key}",
                    lifecycle=lifecycle,
                    priority=priority,
                    title=str(value),
                    path=str(workdir / ".build-loop" / "state.json"),
                    item_id=str(value),
                )
            )
    return rows


def markdown_surface_items(
    workdir: Path,
    *,
    include_proposals: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    dirs = list(ACTIVE_DIRS)
    if include_proposals:
        dirs.append(PROPOSAL_DIR)
    for surface, rel, lifecycle, priority in dirs:
        root = workdir / rel
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            unchecked = _unchecked_items(path)
            if unchecked:
                for idx, title in enumerate(unchecked, start=1):
                    rows.append(
                        _item(
                            surface=surface,
                            lifecycle=lifecycle,
                            priority=priority,
                            title=title,
                            path=str(path),
                            item_id=f"{path.stem}:{idx}",
                        )
                    )
            else:
                rows.append(
                    _item(
                        surface=surface,
                        lifecycle=lifecycle,
                        priority=priority,
                        title=_markdown_title(path),
                        path=str(path),
                        item_id=path.stem,
                    )
                )
    return rows


def memory_backlog_items(
    *,
    workdir: Path,
    memory_root: Path | None = None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    root = memory_root or memory_store_root()
    slug = project or resolve_project(workdir)
    backlog = root / "projects" / slug / "backlog.md"
    if not backlog.is_file():
        return []
    unchecked = _unchecked_items(backlog)
    titles = unchecked or [_markdown_title(backlog)]
    return [
        _item(
            surface="memory_backlog",
            lifecycle="durable-project-backlog",
            priority=80,
            title=title,
            path=str(backlog),
            item_id=f"{backlog.stem}:{idx}",
        )
        for idx, title in enumerate(titles, start=1)
    ]


def collect_task_surface(
    *,
    workdir: Path,
    memory_root: Path | None = None,
    include_memory: bool = True,
    include_proposals: bool = False,
    max_items: int = 100,
) -> dict[str, Any]:
    wd = workdir.expanduser().resolve()
    items = execution_items(wd) + markdown_surface_items(
        wd,
        include_proposals=include_proposals,
    )
    if include_memory:
        items.extend(memory_backlog_items(workdir=wd, memory_root=memory_root))
    items.sort(key=lambda row: (row["priority"], row["surface"], row["id"]))
    counts: dict[str, int] = {}
    for row in items:
        counts[row["surface"]] = counts.get(row["surface"], 0) + 1
    return {
        "action": "task-surface",
        "workdir": str(wd),
        "decision": "derived-active-view-no-new-ledger",
        "open_count": len(items),
        "counts_by_surface": counts,
        "items": items[:max_items],
        "truncated": len(items) > max_items,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--memory-root")
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--include-proposals", action="store_true")
    parser.add_argument("--max-items", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = collect_task_surface(
        workdir=Path(args.workdir),
        memory_root=Path(args.memory_root).expanduser().resolve() if args.memory_root else None,
        include_memory=not args.no_memory,
        include_proposals=args.include_proposals,
        max_items=max(1, args.max_items),
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"{payload['open_count']} open task(s)")
        for row in payload["items"]:
            print(f"- [{row['surface']}] {row['title']} ({row['path']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
