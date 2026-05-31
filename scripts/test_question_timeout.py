#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for question_timeout.py — autonomous-run pending-question resolver.

Guards the contract: SAFE/RISKY questions auto-take the default past the window;
PRODUCTION/irreversible questions NEVER auto-resolve (the single gate holds);
within-window always waits; an answered question is terminal.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import question_timeout as qt  # noqa: E402

_T0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


def _resolve(elapsed_min, classify="SAFE", on_timeout="decide_default", answered=False, timeout=10):
    return qt.resolve(
        posted_at=_T0,
        now=_T0 + timedelta(minutes=elapsed_min),
        timeout_minutes=timeout,
        on_timeout=on_timeout,
        classify=classify,
        answered=answered,
    )


# ---- pure decision logic ---------------------------------------------------

def test_within_window_waits():
    r = _resolve(elapsed_min=5)
    assert r["decision"] == "wait"
    assert r["remaining_seconds"] == 5 * 60


def test_past_window_takes_default():
    r = _resolve(elapsed_min=11)
    assert r["decision"] == "take_default"
    assert r["remaining_seconds"] == 0


def test_exactly_at_window_takes_default():
    r = _resolve(elapsed_min=10)
    assert r["decision"] == "take_default"


def test_production_never_auto_resolves_even_long_past_window():
    r = _resolve(elapsed_min=600, classify="PRODUCTION")
    assert r["decision"] == "wait"
    assert r["production_hold"] is True


def test_confirm_and_block_also_held():
    for c in ("CONFIRM", "BLOCK", "production"):  # case-insensitive
        r = _resolve(elapsed_min=999, classify=c)
        assert r["decision"] == "wait", c
        assert r["production_hold"] is True, c


def test_on_timeout_wait_disables_auto_decide():
    r = _resolve(elapsed_min=999, on_timeout="wait")
    assert r["decision"] == "wait"
    assert r["production_hold"] is False


def test_answered_is_terminal():
    r = _resolve(elapsed_min=999, answered=True)
    assert r["decision"] == "answered"


# ---- config + CLI ----------------------------------------------------------

def test_cli_reads_config_timeout(tmp_path: Path):
    bl = tmp_path / ".build-loop"
    bl.mkdir()
    (bl / "config.json").write_text(json.dumps({"autonomy": {"questionTimeoutMinutes": 30}}))
    posted = _T0.isoformat()
    now = (_T0 + timedelta(minutes=20)).isoformat()  # past 10 but within 30
    out = subprocess.run(
        [sys.executable, str(_HERE / "question_timeout.py"),
         "--workdir", str(tmp_path), "--posted-at", posted, "--now", now,
         "--default", "Recommended", "--classify", "SAFE"],
        capture_output=True, text=True,
    )
    env = json.loads(out.stdout)
    assert env["timeout_minutes"] == 30
    assert env["decision"] == "wait"  # 20m < 30m window


def test_cli_take_default_echoes_default(tmp_path: Path):
    posted = _T0.isoformat()
    now = (_T0 + timedelta(minutes=15)).isoformat()
    out = subprocess.run(
        [sys.executable, str(_HERE / "question_timeout.py"),
         "--workdir", str(tmp_path), "--posted-at", posted, "--now", now,
         "--default", "Use Postgres", "--classify", "DECISION"],
        capture_output=True, text=True,
    )
    env = json.loads(out.stdout)
    assert env["decision"] == "take_default"
    assert env["default"] == "Use Postgres"
    assert out.returncode == 0


def test_cli_bad_timestamp_degrades_to_wait(tmp_path: Path):
    out = subprocess.run(
        [sys.executable, str(_HERE / "question_timeout.py"),
         "--workdir", str(tmp_path), "--posted-at", "not-a-date"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0
    assert json.loads(out.stdout)["decision"] == "wait"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
