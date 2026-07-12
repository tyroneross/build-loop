# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/promotion_queue.py — the FIX-2 queue-and-report primitive.

The load-bearing proof: a durable promotion attempted against a SIMULATED-BUSY
store lands in the queue and is DRAINED on the next (free) pass — never a silent
skip. This is the regression artifact for the 2026-07-11 "pointed --memory-root
at a scratch path to skip" data-loss pattern.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import promotion_queue as pq  # noqa: E402
import append_milestone  # noqa: E402


def _git_init(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()


# ---------------------------------------------------------------------------
# store_busy
# ---------------------------------------------------------------------------


def test_store_busy_env(monkeypatch, tmp_path):
    monkeypatch.setenv(pq.BUSY_ENV, "1")
    assert pq.store_busy(tmp_path) is True
    monkeypatch.setenv(pq.BUSY_ENV, "0")
    assert pq.store_busy(tmp_path) is False


def test_store_busy_marker(monkeypatch, tmp_path):
    monkeypatch.delenv(pq.BUSY_ENV, raising=False)
    root = tmp_path / "mem"
    root.mkdir()
    assert pq.store_busy(root) is False
    (root / pq.PEER_HOLD_MARKER).write_text("")
    assert pq.store_busy(root) is True


def test_store_busy_none_root(monkeypatch):
    monkeypatch.delenv(pq.BUSY_ENV, raising=False)
    assert pq.store_busy(None) is False


# ---------------------------------------------------------------------------
# enqueue + list
# ---------------------------------------------------------------------------


def test_enqueue_then_list(tmp_path):
    env = pq.enqueue(tmp_path, kind="milestone", payload={"summary": "s"}, run_id="r1")
    assert env["queued"] is True
    pending = pq.list_pending(tmp_path)
    assert len(pending) == 1
    assert pending[0]["kind"] == "milestone"
    assert pending[0]["run_id"] == "r1"
    assert pending[0]["status"] == "pending"


def test_enqueue_invalid_kind(tmp_path):
    env = pq.enqueue(tmp_path, kind="bogus", payload={})
    assert env["queued"] is False


# ---------------------------------------------------------------------------
# drain: busy → preserved; free → applied
# ---------------------------------------------------------------------------


def test_drain_busy_preserves_queue(monkeypatch, tmp_path):
    monkeypatch.setenv(pq.BUSY_ENV, "1")
    pq.enqueue(tmp_path, kind="milestone", payload={"summary": "s"})
    out = pq.drain(tmp_path, memory_root=tmp_path / "mem")
    assert out["drained"] == 0
    assert out["remaining"] == 1
    assert "busy" in out["skipped_reason"]
    assert len(pq.list_pending(tmp_path)) == 1  # still queued


def test_drain_free_applies_milestone(monkeypatch, tmp_path):
    """Full FIX-2 cycle: busy enqueue via append_milestone → free drain lands it."""
    monkeypatch.delenv(pq.BUSY_ENV, raising=False)
    repo = tmp_path / "repo"
    head = _git_init(repo)
    mem = tmp_path / "mem"
    mem.mkdir()

    # 1. Store busy (peer-hold marker): append_milestone must QUEUE, not skip.
    (mem / pq.PEER_HOLD_MARKER).write_text("")
    res = append_milestone.append_milestone(
        workdir=str(repo), summary="shipped X", project="demo",
        commit=head, run_id="run-1", memory_root=str(mem),
    )
    assert res["appended"] is False
    assert res["queued"] is True
    milestones = mem / "projects" / "demo" / "milestones.jsonl"
    assert not milestones.exists()  # nothing written to the busy store
    assert len(pq.list_pending(repo)) == 1

    # 2. Store free again (marker removed): drain lands the milestone.
    (mem / pq.PEER_HOLD_MARKER).unlink()
    out = pq.drain(repo, memory_root=str(mem))
    assert out["drained"] == 1
    assert out["remaining"] == 0
    assert milestones.exists()
    line = json.loads(milestones.read_text().strip())
    assert line["summary"] == "shipped X"
    assert line["commit"] == head
    # Queue is emptied; the drained audit log records it.
    assert pq.list_pending(repo) == []
    drained_log = pq._drained_path(repo)
    assert drained_log.exists()


def test_drain_noop_when_empty(tmp_path):
    out = pq.drain(tmp_path, memory_root=tmp_path)
    assert out["drained"] == 0
    assert out["remaining"] == 0


def test_drain_free_applies_retro_durable(monkeypatch, tmp_path):
    """FIX-2 retro seam: busy retro promotion queues, free drain writes durable."""
    monkeypatch.delenv(pq.BUSY_ENV, raising=False)
    from retrospective import write as retro_write

    repo = tmp_path / "repo"
    repo.mkdir()
    mem = tmp_path / "mem"
    mem.mkdir()
    sections = {"summary": "did the thing"}

    # Busy → promote_durable QUEUES (status "queued"), writes nothing durable.
    (mem / pq.PEER_HOLD_MARKER).write_text("")
    res = retro_write.promote_durable(
        workdir=repo, run_id="run-r", sections=sections, repo="demo",
        memory_root=mem,
    )
    assert res["status"] == "queued"
    assert len(pq.list_pending(repo)) == 1
    assert not (mem / "projects" / "demo").exists()

    # Free → drain writes the durable retrospective file.
    (mem / pq.PEER_HOLD_MARKER).unlink()
    out = pq.drain(repo, memory_root=str(mem))
    assert out["drained"] == 1
    durable = list((mem / "projects" / "demo" / "retrospectives").rglob("run-r.md"))
    assert len(durable) == 1


def test_peer_hold_producer_sets_and_clears_marker(monkeypatch, tmp_path):
    """f2: peer_hold gives the busy signal a real producer."""
    monkeypatch.delenv(pq.BUSY_ENV, raising=False)
    mem = tmp_path / "mem"
    mem.mkdir()
    assert pq.store_busy(mem) is False
    with pq.peer_hold(mem):
        assert (mem / pq.PEER_HOLD_MARKER).exists()
        assert pq.store_busy(mem) is True
    assert not (mem / pq.PEER_HOLD_MARKER).exists()
    assert pq.store_busy(mem) is False


def test_cli_hold_release(monkeypatch, tmp_path):
    monkeypatch.delenv(pq.BUSY_ENV, raising=False)
    mem = tmp_path / "mem"
    mem.mkdir()
    pq.main(["--workdir", str(tmp_path), "--memory-root", str(mem), "hold"])
    assert pq.store_busy(mem) is True
    pq.main(["--workdir", str(tmp_path), "--memory-root", str(mem), "release"])
    assert pq.store_busy(mem) is False


def test_drain_preserves_row_enqueued_mid_drain(monkeypatch, tmp_path):
    """f3: a record enqueued between the unlocked snapshot and the locked rewrite
    must NOT be silently dropped."""
    monkeypatch.delenv(pq.BUSY_ENV, raising=False)
    mem = tmp_path / "mem"
    mem.mkdir()
    # Original queued record.
    pq.enqueue(tmp_path, kind="milestone", payload={"summary": "orig"}, run_id="orig")

    # Applier that simulates a concurrent peer enqueue during processing.
    def _racy_applier(record, workdir, memory_root):
        pq.enqueue(workdir, kind="milestone", payload={"summary": "concurrent"}, run_id="concurrent")
        return {"status": "ok"}

    monkeypatch.setitem(pq._APPLIERS, "milestone", _racy_applier)
    out = pq.drain(tmp_path, memory_root=str(mem))
    assert out["drained"] == 1  # the original
    pending = pq.list_pending(tmp_path)
    ids = {r["run_id"] for r in pending}
    assert "concurrent" in ids  # survived the rewrite
    assert "orig" not in ids    # the processed one is gone from the queue


def test_drain_holds_store_during_apply(monkeypatch, tmp_path):
    """f2 producer: the store is marked busy WHILE drain applies, then released."""
    monkeypatch.delenv(pq.BUSY_ENV, raising=False)
    mem = tmp_path / "mem"
    mem.mkdir()
    pq.enqueue(tmp_path, kind="milestone", payload={"summary": "s"}, run_id="r")

    observed = {}

    def _observing_applier(record, workdir, memory_root):
        observed["busy_during_apply"] = pq.store_busy(memory_root)
        return {"status": "ok"}

    monkeypatch.setitem(pq._APPLIERS, "milestone", _observing_applier)
    pq.drain(tmp_path, memory_root=str(mem))
    assert observed["busy_during_apply"] is True     # held during apply
    assert pq.store_busy(mem) is False               # released after
