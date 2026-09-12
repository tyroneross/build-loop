# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Safety contract tests for the report-only worktree reaper."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG = _HERE.parent
_SCRIPTS = _PKG.parent
_REPO = _SCRIPTS.parent
for _d in (_REPO, _SCRIPTS, _PKG):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from worktree_reaper.reaper import reap_worktrees  # noqa: E402


def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


def _make_run_worktree(
    repo: Path,
    short: str,
    *,
    unmerged: bool = False,
) -> tuple[Path, str, str]:
    run_id = f"bl-20260711T000000Z-test-{short}"
    branch = f"bl/run-{short}"
    path = repo / ".build-loop" / "worktrees" / f"run-{short}"
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), "main")
    if unmerged:
        (path / "work.txt").write_text(f"work-{short}\n")
        _git(path, "add", "work.txt")
        _git(path, "commit", "-m", f"work {short}")
    return path, branch, run_id


def _age_folder(path: Path, hours: float = 24) -> None:
    timestamp = time.time() - (hours * 3600)
    os.utime(path, (timestamp, timestamp))


def _write_state(
    repo: Path,
    run_id: str,
    branch: str,
    path: Path,
    *,
    active: bool = False,
    duplicate: bool = False,
) -> None:
    execution = {
        "build_loop_id": run_id,
        "run_worktree_branch": branch,
        "run_worktree_path": str(path.resolve()),
    }
    row = {
        "run_id": run_id,
        "outcome": "pass",
        "summary": f"Completed {run_id}",
        "filesTouched": ["README.md"],
        "createdRefs": [{
            "kind": "worktree",
            "branch": branch,
            "path": str(path.resolve()),
            "status": "open",
        }],
    }
    state = {
        "execution": execution if active else {},
        "historicalExecutions": [] if active else [execution],
        "runs": [row],
    }
    if duplicate:
        state["runs"].append(
            {
                "run_id": run_id + "-other",
                "createdRefs": [{
                    "branch": branch,
                    "path": str(path.resolve()),
                    "status": "open",
                }],
            }
        )
    bl = repo / ".build-loop"
    bl.mkdir(exist_ok=True)
    (bl / "state.json").write_text(json.dumps(state, indent=2))


def test_default_is_report_only_and_non_destructive(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "111111")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    result = reap_worktrees(repo)

    assert result.dry_run is True
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert {k: candidate[k] for k in ("path", "branch", "run_id")} == {
        "path": str(path), "branch": branch, "run_id": run_id
    }
    assert candidate["inventory"]["ok"] is True
    assert result.bundled_and_removed == []
    assert path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0
    assert not (repo / ".build-loop" / "bundles").exists()


def test_act_without_owner_release_remains_report_only(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "222222")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    result = reap_worktrees(repo, dry_run=False, act=True, owner_released=False)

    assert result.dry_run is True
    assert any("owner-released" in row["reason"] for row in result.errors)
    assert path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0


def test_explicit_owner_released_act_delegates_to_strict_collapse(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "333333")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    result = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
        memory_root=tmp_path / "memory",
    )

    assert result.errors == []
    assert len(result.bundled_and_removed) == 1
    finalized = result.bundled_and_removed[0]
    assert Path(finalized["bundle"]).is_file()
    assert Path(finalized["receipt"]).is_file()
    assert finalized["memory_closeout"]["milestone"]["status"] == "recorded"
    assert finalized["memory_closeout"]["source"] == "branch-collapse"
    milestone_path = Path(finalized["memory_closeout"]["milestone"]["append"]["path"])
    record = json.loads(milestone_path.read_text(encoding="utf-8"))
    assert record["run_id"] == run_id
    assert not path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode != 0


def test_active_worktree_is_never_delegated(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "444444")
    _age_folder(path)
    _write_state(repo, run_id, branch, path, active=True)

    result = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
    )

    assert any(row["branch"] == branch for row in result.skipped_active)
    assert result.bundled_and_removed == []
    assert path.exists()


def test_young_and_unmerged_worktrees_are_preserved(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    young, young_branch, young_run = _make_run_worktree(repo, "555555")
    _write_state(repo, young_run, young_branch, young)
    result = reap_worktrees(repo)
    assert any(row["path"] == str(young) for row in result.skipped_too_young)
    assert young.exists()

    # Use a separate repo because state attribution deliberately names one run.
    repo2 = _make_repo(tmp_path / "second")
    unmerged, branch, run_id = _make_run_worktree(repo2, "666666", unmerged=True)
    _age_folder(unmerged)
    _write_state(repo2, run_id, branch, unmerged)
    result2 = reap_worktrees(
        repo2,
        dry_run=False,
        act=True,
        owner_released=True,
    )
    assert any(row["branch"] == branch for row in result2.skipped_unmerged)
    assert unmerged.exists()
    assert _git(repo2, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0


def test_orphan_and_ambiguous_candidates_are_preserved(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    orphan = repo / ".build-loop" / "worktrees" / "run-orphan"
    orphan.mkdir(parents=True)
    (orphan / "data.txt").write_text("unknown\n")
    _age_folder(orphan)
    (repo / ".build-loop" / "state.json").write_text(json.dumps({"runs": []}))

    orphan_result = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
    )
    assert any(row["path"] == str(orphan) for row in orphan_result.skipped_unattributed)
    assert orphan.exists()
    assert orphan_result.removed_orphan == []

    path, branch, run_id = _make_run_worktree(repo, "777777")
    _age_folder(path)
    _write_state(repo, run_id, branch, path, duplicate=True)
    ambiguous = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
    )
    assert any(
        row.get("branch") == branch and "ambiguous" in row["reason"]
        for row in ambiguous.skipped_unattributed
    )
    assert path.exists()


def test_pathless_or_mismatched_attribution_is_never_delegated(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "787878")
    _age_folder(path)
    state = {
        "execution": {},
        "runs": [{
            "run_id": run_id,
            "createdRefs": [{"branch": branch, "status": "open"}],
        }],
    }
    (repo / ".build-loop/state.json").write_text(json.dumps(state, indent=2))

    pathless = reap_worktrees(
        repo,
        dry_run=False,
        act=True,
        owner_released=True,
    )

    assert pathless.bundled_and_removed == []
    assert any(
        row.get("branch") == branch and "no unique durable run attribution" in row["reason"]
        for row in pathless.skipped_unattributed
    )
    assert path.exists()
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0


def test_non_run_prefixed_folder_is_ignored(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    other = repo / ".build-loop" / "worktrees" / "dispatch-1"
    other.mkdir(parents=True)
    _age_folder(other)
    (repo / ".build-loop" / "state.json").write_text(json.dumps({"runs": []}))

    result = reap_worktrees(repo)

    assert any(row["path"] == str(other) for row in result.skipped_not_run)
    assert other.exists()


def test_both_cli_entry_modes_default_to_report_only(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "888888")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    commands = [
        [sys.executable, "-m", "scripts.worktree_reaper"],
        [sys.executable, str(_PKG / "__main__.py")],
    ]
    for command in commands:
        proc = subprocess.run(
            [
                *command,
                "--workdir",
                str(repo),
                "--min-age-hours",
                "0",
                "--json",
            ],
            cwd=_REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["dry_run"] is True
        assert payload["candidates"][0]["branch"] == branch
        assert path.exists()


def test_cli_act_requires_owner_release(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    path, branch, run_id = _make_run_worktree(repo, "999999")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.worktree_reaper",
            "--workdir",
            str(repo),
            "--act",
            "--json",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1
    assert path.exists()
    assert "owner-released" in proc.stdout


def test_candidate_report_names_ignored_files_removal_would_delete(tmp_path: Path) -> None:
    """A removal deletes ignored files too, so the report that precedes it must
    name them. `git status --short` does not, which is how "tool caches only"
    gets approved on evidence that cannot support it."""
    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text("*.log\n.env\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore rules")
    path, branch, run_id = _make_run_worktree(repo, "222222")
    (path / ".env").write_text("API_KEY=synthetic\n")
    (path / "debug.log").write_text("noise\n")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    result = reap_worktrees(repo)

    assert _git(path, "status", "--short").stdout.strip() == "", (
        "precondition: the short status an operator would read shows nothing"
    )
    inventory = result.candidates[0]["inventory"]
    assert ".env" in inventory["ignored"]
    assert "debug.log" in inventory["ignored"]
    assert ".env" in inventory["non_reproducible_ignored"]
    assert inventory["caches_only_claim_supported"] is False


def test_unmerged_skip_report_also_carries_the_inventory(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text("*.log\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore rules")
    path, branch, run_id = _make_run_worktree(repo, "333333", unmerged=True)
    (path / "debug.log").write_text("noise\n")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    result = reap_worktrees(repo)

    assert result.candidates == []
    assert "debug.log" in result.skipped_unmerged[0]["inventory"]["ignored"]


def test_degraded_inventory_keeps_the_packet_shape(tmp_path: Path) -> None:
    """"We could not look" and "we looked and it is clean" must never be the
    same value to a packet reader."""
    from worktree_reaper import reaper as reaper_mod

    # Patch the module object the reaper actually bound: `scripts.worktree_inventory`
    # and top-level `worktree_inventory` are two distinct objects under the
    # dual-import pattern, and patching the wrong one silently no-ops.
    module = reaper_mod.worktree_inventory
    original = module.inventory
    module.inventory = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        degraded = reaper_mod._inventory(tmp_path)
    finally:
        module.inventory = original

    assert degraded["caches_only_claim_supported"] is False
    assert degraded["error"] == "boom"
    assert degraded["non_reproducible_ignored"] == []
    assert "characterization" in degraded


def test_default_output_surfaces_the_caches_only_red_flag(tmp_path: Path) -> None:
    """The strongest signal this tool produces must reach the surface a human
    reads by default, not only --json."""
    import io
    import contextlib

    from worktree_reaper import __main__ as reaper_cli

    repo = _make_repo(tmp_path)
    (repo / ".gitignore").write_text(".env\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore rules")
    path, branch, run_id = _make_run_worktree(repo, "444444")
    (path / ".env").write_text("API_KEY=synthetic\n")
    _age_folder(path)
    _write_state(repo, run_id, branch, path)

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        reaper_cli.main(["--workdir", str(repo)])

    printed = err.getvalue()
    assert str(path) in printed, "the candidate holding a .env must be named by default"
    assert "caches-only characterization is unsupported" in printed
