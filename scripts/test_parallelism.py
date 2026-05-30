#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/parallelism.py."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure scripts/ is importable when run directly via pytest <file>
import sys
sys.path.insert(0, str(Path(__file__).parent))

from parallelism import (
    DEFAULT_MAX,
    HARD_CEILING,
    effective_max_implementers,
    plan_batches,
    describe,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_workdir(tmp_path: Path) -> Path:
    """A workdir with no .build-loop/config.json present."""
    return tmp_path


@pytest.fixture()
def workdir_with_config(tmp_path: Path):
    """Factory: create a workdir with a given maxImplementers value."""
    def _factory(max_impl: int) -> Path:
        cfg_dir = tmp_path / ".build-loop"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(
            json.dumps({"parallelism": {"maxImplementers": max_impl}})
        )
        return tmp_path
    return _factory


# ---------------------------------------------------------------------------
# effective_max_implementers
# ---------------------------------------------------------------------------

class TestEffectiveMaxNoConfig:
    def test_no_config_returns_min_of_default_budget_ceiling(self, tmp_workdir: Path) -> None:
        cpu = os.cpu_count() or 4
        budget = max(1, cpu - 2)
        expected = max(1, min(DEFAULT_MAX, budget, HARD_CEILING))
        assert effective_max_implementers(tmp_workdir) == expected


class TestEffectiveMaxWithConfig:
    def test_config_10_capped_by_budget(self, workdir_with_config) -> None:
        cpu = os.cpu_count() or 4
        if cpu < 4:
            pytest.skip("machine has too few cores for this scenario")
        wd = workdir_with_config(10)
        budget = max(1, cpu - 2)
        expected = max(1, min(10, budget, HARD_CEILING))
        assert effective_max_implementers(wd) == expected

    def test_config_50_capped_at_hard_ceiling_or_budget(self, workdir_with_config) -> None:
        cpu = os.cpu_count() or 4
        wd = workdir_with_config(50)
        budget = max(1, cpu - 2)
        result = effective_max_implementers(wd)
        # Must never exceed HARD_CEILING
        assert result <= HARD_CEILING
        # Must never exceed cpu_budget
        assert result <= budget

    def test_config_50_is_12_on_beefy_machine(self, workdir_with_config) -> None:
        """On a machine with >=14 cores budget>=12, config=50 → HARD_CEILING=12."""
        wd = workdir_with_config(50)
        with patch("parallelism.os.cpu_count", return_value=16):
            assert effective_max_implementers(wd) == HARD_CEILING


class TestEffectiveMaxRequested:
    def test_requested_overrides_config(self, workdir_with_config) -> None:
        wd = workdir_with_config(10)
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(wd, requested=3)
        assert result == 3

    def test_requested_still_capped_by_hard_ceiling(self, workdir_with_config) -> None:
        wd = workdir_with_config(2)
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(wd, requested=100)
        assert result == HARD_CEILING

    def test_requested_capped_by_budget(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=4):
            # budget = max(1, 4-2) = 2
            result = effective_max_implementers(tmp_workdir, requested=8)
        assert result == 2


class TestEffectiveMaxFloor:
    def test_floor_at_1_when_cpu_count_is_2(self, tmp_workdir: Path) -> None:
        """cpu_count=2 → budget=max(1,0)=1 → effective=1."""
        with patch("parallelism.os.cpu_count", return_value=2):
            assert effective_max_implementers(tmp_workdir) == 1

    def test_floor_at_1_when_cpu_count_is_1(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=1):
            assert effective_max_implementers(tmp_workdir) == 1


class TestEffectiveMaxFailSoft:
    def test_unparseable_config_json(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".build-loop"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text("NOT JSON {{{")
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(tmp_path)
        # Falls back to DEFAULT_MAX; on 16-core machine budget=14, ceiling=12
        assert result == min(DEFAULT_MAX, 14, HARD_CEILING)

    def test_missing_parallelism_key(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".build-loop"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps({"other": "stuff"}))
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(tmp_path)
        assert result == min(DEFAULT_MAX, 14, HARD_CEILING)

    def test_missing_config_file(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(tmp_workdir)
        assert result == min(DEFAULT_MAX, 14, HARD_CEILING)


# ---------------------------------------------------------------------------
# plan_batches
# ---------------------------------------------------------------------------

class TestPlanBatches:
    def test_standard_batching(self) -> None:
        result = plan_batches(list(range(1, 11)), 4)
        assert result == [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10]]

    def test_batch_size_zero_treated_as_one(self) -> None:
        result = plan_batches([1, 2, 3], 0)
        assert result == [[1], [2], [3]]

    def test_batch_size_negative_treated_as_one(self) -> None:
        result = plan_batches([1, 2], -5)
        assert result == [[1], [2]]

    def test_empty_list(self) -> None:
        assert plan_batches([], 4) == []

    def test_single_item(self) -> None:
        assert plan_batches([42], 4) == [[42]]

    def test_exact_multiple(self) -> None:
        assert plan_batches([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]

    def test_batch_size_larger_than_list(self) -> None:
        assert plan_batches([1, 2], 10) == [[1, 2]]


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------

class TestDescribe:
    def test_all_keys_present(self, tmp_workdir: Path) -> None:
        result = describe(tmp_workdir)
        expected_keys = {"cpu_count", "cpu_budget", "config_max", "hard_ceiling", "effective_max"}
        assert expected_keys == set(result.keys())

    def test_values_are_positive_ints(self, tmp_workdir: Path) -> None:
        result = describe(tmp_workdir)
        for key, val in result.items():
            assert isinstance(val, int) and val >= 1, f"{key}={val!r} should be a positive int"

    def test_effective_max_consistent(self, tmp_workdir: Path) -> None:
        d = describe(tmp_workdir)
        assert d["effective_max"] == effective_max_implementers(tmp_workdir)

    def test_hard_ceiling_constant(self, tmp_workdir: Path) -> None:
        assert describe(tmp_workdir)["hard_ceiling"] == HARD_CEILING
