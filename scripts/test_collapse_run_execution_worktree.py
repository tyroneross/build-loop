# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``collapse_run``'s integration with the run-entry isolation worktree.

The per-run worktree provisioned at Phase 1 Assess preamble lives on
``state.execution.run_worktree_path`` (not ``runs[N].createdRefs[]``, because
no runs entry exists yet at that point). Closeout must still pick it up so the
run worktree is bundled-then-removed like any other ref.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import collapse_run  # noqa: E402
import stop_closeout  # noqa: E402


def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


def _make_run_worktree(repo: Path, build_loop_id: str) -> tuple[Path, str]:
    """Provision a run worktree the same way build_loop_id does, but inline."""
    short = build_loop_id.rsplit("-", 1)[-1]
    branch = f"bl/run-{short}"
    path = repo / ".build-loop" / "worktrees" / f"run-{short}"
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), "main")
    return path, branch


def _write_state_with_execution(
    repo: Path,
    build_loop_id: str,
    wt_path: Path,
    wt_branch: str,
    run_entry: dict | None = None,
) -> None:
    bl_dir = repo / ".build-loop"
    bl_dir.mkdir(exist_ok=True)
    state = {
        "execution": {
            "build_loop_id": build_loop_id,
            "run_worktree_path": str(wt_path.resolve()),
            "run_worktree_branch": wt_branch,
        },
        "runs": [run_entry] if run_entry else [],
    }
    (bl_dir / "state.json").write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Merged run-worktree → deleted by collapse
# ---------------------------------------------------------------------------

def test_collapse_deletes_merged_run_worktree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    bli_id = "bl-20260601T000000Z-test-000777"
    wt_path, wt_branch = _make_run_worktree(repo, bli_id)

    # Land work on the run worktree, then merge to main so it reads as MERGED.
    work_file = wt_path / "work.txt"
    work_file.write_text("isolated work\n")
    _git(wt_path, "add", "work.txt")
    _git(wt_path, "commit", "-m", "isolated work")
    _git(repo, "merge", "--no-ff", wt_branch, "-m", f"merge {wt_branch}")

    _write_state_with_execution(
        repo,
        bli_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": "run_001", "build_loop_id": bli_id, "createdRefs": []},
    )

    result = collapse_run.collapse(repo, run_id="latest", owner_released=True)

    deleted_branches = [d["branch"] for d in result["deleted"]]
    assert wt_branch in deleted_branches, (
        f"execution-block run worktree branch {wt_branch} not deleted; result={result}"
    )
    # Worktree folder removed.
    assert not wt_path.exists()
    # Bundle was created.
    assert result["bundle_path"] is not None


# ---------------------------------------------------------------------------
# Unmerged run-worktree → surfaced (worktree folder removed, branch kept)
# ---------------------------------------------------------------------------

def test_collapse_surfaces_unmerged_run_worktree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    bli_id = "bl-20260601T010000Z-test-000888"
    wt_path, wt_branch = _make_run_worktree(repo, bli_id)

    # Land work but DON'T merge.
    (wt_path / "work.txt").write_text("orphan work\n")
    _git(wt_path, "add", "work.txt")
    _git(wt_path, "commit", "-m", "orphan work")

    _write_state_with_execution(
        repo,
        bli_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": "run_002", "build_loop_id": bli_id, "createdRefs": []},
    )

    result = collapse_run.collapse(repo, run_id="latest", owner_released=True)

    surfaced = [s["branch"] for s in result["surfaced_unmerged"]]
    assert wt_branch in surfaced, f"expected {wt_branch} surfaced; result={result}"
    # Worktree folder removed; branch ref preserved.
    assert not wt_path.exists()
    assert _git(repo, "branch", "--list", wt_branch).stdout.strip(), (
        "unmerged branch must be preserved"
    )


# ---------------------------------------------------------------------------
# Mismatched build_loop_id → leave execution worktree alone (different run)
# ---------------------------------------------------------------------------

def test_collapse_skips_execution_worktree_when_bli_mismatch(tmp_path: Path) -> None:
    """If state.execution.build_loop_id != run.build_loop_id, the execution
    worktree belongs to a DIFFERENT (still-active) run — do not collapse it."""
    repo = _make_repo(tmp_path)
    bli_id_active = "bl-20260601T020000Z-test-000111"
    wt_path, wt_branch = _make_run_worktree(repo, bli_id_active)

    # state.execution points at the active build_loop_id; runs[0] is a
    # completed-but-different run.
    state = {
        "execution": {
            "build_loop_id": bli_id_active,
            "run_worktree_path": str(wt_path.resolve()),
            "run_worktree_branch": wt_branch,
        },
        "runs": [
            {
                "run_id": "run_old",
                "build_loop_id": "bl-20260530T120000Z-test-000999",
                "createdRefs": [],
            },
        ],
    }
    (repo / ".build-loop").mkdir(exist_ok=True)
    (repo / ".build-loop" / "state.json").write_text(json.dumps(state, indent=2))

    result = collapse_run.collapse(repo, run_id="latest", owner_released=True)

    # Nothing touched — the only ref source for run_old was empty createdRefs[].
    assert result["deleted"] == []
    assert result["kept_for_review"] == []
    assert result["surfaced_unmerged"] == []
    # The active run's worktree is intact.
    assert wt_path.exists()
    assert _git(repo, "branch", "--list", wt_branch).stdout.strip(), (
        "active run's branch must remain"
    )


# ---------------------------------------------------------------------------
# Released identity (stop_closeout terminal release) → fallback to archive
# ---------------------------------------------------------------------------


def test_collapse_recovers_worktree_from_historical_executions(tmp_path: Path) -> None:
    """Audit f4: after the terminal Stop closeout releases identity (execution
    archived to historicalExecutions, cleared), collapse must still find the
    run-entry worktree via the archive instead of stranding it."""
    repo = _make_repo(tmp_path)
    bli = "bl-20260613T000000Z-test-000111"
    wt_path, wt_branch = _make_run_worktree(repo, bli)
    # Merge the worktree branch so collapse classifies it MERGED → delete.
    (wt_path / "f.txt").write_text("x\n")
    _git(wt_path, "add", "f.txt")
    _git(wt_path, "commit", "-m", "work")
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge run")
    state = {
        "execution": {},  # released by stop_closeout._release_identity
        "historicalExecutions": [{
            "build_loop_id": bli,
            "run_worktree_path": str(wt_path.resolve()),
            "run_worktree_branch": wt_branch,
        }],
        "runs": [{
            "run_id": bli,
            "build_loop_id": bli,
            "createdRefs": [],
        }],
    }
    (repo / ".build-loop").mkdir(exist_ok=True)
    (repo / ".build-loop" / "state.json").write_text(json.dumps(state, indent=2))

    result = collapse_run.collapse(repo, run_id=bli, owner_released=True)

    deleted_branches = [d.get("branch") for d in result["deleted"]]
    assert wt_branch in deleted_branches, (
        f"released-run worktree branch must be recovered from historicalExecutions; got {result}"
    )
    assert not wt_path.exists()


def _commit_run_work(wt_path: Path, name: str = "work.txt") -> None:
    (wt_path / name).write_text("work\n")
    _git(wt_path, "add", name)
    _git(wt_path, "commit", "-m", f"add {name}")


def _strict_close(repo: Path, run_id: str, branch: str, **kwargs) -> dict:
    return collapse_run.collapse(
        repo,
        run_id=run_id,
        branch=branch,
        strict=True,
        merged_only=True,
        owner_released=True,
        require_run_root=True,
        release_source="pytest",
        **kwargs,
    )


def test_legacy_run_id_only_completes_transaction(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T105157Z-codex-441882"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    expected_oid = _git(repo, "rev-parse", wt_branch).stdout.strip()
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge legacy run")
    state = {
        "execution": {},
        "historicalExecutions": [{
            "build_loop_id": run_id,
            "run_worktree_path": str(wt_path.resolve()),
            "run_worktree_branch": wt_branch,
        }],
        "runs": [{"run_id": run_id, "createdRefs": []}],
    }
    (repo / ".build-loop" / "state.json").write_text(json.dumps(state, indent=2))

    result = _strict_close(repo, run_id, wt_branch)

    assert result["strict_success"] is True
    assert result["errors"] == []
    assert result["bundle_verified"] is True
    receipt = json.loads(Path(result["receipt_path"]).read_text())
    ref = next(entry for entry in receipt["refs"] if entry["branch"] == wt_branch)
    assert ref["status"] == "closed"
    assert ref["expected_oid"] == expected_oid
    assert ref["prepared_ts"] <= ref["closed_ts"]
    final = json.loads((repo / ".build-loop" / "state.json").read_text())
    assert final["runs"][0]["createdRefs"][0]["status"] == "closed"
    assert final["runs"][0]["branch_closeout"]["status"] == "complete"
    assert not wt_path.exists()
    assert not _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}", check=False).returncode == 0


def test_inline_stop_merge_and_strict_finalize_end_to_end(tmp_path: Path) -> None:
    """The real inline path must carry ownership across Stop into finalization."""
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T105157Z-codex-441883"
    session_id = "codex-inline-e2e"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    state = {
        "phase": "done",
        "triggers": {"riskSurfaceChange": False},
        "execution": {
            "build_loop_id": run_id,
            "current_session_id": session_id,
            "started_by_session_id": session_id,
            "last_heartbeat_at": stop_closeout._utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_worktree_path": str(wt_path.resolve()),
            "run_worktree_branch": wt_branch,
        },
        "runs": [],
    }
    (repo / ".build-loop" / "state.json").write_text(json.dumps(state, indent=2))

    stop_closeout.run_stop(repo, session_id)
    stopped = json.loads((repo / ".build-loop" / "state.json").read_text())
    assert stopped["execution"] == {}
    assert stopped["runs"][0]["createdRefs"][0]["status"] == "open"
    assert stopped["runs"][0]["branch_closeout"]["status"] == "pending_external_merge"

    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge inline run")
    result = _strict_close(repo, run_id, wt_branch)

    assert result["strict_success"] is True
    assert result["bundle_verified"] is True
    assert result["errors"] == []
    final = json.loads((repo / ".build-loop" / "state.json").read_text())
    assert final["runs"][0]["createdRefs"][0]["status"] == "closed"
    assert final["runs"][0]["branch_closeout"]["status"] == "complete"
    assert not wt_path.exists()
    assert _git(
        repo,
        "show-ref",
        "--verify",
        f"refs/heads/{wt_branch}",
        check=False,
    ).returncode != 0


def test_execution_only_state_fails_closed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-000222"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _write_state_with_execution(repo, run_id, wt_path, wt_branch, run_entry=None)

    result = collapse_run.collapse(repo, run_id="latest", owner_released=True)

    assert any("no attributable runs[] row" in error for error in result["errors"])
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0


def test_strict_close_requires_positive_owner_release(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-000333"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )

    result = collapse_run.collapse(
        repo,
        run_id=run_id,
        branch=wt_branch,
        strict=True,
        merged_only=True,
        require_run_root=True,
    )

    assert result["strict_success"] is False
    assert any("owner release required" in error for error in result["errors"])
    assert result["receipt_path"] is None
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0


@pytest.mark.parametrize("unsafe_kind", ["locked", "dirty"])
def test_strict_close_preserves_unsafe_worktree(tmp_path: Path, unsafe_kind: str) -> None:
    repo = _make_repo(tmp_path)
    run_id = f"bl-20260711T000000Z-test-{unsafe_kind}"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )
    if unsafe_kind == "locked":
        _git(repo, "worktree", "lock", "--reason", "terminal owner", str(wt_path))
    else:
        (wt_path / "uncommitted.txt").write_text("dirty\n")

    result = _strict_close(repo, run_id, wt_branch)

    assert result["strict_success"] is False
    assert any(unsafe_kind in error for error in result["errors"])
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0


def test_strict_close_preserves_live_process_cwd(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-000444"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )
    proc = subprocess.Popen(["sleep", "30"], cwd=wt_path)
    try:
        time.sleep(0.1)
        result = _strict_close(repo, run_id, wt_branch)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert result["strict_success"] is False
    assert any("live process cwd" in error for error in result["errors"])
    assert wt_path.exists()


@pytest.mark.parametrize("ledger_path", ["missing", "stale"])
def test_checked_out_branch_requires_exact_ledger_path(
    tmp_path: Path,
    ledger_path: str,
) -> None:
    """A missing/stale path must not turn a checked-out branch into branch-only cleanup."""
    repo = _make_repo(tmp_path)
    run_id = f"bl-20260711T000000Z-test-path-{ledger_path}"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    recorded_path = None
    if ledger_path == "stale":
        recorded_path = str(repo / ".build-loop/worktrees/run-wrong")
    state = {
        "execution": {},
        "runs": [{
            "run_id": run_id,
            "createdRefs": [{
                "branch": wt_branch,
                "path": recorded_path,
                "status": "open",
            }],
        }],
    }
    (repo / ".build-loop/state.json").write_text(json.dumps(state, indent=2))

    result = _strict_close(repo, run_id, wt_branch)

    assert result["strict_success"] is False
    assert any("branch is checked out" in error for error in result["errors"])
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0


def test_conflicting_durable_worktree_paths_are_retained(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-path-conflict"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    state = {
        "execution": {},
        "historicalExecutions": [{
            "build_loop_id": run_id,
            "run_worktree_branch": wt_branch,
            "run_worktree_path": str(wt_path.resolve()),
        }],
        "runs": [{
            "run_id": run_id,
            "createdRefs": [{
                "branch": wt_branch,
                "path": str(repo / ".build-loop/worktrees/run-wrong"),
                "status": "open",
            }],
        }],
    }
    (repo / ".build-loop/state.json").write_text(json.dumps(state, indent=2))

    result = _strict_close(repo, run_id, wt_branch)

    assert result["strict_success"] is False
    assert any("conflicting worktree paths" in error for error in result["errors"])
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0


def test_bundle_failure_causes_no_git_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-000555"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )
    monkeypatch.setattr(
        collapse_run,
        "_create_bundle",
        lambda *args, **kwargs: (None, False, "injected failure"),
    )

    result = _strict_close(repo, run_id, wt_branch)

    assert any("injected failure" in error for error in result["errors"])
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0
    assert not (repo / ".build-loop" / "branch-closeout").exists()


def test_branch_move_after_bundle_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-000666"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )
    real_create = collapse_run._create_bundle

    def create_then_move(*args, **kwargs):
        out = real_create(*args, **kwargs)
        _commit_run_work(wt_path, "late.txt")
        return out

    monkeypatch.setattr(collapse_run, "_create_bundle", create_then_move)

    result = _strict_close(repo, run_id, wt_branch)

    assert any("branch moved after bundle" in error for error in result["errors"])
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0


def test_prepared_receipt_reconciles_after_terminal_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-000777"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )
    real_write = collapse_run._write_receipt
    calls = {"count": 0}

    def fail_terminal_write(path, receipt):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("injected terminal write failure")
        return real_write(path, receipt)

    monkeypatch.setattr(collapse_run, "_write_receipt", fail_terminal_write)
    first = _strict_close(repo, run_id, wt_branch)
    assert any("terminal receipt write failed" in error for error in first["errors"])
    assert not wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}", check=False).returncode != 0

    monkeypatch.setattr(collapse_run, "_write_receipt", real_write)
    second = _strict_close(repo, run_id, wt_branch)

    assert second["strict_success"] is True
    assert second["bundle_verified"] is True
    assert second["bundle_path"] == first["bundle_path"]
    assert second["errors"] == []
    assert second["already_closed"]


def test_prepared_state_failure_precedes_git_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-000888"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )
    real_project = collapse_run._project_ref_state

    def fail_prepared_projection(*args, **kwargs):
        if kwargs.get("branch_closeout_status") == "prepared":
            raise OSError("injected prepared projection failure")
        return real_project(*args, **kwargs)

    monkeypatch.setattr(collapse_run, "_project_ref_state", fail_prepared_projection)

    result = _strict_close(repo, run_id, wt_branch)

    assert any("prepared state projection failed" in error for error in result["errors"])
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0
    receipt = json.loads(Path(result["receipt_path"]).read_text())
    assert receipt["status"] == "prepared"


def test_non_force_removal_preserves_worktree_that_becomes_dirty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-000999"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )
    real_remove = collapse_run._remove_worktree

    def dirty_then_remove(workdir, path):
        (Path(path) / "late-dirty.txt").write_text("changed after inspection\n")
        return real_remove(workdir, path)

    monkeypatch.setattr(collapse_run, "_remove_worktree", dirty_then_remove)

    result = _strict_close(repo, run_id, wt_branch)

    assert any("worktree remove failed" in error for error in result["errors"])
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0


def test_branch_recheckout_after_removal_prevents_ref_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-recheckout"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", wt_branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        wt_branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )
    real_remove = collapse_run._remove_worktree
    replacement = repo / ".build-loop/worktrees/run-recheckout-replacement"

    def remove_then_recheckout(workdir, path):
        error = real_remove(workdir, path)
        assert error is None
        _git(repo, "worktree", "add", str(replacement), wt_branch)
        return None

    monkeypatch.setattr(collapse_run, "_remove_worktree", remove_then_recheckout)

    result = _strict_close(repo, run_id, wt_branch)

    assert result["strict_success"] is False
    assert any("became checked out" in error for error in result["errors"])
    assert replacement.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0


def test_expected_delete_uses_git_checked_out_branch_protection(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-safe-delete"
    wt_path, wt_branch = _make_run_worktree(repo, run_id)
    expected_oid = _git(repo, "rev-parse", wt_branch).stdout.strip()

    error = collapse_run._delete_branch_expected(repo, wt_branch, expected_oid)

    assert error is not None and "checked out" in error
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{wt_branch}").returncode == 0


def test_background_root_guard_preserves_external_worktree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    run_id = "bl-20260711T000000Z-test-001000"
    branch = "bl/run-001000"
    wt_path = tmp_path / "external-run-001000"
    _git(repo, "worktree", "add", "-b", branch, str(wt_path), "main")
    _commit_run_work(wt_path)
    _git(repo, "merge", "--no-ff", branch, "-m", "merge")
    _write_state_with_execution(
        repo,
        run_id,
        wt_path,
        branch,
        run_entry={"run_id": run_id, "createdRefs": []},
    )

    result = _strict_close(repo, run_id, branch)

    assert any("outside .build-loop/worktrees" in error for error in result["errors"])
    assert wt_path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0
