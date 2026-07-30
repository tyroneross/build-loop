# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for cost_rca.py — measured-token aggregation for cost-impact RCA."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cost_rca as c  # noqa: E402


def test_estimate_only_row_excluded_from_measured():
    est = {"model": "m", "tokens_estimate": 5000, "tokens_source": "estimate"}
    assert c._is_measured(est) is False
    real = {"model": "m", "input_tokens": 100, "output_tokens": 10}
    assert c._is_measured(real) is True


def test_bucket_split_and_totals():
    rows = [
        {"model": "claude-sonnet-5", "input_tokens": 100, "output_tokens": 20,
         "cache_read_input_tokens": 400, "cache_creation_input_tokens": 50},
        {"model": "claude-sonnet-5", "input_tokens": 100, "output_tokens": 20},
    ]
    agg = c.aggregate(rows, group_by="model")
    assert agg["measured_rows"] == 2
    assert agg["total"] == {"inbound": 200, "outbound": 40, "cache_read": 400, "cache_write": 50}
    assert agg["total_tokens"] == 690
    assert agg["by_model"]["claude-sonnet-5"]["cache_read"] == 400


def test_estimate_rows_surfaced_separately():
    rows = [
        {"model": "m", "input_tokens": 100, "output_tokens": 10},
        {"model": "m", "tokens_estimate": 3000, "tokens_source": "estimate"},
    ]
    agg = c.aggregate(rows)
    assert agg["measured_rows"] == 1
    assert agg["estimate_only_rows"] == 1
    assert agg["estimate_only_tokens"] == 3000


def test_field_aliases():
    rows = [{"model": "m", "prompt_tokens": 10, "completion_tokens": 5, "cache_read": 7}]
    agg = c.aggregate(rows)
    assert agg["total"]["inbound"] == 10
    assert agg["total"]["outbound"] == 5
    assert agg["total"]["cache_read"] == 7


def test_context_utilization():
    rows = [{"model": "claude-haiku-4-5", "input_tokens": 100_000, "output_tokens": 10}]
    agg = c.aggregate(rows)
    u = agg["context_utilization"]["claude-haiku-4-5"]
    assert u["context_window"] == 200_000
    assert u["utilization_pct"] == 50.0


def test_est_cost_usd_never_used():
    # A row carrying est_cost_usd must not leak a dollar figure into the aggregate.
    rows = [{"model": "m", "input_tokens": 10, "output_tokens": 1, "est_cost_usd": 9.99}]
    agg = c.aggregate(rows)
    blob = json.dumps(agg)
    assert "9.99" not in blob
    assert "price" in agg["note"].lower()


def test_load_rows_filters_run_id(tmp_path):
    led = tmp_path / "l.jsonl"
    led.write_text(
        json.dumps({"run_id": "A", "input_tokens": 1, "output_tokens": 1}) + "\n"
        + json.dumps({"run_id": "B", "input_tokens": 9, "output_tokens": 9}) + "\n"
    )
    rows = c.load_rows(led, run_id="A")
    assert len(rows) == 1 and rows[0]["run_id"] == "A"
