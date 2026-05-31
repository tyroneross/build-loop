#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Resolve a pending operator question during an autonomous/long run.

When the orchestrator surfaces a question in autonomous mode it states a
recommended default and a deadline. If the operator does not answer within the
window, the run must not stall — it takes the default, records the assumption,
and continues. This is the deterministic decision the orchestrator consults on
resume (mirrors budget_check.py: stdout = one JSON envelope, exit 0 always).

HARD carve-out: a question whose underlying action is production / irreversible
(autonomy-gate `confirm` or `block`) NEVER auto-resolves — it waits indefinitely
no matter how long it has been pending. The 10-min timeout only auto-decides
SAFE / RISKY / reversible questions. This preserves the single production gate.

Config (.build-loop/config.json.autonomy):
  questionTimeoutMinutes  int   default 10
  onTimeout               str   "decide_default" (default) | "wait"

Envelope:
  {
    "decision":          "answered" | "take_default" | "wait",
    "reason":            str,
    "default":           str | null,   # echoed when decision == take_default
    "elapsed_seconds":   int,
    "remaining_seconds": int,          # 0 when elapsed past window
    "timeout_minutes":   int,
    "on_timeout":        str,
    "production_hold":   bool          # true => held by the never-auto-resolve carve-out
  }
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Classifications that must never auto-resolve, regardless of elapsed time.
_NEVER_AUTO = {"PRODUCTION", "CONFIRM", "BLOCK"}
_DEFAULT_TIMEOUT_MIN = 10
_DEFAULT_ON_TIMEOUT = "decide_default"
_VALID_ON_TIMEOUT = {"decide_default", "wait"}


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; tolerate a trailing 'Z'. Returns aware UTC."""
    txt = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(txt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_autonomy_config(workdir: str) -> dict:
    cfg_path = Path(workdir) / ".build-loop" / "config.json"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    auto = data.get("autonomy")
    return auto if isinstance(auto, dict) else {}


def resolve(
    *,
    posted_at: datetime,
    now: datetime,
    timeout_minutes: int,
    on_timeout: str,
    classify: str,
    answered: bool,
) -> dict:
    """Pure decision function — all I/O resolved by the caller. Easy to unit-test."""
    elapsed = max(0, int((now - posted_at).total_seconds()))
    window = max(0, timeout_minutes * 60)
    remaining = max(0, window - elapsed)
    base = {
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "timeout_minutes": timeout_minutes,
        "on_timeout": on_timeout,
        "production_hold": False,
        "default": None,
    }

    if answered:
        return {**base, "decision": "answered", "reason": "operator answered"}

    # Production / irreversible questions never auto-resolve — the single gate holds.
    if classify.upper() in _NEVER_AUTO:
        return {
            **base,
            "decision": "wait",
            "production_hold": True,
            "reason": f"classify={classify}: production/irreversible never auto-resolves; waiting indefinitely",
        }

    if on_timeout == "wait":
        return {**base, "decision": "wait", "reason": "onTimeout=wait (auto-decide disabled)"}

    if elapsed >= window:
        return {
            **base,
            "decision": "take_default",
            "default": base["default"],  # filled by caller from --default
            "reason": f"no answer in {timeout_minutes}m ({elapsed}s elapsed); taking recommended default",
        }

    return {**base, "decision": "wait", "reason": f"within window; {remaining}s remaining"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--posted-at", required=True, help="ISO-8601 when the question was surfaced")
    p.add_argument("--now", default=None, help="ISO-8601 'now' (default: current UTC)")
    p.add_argument("--default", default=None, help="recommended default to take on timeout")
    p.add_argument(
        "--classify",
        default="SAFE",
        help="SAFE | RISKY | DECISION | PRODUCTION (PRODUCTION/CONFIRM/BLOCK never auto-resolve)",
    )
    p.add_argument("--answered", action="store_true", help="operator has answered")
    p.add_argument("--timeout-minutes", type=int, default=None, help="override config window")
    args = p.parse_args(argv)

    auto = _load_autonomy_config(args.workdir)
    timeout_minutes = (
        args.timeout_minutes
        if args.timeout_minutes is not None
        else int(auto.get("questionTimeoutMinutes", _DEFAULT_TIMEOUT_MIN))
    )
    on_timeout = str(auto.get("onTimeout", _DEFAULT_ON_TIMEOUT))
    if on_timeout not in _VALID_ON_TIMEOUT:
        on_timeout = _DEFAULT_ON_TIMEOUT

    try:
        posted = _parse_iso(args.posted_at)
        now = _parse_iso(args.now) if args.now else datetime.now(timezone.utc)
    except ValueError as e:
        print(json.dumps({"decision": "wait", "reason": f"bad timestamp: {e}"}), flush=True)
        return 0

    env = resolve(
        posted_at=posted,
        now=now,
        timeout_minutes=timeout_minutes,
        on_timeout=on_timeout,
        classify=args.classify,
        answered=args.answered,
    )
    if env["decision"] == "take_default":
        env["default"] = args.default
    print(json.dumps(env, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
