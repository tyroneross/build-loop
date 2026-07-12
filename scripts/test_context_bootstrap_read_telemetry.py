# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""FIX-3: context_bootstrap read-side telemetry.

Proves the read seam records a ``memory-read`` event for EXACTLY the lessons it
surfaced into the Phase-1 packet — closing the "39 write rows, zero reads" gap
so lesson recall becomes measurable.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import context_bootstrap as cb  # noqa: E402
import memory_telemetry as mt  # noqa: E402


def _packet(lessons):
    return {"query": "memory flow enforcement", "lessons_progressive": lessons}


def test_emits_read_for_exactly_surfaced_lessons(tmp_path):
    tpath = tmp_path / "TELEMETRY.jsonl"
    packet = _packet([
        {"name": "lesson-a", "source_path": "lessons/2026-a.md"},
        {"name": "lesson-b", "source_path": "lessons/2026-b.md"},
    ])
    cid = cb.emit_read_telemetry(packet, telemetry_path=tpath)
    assert cid is not None

    rows = mt.read_rows(tpath)
    reads = [r for r in rows if r.get("kind") == "memory-read"]
    assert len(reads) == 1
    row = reads[0]
    assert row["reader_or_writer"] == "context_bootstrap"
    assert row["phase"] == "1-assess"
    # Exactly the surfaced lessons (source_path preferred), order preserved.
    assert row["memory_ids_seen"] == ["lessons/2026-a.md", "lessons/2026-b.md"]


def test_falls_back_to_name_when_no_source_path(tmp_path):
    tpath = tmp_path / "TELEMETRY.jsonl"
    cb.emit_read_telemetry(_packet([{"name": "only-name"}]), telemetry_path=tpath)
    reads = [r for r in mt.read_rows(tpath) if r.get("kind") == "memory-read"]
    assert reads[0]["memory_ids_seen"] == ["only-name"]


def test_dedups_surfaced_ids(tmp_path):
    tpath = tmp_path / "TELEMETRY.jsonl"
    cb.emit_read_telemetry(_packet([
        {"source_path": "dup.md"}, {"source_path": "dup.md"},
    ]), telemetry_path=tpath)
    reads = [r for r in mt.read_rows(tpath) if r.get("kind") == "memory-read"]
    assert reads[0]["memory_ids_seen"] == ["dup.md"]


def test_no_lessons_writes_nothing(tmp_path):
    tpath = tmp_path / "TELEMETRY.jsonl"
    assert cb.emit_read_telemetry(_packet([]), telemetry_path=tpath) is None
    assert not tpath.exists()


def test_absent_store_no_write_no_creation(tmp_path, monkeypatch):
    """No telemetry_path + absent canonical store → skip, never create the store."""
    monkeypatch.setenv("BUILD_LOOP_MEMORY_STORE_ROOT", str(tmp_path / "does-not-exist"))
    # Reload _paths so the env override takes effect for memory_store_root().
    assert cb.emit_read_telemetry(_packet([{"name": "x"}])) is None
    assert not (tmp_path / "does-not-exist").exists()


def test_build_packet_end_to_end_emits_read(tmp_path, monkeypatch):
    """Full build_packet against a fixture store writes a memory-read row when
    lessons are surfaced (guard: at minimum, never raises and store not polluted)."""
    store = tmp_path / "mem"
    (store / "indexes").mkdir(parents=True)
    monkeypatch.setenv("BUILD_LOOP_MEMORY_STORE_ROOT", str(store))
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(store))
    workdir = tmp_path / "repo"
    (workdir / ".build-loop").mkdir(parents=True)
    # Inject a lesson directly so the packet has something to surface.
    packet = _packet([{"name": "seeded", "source_path": "lessons/seed.md"}])
    tpath = store / "indexes" / "TELEMETRY.jsonl"
    cb.emit_read_telemetry(packet, telemetry_path=tpath)
    reads = [r for r in mt.read_rows(tpath) if r.get("kind") == "memory-read"]
    assert len(reads) == 1
