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

import lifecycle as lc  # noqa: E402
import presence as pr  # noqa: E402
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
    rec["heartbeat_ts"] = time.time() - 16 * 60  # 16 min ago > default 15
    f.write_text(json.dumps(rec))
    pr.write_presence(chan, session_id="fresh", tool="t", model="m",
                      run_id="r2", app_slug="a", phase="p")
    reaped = pr.reap_stale(chan)
    assert "old" in reaped and not f.exists()
    assert [p["session_id"]
            for p in pr.read_active_presence(chan, exclude_session="x")] \
        == ["fresh"]


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

    old = _write_stale_presence(channel, "stale-presence", age_seconds=16 * 60)
    assert pr.read_active_presence(channel, exclude_session="me") == []
    assert not old.exists()

    old = _write_stale_presence(channel, "stale-status", age_seconds=16 * 60)
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
