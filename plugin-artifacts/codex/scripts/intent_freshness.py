#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""intent_freshness.py — advisory per-run freshness check for .build-loop/intent.md.

ROOT CAUSE (bl-intent-refresh-per-run, 2026-06-09)
--------------------------------------------------
The intent-restatement protocol is "always-on, LLM-judged, NEVER a regex/detector/
gate". Because it carries no machine signal, nothing reconciles the intent.md on disk
against the CURRENT run. When a run resumes or runs back-to-back in the same workdir,
a prior run's intent.md is already present; the "auto-execute fast path" (Step A says
"write the line and move on" for a concrete goal) competes with "always write", so an
orchestrator can treat the existing, plausible-looking file as satisfying the protocol
and skip the rewrite. Result: the A–H consolidation run's intent.md described the PRIOR
run and listed as a non-goal what WP-A actually shipped.

THE FIX (advisory, structural — not a content gate)
---------------------------------------------------
Stamp intent.md with the run_id it was written for (an HTML comment marker the LLM
prose never touches). This checker compares that stamp to the CURRENT run_id. A run_id
mismatch is an OBJECTIVE structural fact (equality, not a judgment), so detecting it does
not violate the "never a content gate" rule — only the REFRESH (the LLM rewriting the
prose) is judgment. Verdict is advisory: the orchestrator refreshes on `stale`/`unstamped`
and surfaces the line; it never hard-blocks Phase 1.

CLI
---
  python3 scripts/intent_freshness.py --workdir <repo> [--current-run-id <id>] [--json]

Verdicts: ``fresh`` | ``stale`` | ``unstamped`` | ``no_intent`` | ``no_run``.
Exit code is ALWAYS 0 (advisory). ``--json`` emits the machine envelope.

Helper for the writer side: ``stamp_marker(run_id)`` returns the marker line; the
orchestrator appends/replaces it when it (re)writes intent.md. Zero deps. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MARKER_RE = re.compile(r"<!--\s*intent_run_id:\s*(?P<rid>[^\s>]+)\s*-->")


def stamp_marker(run_id: str) -> str:
    """The HTML-comment stamp the orchestrator writes into intent.md."""
    return f"<!-- intent_run_id: {run_id} -->"


def read_stamped_run_id(intent_path: Path) -> str | None:
    """Return the run_id stamped in intent.md, or None if absent/unreadable."""
    try:
        text = intent_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = MARKER_RE.search(text)
    return m.group("rid") if m else None


def current_run_id(workdir: Path, override: str | None) -> str | None:
    """Resolve the current run_id: explicit override > execution.run_id > runs[-1].run_id."""
    if override:
        return override
    state_path = workdir / ".build-loop" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    execution = state.get("execution")
    if isinstance(execution, dict) and execution.get("run_id"):
        return str(execution["run_id"])
    runs = state.get("runs")
    if isinstance(runs, list) and runs:
        last = runs[-1]
        if isinstance(last, dict) and last.get("run_id"):
            return str(last["run_id"])
    return None


def evaluate(workdir: Path, override: str | None = None) -> dict:
    """Return the advisory freshness envelope. Never raises."""
    intent_path = workdir / ".build-loop" / "intent.md"
    cur = current_run_id(workdir, override)
    if not intent_path.exists():
        return {
            "verdict": "no_intent",
            "stamped_run_id": None,
            "current_run_id": cur,
            "stale": False,
            "advice": "intent.md absent — Phase 1 must write it for this run.",
        }
    stamped = read_stamped_run_id(intent_path)
    if cur is None:
        return {
            "verdict": "no_run",
            "stamped_run_id": stamped,
            "current_run_id": None,
            "stale": False,
            "advice": "No current run_id resolvable (state.json.execution/runs empty) — cannot compare.",
        }
    if stamped is None:
        return {
            "verdict": "unstamped",
            "stamped_run_id": None,
            "current_run_id": cur,
            "stale": True,
            "advice": (
                f"intent.md carries no run-id stamp — refresh it for run {cur} and stamp "
                f"`{stamp_marker(cur)}`."
            ),
        }
    if stamped == cur:
        return {
            "verdict": "fresh",
            "stamped_run_id": stamped,
            "current_run_id": cur,
            "stale": False,
            "advice": "intent.md is stamped for the current run.",
        }
    return {
        "verdict": "stale",
        "stamped_run_id": stamped,
        "current_run_id": cur,
        "stale": True,
        "advice": (
            f"intent.md is stamped for a PRIOR run ({stamped}) but the current run is {cur}. "
            f"Re-run the intent-restatement protocol and re-stamp; the on-disk intent likely "
            f"describes prior work."
        ),
    }


def _format_human(env: dict) -> str:
    return f"intent_freshness: verdict={env['verdict']} stale={env['stale']} — {env['advice']}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="intent_freshness", description=__doc__)
    ap.add_argument("--workdir", required=True, help="build-loop project workdir")
    ap.add_argument("--current-run-id", default=None, help="override the resolved current run_id")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    env = evaluate(Path(args.workdir).resolve(), override=args.current_run_id)
    if args.json:
        print(json.dumps(env, indent=2, sort_keys=True))
    else:
        print(_format_human(env))
    return 0  # advisory: always 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
