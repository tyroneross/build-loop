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
import re
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

SURFACE_RANK_BONUS = {
    "state.in_flight_chunks": 35,
    "state.in_flight": 35,
    "state.queued_chunks": 25,
    "state.queued": 25,
    "status_current": 22,
    "ux_queue": 18,
    "issues": 16,
    "followup": 10,
    "backlog": 5,
    "memory_backlog": 2,
    "proposals": -10,
}

SURFACE_ACTION = {
    "state.in_flight_chunks": "continue_in_flight",
    "state.in_flight": "continue_in_flight",
    "state.queued_chunks": "dispatch_next",
    "state.queued": "dispatch_next",
    "status_current": "address_status_item",
    "ux_queue": "iterate_now",
    "issues": "investigate_issue",
    "followup": "resume_followup",
    "backlog": "consider_backlog",
    "memory_backlog": "review_durable_backlog",
    "proposals": "review_proposal",
}


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


def iteration_summary(workdir: Path) -> dict[str, dict[str, Any]]:
    state = _read_json(workdir / ".build-loop" / "state.json")
    execution = state.get("execution") if isinstance(state.get("execution"), dict) else {}
    item_iterations = execution.get("item_iterations")
    if not isinstance(item_iterations, dict):
        return {}
    summaries: dict[str, dict[str, Any]] = {}
    for item_id, attempts in item_iterations.items():
        if not isinstance(attempts, list):
            continue
        normalized = [row for row in attempts if isinstance(row, dict)]
        if not normalized:
            continue
        last = normalized[-1]
        summary: dict[str, Any] = {
            "attempts": len(normalized),
            "last_status": last.get("status"),
            "last_phase": last.get("phase"),
            "last_recorded_at": last.get("recorded_at"),
        }
        if last.get("criterion"):
            summary["last_criterion"] = last["criterion"]
        if last.get("stop_reason"):
            summary["stop_reason"] = last["stop_reason"]
        summaries[str(item_id)] = summary
    return summaries


def _validation_clarity(row: dict[str, Any]) -> str:
    path = Path(str(row.get("path", "")))
    if row.get("surface", "").startswith("state."):
        return "clear"
    if path.exists():
        return "clear"
    return "unknown"


def _risk_level(row: dict[str, Any]) -> str:
    surface = row.get("surface")
    if surface == "proposals":
        return "decision-review"
    if surface == "memory_backlog":
        return "alignment-review"
    if str(surface).startswith("state."):
        return "active-run"
    return "safe-candidate"


def _rank_score(row: dict[str, Any]) -> int:
    priority = int(row.get("priority", 100))
    return max(0, 100 - priority) + SURFACE_RANK_BONUS.get(str(row.get("surface")), 0)


def rank_task_items(
    items: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in items:
        item = dict(row)
        item["rank_score"] = _rank_score(item)
        item["dry_run_action"] = SURFACE_ACTION.get(str(item.get("surface")), "review")
        item["risk"] = _risk_level(item)
        item["validation_clarity"] = _validation_clarity(item)
        if item["id"] in summaries:
            item["iteration_summary"] = summaries[item["id"]]
        ranked.append(item)
    ranked.sort(key=lambda row: (-row["rank_score"], row["priority"], row["surface"], row["id"]))
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    return ranked


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


def _current_open_work(path: Path) -> list[str]:
    """Parse the numbered items under a '## Current open work' heading in CURRENT.md."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    items: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.lower().startswith("## current open work")
            continue
        if not in_section:
            continue
        match = re.match(r"^\d+\.\s+(.*)", stripped)
        if match:
            items.append(_clean_markdown(match.group(1)))
    return items


def _clean_markdown(text: str) -> str:
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def status_current_items(
    *,
    workdir: Path,
    memory_root: Path | None = None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Surface the code-grounded 'Current open work' from the canonical CURRENT.md."""
    root = memory_root or memory_store_root()
    slug = project or resolve_project(workdir)
    current = root / "projects" / slug / "status" / "CURRENT.md"
    if not current.is_file():
        return []
    return [
        _item(
            surface="status_current",
            lifecycle="code-grounded-status",
            priority=15,
            title=title,
            path=str(current),
            item_id=f"CURRENT:{idx}",
        )
        for idx, title in enumerate(_current_open_work(current), start=1)
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
        items.extend(status_current_items(workdir=wd, memory_root=memory_root))
        items.extend(memory_backlog_items(workdir=wd, memory_root=memory_root))
    summaries = iteration_summary(wd)
    ranked_items = rank_task_items(items, summaries)
    counts: dict[str, int] = {}
    for row in ranked_items:
        counts[row["surface"]] = counts.get(row["surface"], 0) + 1
    next_item = ranked_items[0] if ranked_items else None
    return {
        "action": "task-surface",
        "workdir": str(wd),
        "decision": "derived-active-view-no-new-ledger",
        "dry_run": {
            "mode": "rank-only",
            "next_item": next_item,
            "ranked_count": len(ranked_items),
            "skipped_count": 0,
            "stop_reasons": [],
        },
        "open_count": len(items),
        "counts_by_surface": counts,
        "iteration_summary": summaries,
        "items": ranked_items[:max_items],
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
            print(f"- #{row['rank']} [{row['surface']}] {row['title']} ({row['path']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
