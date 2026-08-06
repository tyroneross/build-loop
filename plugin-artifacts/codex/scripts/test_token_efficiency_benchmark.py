#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from token_efficiency_benchmark import compare, load_rows, measured_tokens  # noqa: E402


def _row(task: str, variant: str, *, tokens: int | None, passed: bool = True, snapshot: str = "sha1") -> dict:
    row = {
        "task_id": task,
        "variant": variant,
        "model": "gpt-5.6-terra",
        "snapshot": snapshot,
        "passed": passed,
        "escaped_defects": 0,
        "calls": 2,
        "duration_seconds": 10,
    }
    if tokens is not None:
        row["input_tokens"] = tokens - 100
        row["output_tokens"] = 100
    else:
        row["tokens_estimate"] = 99_999
    return row


def test_measured_tokens_excludes_estimates() -> None:
    assert measured_tokens({"tokens_estimate": 1000}) is None
    assert measured_tokens({"input_tokens": 700, "output_tokens": 300}) == 1000


def test_compare_uses_only_exact_repeat_pairs() -> None:
    rows = [
        _row("t1", "baseline", tokens=10_000),
        _row("t1", "candidate", tokens=6_000),
        _row("t2", "baseline", tokens=20_000, snapshot="sha-old"),
        _row("t2", "candidate", tokens=5_000, snapshot="sha-new"),
    ]
    result = compare(rows, baseline="baseline", candidate="candidate")
    paired = result["exact_repeat"]
    assert paired["pairs"] == 1
    assert paired["measured_pairs"] == 1
    assert paired["token_change_pct"] == -40.0
    assert paired["quality_non_inferior"] is True


def test_quality_regression_blocks_non_inferior_verdict() -> None:
    rows = [
        _row("t1", "baseline", tokens=10_000, passed=True),
        _row("t1", "candidate", tokens=4_000, passed=False),
    ]
    result = compare(rows, baseline="baseline", candidate="candidate")
    assert result["exact_repeat"]["token_change_pct"] == -60.0
    assert result["exact_repeat"]["quality_non_inferior"] is False


def test_unmeasured_pair_is_reported_without_token_claim() -> None:
    rows = [
        _row("t1", "baseline", tokens=None),
        _row("t1", "candidate", tokens=None),
    ]
    result = compare(rows, baseline="baseline", candidate="candidate")
    assert result["exact_repeat"]["pairs"] == 1
    assert result["exact_repeat"]["measured_pairs"] == 0
    assert result["exact_repeat"]["token_change_pct"] is None


def test_cli_and_input_validation(tmp_path: Path) -> None:
    results = tmp_path / "results.jsonl"
    results.write_text(
        "\n".join(json.dumps(row) for row in (
            _row("t1", "baseline", tokens=1000),
            _row("t1", "candidate", tokens=800),
        )) + "\n",
        encoding="utf-8",
    )
    loaded = load_rows(results)
    assert len(loaded) == 2
    completed = subprocess.run(
        [
            sys.executable,
            str(HERE / "token_efficiency_benchmark.py"),
            "--results", str(results),
            "--baseline", "baseline",
            "--candidate", "candidate",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["exact_repeat"]["token_change_pct"] == -20.0


def test_missing_required_field_fails(tmp_path: Path) -> None:
    results = tmp_path / "bad.jsonl"
    results.write_text(json.dumps({"task_id": "t1"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing variant"):
        load_rows(results)
