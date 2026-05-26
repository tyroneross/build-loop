# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the embedded agent-rally plugin boundary manifest."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from rally_point import boundary  # noqa: E402


def test_boundary_manifest_validates_current_checkout():
    result = boundary.validate_manifest(REPO_ROOT)
    assert result["ok"], result["findings"]
    assert result["plugins"] == ["agent-rally-point", "agent-rally-watcher"]
    assert result["skill_entrypoints"] == {
        "agent-rally-point": ["skills/agent-rally-point/SKILL.md"],
        "agent-rally-watcher": ["skills/agent-rally-watcher/SKILL.md"],
    }


def test_boundary_cli_check_json():
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "rally_point" / "boundary.py"),
            "--repo",
            str(REPO_ROOT),
            "--check",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert "agent-rally-point" in payload["plugins"]
    assert "agent-rally-watcher" in payload["plugins"]
    assert payload["skill_entrypoints"]["agent-rally-point"] == [
        "skills/agent-rally-point/SKILL.md"
    ]
