#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
RUNNER = HERE / "self_review_run.sh"


def test_runner_is_valid_bash() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(RUNNER)], capture_output=True, text=True, timeout=30
    )
    assert completed.returncode == 0, completed.stderr


def test_proposal_consumer_runs_before_light_mode_exit() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    drain_at = text.index("scripts/drain_self_review_proposals.py")
    light_exit_at = text.index('if [[ "$MODE" == "light" ]]')
    assert drain_at < light_exit_at
    assert "--archive" in text[drain_at:light_exit_at]


def test_proposal_consumer_is_fail_soft() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    drain_line = next(
        line for line in text.splitlines() if "scripts/drain_self_review_proposals.py" in line
    )
    assert "python3" in drain_line
    # The continued command is captured with `|| true`, preserving launchd's
    # fail-soft contract when maintenance cannot run.
    drain_block = text[text.index(drain_line): text.index(drain_line) + 240]
    assert "|| true" in drain_block
