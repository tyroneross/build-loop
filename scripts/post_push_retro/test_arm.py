# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Arm writes a unique baton + spawns detached, and does NO synchronous retro
work — the "trigger does not block the push" acceptance."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post_push_retro import arm, coverage  # noqa: E402

ZERO = "0" * 40


def _init(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(["git", "-C", str(repo), "init", "-q", "-b", "main"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.email", "t@e.com"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.name", "T"])
    (repo / "x").write_text("1")
    subprocess.check_call(["git", "-C", str(repo), "add", "x"])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-q", "-m", "init"])
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def test_parse_pushed_range_normal():
    info = arm.parse_pushed_range([f"refs/heads/main aaa refs/heads/main bbb"])
    assert info["pushed_range"] == "bbb..aaa"
    assert info["branch"] == "main"


def test_parse_pushed_range_new_branch():
    info = arm.parse_pushed_range([f"refs/heads/feat aaa refs/heads/feat {ZERO}"])
    assert info["pushed_range"] == ""  # no remote base yet
    assert info["branch"] == "feat"


def test_parse_pushed_range_delete_skipped():
    info = arm.parse_pushed_range([f"(delete) {ZERO} refs/heads/old {ZERO}"])
    assert info["local_sha"] == ""  # a delete is skipped, no range


def test_arm_writes_baton_and_spawns_without_blocking(tmp_path):
    repo = tmp_path / "r"
    head = _init(repo)
    spawned = {}

    def fake_spawn(scripts_dir, baton, r):
        spawned["baton"] = baton
        spawned["repo"] = r

    r = arm.arm_and_spawn(repo, [f"refs/heads/main {head} refs/heads/main {ZERO}"],
                          spawn_fn=fake_spawn)
    assert r["armed"] is True and r["spawned"] is True
    baton = Path(r["baton"])
    assert baton.exists()
    payload = json.loads(baton.read_text())
    assert payload["branch"] == "main"
    assert spawned["baton"] == baton
    # NON-BLOCKING PROOF: arm did NO retro work synchronously — no retrospectives
    # output, no checkpoint advance. The retro is deferred to the detached child.
    assert not (coverage.retro_state_dir(repo) / "checkpoint.json").exists()
    assert not (repo / ".build-loop" / "retrospectives").exists()


def test_arm_unique_baton_per_push(tmp_path):
    repo = tmp_path / "r"
    head = _init(repo)
    line = f"refs/heads/main {head} refs/heads/main {ZERO}"
    b1 = arm.arm_and_spawn(repo, [line], spawn_fn=lambda *a: None)["baton"]
    b2 = arm.arm_and_spawn(repo, [line], spawn_fn=lambda *a: None)["baton"]
    assert b1 != b2  # concurrent pushes never collide on one fixed baton


def test_arm_fail_open_on_error(tmp_path):
    # A broken spawn must NOT raise out of the hook (would break the push).
    repo = tmp_path / "r"
    _init(repo)

    def boom(*a):
        raise RuntimeError("spawn failed")

    r = arm.arm_and_spawn(repo, ["refs/heads/main aaa refs/heads/main bbb"], spawn_fn=boom)
    assert r["armed"] is False
    assert "error" in r


def test_real_detached_spawn_returns_immediately(tmp_path):
    # Exercise the REAL Popen path (start_new_session detached) against a repo;
    # arm must return promptly and the child is fire-and-forget. We only assert
    # arm() returns armed=True quickly — the child runs independently.
    import time
    repo = tmp_path / "r"
    head = _init(repo)
    t0 = time.time()
    r = arm.arm_and_spawn(repo, [f"refs/heads/main {head} refs/heads/main {ZERO}"])
    elapsed = time.time() - t0
    assert r["armed"] is True
    assert elapsed < 5.0  # arming + detached spawn is near-instant, never blocks
