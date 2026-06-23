# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for coordination_policy.load_policy (config tunable resolution)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import coordination_policy as cp  # noqa: E402


def _write_config(workdir: Path, obj: dict) -> None:
    d = workdir / ".build-loop"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(obj), encoding="utf-8")


def test_defaults_when_no_config(tmp_path: Path):
    pol = cp.load_policy(tmp_path)
    assert pol == cp.CoordinationPolicy()
    assert pol.half_life_hours == 48.0
    assert pol.archive_floor_weight == 0.05
    assert pol.reclaim_small_minutes == 30
    assert pol.reclaim_large_minutes == 120
    assert pol.half_life_secs == 48 * 3600


def test_config_overrides_defaults(tmp_path: Path):
    _write_config(
        tmp_path,
        {
            "coordinationPolicy": {
                "half_life_hours": 24,
                "archive_floor_weight": 0.1,
                "reclaim_small_minutes": 10,
                "reclaim_large_minutes": 60,
            }
        },
    )
    pol = cp.load_policy(tmp_path)
    assert pol.half_life_hours == 24.0
    assert pol.archive_floor_weight == 0.1
    assert pol.reclaim_small_minutes == 10
    assert pol.reclaim_large_minutes == 60


def test_partial_config_keeps_other_defaults(tmp_path: Path):
    _write_config(tmp_path, {"coordinationPolicy": {"half_life_hours": 72}})
    pol = cp.load_policy(tmp_path)
    assert pol.half_life_hours == 72.0
    assert pol.archive_floor_weight == 0.05  # default kept


def test_malformed_values_ignored(tmp_path: Path):
    _write_config(
        tmp_path,
        {
            "coordinationPolicy": {
                "half_life_hours": -5,  # non-positive -> ignored
                "archive_floor_weight": 2.0,  # out of (0,1) -> ignored
                "reclaim_small_minutes": "nope",  # non-int -> ignored
                "reclaim_large_minutes": 0,  # non-positive -> ignored
            }
        },
    )
    pol = cp.load_policy(tmp_path)
    assert pol == cp.CoordinationPolicy()


def test_malformed_json_falls_back_to_defaults(tmp_path: Path):
    d = tmp_path / ".build-loop"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{not json", encoding="utf-8")
    assert cp.load_policy(tmp_path) == cp.CoordinationPolicy()


def test_bool_rejected_for_int_fields(tmp_path: Path):
    _write_config(tmp_path, {"coordinationPolicy": {"reclaim_small_minutes": True}})
    pol = cp.load_policy(tmp_path)
    assert pol.reclaim_small_minutes == 30  # bool rejected, default kept
