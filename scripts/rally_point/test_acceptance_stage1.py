# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Stage 1 acceptance gate (the spec's empirical acceptance, not units).

Asserts, end to end:
  1. Two simulated sessions (two distinct run_ids) on the SAME app:
     session A commits via the real post-commit path -> session B's
     `checkpoint_read` surfaces it within ONE call.
  2. A dependency-manifest commit additionally yields a `dep-change`.
  3. Slug parity: the resolver returns the SAME canonical slug from a
     temp `git worktree` and the main checkout (the D1 guarantee).
  4. The reaper clears a stale-heartbeat session.
  5. The post-commit hook is fire-and-forget (a commit still succeeds
     even when the channel write target is unwritable).
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import channel_paths as ap  # noqa: E402
import changes as ch  # noqa: E402
import checkpoint as cp  # noqa: E402
import install_git_hook as igh  # noqa: E402
import presence as pr  # noqa: E402


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def app_repo(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", str(tmp_path / "apps"))
    repo = tmp_path / "the-app"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@e.com"], repo)
    _git(["config", "user.name", "t"], repo)
    # The pre-commit guard fails closed without a denylist; provide one
    # so the install-and-commit flow under test is not blocked by it.
    (repo / ".private-slugs").write_text("nonexistent-private-slug\n")
    assert igh.install(repo) is True
    return repo


def _poll_changes(chan: Path, predicate, tries=60):
    for _ in range(tries):
        recs, _o = ch.read_changes_since(chan, 0)
        if predicate(recs):
            return recs
        time.sleep(0.1)
    return ch.read_changes_since(chan, 0)[0]


def test_stage1_acceptance(app_repo: Path, tmp_path: Path):
    slug = ap.app_slug(cwd=app_repo)
    chan = ap.app_channel_dir(slug)

    # --- Two sessions, distinct run_ids, same app ---------------------
    pr.write_presence(chan, session_id="B", tool="claude", model="opus",
                      run_id="run-B", app_slug=slug, phase="assess")
    pr.write_presence(chan, session_id="A", tool="codex", model="m",
                      run_id="run-A", app_slug=slug, phase="execute",
                      files_in_flight=["server.py"])

    # --- (1) session A commits via the real post-commit hook ----------
    (app_repo / "server.py").write_text("print('v1')\n")
    _git(["add", "."], app_repo)
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "feat"],
         app_repo)
    _poll_changes(chan, lambda r: any(x["kind"] == "commit" for x in r))

    # --- (3) slug parity: temp worktree vs main checkout (post-commit,
    # so HEAD is born and `git worktree add` succeeds) -----------------
    wt = tmp_path / "agent-worktree"
    _git(["worktree", "add", "-q", str(wt), "HEAD"], app_repo)
    assert ap.app_slug(cwd=wt) == ap.app_slug(cwd=app_repo) == "the-app"

    env = cp.checkpoint_read(chan, session_id="B", my_files=["server.py"])
    assert env["changed"] is True
    assert any(c["kind"] == "commit" for c in env["new_changes"])
    assert any(p["session_id"] == "A" for p in env["active_peers"])
    sc = [r for r in env["reactions"] if r["type"] == "soft-claim"]
    # 2026-05-19: severity is now reason-keyed (merged_residue /
    # squash_landed -> informational; active_conflict -> warning). D4
    # ("never a block") is unchanged.
    assert sc and sc[0]["severity"] in {"warning", "informational"}
    assert sc[0].get("reason") in {"merged_residue", "squash_landed",
                                   "active_conflict"}

    # --- (2) dependency-manifest commit -> dep-change -----------------
    (app_repo / "requirements.txt").write_text("requests==2.0\n")
    _git(["add", "."], app_repo)
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "deps"],
         app_repo)
    _poll_changes(chan, lambda r: any(x["kind"] == "dep-change" for x in r))
    env2 = cp.checkpoint_read(chan, session_id="B", my_files=[])
    kinds = [c["kind"] for c in env2["new_changes"]]
    assert "dep-change" in kinds
    assert any(r["type"] == "reinstall" for r in env2["reactions"])

    # --- (4) reaper clears a stale-heartbeat session ------------------
    import json
    af = chan / "sessions" / "A.json"
    arec = json.loads(af.read_text())
    arec["heartbeat_ts"] = time.time() - 999 * 60
    af.write_text(json.dumps(arec))
    reaped = pr.reap_stale(chan)
    assert "A" in reaped and not af.exists()
    assert pr.read_active_presence(chan, exclude_session="B") == []

    # --- non-goal guard: no frequency/invocation keys anywhere --------
    bad = {"count", "frequency", "invocations", "calls", "num_calls",
           "hits", "usage", "call_count"}

    def _scan(o):
        if isinstance(o, dict):
            for k, v in o.items():
                assert k.lower() not in bad
                _scan(v)
        elif isinstance(o, list):
            for v in o:
                _scan(v)

    _scan(env)
    _scan(env2)


def test_post_commit_is_fire_and_forget(app_repo: Path, monkeypatch):
    """(5) An unwritable channel target must NOT fail the commit."""
    monkeypatch.setenv("BUILD_LOOP_APPS_ROOT", "/proc/nope/cannot")
    (app_repo / "x.txt").write_text("y")
    _git(["add", "."], app_repo)
    # check=True would raise on a non-zero hook -> commit failure.
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "ok"],
         app_repo)
