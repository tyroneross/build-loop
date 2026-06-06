#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Compose a build-loop handoff document from .build-loop/ run state.

Usage:
    python3 scripts/handoff [--workdir DIR] [--output FILE] [--json]

Reads:
  .build-loop/intent.md        — north star
  .build-loop/goal.md          — current goal + F-criteria
  .build-loop/state.json       — phase, execution, runs[]
  .build-loop/feedback.md      — gotchas/lessons (optional)
  .build-loop/followup/*.md    — overflow queue (optional)
  .build-loop/backlog/*.md     — deferred backlog (optional)
  .build-loop/ux-queue/*.md    — UX findings (optional)
  .build-loop/issues/*.md      — current-run issues (optional)
  git status + git log         — recent commits, branch, ahead/behind

Emits a fixed-template handoff document. Absent sources render as
explicit "none" / "n/a" — never crash.

Zero new dependencies. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

def _read_file(path: Path) -> tuple[str | None, str | None]:
    if not path.exists():
        return None, f"not found: {path.name}"
    try:
        return path.read_text(encoding="utf-8").strip(), None
    except OSError as exc:
        return None, str(exc)


def _read_state(bl: Path) -> dict:
    state_path = bl / "state.json"
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _queue_titles(directory: Path, limit: int = 5) -> list[str]:
    """Return first heading or filename for each queue item (up to limit)."""
    if not directory.exists():
        return []
    titles: list[str] = []
    for p in sorted(directory.iterdir()):
        if p.suffix != ".md":
            continue
        title = p.stem
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        except OSError:
            pass
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def _git_state(workdir: Path) -> dict:
    """Collect git branch, ahead/behind, last 8 commits. Never raises."""
    result: dict = {
        "branch": "unknown",
        "ahead_behind": "unknown",
        "recent_commits": [],
        "status_summary": "clean",
    }

    def _run(*args: str) -> str:
        try:
            return subprocess.check_output(
                list(args), cwd=str(workdir), stderr=subprocess.DEVNULL,
                text=True, timeout=10
            ).strip()
        except Exception:
            return ""

    result["branch"] = _run("git", "rev-parse", "--abbrev-ref", "HEAD") or "unknown"

    ahead_behind_raw = _run("git", "status", "--porcelain=v2", "--branch")
    for line in ahead_behind_raw.splitlines():
        if line.startswith("# branch.ab"):
            parts = line.split()
            if len(parts) >= 4:
                result["ahead_behind"] = f"{parts[2]} {parts[3]}"
            break

    log_raw = _run("git", "log", "--oneline", "--no-decorate", "-8",
                   "--format=%h %s (%ar)")
    result["recent_commits"] = log_raw.splitlines() if log_raw else []

    status_raw = _run("git", "status", "--short")
    result["status_summary"] = status_raw or "clean"
    return result


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _phase_from_state(state: dict) -> str:
    ex = state.get("execution", {})
    return ex.get("phase") or state.get("phase") or "unknown"


def _checklist_from_state(state: dict) -> str:
    """Extract live checklist / phase progress from state.json."""
    ex = state.get("execution", {})
    checklist = ex.get("checklist", {})
    if checklist:
        return "\n".join(f"  - {k}: {v}" for k, v in checklist.items())

    # Fallback: last run phase summary
    runs = state.get("runs", [])
    if runs:
        last = runs[-1]
        phases = last.get("phases", {})
        goal = last.get("goal", "")
        lines = []
        if goal:
            lines.append(f"  Last run goal: {goal[:120]}")
        for ph, info in (phases or {}).items():
            if isinstance(info, dict):
                status = info.get("status") or info.get("outcome") or "?"
                lines.append(f"  - {ph}: {status}")
            else:
                lines.append(f"  - {ph}: {info}")
        if lines:
            return "\n".join(lines)

    phase = _phase_from_state(state)
    run_id = ex.get("run_id") or ex.get("build_loop_id")
    return f"  phase={phase}" + (f"  run_id={run_id}" if run_id else "")


def _run_summary(state: dict) -> str:
    runs = state.get("runs", [])
    if not runs:
        return "none"
    last = runs[-1]
    parts = []
    if last.get("run_id"):
        parts.append(f"run_id={last['run_id']}")
    if last.get("date"):
        parts.append(f"date={last['date']}")
    parts.append(f"outcome={last.get('outcome', '?')}")
    if last.get("goal"):
        parts.append(f'goal="{last["goal"][:100]}"')
    return " | ".join(parts) if parts else "unknown"


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

_TEMPLATE = """\
# Build-Loop Handoff — {ts}

## 1. North Star (intent)
{intent}

## 2. Current Goal
{goal}

## 3. Phase + Live Checklist
Current phase: **{phase}**

{checklist}

## 4. Git State
Branch: {branch}
Ahead/behind: {ahead_behind}

### Recent commits
{recent_commits}

### Working-tree status
```
{status_summary}
```

## 5. Queues

### followup/ ({followup_count} items)
{followup_items}

### backlog/ ({backlog_count} items)
{backlog_items}

### ux-queue/ ({ux_count} items)
{ux_items}

### issues/ ({issues_count} items)
{issues_items}

## 6. Gotchas / Lessons
{feedback}

## 7. Last Run Summary
{run_summary}

## 8. Resume Instructions
1. Open the STABLE checkout (not a worktree): `cd {workdir}`
2. Load the `build-loop:build-loop` skill.
3. Share this document so the new session starts with full context.
4. If phase is mid-Execute, resume from the last committed chunk boundary.
5. Verify `.build-loop/state.json` `execution.run_id` matches — if different,
   a new run was started between handoff and resume.

---
*Generated by `scripts/handoff` — {ts}*
"""


def _section(items: list[str], fallback: str = "none") -> str:
    if not items:
        return fallback
    return "\n".join(f"- {t}" for t in items)


def compose(workdir: Path) -> dict:
    """Read all sources, return {document, sources, errors}."""
    bl = workdir / ".build-loop"
    sources: list[str] = []
    errors: list[str] = []

    def _load_md(name: str, max_lines: int = 30) -> str:
        raw, err = _read_file(bl / name)
        if raw:
            sources.append(name)
            lines = raw.splitlines()
            suffix = (f"\n\n*(truncated — see .build-loop/{name})*"
                      if len(lines) > max_lines else "")
            return "\n".join(lines[:max_lines]) + suffix
        if err and "not found" not in err:
            errors.append(err)
        return "n/a"

    intent = _load_md("intent.md", 30)
    goal = _load_md("goal.md", 20)
    feedback = _load_md("feedback.md", 15)

    state = _read_state(bl)
    if state:
        sources.append("state.json")

    phase = _phase_from_state(state)
    checklist = _checklist_from_state(state)
    run_summary = _run_summary(state)

    git = _git_state(workdir)
    recent_commits = "\n".join(f"  {c}" for c in git["recent_commits"]) or "  (none)"

    followup_titles = _queue_titles(bl / "followup")
    backlog_titles = _queue_titles(bl / "backlog")
    ux_titles = _queue_titles(bl / "ux-queue")
    issues_titles = _queue_titles(bl / "issues")
    if any([followup_titles, backlog_titles, ux_titles, issues_titles]):
        sources.append("queues")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = _TEMPLATE.format(
        ts=ts,
        intent=intent,
        goal=goal,
        phase=phase,
        checklist=checklist,
        branch=git["branch"],
        ahead_behind=git["ahead_behind"],
        recent_commits=recent_commits,
        status_summary=git["status_summary"],
        followup_count=len(followup_titles),
        followup_items=_section(followup_titles),
        backlog_count=len(backlog_titles),
        backlog_items=_section(backlog_titles),
        ux_count=len(ux_titles),
        ux_items=_section(ux_titles),
        issues_count=len(issues_titles),
        issues_items=_section(issues_titles),
        feedback=feedback,
        run_summary=run_summary,
        workdir=str(workdir),
    )

    return {"document": document, "sources": sources, "errors": errors, "ts": ts}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compose a build-loop handoff document from .build-loop/ run state."
    )
    parser.add_argument("--workdir", type=Path, default=Path("."),
                        help="Repo root (default: cwd)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write handoff doc to this file (default: stdout)")
    parser.add_argument("--json", dest="json_out", action="store_true",
                        help="Emit JSON envelope: {document, sources, errors, ts}")
    args = parser.parse_args()

    workdir = args.workdir.resolve()
    result = compose(workdir)

    if args.json_out:
        print(json.dumps(result, ensure_ascii=False))
        return

    doc = result["document"]
    if args.output:
        args.output.write_text(doc, encoding="utf-8")
        print(f"Handoff written to {args.output}", file=sys.stderr)
        if result["errors"]:
            print(f"Warnings: {'; '.join(result['errors'])}", file=sys.stderr)
    else:
        print(doc)
        if result["errors"]:
            print(f"\n<!-- warnings: {'; '.join(result['errors'])} -->", file=sys.stderr)


if __name__ == "__main__":
    main()
