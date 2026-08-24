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
    DEFAULT_CLOUD_TOKEN_BUDGET,
    DEFAULT_MAX,
    HARD_CEILING,
    classify_execution_location,
    classify_model_size,
    estimate_tokens_per_worker,
    effective_max_implementers,
    measured_tokens_per_worker,
    plan_batches,
    partition_overlap,
    describe,
    resolve_fanout,
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
    def test_no_config_adds_conservative_cloud_token_cap(self, tmp_workdir: Path) -> None:
        cpu = os.cpu_count() or 4
        budget = max(1, cpu - 2)
        token_cap = DEFAULT_CLOUD_TOKEN_BUDGET // estimate_tokens_per_worker()
        expected = max(1, min(DEFAULT_MAX, budget, HARD_CEILING, token_cap))
        assert effective_max_implementers(tmp_workdir) == expected


class TestEffectiveMaxWithConfig:
    def test_config_10_capped_by_budget(self, workdir_with_config) -> None:
        cpu = os.cpu_count() or 4
        if cpu < 4:
            pytest.skip("machine has too few cores for this scenario")
        wd = workdir_with_config(10)
        budget = max(1, cpu - 2)
        token_cap = DEFAULT_CLOUD_TOKEN_BUDGET // estimate_tokens_per_worker()
        expected = max(1, min(10, budget, HARD_CEILING, token_cap))
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

    def test_config_50_remains_cpu_bounded_on_local_machine(self, workdir_with_config) -> None:
        wd = workdir_with_config(50)
        with patch("parallelism.os.cpu_count", return_value=16):
            assert effective_max_implementers(
                wd, execution_location="local", model_size="small"
            ) == 14


class TestEffectiveMaxRequested:
    def test_requested_overrides_config(self, workdir_with_config) -> None:
        wd = workdir_with_config(10)
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(wd, requested=3)
        assert result == 3

    def test_requested_still_capped_by_cpu_capacity(self, workdir_with_config) -> None:
        wd = workdir_with_config(2)
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(
                wd, requested=100, execution_location="local", model_size="small"
            )
        assert result == 14

    def test_requested_capped_by_budget(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=4):
            # budget = max(1, 4-2) = 2
            result = effective_max_implementers(
                tmp_workdir, requested=8, execution_location="local", model_size="small"
            )
        assert result == 2


class TestEffectiveMaxFloor:
    def test_floor_at_1_when_cpu_count_is_2(self, tmp_workdir: Path) -> None:
        """cpu_count=2 → budget=max(1,0)=1 → effective=1."""
        with patch("parallelism.os.cpu_count", return_value=2):
            assert effective_max_implementers(tmp_workdir, execution_location="local") == 1

    def test_floor_at_1_when_cpu_count_is_1(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=1):
            assert effective_max_implementers(tmp_workdir, execution_location="local") == 1


class TestEffectiveMaxFailSoft:
    def test_unparseable_config_json(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".build-loop"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text("NOT JSON {{{")
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(tmp_path)
        # Falls back to DEFAULT_MAX; on 16-core machine budget=14, ceiling=12
        assert result == min(
            DEFAULT_MAX,
            14,
            HARD_CEILING,
            DEFAULT_CLOUD_TOKEN_BUDGET // estimate_tokens_per_worker(),
        )

    def test_missing_parallelism_key(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".build-loop"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps({"other": "stuff"}))
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(tmp_path)
        assert result == min(
            DEFAULT_MAX,
            14,
            HARD_CEILING,
            DEFAULT_CLOUD_TOKEN_BUDGET // estimate_tokens_per_worker(),
        )

    def test_missing_config_file(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=16):
            result = effective_max_implementers(tmp_workdir)
        assert result == min(
            DEFAULT_MAX,
            14,
            HARD_CEILING,
            DEFAULT_CLOUD_TOKEN_BUDGET // estimate_tokens_per_worker(),
        )


class TestResourceAwareFanout:
    def test_absolute_ceiling_is_reachable_only_when_every_cap_allows_it(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=256):
            profile = resolve_fanout(
                tmp_workdir,
                requested=300,
                execution_location="cloud",
                model_size="small",
                token_budget=3_000_000,
                independent_items=240,
                shared_capacity=200,
            )
        assert HARD_CEILING == 150
        assert profile["effective_max"] == 150
        assert profile["limiting_factors"] == ["hard_ceiling"]

    def test_independent_work_and_other_sessions_reduce_admission(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=64):
            profile = resolve_fanout(
                tmp_workdir,
                requested=80,
                execution_location="cloud",
                model_size="small",
                token_budget=1_000_000,
                independent_items=9,
                shared_capacity=12,
                active_elsewhere=5,
            )
        assert profile["available_shared_capacity"] == 7
        assert profile["effective_max"] == 7
        assert profile["limiting_factors"] == ["shared_capacity"]

    def test_exhausted_shared_capacity_admits_zero_workers(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=64):
            profile = resolve_fanout(
                tmp_workdir,
                requested=20,
                execution_location="local",
                model_size="small",
                shared_capacity=150,
                active_elsewhere=150,
            )
        assert profile["available_shared_capacity"] == 0
        assert profile["effective_max"] == 0

    def test_zero_shared_capacity_is_binding(self, tmp_workdir: Path) -> None:
        profile = resolve_fanout(
            tmp_workdir,
            requested=10,
            independent_items=10,
            shared_capacity=0,
        )
        assert profile["available_shared_capacity"] == 0
        assert profile["effective_max"] == 0
        assert "shared_capacity" in profile["limiting_factors"]

    def test_cloud_uses_measured_tokens_as_primary_cap(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=16):
            profile = resolve_fanout(
                tmp_workdir,
                requested=8,
                execution_location="cloud",
                token_budget=100_000,
                measured_tokens=40_000,
            )
        assert profile["primary_constraint"] == "token"
        assert profile["token_estimate_source"] == "measured"
        assert profile["token_cap"] == 2
        assert profile["effective_max"] == 2

    def test_cloud_falls_back_to_model_and_output_tshirts(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=16):
            profile = resolve_fanout(
                tmp_workdir,
                requested=8,
                execution_location="cloud",
                model="gpt-5.6-terra",
                output_size="large",
                effort="xhigh",
                token_budget=100_000,
            )
        assert profile["model_size"] == "medium"
        assert profile["token_estimate_source"] == "heuristic"
        assert profile["tokens_per_worker"] == 49_000
        assert profile["effective_max"] == 2

    def test_agentic_code_uses_high_effort_when_unspecified(self, tmp_workdir: Path) -> None:
        profile = resolve_fanout(
            tmp_workdir,
            execution_location="cloud",
            model="gpt-5.6-terra",
            segment="agentic_execution",
            tier="code",
            token_budget=100_000,
        )
        assert profile["effort"] == "high"
        assert profile["effort_source"] == "role-preferred"

    def test_explicit_effort_preserves_dispatch_flexibility(self, tmp_workdir: Path) -> None:
        profile = resolve_fanout(
            tmp_workdir,
            model="sonnet",
            segment="agentic_execution",
            tier="code",
            effort="medium",
        )
        assert profile["effort"] == "medium"
        assert profile["effort_source"] == "explicit"

    def test_missing_role_preserves_medium_fallback(self, tmp_workdir: Path) -> None:
        profile = resolve_fanout(tmp_workdir, model="sonnet")
        assert profile["effort"] == "medium"
        assert profile["effort_source"] == "fallback"

    def test_sol_high_is_more_conservative_than_terra_medium(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=16):
            terra = resolve_fanout(
                tmp_workdir, model="gpt-5.6-terra", effort="medium", token_budget=96_000
            )
            sol = resolve_fanout(
                tmp_workdir, model="gpt-5.6-sol", effort="high", token_budget=96_000
            )
        assert terra["effective_max"] == 6
        assert sol["effective_max"] == 2

    def test_local_small_model_is_cpu_led(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=10):
            profile = resolve_fanout(
                tmp_workdir,
                requested=8,
                execution_location="local",
                model_size="small",
            )
        assert profile["primary_constraint"] == "cpu"
        assert profile["token_budget"] is None
        assert profile["cpu_cap"] == 8
        assert profile["effective_max"] == 8

    def test_local_large_model_reserves_more_cpu(self, tmp_workdir: Path) -> None:
        with patch("parallelism.os.cpu_count", return_value=10):
            profile = resolve_fanout(
                tmp_workdir,
                requested=8,
                execution_location="local",
                model_size="large",
            )
        assert profile["cpu_per_worker"] == 4
        assert profile["cpu_cap"] == 2
        assert profile["effective_max"] == 2

    def test_exact_measured_rows_override_heuristic(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.jsonl"
        ledger.write_text(
            "\n".join(
                json.dumps({
                    "model": "cloud-code",
                    "agent": "implementer",
                    "status": "completed",
                    "input_tokens": inp,
                    "output_tokens": out,
                })
                for inp, out in ((10_000, 2_000), (20_000, 4_000), (30_000, 6_000))
            ) + "\n"
        )
        assert measured_tokens_per_worker(
            ledger, model="cloud-code", agent="implementer"
        ) == 24_000

    def test_location_inference_uses_provider_not_open_weight_name(self) -> None:
        assert classify_execution_location("qwen-32b", provider="cloud") == "cloud"
        assert classify_execution_location("qwen-32b", provider="ollama") == "local"

    def test_model_size_classification(self) -> None:
        assert classify_model_size("gpt-5.6-sol") == "xlarge"
        assert classify_model_size("gpt-5.6-terra") == "medium"
        assert classify_model_size("claude-haiku") == "small"


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
        expected_keys = {
            "cpu_count", "cpu_budget", "config_max", "hard_ceiling",
            "effective_max", "primary_constraint", "token_estimate_source",
            "tokens_per_worker", "cpu_cap", "token_cap",
        }
        assert expected_keys <= set(result.keys())

    def test_numeric_caps_are_positive_ints(self, tmp_workdir: Path) -> None:
        result = describe(tmp_workdir)
        for key in ("cpu_count", "cpu_budget", "config_max", "hard_ceiling", "effective_max"):
            val = result[key]
            assert isinstance(val, int) and val >= 1, f"{key}={val!r} should be a positive int"

    def test_effective_max_consistent(self, tmp_workdir: Path) -> None:
        d = describe(tmp_workdir)
        assert d["effective_max"] == effective_max_implementers(tmp_workdir)

    def test_hard_ceiling_constant(self, tmp_workdir: Path) -> None:
        assert describe(tmp_workdir)["hard_ceiling"] == HARD_CEILING


# ---------------------------------------------------------------------------
# partition_overlap — MECE non-overlap check for parallel dispatch
# ---------------------------------------------------------------------------

class TestPartitionOverlap:
    def test_disjoint_partition_is_clean(self) -> None:
        assignments = {"A": ["src/a.ts", "src/b.ts"], "B": ["src/c.ts"]}
        assert partition_overlap(assignments) == {}

    def test_overlapping_file_reported_with_all_claimants(self) -> None:
        assignments = {"A": ["src/shared.ts"], "B": ["src/shared.ts"], "C": ["src/c.ts"]}
        overlaps = partition_overlap(assignments)
        assert overlaps == {"src/shared.ts": ["A", "B"]}

    def test_trailing_slash_and_whitespace_normalized(self) -> None:
        assignments = {"A": ["scripts/x.py/"], "B": [" scripts/x.py "]}
        assert partition_overlap(assignments) == {"scripts/x.py": ["A", "B"]}

    def test_empty_paths_ignored(self) -> None:
        assignments = {"A": ["", "  "], "B": ["src/c.ts"]}
        assert partition_overlap(assignments) == {}

    def test_same_agent_listing_a_path_twice_is_not_an_overlap(self) -> None:
        assignments = {"A": ["src/a.ts", "src/a.ts"]}
        assert partition_overlap(assignments) == {}

    def test_three_way_overlap_sorted(self) -> None:
        assignments = {"C": ["f"], "A": ["f"], "B": ["f"]}
        assert partition_overlap(assignments) == {"f": ["A", "B", "C"]}
