# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/worktree_guard.py and the created_ref kind in log_decision.py."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import worktree_guard
import log_decision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_state(workdir: Path) -> dict:
    return json.loads((workdir / ".build-loop" / "state.json").read_text())


def _init_git_repo(path: Path) -> None:
    """Initialise a minimal git repo with a first commit so worktree add works."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(path), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path), check=True, capture_output=True,
    )
    (path / "README.md").write_text("test repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(path), check=True, capture_output=True,
    )


# ---------------------------------------------------------------------------
# assert_worktree_path
# ---------------------------------------------------------------------------


def test_assert_worktree_path_accepts_canonical(tmp_path: Path) -> None:
    valid = str(tmp_path / ".build-loop" / "worktrees" / "my-slug")
    # Should not raise
    worktree_guard.assert_worktree_path(tmp_path, valid)


def test_assert_worktree_path_rejects_sibling(tmp_path: Path) -> None:
    sibling = str(tmp_path / "worktrees" / "my-slug")
    with pytest.raises(ValueError, match="canonical root"):
        worktree_guard.assert_worktree_path(tmp_path, sibling)


def test_assert_worktree_path_rejects_claude_worktrees(tmp_path: Path) -> None:
    bad = str(tmp_path / ".claude" / "worktrees" / "my-slug")
    with pytest.raises(ValueError, match="canonical root"):
        worktree_guard.assert_worktree_path(tmp_path, bad)


def test_assert_worktree_path_rejects_parent_escape(tmp_path: Path) -> None:
    # Path traversal attempt
    bad = str(tmp_path / ".build-loop" / "worktrees" / ".." / ".." / "escaped")
    with pytest.raises(ValueError, match="canonical root"):
        worktree_guard.assert_worktree_path(tmp_path, bad)


# ---------------------------------------------------------------------------
# canonical_branch_name
# ---------------------------------------------------------------------------


def test_canonical_branch_name_simple() -> None:
    assert worktree_guard.canonical_branch_name("my-run") == "bl/my-run"


def test_canonical_branch_name_with_chunk() -> None:
    assert worktree_guard.canonical_branch_name("my-run", "chunk-a") == "bl/my-run-chunk-a"


def test_canonical_branch_name_sanitizes_uppercase() -> None:
    assert worktree_guard.canonical_branch_name("MyRun") == "bl/myrun"


def test_canonical_branch_name_sanitizes_special_chars() -> None:
    result = worktree_guard.canonical_branch_name("run/2026@05")
    assert result.startswith("bl/")
    assert "@" not in result
    assert "/" not in result[3:]  # after bl/ prefix


def test_canonical_branch_name_collapses_dashes() -> None:
    result = worktree_guard.canonical_branch_name("run--double--dash")
    assert "--" not in result


def test_canonical_branch_name_strips_leading_trailing_dashes() -> None:
    result = worktree_guard.canonical_branch_name("-leading-trailing-")
    # After bl/ the slug should not start or end with dash
    slug_part = result[len("bl/"):]
    assert not slug_part.startswith("-")
    assert not slug_part.endswith("-")


# ---------------------------------------------------------------------------
# canonical_worktree_path
# ---------------------------------------------------------------------------


def test_canonical_worktree_path(tmp_path: Path) -> None:
    p = worktree_guard.canonical_worktree_path(tmp_path, "my-slug")
    assert p == tmp_path / ".build-loop" / "worktrees" / "my-slug"


def test_canonical_worktree_path_sanitizes_slug(tmp_path: Path) -> None:
    p = worktree_guard.canonical_worktree_path(tmp_path, "My Slug!")
    assert " " not in str(p)
    assert "!" not in str(p)


# ---------------------------------------------------------------------------
# create_guarded_worktree — real git repo
# ---------------------------------------------------------------------------


def test_create_guarded_worktree_creates_under_canonical_root(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    result = worktree_guard.create_guarded_worktree(tmp_path, "test-slug")

    assert result["error"] is None, f"git error: {result['error']}"
    assert result["created"] is True
    assert result["branch"].startswith("bl/")

    # Worktree directory must exist under .build-loop/worktrees/
    wt_path = Path(result["path"])
    assert wt_path.exists()
    assert ".build-loop/worktrees/" in str(wt_path)


def test_create_guarded_worktree_branch_is_bl_prefixed(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    result = worktree_guard.create_guarded_worktree(tmp_path, "feature-x")

    assert result["error"] is None, result["error"]
    assert result["branch"].startswith("bl/")


def test_create_guarded_worktree_records_created_ref(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    result = worktree_guard.create_guarded_worktree(tmp_path, "recorded-slug")

    assert result["error"] is None, result["error"]

    state = _read_state(tmp_path)
    assert len(state["runs"]) == 1
    refs = state["runs"][0].get("createdRefs", [])
    assert len(refs) == 1
    ref = refs[0]
    assert ref["kind"] == "worktree"
    assert ref["branch"].startswith("bl/")
    assert ref["path"] == result["path"]
    assert ref["review_hold"] is False
    assert ref["merge_target"] == "main"
    assert ref["purpose"] == "isolated worktree for recorded-slug"
    assert ref["status"] == "open"
    assert ref["close_reason"] is None
    assert ref["closed_ts"] is None
    assert ref["close_criteria"]
    assert ref["created_ts"]
    assert ref["last_status_ts"]


def test_create_guarded_worktree_records_custom_lifecycle(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    result = worktree_guard.create_guarded_worktree(
        tmp_path,
        "custom-ledger",
        purpose="verify isolated ledger behavior",
        close_criteria=["tests pass", "merged into main", "worktree removed"],
    )

    assert result["error"] is None, result["error"]
    state = _read_state(tmp_path)
    ref = state["runs"][0]["createdRefs"][0]
    assert ref["purpose"] == "verify isolated ledger behavior"
    assert ref["close_criteria"] == ["tests pass", "merged into main", "worktree removed"]


def test_create_guarded_worktree_no_record_skips_state(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    result = worktree_guard.create_guarded_worktree(tmp_path, "no-record-slug", record=False)

    assert result["error"] is None, result["error"]
    state_path = tmp_path / ".build-loop" / "state.json"
    # state.json should NOT have been created when record=False
    assert not state_path.exists()


def test_create_guarded_worktree_bad_base_returns_error(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    result = worktree_guard.create_guarded_worktree(
        tmp_path, "bad-base-slug", base="nonexistent-ref-xyz"
    )
    assert result["created"] is False
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# log_decision created_ref kind — unit tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    return tmp_path


def test_log_created_ref_appends(workdir: Path) -> None:
    payload = {
        "kind": "worktree",
        "path": "/repo/.build-loop/worktrees/my-slug",
        "branch": "bl/my-slug",
    }
    entry = log_decision.log_created_ref(workdir, payload)

    assert entry["kind"] == "worktree"
    assert entry["branch"] == "bl/my-slug"
    assert entry["merge_target"] == "main"
    assert entry["review_hold"] is False
    assert entry["purpose"] == ""
    assert entry["status"] == "open"
    assert entry["close_reason"] is None
    assert entry["closed_ts"] is None
    assert entry["close_criteria"]
    assert entry["created_ts"]
    assert entry["last_status_ts"]

    state = _read_state(workdir)
    assert len(state["runs"][0]["createdRefs"]) == 1


def test_log_created_ref_idempotent_on_branch(workdir: Path) -> None:
    payload = {
        "kind": "branch",
        "branch": "bl/my-feature",
    }
    log_decision.log_created_ref(workdir, payload)
    log_decision.log_created_ref(workdir, payload)
    log_decision.log_created_ref(workdir, payload)

    state = _read_state(workdir)
    assert len(state["runs"][0]["createdRefs"]) == 1


def test_log_created_ref_multiple_distinct_branches(workdir: Path) -> None:
    for branch in ["bl/a", "bl/b", "bl/c"]:
        log_decision.log_created_ref(workdir, {"kind": "branch", "branch": branch})

    state = _read_state(workdir)
    branches = [r["branch"] for r in state["runs"][0]["createdRefs"]]
    assert branches == ["bl/a", "bl/b", "bl/c"]


def test_log_created_ref_missing_kind_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="missing required fields"):
        log_decision.log_created_ref(workdir, {"branch": "bl/x"})


def test_log_created_ref_missing_branch_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="missing required fields"):
        log_decision.log_created_ref(workdir, {"kind": "worktree"})


def test_log_created_ref_invalid_kind_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="kind must be one of"):
        log_decision.log_created_ref(workdir, {"kind": "tag", "branch": "bl/x"})


def test_log_created_ref_invalid_status_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="status must be one of"):
        log_decision.log_created_ref(
            workdir,
            {"kind": "branch", "branch": "bl/x", "status": "half-open"},
        )


def test_log_created_ref_invalid_close_criteria_raises(workdir: Path) -> None:
    with pytest.raises(SystemExit, match="close_criteria"):
        log_decision.log_created_ref(
            workdir,
            {"kind": "branch", "branch": "bl/x", "close_criteria": ["ok", ""]},
        )


def test_log_created_ref_branch_kind_no_path(workdir: Path) -> None:
    """kind=branch without path should succeed (path is only required/warned for worktree)."""
    entry = log_decision.log_created_ref(workdir, {"kind": "branch", "branch": "bl/feature"})
    assert entry["path"] is None


def test_log_created_ref_worktree_without_path_warns(
    workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """kind=worktree without path emits a warning but does not fail."""
    entry = log_decision.log_created_ref(workdir, {"kind": "worktree", "branch": "bl/x"})
    assert entry["kind"] == "worktree"
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()


def test_log_created_ref_respects_custom_merge_target(workdir: Path) -> None:
    entry = log_decision.log_created_ref(
        workdir,
        {"kind": "branch", "branch": "bl/x", "merge_target": "develop"},
    )
    assert entry["merge_target"] == "develop"


def test_log_created_ref_review_hold_true(workdir: Path) -> None:
    entry = log_decision.log_created_ref(
        workdir,
        {"kind": "branch", "branch": "bl/risky", "review_hold": True},
    )
    assert entry["review_hold"] is True
    assert "human review disposition" in " ".join(entry["close_criteria"])


def test_log_created_ref_cli(workdir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload_file = tmp_path / "p.json"
    payload_file.write_text(json.dumps({
        "kind": "branch",
        "branch": "bl/cli-test",
    }))
    rc = log_decision.main([
        "--workdir", str(workdir),
        "--kind", "created_ref",
        "--payload-json", str(payload_file),
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["branch"] == "bl/cli-test"


# ---------------------------------------------------------------------------
# Backward-compat: existing kinds still work after extension
# ---------------------------------------------------------------------------


def test_autonomous_default_unaffected(workdir: Path) -> None:
    payload = {
        "decision_id": "d-compat",
        "phase": "execute",
        "chosen": "A",
        "options": [{"id": "A"}, {"id": "B"}],
        "confidence": "high",
        "rationale": "backward compat check",
    }
    entry = log_decision.log_autonomous_default(workdir, payload)
    assert entry["decision_id"] == "d-compat"


def test_risky_branch_unaffected(workdir: Path) -> None:
    payload = {
        "branch": "buildloop-risky-compat",
        "hash": "deadbeef",
        "files": ["foo.py"],
    }
    entry = log_decision.log_risky_branch(workdir, payload)
    assert entry["hash"] == "deadbeef"
