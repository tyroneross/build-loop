#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Cursor Shell PreToolUse must emit one JSON allow object even if rally is absent."""

from __future__ import annotations

import json
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"


def test_shell_matcher_always_prints_permission_allow():
    data = json.loads(HOOKS.read_text(encoding="utf-8"))
    pre = data["hooks"]["PreToolUse"]
    shell = [block for block in pre if block.get("matcher") == "Shell"]
    assert shell, "PreToolUse must include a Cursor Shell matcher"
    commands = [h["command"] for h in shell[0]["hooks"]]
    assert any("permission" in c and "allow" in c for c in commands)
    assert any("pre-edit-rally-point.sh" in c for c in commands)
    # Missing script must not leak bash errors onto stdout.
    assert any(">/dev/null" in c and "2>&1" in c for c in commands)
