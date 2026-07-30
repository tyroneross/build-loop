"""Tests for scripts/write_subagent_result.py (M1)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from write_subagent_result import (  # noqa: E402
    M1_VALID_STATUS,
    write_subagent_result,
    main,
)


def _envelope(**overrides):
    base = {
        "chunk_id": "phase-h-embed-daemon",
        "status": "fixed",
        "files_changed": ["scripts/embed_daemon.py"],
        "verifications": ["pytest tests/test_embed_daemon.py: 25/25 passed"],
        "notes": "ok",
        "attempt": 1,
    }
    base.update(overrides)
    return base


def test_atomic_write_succeeds(tmp_path):
    target = write_subagent_result(tmp_path, "run_x", _envelope())
    assert target.exists()
    data = json.loads(target.read_text())
    assert data["chunk_id"] == "phase-h-embed-daemon"
    assert data["status"] == "fixed"
    assert "received_at" in data  # auto-stamped


def test_path_layout(tmp_path):
    target = write_subagent_result(tmp_path, "run_abc", _envelope(chunk_id="c1", attempt=1))
    expected = tmp_path / ".build-loop" / "subagent-results" / "run_abc" / "c1.attempt-1.json"
    assert target == expected


def test_retry_suffix(tmp_path):
    write_subagent_result(tmp_path, "run_x", _envelope(attempt=1))
    write_subagent_result(tmp_path, "run_x", _envelope(attempt=2, status="failed"))
    out_dir = tmp_path / ".build-loop" / "subagent-results" / "run_x"
    files = sorted(p.name for p in out_dir.iterdir())
    assert files == ["phase-h-embed-daemon.attempt-1.json", "phase-h-embed-daemon.attempt-2.json"]


def test_double_write_same_attempt_raises(tmp_path):
    write_subagent_result(tmp_path, "run_x", _envelope(attempt=1))
    with pytest.raises(FileExistsError):
        write_subagent_result(tmp_path, "run_x", _envelope(attempt=1))


def test_validation_missing_field(tmp_path):
    bad = _envelope()
    del bad["files_changed"]
    with pytest.raises(ValueError, match="files_changed"):
        write_subagent_result(tmp_path, "run_x", bad)


def test_validation_bad_status(tmp_path):
    with pytest.raises(ValueError, match="status"):
        write_subagent_result(tmp_path, "run_x", _envelope(status="bogus"))


def test_validation_bad_attempt(tmp_path):
    with pytest.raises(ValueError, match="attempt"):
        write_subagent_result(tmp_path, "run_x", _envelope(attempt=0))


def test_all_valid_statuses_accepted(tmp_path):
    for i, status in enumerate(sorted(M1_VALID_STATUS), start=1):
        write_subagent_result(tmp_path, "run_x", _envelope(chunk_id=f"c{i}", status=status))


def test_cli_stdin(tmp_path, capsys, monkeypatch):
    payload = json.dumps(_envelope(chunk_id="cli1"))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    rc = main(["--workdir", str(tmp_path), "--run-id", "run_y", "--envelope", "-"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert "cli1.attempt-1.json" in out
    assert Path(out).exists()


def test_cli_invalid_json_returns_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
    rc = main(["--workdir", str(tmp_path), "--run-id", "run_y", "--envelope", "-"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "validation error" in err


def test_concurrent_calls_distinct_attempts(tmp_path):
    """Concurrent calls with the SAME attempt collide; with DIFFERENT attempts they don't."""
    # Different attempts: both succeed
    write_subagent_result(tmp_path, "run_x", _envelope(attempt=1))
    write_subagent_result(tmp_path, "run_x", _envelope(attempt=2))
    out_dir = tmp_path / ".build-loop" / "subagent-results" / "run_x"
    assert len(list(out_dir.iterdir())) == 2
