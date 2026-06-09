#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for host_capabilities."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_capabilities as hc  # noqa: E402


def test_detect_explicit_tool_ids():
    assert hc.detect_host("claude_code:audit", env={}) == "claude_code"
    assert hc.detect_host("codex-01", env={}) == "codex"
    assert hc.detect_host("gpt", env={}) == "codex"
    assert hc.detect_host("cc", env={}) == "claude_code"
    assert hc.detect_host("gemini", env={}) == "gemini"
    assert hc.detect_host("weirdtool", env={}) == "unknown"


def test_detect_env_heuristics():
    assert hc.detect_host(None, env={"CODEX_HOME": "/x"}) == "codex"
    assert hc.detect_host(None, env={"CLAUDE_PLUGIN_ROOT": "/x"}) == "claude_code"
    assert hc.detect_host(None, env={"GEMINI_API_KEY": "x"}) == "gemini"
    assert hc.detect_host(None, env={}) == "unknown"


def test_explicit_tool_beats_env():
    # explicit tool id wins over conflicting env
    assert hc.detect_host("codex", env={"CLAUDE_PLUGIN_ROOT": "/x"}) == "codex"


def test_capabilities_host_specific():
    assert hc.capabilities("claude_code")["schedule_wakeup"] is True
    assert hc.capabilities("codex")["schedule_wakeup"] is False
    assert hc.capabilities("codex")["auto_reinvoke"] is False
    # poll + os_scheduler are universal
    for host in ("claude_code", "codex", "gemini", "unknown"):
        caps = hc.capabilities(host)
        assert caps["poll"] is True
        assert caps["os_scheduler"] is True


def test_wake_tiers_ordering_and_membership():
    cc = hc.wake_tiers("claude_code")
    assert cc[0] == "auto_reinvoke" and "schedule_wakeup" in cc and "poll" in cc
    cx = hc.wake_tiers("codex")
    assert "schedule_wakeup" not in cx and "auto_reinvoke" not in cx
    assert "poll" in cx and "os_scheduler" in cx


def test_resolve_and_main():
    out = hc.resolve("codex")
    assert out["host"] == "codex" and out["known"] is True
    assert hc.main(["--tool", "claude_code", "--json"]) == 0
