# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for orphan_notice — the read-only NOTIFY half of EC-04 coord.

Asserts: (1) it WARNs at/above threshold, silent below; (2) it is strictly
non-mutating (no presence file unlinked); (3) fail-open on a missing room."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import orphan_notice  # type: ignore


def test_warn_line_threshold():
    assert orphan_notice.warn_line_for([], threshold=2) == ""
    assert orphan_notice.warn_line_for(["a"], threshold=2) == ""      # under-warn
    line = orphan_notice.warn_line_for(["a", "b"], threshold=2)
    assert "2 orphan" in line and "rally sessions --reap" in line


def test_warn_line_truncates_ids():
    line = orphan_notice.warn_line_for([f"s{i}" for i in range(9)], threshold=2)
    assert "9 orphan" in line and "…" in line


def test_orphan_notice_reads_dry_run_only(tmp_path, monkeypatch):
    """The read-only guarantee: orphan_notice must call reap_stale with
    apply=False (never a mutating reap) and surface the returned count."""
    real_room = tmp_path / "room"; real_room.mkdir()
    monkeypatch.setattr(orphan_notice, "_channel_dir", lambda wd: real_room)
    calls = {}

    def fake_reap(cdir, *, apply=True):
        calls["apply"] = apply
        return ["sess-a", "sess-b"]
    monkeypatch.setattr(orphan_notice.presence, "reap_stale", fake_reap)

    line = orphan_notice.orphan_notice(tmp_path, threshold=2)
    assert calls["apply"] is False, "must dry-run (apply=False) — never mutate"
    assert "2 orphan" in line


def test_orphan_notice_below_threshold_silent(tmp_path, monkeypatch):
    real_room = tmp_path / "room"; real_room.mkdir()
    monkeypatch.setattr(orphan_notice, "_channel_dir", lambda wd: real_room)
    monkeypatch.setattr(orphan_notice.presence, "reap_stale", lambda cdir, *, apply=True: ["only-one"])
    assert orphan_notice.orphan_notice(tmp_path, threshold=2) == ""


def test_orphan_notice_failopen_no_room(tmp_path, monkeypatch):
    # channel dir does not exist → empty string, no raise
    monkeypatch.setattr(orphan_notice, "_channel_dir", lambda wd: tmp_path / "nope")
    assert orphan_notice.orphan_notice(tmp_path) == ""


def test_orphan_notice_failopen_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(orphan_notice, "_channel_dir",
                        lambda wd: (_ for _ in ()).throw(RuntimeError("boom")))
    assert orphan_notice.orphan_notice(tmp_path) == ""  # must not raise


def test_main_exits_zero(tmp_path):
    assert orphan_notice.main(["--workdir", str(tmp_path)]) == 0
