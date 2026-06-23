# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/rally_point/presence.py — presence, reaper, cursor.

  - presence schema + overwrite-in-place
  - reap_stale drops files older than heartbeat_minutes (default 15,
    config-overridable via apps/<slug>/config.json — OQ2)
  - read_active_presence excludes self + reaped
  - cursor get/set round-trips
  - graceful absence
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_HERE.parent) not in sys.path:
    sys.path.append(str(_HERE.parent))

# Import via the rally_point package, NOT the bare top-level name. Three
# different `lifecycle.py` modules exist in the tree (rally_point/,
# app_pulse/, memory_consolidate/lifecycle/); a bare `import lifecycle`
# resolves to whichever landed in sys.modules first, so in a full run an
# earlier test could leave the wrong `lifecycle` cached (symptom:
# AttributeError: module 'lifecycle' has no attribute 'reap_stale_sessions').
from rally_point import lifecycle as lc  # noqa: E402
from rally_point import presence as pr  # noqa: E402
import coordination_status as cs  # noqa: E402


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
    # 40 min ago > the default adaptive window (5-min cadence -> 31 min).
    rec["heartbeat_ts"] = time.time() - 40 * 60
    f.write_text(json.dumps(rec))
    pr.write_presence(chan, session_id="fresh", tool="t", model="m",
                      run_id="r2", app_slug="a", phase="p")
    reaped = pr.reap_stale(chan)
    assert "old" in reaped and not f.exists()
    assert [p["session_id"]
            for p in pr.read_active_presence(chan, exclude_session="x")] \
        == ["fresh"]


def test_reap_within_adaptive_window_keeps_presence(chan: Path):
    # 20 min old on the default 5-min cadence (31-min window) is NOT stale.
    pr.write_presence(chan, session_id="busy", tool="t", model="m",
                      run_id="r", app_slug="a", phase="p")
    f = chan / "sessions" / "busy.json"
    rec = json.loads(f.read_text())
    rec["heartbeat_ts"] = time.time() - 20 * 60
    f.write_text(json.dumps(rec))
    assert pr.reap_stale(chan) == []
    assert f.exists()


def test_five_hour_cadence_idle_two_hours_kept(chan: Path):
    # A declared 5-hour cadence (window ~30 h) idle 2 h stays alive.
    pr.write_presence(chan, session_id="slow", tool="t", model="m",
                      run_id="r", app_slug="a", phase="p",
                      planned_heartbeat_secs=18000)
    f = chan / "sessions" / "slow.json"
    rec = json.loads(f.read_text())
    assert rec["planned_heartbeat_secs"] == 18000
    rec["heartbeat_ts"] = time.time() - 2 * 60 * 60  # 2 h
    f.write_text(json.dumps(rec))
    assert pr.reap_stale(chan) == []
    assert f.exists()


def test_code_progress_keeps_stale_heartbeat_alive(chan: Path):
    # Poll 1: fresh heartbeat + sha-aaaa -> kept (establishes the sha cache).
    pr.write_presence(chan, session_id="coder", tool="t", model="m",
                      run_id="r", app_slug="a", phase="p")
    f = chan / "sessions" / "coder.json"
    rec = json.loads(f.read_text())
    rec["heartbeat_ts"] = time.time() - 60  # fresh
    rec["branch_head_sha"] = "sha-aaaa"
    f.write_text(json.dumps(rec))
    assert pr.reap_stale(chan) == []
    assert f.exists()
    # Poll 2: heartbeat lapsed (40 min) BUT HEAD moved (sha-aaaa -> sha-bbbb) ->
    # fresh code progress overrides the stale heartbeat -> kept.
    rec = json.loads(f.read_text())
    rec["heartbeat_ts"] = time.time() - 40 * 60
    rec["branch_head_sha"] = "sha-bbbb"
    f.write_text(json.dumps(rec))
    assert pr.reap_stale(chan) == []
    assert f.exists()


def test_stale_heartbeat_unmoving_head_is_reaped(chan: Path):
    # A session that stamps a sha but never moves HEAD must still go stale: a
    # first-seen sha is NOT proof of progress (no free keep-alive).
    pr.write_presence(chan, session_id="stuck", tool="t", model="m",
                      run_id="r", app_slug="a", phase="p")
    f = chan / "sessions" / "stuck.json"
    rec = json.loads(f.read_text())
    rec["heartbeat_ts"] = time.time() - 40 * 60  # stale
    rec["branch_head_sha"] = "sha-frozen"
    f.write_text(json.dumps(rec))
    # First poll: sha first-seen (not progress) + stale heartbeat -> reaped.
    assert "stuck" in pr.reap_stale(chan)
    assert not f.exists()


def _write_stale_presence(channel: Path, session_id: str, *, age_seconds: int) -> Path:
    sessions = channel / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    p = sessions / f"{session_id}.json"
    stale_ts = time.time() - age_seconds
    p.write_text(json.dumps({
        "session_id": session_id,
        "tool": "codex",
        "model": "gpt",
        "run_id": "r",
        "app_slug": "a",
        "phase": "execute",
        "files_in_flight": ["scripts/coordination_bootstrap.py"],
        "heartbeat_ts": stale_ts,
        "cursor": {"revision": 0, "changes_offset": 0},
    }))
    # lifecycle.reap_stale_sessions keys off mtime, not heartbeat_ts.
    p.touch()
    os.utime(p, (stale_ts, stale_ts))
    return p


def test_stale_presence_regression_across_presence_status_and_lifecycle(tmp_path: Path, monkeypatch):
    """R4: stale heartbeats must not surface as active peers."""
    apps_root = tmp_path / "apps"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(apps_root))
    slug = cs.channel_paths.app_slug(workdir)
    channel = cs.channel_paths.ensure_channel_dir(slug)

    # 40 min > the default adaptive window (31 min) and no branch sha stamped,
    # so the presence is reaped on read.
    old = _write_stale_presence(channel, "stale-presence", age_seconds=40 * 60)
    assert pr.read_active_presence(channel, exclude_session="me") == []
    assert not old.exists()

    # build_status resolves its channel via the discovery bridge (post-cutover
    # behavior), which may differ from the legacy BUILD_LOOP_APPS_ROOT channel
    # used above (e.g. it resolves to <repo>/.rally for a repo-local workdir).
    # Seed the stale file into the channel build_status will actually READ so
    # the reaping assertion exercises build_status's real read path.
    _bs_slug, bs_channel, _via = cs._resolve_channel_dir(workdir.resolve())
    bs_channel.mkdir(parents=True, exist_ok=True)
    old = _write_stale_presence(bs_channel, "stale-status", age_seconds=40 * 60)
    args = cs.parse_args([
        "--workdir", str(workdir),
        "--session-id", "me",
        "--json",
    ])
    status = cs.build_status(args)
    assert status["active_peers"] == []
    assert not old.exists()

    old = _write_stale_presence(channel, "stale-lifecycle", age_seconds=2 * 3600)
    assert lc.reap_stale_sessions(channel, stale_after_seconds=3600) == 1
    assert not old.exists()


def test_reap_respects_config_override(chan: Path):
    # Legacy `heartbeat_minutes` acts as the declared cadence: 1-min cadence ->
    # adaptive window 1*60*6+60 = 420 s (7 min). A 10-min-old presence is past it.
    (chan / "config.json").write_text(json.dumps({"heartbeat_minutes": 1}))
    pr.write_presence(chan, session_id="s", tool="t", model="m",
                      run_id="r", app_slug="a", phase="p")
    f = chan / "sessions" / "s.json"
    rec = json.loads(f.read_text())
    rec["heartbeat_ts"] = time.time() - 10 * 60
    f.write_text(json.dumps(rec))
    assert "s" in pr.reap_stale(chan)


def test_planned_heartbeat_overrides_legacy_config(chan: Path):
    # When a record declares planned_heartbeat_secs it wins over the channel's
    # heartbeat_minutes config. 5-min declared cadence -> 31-min window; a record
    # 10 min old stays alive even though the legacy 1-min config would reap it.
    (chan / "config.json").write_text(json.dumps({"heartbeat_minutes": 1}))
    pr.write_presence(chan, session_id="d", tool="t", model="m",
                      run_id="r", app_slug="a", phase="p",
                      planned_heartbeat_secs=300)
    f = chan / "sessions" / "d.json"
    rec = json.loads(f.read_text())
    rec["heartbeat_ts"] = time.time() - 10 * 60
    f.write_text(json.dumps(rec))
    assert pr.reap_stale(chan) == []
    assert f.exists()


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


# ---------------------------------------------------------------------------
# Branch merge-status fields (2026-05-19 — peer-merged gate)
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    """Run git with a hardcoded committer identity; raise on failure."""
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, timeout=5, env=env, check=True,
    )


def _make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_branch_merge_status_merged(chan: Path, tmp_path: Path):
    """HEAD is an ancestor of main -> 'merged'."""
    repo = _make_repo(tmp_path / "repo")
    pr.write_presence(
        chan, session_id="s", tool="t", model="m", run_id="r",
        app_slug="a", phase="p", cwd=repo,
    )
    rec = json.loads((chan / "sessions" / "s.json").read_text())
    assert rec["branch_merge_status"] == "merged"
    assert rec["branch_name"] == "main"
    assert rec["branch_head_sha"] != "unknown"
    assert isinstance(rec["branch_merge_status_checked_ts"], (int, float))


def test_branch_merge_status_unmerged(chan: Path, tmp_path: Path):
    """A feature branch ahead of main -> 'unmerged'."""
    repo = _make_repo(tmp_path / "repo")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "b.txt").write_text("b")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "wip")
    pr.write_presence(
        chan, session_id="s", tool="t", model="m", run_id="r",
        app_slug="a", phase="p", cwd=repo,
    )
    rec = json.loads((chan / "sessions" / "s.json").read_text())
    assert rec["branch_merge_status"] == "unmerged"
    assert rec["branch_name"] == "feat"


def test_branch_merge_status_unknown_non_git(chan: Path, tmp_path: Path):
    """Non-git directory -> all 'unknown', no raise."""
    plain = tmp_path / "plain"
    plain.mkdir()
    pr.write_presence(
        chan, session_id="s", tool="t", model="m", run_id="r",
        app_slug="a", phase="p", cwd=plain,
    )
    rec = json.loads((chan / "sessions" / "s.json").read_text())
    assert rec["branch_merge_status"] == "unknown"
    assert rec["branch_name"] == "unknown"
    assert rec["branch_head_sha"] == "unknown"
