# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Production-shaped tests for the terminal run-closeout forcing function."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from branch_closeout_gate import check_branch_closeout  # noqa: E402
from rally_point.post import post  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-b", "main").returncode == 0
    assert _git(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _git(repo, "config", "user.name", "Test").returncode == 0
    (repo / "README.md").write_text("seed\n")
    assert _git(repo, "add", "README.md").returncode == 0
    assert _git(repo, "commit", "-m", "initial").returncode == 0
    (repo / ".build-loop").mkdir()
    return repo


def _post_closeout(repo: Path, channel: Path, run_id: str) -> int | None:
    return post(
        channel_dir=channel,
        kind="phase",
        tool="codex",
        model="gpt-5",
        run_id=run_id,
        app_slug="fixture",
        payload={"phase": "run-closeout", "summary": "done"},
        workdir=repo,
    )


def test_terminal_post_rejects_missing_or_incomplete_state(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    channel = tmp_path / "channel"
    run_id = "bl-gate-incomplete"

    assert check_branch_closeout(repo, run_id)["ready"] is False
    assert _post_closeout(repo, channel, run_id) is None
    assert not channel.exists()

    state = {
        "execution": {},
        "historicalExecutions": [{
            "build_loop_id": run_id,
            "run_worktree_branch": "bl/run-incomplete",
            "run_worktree_path": str(repo / ".build-loop/worktrees/run-incomplete"),
        }],
        "runs": [{
            "run_id": run_id,
            "createdRefs": [{
                "branch": "bl/run-incomplete",
                "path": str(repo / ".build-loop/worktrees/run-incomplete"),
                "status": "open",
            }],
            "branch_closeout": {"status": "pending_external_merge"},
        }],
    }
    (repo / ".build-loop/state.json").write_text(json.dumps(state, indent=2))

    verdict = check_branch_closeout(repo, run_id)
    assert verdict["ready"] is False
    assert any("not complete" in error for error in verdict["errors"])
    assert _post_closeout(repo, channel, run_id) is None
    assert not channel.exists()


def test_solo_main_run_can_post_without_receipt(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    channel = tmp_path / "channel"
    run_id = "bl-gate-solo"
    state = {"execution": {}, "runs": [{"run_id": run_id, "createdRefs": []}]}
    (repo / ".build-loop/state.json").write_text(json.dumps(state, indent=2))

    assert check_branch_closeout(repo, run_id)["ready"] is True
    assert _post_closeout(repo, channel, run_id) == 1
    assert (channel / "changes.jsonl").is_file()


def test_verified_terminal_receipt_unlocks_run_closeout_post(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    channel = tmp_path / "channel"
    run_id = "bl-gate-closed"
    branch = "bl/run-gate-closed"
    absent_path = repo / ".build-loop/worktrees/run-gate-closed"
    assert _git(repo, "branch", branch).returncode == 0
    expected_oid = _git(repo, "rev-parse", branch).stdout.strip()
    bundle = repo / ".build-loop/bundles/gate.bundle"
    bundle.parent.mkdir(parents=True)
    assert _git(repo, "bundle", "create", str(bundle), f"refs/heads/{branch}").returncode == 0
    assert _git(repo, "update-ref", "-d", f"refs/heads/{branch}", expected_oid).returncode == 0
    receipt_path = repo / ".build-loop/branch-closeout" / f"{run_id}.json"
    receipt_path.parent.mkdir(parents=True)
    receipt = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "complete",
        "refs": [{
            "branch": branch,
            "path": str(absent_path),
            "status": "closed",
            "expected_oid": expected_oid,
            "bundle_path": str(bundle),
            "bundle_verified": True,
        }],
    }
    receipt_path.write_text(json.dumps(receipt, indent=2))
    state = {
        "execution": {},
        "historicalExecutions": [{
            "build_loop_id": run_id,
            "run_worktree_branch": branch,
            "run_worktree_path": str(absent_path),
        }],
        "runs": [{
            "run_id": run_id,
            "createdRefs": [{
                "branch": branch,
                "path": str(absent_path),
                "status": "closed",
            }],
            "branch_closeout": {
                "status": "complete",
                "receipt_path": str(receipt_path),
            },
        }],
    }
    (repo / ".build-loop/state.json").write_text(json.dumps(state, indent=2))

    verdict = check_branch_closeout(repo, run_id)
    assert verdict["ready"] is True, verdict
    assert _post_closeout(repo, channel, run_id) == 1

    receipt["refs"][0]["expected_oid"] = "0" * 40
    receipt_path.write_text(json.dumps(receipt, indent=2))
    tampered = check_branch_closeout(repo, run_id)
    assert tampered["ready"] is False
    assert any("bundle is invalid" in error for error in tampered["errors"])

    # Reproduce the historical hazard: update-ref can remove a branch that is
    # still checked out. The terminal post must independently reject that Git
    # registration even when the stale ledger path is absent.
    receipt["refs"][0]["expected_oid"] = expected_oid
    receipt_path.write_text(json.dumps(receipt, indent=2))
    live_path = repo / ".build-loop/worktrees/live-hidden"
    assert _git(repo, "branch", branch, expected_oid).returncode == 0
    assert _git(repo, "worktree", "add", str(live_path), branch).returncode == 0
    assert _git(repo, "update-ref", "-d", f"refs/heads/{branch}", expected_oid).returncode == 0

    still_live = check_branch_closeout(repo, run_id)
    assert still_live["ready"] is False
    assert any("still checked out" in error for error in still_live["errors"])
