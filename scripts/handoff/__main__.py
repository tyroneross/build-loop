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


# Derived views that restate a queue rather than being an item in it. Counting
# these instead of the real items understates a queue -- observed 2026-07-26:
# backlog/ reported "1 item" (INDEX.md) while backlog/items/ held 7, including
# release blockers. INDEX.md says so itself: "Do not hand-edit -- this file is
# a derived view; the canonical truth is the items in items/."
_DERIVED_INDEX_NAMES = {"INDEX.md", "README.md", "BACKLOG.md"}
# Terminal subdirectories: their contents are closed, not open work.
_CLOSED_SUBDIRS = {"resolved", "archive", "done", "closed", "_archive"}


def _queue_items(directory: Path) -> list[str]:
    """Every OPEN item in a queue -- full count, no silent cap.

    Two bugs this replaces (both observed 2026-07-26, both silent):
      * a `limit=5` default truncated the LIST *and* the COUNT, so a followup
        queue of 8 reported "5 items" and dropped three `judgment-owed-*`
        entries -- unpaid independent-auditor debt, exactly what a handoff
        exists to carry forward.
      * only top-level `*.md` was read, so `backlog/` (canonical items live in
        `backlog/items/`) reported its derived INDEX.md as the single item.

    Recurses one level so `<queue>/items/*.md` is found, skips derived indexes
    and closed subdirectories. Returns titles; the caller counts what it gets.
    """
    if not directory.exists():
        return []
    canonical = directory / "items"
    root = canonical if canonical.is_dir() else directory
    titles: list[str] = []
    for p in sorted(root.rglob("*.md")):
        if p.name in _DERIVED_INDEX_NAMES:
            continue
        if any(part in _CLOSED_SUBDIRS for part in p.relative_to(root).parts[:-1]):
            continue
        title = p.stem
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break
                if stripped.startswith("title:"):
                    title = stripped.split(":", 1)[1].strip().strip('"\'')
                    break
        except OSError:
            pass
        titles.append(title)
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
    """Phase, or an explicit statement that no run is active.

    A bare "unknown" printed above a checklist of all-`complete` phases reads
    as a broken state file (cold-read review, 2026-07-26: "I cannot tell if
    'unknown' means no active run or the state file is corrupt"). An empty
    `execution` block is the NORMAL resting state between runs, so say that.
    """
    ex = state.get("execution") or {}
    phase = ex.get("phase") or state.get("phase")
    if phase:
        return phase
    runs = state.get("runs") or []
    if runs:
        last = runs[-1]
        return (f"no run in progress — last run closed `{last.get('outcome', '?')}` "
                f"({last.get('run_id', 'unknown id')})")
    return "no run in progress — no run history recorded"


def _landmines(bl: Path, state: dict) -> list[str]:
    """Stale markers that mislead or misdirect a resumer's FIRST action.

    Added 2026-07-26 after a cold-read review found four traps that fire
    immediately on resume, none of which appeared in the handoff: a 45-day-
    stale `.current-run-id`, a `.push-hold` from a foreign run, an emptied
    `execution` block that made the doc's own verification step
    unfollowable, and top-level `state.json` keys describing a different
    build. Each is cheap to detect and expensive to hit.
    """
    out: list[str] = []
    runs = state.get("runs") or []
    current_run_id = (runs[-1].get("run_id") if runs else None)

    marker = bl / ".current-run-id"
    if marker.exists():
        try:
            recorded = marker.read_text(encoding="utf-8").strip()
            if recorded and current_run_id and recorded != current_run_id:
                out.append(
                    f"`.build-loop/.current-run-id` says `{recorded}`, but the newest "
                    f"run is `{current_run_id}`. Trust `runs[-1]`; the marker is stale."
                )
        except OSError:
            pass

    hold = bl / ".push-hold"
    if hold.exists():
        try:
            data = json.loads(hold.read_text(encoding="utf-8"))
            out.append(
                f"`.build-loop/.push-hold` is SET — reason: {data.get('reason', '?')!r}, "
                f"run_id: `{data.get('run_id', '?')}`, set_at: {data.get('set_at', '?')}. "
                f"Check whether it applies to you before pushing "
                f"(`python3 <build-loop>/scripts/push_hold.py --status --workdir .`)."
            )
        except (OSError, json.JSONDecodeError):
            out.append("`.build-loop/.push-hold` exists but is unreadable — inspect before pushing.")

    ex = state.get("execution") or {}
    if ex.get("crashed_at"):
        out.append(
            f"`state.json.execution` holds a crash marker from {ex['crashed_at']} "
            f"({ex.get('crash_signal', 'unknown signal')}). Confirm it belongs to the "
            f"current run before resuming from it."
        )

    if runs and runs[-1].get("reconstructed"):
        out.append(
            f"The newest `runs[]` entry is RECONSTRUCTED after the fact, not written by "
            f"the orchestrator that did the work. Notes: {runs[-1].get('notes', 'n/a')}"
        )

    # Top-level keys that describe a build other than the newest run.
    stale_top = [k for k in ("uiTarget", "wp_progress", "scopeAuditorStatus")
                 if state.get(k)]
    if stale_top and current_run_id:
        out.append(
            f"`state.json` top-level keys {', '.join('`'+k+'`' for k in stale_top)} may "
            f"describe an EARLIER build — only `runs[]` is known-current. Do not read "
            f"them as this run's context."
        )
    return out


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

## 8. Landmines — read before your first action
{landmines}

## 9. Resume Instructions
1. Open the STABLE checkout (not a worktree): `cd {workdir}`
2. Load the `build-loop:build-loop` skill.
3. Share this document so the new session starts with full context.
4. Read §8 Landmines FIRST — those are the traps that fire on a first action.
5. Confirm nobody started a new run since this doc was written: the newest
   `runs[]` entry in `.build-loop/state.json` should still be `{last_run_id}`.
   (Read `runs[-1].run_id`. Do NOT read `execution.run_id` — `execution` is
   empty between runs, and `.current-run-id` can be months stale.)
6. **There is no pre-assigned next task.** This doc reports state, not a plan.
   Pick from §5 Queues, or ask the owner. Nothing in §5 is authorized by
   default — `followup/` items in particular may carry unpaid audit debt
   (`judgment-owed-*`) that belongs to a different run.
7. If a run WAS mid-Execute, resume from the last committed chunk boundary.

---
*Generated by build-loop's `scripts/handoff` (lives in the build-loop repo, not
this one) — {ts}*
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

    def _load_md(name: str) -> str:
        """Inline the WHOLE file. Truncation here is a correctness bug.

        Previously capped intent.md at 30 lines and goal.md at 20, with a
        "(truncated -- see .build-loop/<name>)" pointer. Both caps landed
        exactly on the load-bearing text: intent.md's cut fell immediately
        before `non_goals`, and goal.md's before `Open / held`. A cold-read
        review on 2026-07-26 scored the result 2/5 and could not answer "what
        must I NOT do" -- the answer was in the removed lines. Telling a
        resumer to go open the source file also defeats the point: a handoff
        that requires reading .build-loop/ is not a handoff.

        These files are hand-written run context, not logs; they are small.
        Completeness beats brevity here.
        """
        raw, err = _read_file(bl / name)
        if raw:
            sources.append(name)
            return raw
        if err and "not found" not in err:
            errors.append(err)
        return "n/a"

    intent = _load_md("intent.md")
    goal = _load_md("goal.md")
    feedback = _load_md("feedback.md")

    state = _read_state(bl)
    if state:
        sources.append("state.json")

    phase = _phase_from_state(state)
    checklist = _checklist_from_state(state)
    run_summary = _run_summary(state)

    git = _git_state(workdir)
    recent_commits = "\n".join(f"  {c}" for c in git["recent_commits"]) or "  (none)"

    followup_titles = _queue_items(bl / "followup")
    backlog_titles = _queue_items(bl / "backlog")
    ux_titles = _queue_items(bl / "ux-queue")
    issues_titles = _queue_items(bl / "issues")
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
        landmines=_section(
            _landmines(bl, state),
            "None detected. (Checked: `.current-run-id` drift, `.push-hold`, "
            "a stale `execution` crash marker, a reconstructed run record, and "
            "top-level `state.json` keys describing an earlier build.)",
        ),
        last_run_id=((state.get("runs") or [{}])[-1].get("run_id") or "unknown"),
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
