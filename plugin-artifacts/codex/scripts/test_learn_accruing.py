# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for learn_accruing.py — EC-01 accruing->miner bridge."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import learn_accruing as la  # noqa: E402


def test_fire_is_non_gating_and_creates_pending(tmp_path, monkeypatch):
    # Stub the miner subprocess so the test is hermetic + writes a candidates file.
    def fake_run(cmd, cwd, capture_output, text, timeout):
        # emulate the miner writing .candidates.json into --out-dir
        out_idx = cmd.index("--out-dir") + 1
        out_dir = Path(cmd[out_idx])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / ".candidates.json").write_text(json.dumps([{"id": "c1"}, {"id": "c2"}]))
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(la.subprocess, "run", fake_run)
    summary = la.fire(tmp_path)
    assert summary["fired"] is True
    assert summary["candidates"] == 2
    assert (tmp_path / ".build-loop" / "learn" / "pending" / "manifest.json").exists()


def test_fire_never_raises_on_miner_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("miner exploded")
    monkeypatch.setattr(la.subprocess, "run", boom)
    summary = la.fire(tmp_path)
    assert summary["fired"] is False
    assert "error" in summary  # captured, not raised


def test_read_pending_roundtrip(tmp_path, monkeypatch):
    def fake_run(cmd, cwd, capture_output, text, timeout):
        out_dir = Path(cmd[cmd.index("--out-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / ".candidates.json").write_text(json.dumps([{"id": "x"}]))
        class R:
            returncode = 0
        return R()
    monkeypatch.setattr(la.subprocess, "run", fake_run)
    la.fire(tmp_path)
    got = la.read_pending(tmp_path)
    assert got["exists"] is True
    assert got["candidates"] == [{"id": "x"}]
    assert got["manifest"]["candidates"] == 1


def test_read_pending_empty_when_never_fired(tmp_path):
    got = la.read_pending(tmp_path)
    assert got["exists"] is False and got["candidates"] == []
