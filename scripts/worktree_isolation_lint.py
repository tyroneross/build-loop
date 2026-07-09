#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Worktree-isolation lint — block background committing agents from the live checkout.

WHY THIS EXISTS (named, observed failure — RCA 2026-06-22)
----------------------------------------------------------
A headless launchd autonomy poller (``com.tyroneross.codex-autonomy-poller.build-loop``)
ran with ``WorkingDirectory`` set to the live interactive build-loop checkout. It woke a
background codex writer that then COMMITTED into that single shared checkout while an
interactive session worked there too. Three distinct corruptions resulted in one session:
a commit landing on the wrong branch, a staged commit silently reduced to rename-only
(content stranded in the working tree), and freshly-regenerated architecture inventory
pushed stale. The exact class is documented in CLAUDE.md §"Concurrent dispatch isolation"
— but that control was ADVISORY ("the caller's contract") and scoped only to the
Agent-tool dispatch path, leaving headless launchd pollers/watchers uncovered.

This lint is the enforcement artifact: it flags any background committing writer that
would write to a live interactive checkout instead of a dedicated git worktree, and it
covers BOTH the headless launchd path and the in-repo wake/dispatch path.

THE STRUCTURAL HAZARD WE DETECT
-------------------------------
A persistent background launchd job (``RunAtLoad`` and/or ``KeepAlive``) whose label
matches autonomy/poller/watcher AND whose ``WorkingDirectory`` is a LIVE git checkout
(a real repo root, NOT a dedicated worktree under ``.build-loop/worktrees`` or
``*.worktrees/``) is the hazard. A committing writer woken in that cwd inherits it and
races the interactive session on HEAD/index/branch. The poller text need not itself
contain ``git commit`` — it only has to put a woken writer in the wrong directory.

HOW A JOB DECLARES ISOLATION (passes the lint)
----------------------------------------------
A background job is compliant when ANY of these is true:
  1. Its ``WorkingDirectory`` is a dedicated worktree (matches WORKTREE_DIR_MARKERS),
     so a woken writer commits there, never in the live checkout.
  2. It sets the env var ``BUILD_LOOP_WORKTREE_ISOLATED=1`` in its plist (an explicit
     declaration that the program provisions/uses a worktree before committing).
  3. It is a recognized NOTIFY-ONLY program (NOTIFY_ONLY_BASENAMES) — a watcher that
     detects a transition and notifies/injects but never edits/commits, per the rally
     "watchers stay narrow" doctrine. These are exempt from the cwd hazard because they
     never write.

IN-REPO WAKE/DISPATCH PATH
--------------------------
The canonical wake surface (``scripts/wake_scheduler.py``, ``scripts/agent_rally_watcher/``)
is notify-only by design: wake_scheduler is a pure decision engine (it never calls a host
tool), and the rally watcher only emits ``rally_wake_due`` events. The lint asserts that
contract holds — if either grows a ``git commit`` / worktree-less commit call, it fails.

OUTPUT / EXIT CODES
-------------------
stdout = one JSON envelope with ``findings[]``. Exit 0 = clean, exit 1 = at least one
BLOCKER finding (a committing background writer pointed at a live checkout). Mirrors the
plan_verify.py / review_finding_gate.py convention.

Usage:
    python3 scripts/worktree_isolation_lint.py [--workdir .] \
        [--launch-agents-dir ~/Library/LaunchAgents] [--json]
"""
from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
import sys
import xml.parsers.expat
from pathlib import Path
from typing import Any

# A WorkingDirectory is a "dedicated worktree" (safe) when it matches one of these.
# build-loop canonical worktrees live under <repo>/.build-loop/worktrees (see
# worktree_guard.CANONICAL_WORKTREE_ROOT); the deliverable also names the shape
# build-loop.worktrees/<agent>-<task>. Accept either convention.
WORKTREE_DIR_MARKERS = (
    "/.build-loop/worktrees/",
    ".worktrees/",
    "/.claude/worktrees/",
)

# Label/program substrings that mark a background job as an autonomy/poller/watcher.
BACKGROUND_LABEL_MARKERS = ("autonomy", "poller", "watcher")

# Programs that are recognized notify-only (never commit) → exempt from the cwd hazard.
NOTIFY_ONLY_BASENAMES = (
    "watch.py",            # scripts/agent_rally_watcher/watch.py
    "wake_scheduler.py",   # pure decision engine
    "coordination_watch.py",
)

# Explicit env-var declaration a job can set to assert it provisions a worktree.
ISOLATION_ENV_KEY = "BUILD_LOOP_WORKTREE_ISOLATED"

# In-repo wake/dispatch files asserted to stay notify-only.
IN_REPO_NOTIFY_ONLY = (
    "scripts/wake_scheduler.py",
    "scripts/agent_rally_watcher/watch.py",
)

# A commit-without-worktree signal: a `git commit` (or quoted "commit" subcommand) whose
# surrounding code does not also create/cd into a guarded worktree. Deliberately narrow —
# it only fires on a LITERAL commit call, never on a doc mention of the word "commit".
_COMMIT_CALL_RE = re.compile(r"""['"]commit['"]|git\s+commit""")
_WORKTREE_CALL_RE = re.compile(r"worktree|create_guarded_worktree|build-loop\.worktrees")

# A bare python interpreter: `python`, `python3`, `python3.14`, … with NO path
# separator (so it resolves via PATH, not a pinned venv). A venv-pinned interpreter
# (e.g. `/Users/x/.venv/bin/python3`) carries a `/` and is therefore stable.
_PYTHON_BASENAME_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?$", re.IGNORECASE)


def _is_bare_python_interpreter(interp: str) -> bool:
    """True when ``interp`` is a bare python command name resolved via PATH.

    A background job launched with a bare ``python3`` inherits whatever python
    happens to be first on PATH; a peer reinstalling the system interpreter
    (F1 freeze: ``python@3.14`` replaced the shared interpreter) silently breaks
    the job. A ``.venv``-pinned absolute path is stable. Empty/None → False."""
    interp = (interp or "").strip()
    if not interp or "/" in interp:
        return False
    return bool(_PYTHON_BASENAME_RE.match(interp))


def _finding(
    severity: str, rule: str, location: str, message: str, remedy: str
) -> dict[str, str]:
    return {
        "severity": severity,
        "rule": rule,
        "location": location,
        "message": message,
        "remedy": remedy,
    }


def _is_git_checkout(path: Path) -> bool:
    """True iff ``path`` is inside a git working tree (a real checkout)."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return cp.returncode == 0 and cp.stdout.strip() == "true"


def _is_dedicated_worktree(path: str) -> bool:
    norm = path.rstrip("/") + "/"
    return any(marker in norm for marker in WORKTREE_DIR_MARKERS)


def _is_background_autonomy_job(label: str, program: str) -> bool:
    haystack = f"{label} {program}".lower()
    return any(marker in haystack for marker in BACKGROUND_LABEL_MARKERS)


def _is_notify_only_program(program: str) -> bool:
    base = Path(program).name
    return base in NOTIFY_ONLY_BASENAMES


def _persistent(plist: dict[str, Any]) -> bool:
    """A job that runs at load and/or is kept alive is a persistent background writer."""
    if plist.get("RunAtLoad"):
        return True
    ka = plist.get("KeepAlive")
    return bool(ka) if ka is not None else False


def lint_launch_agents(launch_agents_dir: Path) -> list[dict[str, str]]:
    """Scan launchd jobs for committing background agents pointed at a live checkout."""
    findings: list[dict[str, str]] = []
    if not launch_agents_dir.exists():
        return findings

    for plist_path in sorted(launch_agents_dir.glob("*.plist")):
        try:
            with plist_path.open("rb") as fh:
                plist = plistlib.load(fh)
        except (
            plistlib.InvalidFileException,
            xml.parsers.expat.ExpatError,
            OSError,
            ValueError,
        ):
            continue  # malformed/unreadable plist is not our concern; fail open

        label = str(plist.get("Label", plist_path.stem))
        args = plist.get("ProgramArguments") or []
        program = ""
        if args:
            program = str(args[0])
        elif plist.get("Program"):
            program = str(plist.get("Program"))

        if not _is_background_autonomy_job(label, program):
            continue
        if not _persistent(plist):
            continue

        # WARN (venv isolation, EC-02 coord): a background job launched with a bare
        # `python3` resolves the interpreter via PATH, so a peer reinstalling the
        # system python (F1 freeze: `python@3.14`) silently breaks it. Independent
        # of the cwd/commit hazard below — a worktree-isolated job can still run a
        # fragile bare interpreter. Non-gating (WARN never sets the exit code).
        interp = str(args[0]) if args else program
        if _is_bare_python_interpreter(interp):
            findings.append(
                _finding(
                    "WARN",
                    "bare-python-interpreter",
                    str(plist_path),
                    f"launchd job '{label}' launches a bare `{interp}` interpreter "
                    f"(resolved via PATH). A peer reinstalling the system python "
                    f"(F1 freeze: python@3.14) would silently break this job.",
                    "Pin the interpreter to a venv-absolute path (e.g. "
                    "<repo>/.venv/bin/python3) in ProgramArguments[0] so a PATH "
                    "change cannot swap the interpreter under the job.",
                )
            )

        # Notify-only programs never commit → no cwd hazard.
        if _is_notify_only_program(program):
            continue

        env = plist.get("EnvironmentVariables") or {}
        if str(env.get(ISOLATION_ENV_KEY, "")).strip() == "1":
            continue  # explicitly declared isolation

        workdir = plist.get("WorkingDirectory")
        if not workdir:
            # No cwd pin → inherits launchd cwd (~); not the live-checkout hazard.
            continue
        if _is_dedicated_worktree(str(workdir)):
            continue  # commits land in a dedicated worktree — safe
        if not _is_git_checkout(Path(workdir)):
            continue  # cwd is not a git checkout → no HEAD/index to race

        findings.append(
            _finding(
                "BLOCKER",
                "background-committer-in-live-checkout",
                str(plist_path),
                f"launchd job '{label}' is a persistent autonomy/poller/watcher whose "
                f"WorkingDirectory '{workdir}' is a LIVE git checkout, not a dedicated "
                f"worktree. A committing writer woken here races the interactive session "
                f"on HEAD/index/branch (RCA 2026-06-22).",
                "Point WorkingDirectory at a dedicated worktree (build-loop.worktrees/"
                "<agent>-<task> or .build-loop/worktrees/<slug>), OR set "
                f"EnvironmentVariables {ISOLATION_ENV_KEY}=1 to declare the program "
                "provisions a worktree before committing, OR make the program notify-only.",
            )
        )
    return findings


def lint_in_repo_wake_path(workdir: Path) -> list[dict[str, str]]:
    """Assert the canonical in-repo wake/dispatch files stay notify-only (no worktree-less commit)."""
    findings: list[dict[str, str]] = []
    for rel in IN_REPO_NOTIFY_ONLY:
        path = workdir / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _COMMIT_CALL_RE.search(text) and not _WORKTREE_CALL_RE.search(text):
            findings.append(
                _finding(
                    "BLOCKER",
                    "wake-path-grew-a-commit",
                    rel,
                    f"{rel} is a canonical wake/dispatch file that must stay notify-only, "
                    f"but it now contains a git-commit call with no worktree provisioning. "
                    f"A wake path that commits into the shared checkout reintroduces the "
                    f"race class (RCA 2026-06-22).",
                    "Keep watchers narrow: detect a transition and notify/inject only. If "
                    "work must be committed, dispatch it into a dedicated worktree via "
                    "scripts/worktree_guard.create_guarded_worktree; never commit inline.",
                )
            )
    return findings


def run_lint(workdir: Path, launch_agents_dir: Path) -> dict[str, Any]:
    findings = lint_launch_agents(launch_agents_dir) + lint_in_repo_wake_path(workdir)
    blockers = [f for f in findings if f["severity"] == "BLOCKER"]
    return {
        "ok": len(blockers) == 0,
        "blocker_count": len(blockers),
        "finding_count": len(findings),
        "findings": findings,
        "scanned": {
            "launch_agents_dir": str(launch_agents_dir),
            "workdir": str(workdir),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".", help="Repository root (default: .)")
    p.add_argument(
        "--launch-agents-dir",
        default=str(Path.home() / "Library" / "LaunchAgents"),
        help="Directory of launchd plists to scan (default: ~/Library/LaunchAgents).",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON envelope.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workdir = Path(args.workdir).resolve()
    launch_agents_dir = Path(args.launch_agents_dir).expanduser()
    result = run_lint(workdir, launch_agents_dir)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["ok"]:
            print(
                f"worktree-isolation lint: OK "
                f"({result['finding_count']} finding(s), 0 blockers)"
            )
        else:
            print(
                f"worktree-isolation lint: {result['blocker_count']} BLOCKER(s)",
                file=sys.stderr,
            )
            for f in result["findings"]:
                print(
                    f"  [{f['severity']}] {f['rule']} @ {f['location']}\n"
                    f"    {f['message']}\n    remedy: {f['remedy']}",
                    file=sys.stderr,
                )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
