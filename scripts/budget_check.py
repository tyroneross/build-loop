#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Wall-clock budget tracker for the autonomous iterate loop (plan §14.4 B).

Reads `.build-loop/state.json.execution.budget` (added by the orchestrator at
autonomous-mode start) and returns a routing envelope the orchestrator consults
at every iterate-loop entry, every commit, and every phase boundary.

Contract:
  stdout  -> single JSON object (the envelope)
  stderr  -> human-readable log lines (warnings only)
  exit 0  -> always (informational; never blocks the build)

Envelope shape:
  {
    "within_budget":              bool,
    "remaining_seconds":          int,           # negative when over budget
    "remaining_pct":              int,           # 0..100, clamped
    "action":                     "continue" | "checkin" | "finalize_and_stop",
    "should_push_now":            bool,          # K-commit-batch heuristic
    "elapsed_since_last_checkin_s": int,
    "mode":                       "default" | "long" | "custom",
    "budget_seconds":             int,
    "used_seconds":               int,
    "reason":                     str            # one-line trace
  }

Action semantics (plan §14.4 B):
  - continue            -> within budget, no scheduled check-in due
  - checkin             -> at scheduled checkpoint OR ≥checkinIntervalPct% elapsed since last
  - finalize_and_stop   -> budget elapsed; orchestrator must finish current chunk + final push

State block shape it expects:
  state.execution.budget = {
    "mode":               "default" | "long" | "custom",
    "started_at":         "<iso8601 UTC>",
    "deadline_at":        "<iso8601 UTC>",                # frozen at start; resume preserves
    "last_checkin_at":    "<iso8601 UTC>" | null,
    "commits_since_push": int,
    "checkin_interval_pct": int                            # default 50; from config.autonomy
  }

Push heuristic (Phase A — autonomous_push.py NOT yet wired; informational only):
  should_push_now = (commits_since_push >= batch_size) where batch_size from
  config.autonomy.batchSize (default 3). Orchestrator may choose to ignore.

Sub-5ms target: one file read, no subprocesses, no network. Zero deps. Python 3.11+.

Exit code is always 0 — graceful degradation for missing state, missing block,
malformed JSON. The envelope's `reason` field surfaces what went wrong so the
orchestrator can decide how to react. Never crash a long run on a telemetry
parse error.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_BUDGET_SECONDS = 2 * 60 * 60          # 2h
LONG_BUDGET_SECONDS = 8 * 60 * 60             # 8h
DEFAULT_CHECKIN_INTERVAL_PCT = 50
DEFAULT_BATCH_SIZE = 3
VALID_MODES = {"default", "long", "custom"}


def _iso_to_dt(s: str) -> datetime | None:
    """Parse an ISO 8601 timestamp; accepts trailing 'Z' or explicit offset."""
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_state(workdir: Path) -> dict | None:
    p = workdir / ".build-loop" / "state.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _read_config(workdir: Path) -> dict:
    """Best-effort load of .build-loop/config.json.autonomy. Always returns a dict."""
    p = workdir / ".build-loop" / "config.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    aut = data.get("autonomy")
    return aut if isinstance(aut, dict) else {}


def _empty_envelope(reason: str) -> dict[str, Any]:
    """Returned when no autonomous budget block is present (degraded mode)."""
    return {
        "within_budget": True,
        "remaining_seconds": 0,
        "remaining_pct": 100,
        "action": "continue",
        "should_push_now": False,
        "elapsed_since_last_checkin_s": 0,
        "mode": "default",
        "budget_seconds": 0,
        "used_seconds": 0,
        "reason": reason,
    }


def compute_envelope(
    state: dict | None,
    config_autonomy: dict | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure function — separated for unit testability."""
    now = now or datetime.now(timezone.utc)

    if state is None:
        return _empty_envelope("no state.json present — autonomous mode not active")

    execution = state.get("execution")
    if not isinstance(execution, dict):
        return _empty_envelope("state.json has no execution block — autonomous mode not active")

    budget = execution.get("budget")
    if not isinstance(budget, dict):
        return _empty_envelope("state.execution.budget missing — autonomous mode not active")

    mode = budget.get("mode")
    if mode not in VALID_MODES:
        mode = "default"

    started = _iso_to_dt(budget.get("started_at", ""))
    deadline = _iso_to_dt(budget.get("deadline_at", ""))
    if started is None or deadline is None:
        return _empty_envelope(f"budget timestamps malformed (started_at={budget.get('started_at')!r}, deadline_at={budget.get('deadline_at')!r})")

    budget_seconds = int((deadline - started).total_seconds())
    if budget_seconds <= 0:
        # Resume safety net: a malformed or zero-duration budget should still degrade gracefully.
        return _empty_envelope(f"budget duration non-positive ({budget_seconds}s) — treating as unbounded")

    used = int((now - started).total_seconds())
    remaining = budget_seconds - used
    within_budget = remaining > 0
    remaining_pct = max(0, min(100, int(round((remaining / budget_seconds) * 100)))) if budget_seconds else 100

    autonomy_cfg = config_autonomy if isinstance(config_autonomy, dict) else {}
    checkin_interval_pct = int(
        budget.get("checkin_interval_pct")
        or autonomy_cfg.get("checkinIntervalPct")
        or DEFAULT_CHECKIN_INTERVAL_PCT
    )
    if not (1 <= checkin_interval_pct <= 100):
        checkin_interval_pct = DEFAULT_CHECKIN_INTERVAL_PCT

    last_checkin = _iso_to_dt(budget.get("last_checkin_at", "")) or started
    elapsed_since_checkin = max(0, int((now - last_checkin).total_seconds()))
    checkin_interval_seconds = max(1, int(budget_seconds * (checkin_interval_pct / 100.0)))

    batch_size = int(autonomy_cfg.get("batchSize") or DEFAULT_BATCH_SIZE)
    commits_since_push = int(budget.get("commits_since_push") or 0)
    should_push_now = commits_since_push >= batch_size and within_budget

    # Action decision tree — strict order.
    if not within_budget:
        action = "finalize_and_stop"
        reason = f"budget elapsed ({used}s used / {budget_seconds}s budget)"
    elif elapsed_since_checkin >= checkin_interval_seconds:
        action = "checkin"
        reason = (
            f"check-in due: {elapsed_since_checkin}s since last check-in "
            f"(threshold {checkin_interval_seconds}s @ {checkin_interval_pct}% of budget)"
        )
    else:
        action = "continue"
        reason = f"within budget — {remaining}s remaining ({remaining_pct}%)"

    return {
        "within_budget": within_budget,
        "remaining_seconds": remaining,
        "remaining_pct": remaining_pct,
        "action": action,
        "should_push_now": should_push_now,
        "elapsed_since_last_checkin_s": elapsed_since_checkin,
        "mode": mode,
        "budget_seconds": budget_seconds,
        "used_seconds": used,
        "reason": reason,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Autonomous-mode wall-clock budget tracker (plan §14.4 B).")
    p.add_argument("--workdir", required=True, help="Project root containing .build-loop/")
    p.add_argument("--now", default=None, help="Override 'now' for testing (ISO 8601 UTC)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    workdir = Path(args.workdir).resolve()

    now: datetime | None = None
    if args.now:
        parsed = _iso_to_dt(args.now)
        if parsed is None:
            print(f"warn: --now {args.now!r} unparseable; using real-time", file=sys.stderr)
        else:
            now = parsed

    state = _read_state(workdir)
    config_autonomy = _read_config(workdir)
    envelope = compute_envelope(state, config_autonomy, now=now)

    print(json.dumps(envelope, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
