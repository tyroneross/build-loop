"""Tests for scripts/app_pulse/presence.py — presence, reaper, cursor.

  - presence schema + overwrite-in-place
  - reap_stale drops files older than heartbeat_minutes (default 15,
    config-overridable via apps/<slug>/config.json — OQ2)
  - read_active_presence excludes self + reaped
  - cursor get/set round-trips
  - graceful absence
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import presence as pr  # noqa: E402


@pytest.fixture()
def chan(tmp_path: Path) -> Path:
    d = tmp_path / "chan"
    d.mkdir()
    return d


def test_write_and_schema(chan: Path):
    pr.write_presence(
        chan, session_id="s1", tool="claude", model="opus", run_id="r1",
        app_slug="a", phase="execute", files_in_flight=["x.py"],
    )
    f = chan / "sessions" / "s1.json"
    rec = json.loads(f.read_text())
    assert set(rec) >= {
        "session_id", "tool", "model", "run_id", "app_slug", "phase",
        "files_in_flight", "heartbeat_ts", "cursor",
    }
    assert rec["cursor"] == {"revision": 0, "changes_offset": 0}
    # overwrite-in-place
    pr.write_presence(chan, session_id="s1", tool="claude", model="opus",
                      run_id="r1", app_slug="a", phase="review",
                      files_in_flight=[])
    assert json.loads(f.read_text())["phase"] == "review"
    assert len(list((chan / "sessions").glob("*.json"))) == 1


def test_read_active_excludes_self(chan: Path):
    pr.write_presence(chan, session_id="s1", tool="t", model="m",
                      run_id="r1", app_slug="a", phase="p")
    pr.write_presence(chan, session_id="s2", tool="t", model="m",
                      run_id="r2", app_slug="a", phase="p")
    peers = pr.read_active_presence(chan, exclude_session="s1")
    assert [p["session_id"] for p in peers] == ["s2"]


def test_reap_stale(chan: Path):
    pr.write_presence(chan, session_id="old", tool="t", model="m",
                      run_id="r", app_slug="a", phase="p")
    f = chan / "sessions" / "old.json"
    rec = json.loads(f.read_text())
    rec["heartbeat_ts"] = time.time() - 16 * 60  # 16 min ago > default 15
    f.write_text(json.dumps(rec))
    pr.write_presence(chan, session_id="fresh", tool="t", model="m",
                      run_id="r2", app_slug="a", phase="p")
    reaped = pr.reap_stale(chan)
    assert "old" in reaped and not f.exists()
    assert [p["session_id"]
            for p in pr.read_active_presence(chan, exclude_session="x")] \
        == ["fresh"]


def test_reap_respects_config_override(chan: Path):
    (chan / "config.json").write_text(json.dumps({"heartbeat_minutes": 1}))
    pr.write_presence(chan, session_id="s", tool="t", model="m",
                      run_id="r", app_slug="a", phase="p")
    f = chan / "sessions" / "s.json"
    rec = json.loads(f.read_text())
    rec["heartbeat_ts"] = time.time() - 2 * 60  # 2 min ago > 1 min override
    f.write_text(json.dumps(rec))
    assert "s" in pr.reap_stale(chan)


def test_cursor_round_trip(chan: Path):
    pr.write_presence(chan, session_id="s", tool="t", model="m",
                      run_id="r", app_slug="a", phase="p")
    assert pr.get_cursor(chan, "s") == {"revision": 0, "changes_offset": 0}
    pr.set_cursor(chan, "s", revision=7, changes_offset=128)
    assert pr.get_cursor(chan, "s") == {"revision": 7, "changes_offset": 128}
    # other presence fields preserved across cursor write
    assert json.loads((chan / "sessions" / "s.json").read_text())["phase"] \
        == "p"


def test_graceful_absence(chan: Path):
    assert pr.read_active_presence(chan / "nope", exclude_session="x") == []
    assert pr.reap_stale(chan / "nope") == []
    assert pr.get_cursor(chan / "nope", "s") == {
        "revision": 0, "changes_offset": 0,
    }
