#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for wake_scheduler — host-portable self-resume planner."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wake_scheduler as ws  # noqa: E402


def test_cache_safe_delay_avoids_deadzone():
    assert ws.cache_safe_delay(30) == 60        # clamp up to floor
    assert ws.cache_safe_delay(60) == 60
    assert ws.cache_safe_delay(270) == 270      # warm window kept
    assert ws.cache_safe_delay(300) == 1200     # dead-zone → push up
    assert ws.cache_safe_delay(600) == 1200
    assert ws.cache_safe_delay(1200) == 1200
    assert ws.cache_safe_delay(5000) == 3600    # clamp to ceiling


def test_reversibility_gate_stops_on_irreversible():
    for verdict in ("confirm", "block"):
        env = ws.plan_wake("claude_code", ws.EXTERNAL, next_action="git push prod",
                            action_class=verdict)
        assert env["decision"] == "stop_surface_human"
        assert env["tier"] is None


def test_gate_unavailable_fails_safe():
    env = ws.plan_wake("claude_code", ws.EXTERNAL, next_action="something",
                       action_class="unavailable")
    assert env["decision"] == "stop_surface_human"


def test_reversible_action_proceeds():
    env = ws.plan_wake("claude_code", ws.EXTERNAL, next_action="git commit -m x",
                       action_class="auto")
    assert env["decision"] == "self_wake"


def test_host_portability_claude_vs_codex_external():
    cc = ws.plan_wake("claude_code", ws.EXTERNAL, action_class="auto")
    assert cc["tier"] == "schedule_wakeup"          # Claude has it
    cx = ws.plan_wake("codex", ws.EXTERNAL, action_class="auto")
    assert cx["tier"] != "schedule_wakeup"          # Codex must NOT use a Claude-only feature
    assert cx["tier"] in ("poll", "os_scheduler")
    assert cx["decision"] == "self_wake"


def test_tracked_subwork_auto_reinvoke_only_where_available():
    cc = ws.plan_wake("claude_code", ws.TRACKED, action_class="auto")
    assert cc["decision"] == "harness_auto" and cc["tier"] == "auto_reinvoke"
    cx = ws.plan_wake("codex", ws.TRACKED, action_class="auto")
    assert cx["decision"] == "self_wake" and cx["tier"] in ("poll", "os_scheduler")


def test_survive_shutdown_prefers_cron_then_os():
    cc = ws.plan_wake("claude_code", ws.EXTERNAL, action_class="auto", survive_shutdown=True)
    assert cc["tier"] == "cron"
    cx = ws.plan_wake("codex", ws.EXTERNAL, action_class="auto", survive_shutdown=True)
    assert cx["tier"] == "os_scheduler"             # no cron on codex → next survivable tier


def test_audience_extracted_from_intent(tmp_path: Path):
    intent = tmp_path / "intent.md"
    intent.write_text(
        "# Intent\n- App/repo purpose: what this is for and who it serves: build-loop agents\n"
        "## Restated intent\nMake loops self-resume across hosts.\n",
        encoding="utf-8",
    )
    env = ws.plan_wake("codex", ws.EXTERNAL, action_class="auto", intent_path=str(intent))
    assert "build-loop agents" in env["audience"]["serves"]
    assert "self-resume" in env["audience"]["restated_intent"]


def test_self_wake_carries_reinjection_ref():
    env = ws.plan_wake("codex", ws.PEER_ACK, action_class="auto", prompt_ref="resume X")
    assert env["reinject_prompt_ref"] == "resume X"


def test_main_exit_zero():
    assert ws.main(["--tool", "codex", "--wait-kind", "external", "--json"]) == 0
