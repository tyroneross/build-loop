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
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _paths import memory_store_root  # type: ignore  # noqa: E402
from project_resolver import resolve_project  # type: ignore  # noqa: E402

ACTIVE_DIRS = [
    ("queue", ".build-loop/queue", "queued", 20),
    ("ux_queue", ".build-loop/ux-queue", "active-iterate", 30),
    ("issues", ".build-loop/issues", "open-issue", 40),
    ("followup", ".build-loop/followup", "deferred-active", 50),
]
BACKLOG_DIR = ("backlog", ".build-loop/backlog", "deferred", 60)
PROPOSAL_DIR = ("proposals", ".build-loop/proposals", "candidate", 90)
OPERATIONS_CENTER_URL = "http://127.0.0.1:3766/api/tasks"
MAX_OPERATIONS_CENTER_BYTES = 1024 * 1024
TERMINAL_STATUSES = {"closed", "complete", "completed", "done", "dropped", "superseded", "wontfix"}

SURFACE_RANK_BONUS = {
    "state.in_flight_chunks": 35,
    "state.in_flight": 35,
    "state.queued_chunks": 25,
    "state.queued": 25,
    "status_current": 22,
    "queue": 20,
    "ux_queue": 18,
    "issues": 16,
    "followup": 10,
    "backlog": -20,
    "memory_backlog": 2,
    "proposals": -10,
    "operations_center": 12,
}

SURFACE_ACTION = {
    "state.in_flight_chunks": "continue_in_flight",
    "state.in_flight": "continue_in_flight",
    "state.queued_chunks": "dispatch_next",
    "state.queued": "dispatch_next",
    "status_current": "address_status_item",
    "queue": "dispatch_next",
    "ux_queue": "iterate_now",
    "issues": "investigate_issue",
    "followup": "resume_followup",
    "backlog": "review_deferred_policy",
    "memory_backlog": "review_durable_backlog",
    "proposals": "review_proposal",
    "operations_center": "continue_operations_center_task",
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


def _frontmatter(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        data[key.strip().lower()] = value.strip().strip("\"'")
    return data


def _include_markdown_surface_item(surface: str, root: Path, path: Path) -> bool:
    if surface == "backlog":
        relative_parts = path.relative_to(root).parts
        if path.name == "INDEX.md" or "archive" in relative_parts:
            return False
    status = _frontmatter(path).get("status", "").lower()
    return status not in TERMINAL_STATUSES


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
    owner: str = "",
    created_by: str = "",
) -> dict[str, Any]:
    return {
        "surface": surface,
        "lifecycle": lifecycle,
        "priority": priority,
        "title": title,
        "path": path,
        "id": item_id,
        "owner": owner,
        "created_by": created_by,
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
            record = value if isinstance(value, dict) else {}
            item_id = str(record.get("chunk_id") or record.get("id") or record.get("task_id") or value)
            title = str(record.get("title") or record.get("summary") or record.get("label") or item_id)
            rows.append(
                _item(
                    surface=f"state.{key}",
                    lifecycle=lifecycle,
                    priority=priority,
                    title=title,
                    path=str(workdir / ".build-loop" / "state.json"),
                    item_id=item_id,
                    owner=str(record.get("owner") or record.get("agent") or record.get("assigned_to") or ""),
                    created_by="Build Loop",
                )
            )
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = by_id.get(row["id"])
        if current is None or row["priority"] < current["priority"]:
            by_id[row["id"]] = row
    return list(by_id.values())


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
    if surface == "backlog":
        return {
            "initiative": "user-approval-required",
            "decision": "contextual-user-decision",
        }.get(str(row.get("bucket")), "planning-boundary")
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
        item["execution_eligible"] = item.get("surface") not in {
            "backlog", "memory_backlog", "proposals"
        }
        if item.get("surface") == "operations_center" and item.get("lifecycle") in {
            "failed", "needs_input"
        }:
            item["execution_eligible"] = False
        if item.get("surface") == "backlog":
            item["pickup_policy"] = {
                "planned": "promote-at-planning-boundary",
                "initiative": "user-approval-plus-isolated-worktree",
                "decision": "surface-only-for-matching-workstream",
            }.get(str(item.get("bucket")), "promote-at-planning-boundary")
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
    dirs = list(ACTIVE_DIRS) + [BACKLOG_DIR]
    if include_proposals:
        dirs.append(PROPOSAL_DIR)
    for surface, rel, lifecycle, priority in dirs:
        root = workdir / rel
        if not root.exists():
            continue
        try:
            resolved_root = root.resolve()
            resolved_root.relative_to(workdir.resolve())
        except (OSError, ValueError):
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                path.resolve().relative_to(resolved_root)
            except (OSError, ValueError):
                continue
            if not _include_markdown_surface_item(surface, root, path):
                continue
            fm = _frontmatter(path)

            def _append(row: dict[str, Any]) -> None:
                row["owner"] = fm.get("owner") or fm.get("agent") or fm.get("assigned_to", "")
                row["created_by"] = fm.get("created_by") or fm.get("source", "Build Loop")
                if surface == "backlog":
                    row["bucket"] = fm.get("bucket") or (
                        "decision" if fm.get("type") == "decision" else "planned"
                    )
                    row["workstream"] = fm.get("workstream", "")
                rows.append(row)

            unchecked = _unchecked_items(path)
            if unchecked:
                for idx, title in enumerate(unchecked, start=1):
                    _append(_item(
                            surface=surface,
                            lifecycle=lifecycle,
                            priority=priority,
                            title=title,
                            path=str(path),
                            item_id=f"{path.stem}:{idx}",
                        ))
            else:
                row = _item(
                    surface=surface,
                    lifecycle=lifecycle,
                    priority=priority,
                    title=_markdown_title(path),
                    path=str(path),
                    item_id=path.stem,
                )
                _append(row)
    return rows


def _operations_center_target_matches(workdir: Path, value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    target = value.strip()
    if target.casefold() == workdir.name.casefold():
        return True
    try:
        return Path(target).expanduser().resolve() == workdir
    except OSError:
        return False


def operations_center_items(
    *,
    workdir: Path,
    url: str = OPERATIONS_CENTER_URL,
    timeout_seconds: float = 0.4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read bounded, repo-scoped tasks from the shared cross-agent queue."""
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            raw = response.read(MAX_OPERATIONS_CENTER_BYTES + 1)
        if len(raw) > MAX_OPERATIONS_CENTER_BYTES:
            raise ValueError("response exceeded the read limit")
        payload = json.loads(raw.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
        return [], {"status": "unavailable", "detail": str(exc)}

    tasks = payload.get("tasks", []) if isinstance(payload, dict) else payload
    if not isinstance(tasks, list):
        return [], {"status": "unavailable", "detail": "response did not contain a task list"}

    rows: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict) or not _operations_center_target_matches(workdir, task.get("target_repo")):
            continue
        status = str(task.get("status") or "todo").strip().lower()
        if status in TERMINAL_STATUSES:
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        priority = task.get("priority")
        priority_rank = 12 + int(priority) if isinstance(priority, int) else 15
        rows.append(_item(
            surface="operations_center",
            lifecycle=status,
            priority=priority_rank,
            title=str(task.get("title") or task_id),
            path=url,
            item_id=task_id,
            owner="",
            created_by=str(task.get("logged_by") or task.get("origin") or "Operations Center"),
        ))
    return rows, {"status": "available", "matched_count": len(rows)}


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
    include_operations_center: bool = False,
    operations_center_url: str = OPERATIONS_CENTER_URL,
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
    operations_center = {"status": "not_requested", "matched_count": 0}
    if include_operations_center:
        external_items, operations_center = operations_center_items(
            workdir=wd,
            url=operations_center_url,
        )
        items.extend(external_items)
    summaries = iteration_summary(wd)
    ranked_items = rank_task_items(items, summaries)
    counts: dict[str, int] = {}
    for row in ranked_items:
        counts[row["surface"]] = counts.get(row["surface"], 0) + 1
    next_item = next((row for row in ranked_items if row["execution_eligible"]), None)
    execution_count = sum(1 for row in ranked_items if row["execution_eligible"])
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
        "execution_queue_count": execution_count,
        "deferred_count": len(items) - execution_count,
        "counts_by_surface": counts,
        "iteration_summary": summaries,
        "items": ranked_items[:max_items],
        "truncated": len(items) > max_items,
        "operations_center": operations_center,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--memory-root")
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--include-proposals", action="store_true")
    parser.add_argument("--include-operations-center", action="store_true")
    parser.add_argument("--operations-center-url", default=OPERATIONS_CENTER_URL)
    parser.add_argument("--max-items", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = collect_task_surface(
        workdir=Path(args.workdir),
        memory_root=Path(args.memory_root).expanduser().resolve() if args.memory_root else None,
        include_memory=not args.no_memory,
        include_proposals=args.include_proposals,
        include_operations_center=args.include_operations_center,
        operations_center_url=args.operations_center_url,
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
