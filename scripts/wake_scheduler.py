#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Host-portable self-resume planner — `wake_when` decision engine.

Given a wait the loop is about to enter, decide ONE of:
  - stop_surface_human : the pending next action is irreversible/production
                         (autonomy_gate verdict confirm/block) → the user is truly needed
  - harness_auto       : tracked sub-work; the host re-invokes us for free (no directive)
  - self_wake          : schedule our own continuation via the best AVAILABLE host tier

It is host-portable BY DESIGN: ScheduleWakeup is only emitted on hosts that have it
(`host_capabilities`); Codex/Gemini/others get a poll or os_scheduler directive; full
shutdown survival routes to cron where available. The caller (host adapter) executes
the directive — this script never calls a host tool itself.

Two lessons baked in:
  1. Cache-window economics: chosen delay avoids the 300s prompt-cache dead-zone.
  2. Re-inject, never rely on memory: the directive carries the prompt + intent ref,
     and the envelope surfaces the loop's AUDIENCE (who it serves) from intent.md.

stdout = one JSON envelope; exit 0 always (mirrors budget_check.py / question_timeout.py).

Usage:
    python3 scripts/wake_scheduler.py --tool <id> --wait-kind <kind> \
        [--next-action "<cmd>"] [--desired-seconds N] [--survive-shutdown] \
        [--intent .build-loop/intent.md] [--prompt-ref "<resume prompt>"] --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:  # same-dir import whether run as script or imported
    from host_capabilities import capabilities, detect_host
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from host_capabilities import capabilities, detect_host

# wait kinds the loop can enter
TRACKED = "tracked_subwork"   # harness-tracked Agent/Task → auto re-invoke where available
EXTERNAL = "external"          # CI, remote queue, external API the harness can't observe
PEER_ACK = "peer_ack"          # waiting on a rally peer (Codex etc.)

CACHE_WARM_CEIL = 270          # stay under the 300s prompt-cache TTL
CACHE_DEADZONE_TOP = 1200      # if past warm, commit to >= 20min (amortize the miss)
CLAMP_MIN, CLAMP_MAX = 60, 3600


def cache_safe_delay(desired: int) -> int:
    """Snap a desired wait away from the 300s cache dead-zone, then clamp [60,3600]."""
    d = max(CLAMP_MIN, int(desired))
    if d <= CACHE_WARM_CEIL:
        pass                                  # warm window — keep
    elif d < CACHE_DEADZONE_TOP:
        d = CACHE_DEADZONE_TOP                # in the dead-zone — push up to amortize
    return min(d, CLAMP_MAX)


def classify_action(command: str, workdir: str | None = None) -> str:
    """Return autonomy_gate verdict: auto|warn|confirm|block|unavailable."""
    if not command:
        return "auto"
    gate = Path(__file__).resolve().parent / "autonomy_gate.py"
    if not gate.exists():
        return "unavailable"
    try:
        cp = subprocess.run(
            [sys.executable, str(gate), "--command", command, "--json"]
            + (["--workdir", workdir] if workdir else []),
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(cp.stdout.strip().splitlines()[-1])
        return data.get("action", "unavailable")
    except (subprocess.SubprocessError, ValueError, OSError, IndexError):
        return "unavailable"


def read_audience(intent_path: str | None) -> dict[str, str]:
    """Best-effort extract of who the loop serves + restated intent from intent.md."""
    out = {"serves": "", "restated_intent": ""}
    if not intent_path:
        return out
    p = Path(intent_path)
    if not p.exists():
        return out
    txt = p.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"who it serves[:\s]*([^\n]+)", txt, re.IGNORECASE)
    if m:
        out["serves"] = m.group(1).strip(" .*-")
    m = re.search(r"##\s*Restated intent\s*\n+([^\n]+)", txt, re.IGNORECASE)
    if m:
        out["restated_intent"] = m.group(1).strip()
    return out


def select_tier(host: str, wait_kind: str, survive_shutdown: bool) -> tuple[str, str]:
    """Pick the best available (tier, directive) for this host + wait shape."""
    caps = capabilities(host)
    if survive_shutdown:
        order = ["cron", "os_scheduler", "poll"]
    elif wait_kind == TRACKED:
        order = ["auto_reinvoke", "schedule_wakeup", "poll", "os_scheduler"]
    else:  # external / peer_ack
        order = ["schedule_wakeup", "poll", "os_scheduler", "cron"]
    for tier in order:
        if caps.get(tier):
            return tier, _directive(tier)
    return "poll", _directive("poll")  # universal floor


def _directive(tier: str) -> str:
    return {
        "auto_reinvoke": "none — harness re-invokes when tracked sub-work completes",
        "schedule_wakeup": "call ScheduleWakeup(delaySeconds, prompt) [Claude Code host]",
        "cron": "create a cloud routine / CronCreate [survives shutdown]",
        "os_scheduler": "register launchd/systemd timer (rally watch --print-launchd/--print-systemd)",
        "poll": "run scripts/coordination_watch.py --interval <delay> [universal fallback]",
    }[tier]


def plan_wake(
    tool_id: str | None,
    wait_kind: str,
    *,
    next_action: str | None = None,
    action_class: str | None = None,   # test override; else resolved via autonomy_gate
    desired_seconds: int = 270,
    survive_shutdown: bool = False,
    intent_path: str | None = None,
    prompt_ref: str | None = None,
    workdir: str | None = None,
) -> dict:
    host = detect_host(tool_id)
    audience = read_audience(intent_path)

    # 1) Reversibility gate — only stop when the user is TRULY needed.
    verdict = action_class if action_class is not None else (
        classify_action(next_action, workdir) if next_action else "auto"
    )
    envelope = {
        "host": host,
        "wait_kind": wait_kind,
        "audience": audience,
        "next_action_class": verdict,
    }
    if verdict in ("confirm", "block", "unavailable") and next_action:
        envelope.update({
            "decision": "stop_surface_human",
            "tier": None,
            "directive": "surface to user — pending action is irreversible/production"
                         + (" (gate unavailable; failing safe)" if verdict == "unavailable" else ""),
            "reason": f"autonomy_gate verdict={verdict}",
        })
        return envelope

    # 2) Tracked sub-work on an auto-reinvoke host → free re-entry, no directive.
    tier, directive = select_tier(host, wait_kind, survive_shutdown)
    if tier == "auto_reinvoke":
        envelope.update({
            "decision": "harness_auto",
            "tier": tier,
            "directive": directive,
            "delay_seconds": None,
        })
        return envelope

    # 3) Self-wake via the best available timed/poll tier.
    delay = cache_safe_delay(desired_seconds)
    envelope.update({
        "decision": "self_wake",
        "tier": tier,
        "directive": directive,
        "delay_seconds": delay,
        "cache_warm": delay <= CACHE_WARM_CEIL,
        "reinject_prompt_ref": prompt_ref or "(carry intent.md + plan.md — never rely on memory)",
    })
    return envelope


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Host-portable self-resume planner (wake_when).")
    p.add_argument("--tool", default=None)
    p.add_argument("--wait-kind", default=EXTERNAL, choices=[TRACKED, EXTERNAL, PEER_ACK])
    p.add_argument("--next-action", default=None, help="Pending command to classify for reversibility")
    p.add_argument("--desired-seconds", type=int, default=270)
    p.add_argument("--survive-shutdown", action="store_true")
    p.add_argument("--intent", default=None, help="Path to .build-loop/intent.md")
    p.add_argument("--prompt-ref", default=None)
    p.add_argument("--workdir", default=None)
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv if argv is not None else sys.argv[1:])
    env = plan_wake(
        a.tool, a.wait_kind, next_action=a.next_action, desired_seconds=a.desired_seconds,
        survive_shutdown=a.survive_shutdown, intent_path=a.intent, prompt_ref=a.prompt_ref,
        workdir=a.workdir,
    )
    print(json.dumps(env, indent=2) if a.json else f"{env['decision']} via {env.get('tier')} ({env.get('directive')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
