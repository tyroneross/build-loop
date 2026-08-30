"""Tests for scripts/resume_resolver.py (M3).

Covers: schema-version mismatch, run_id mismatch, phase=report refusal,
latest resolution, concurrent-modification demotion, in-flight-no-return
demotion, no-state.json handling, heartbeat-staleness no-resume path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import resume_resolver  # noqa: E402
from resume_resolver import resolve  # noqa: E402
from write_run_entry import update_execution_state  # noqa: E402
from write_subagent_result import write_subagent_result  # noqa: E402


def _setup_started_run(tmp_path: Path, *, run_id="run_test_001", queued=("c1", "c2", "c3", "c4")) -> Path:
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    update_execution_state(
        state_path, "start",
        run_id=run_id,
        queued_chunks=list(queued),
        file_ownership={c: [f"{c}.py"] for c in queued},
        now=datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc),
    )
    return state_path


def _write_terminal_legacy_crash(tmp_path: Path) -> tuple[Path, dict]:
    """Reproduce the schema-less July crash whose run worktree recorded pass."""
    _make_git_repo(tmp_path)
    run_id = "bl-20260728T233835Z-codex-092307"
    run_worktree = tmp_path / ".build-loop" / "worktrees" / "run-092307"
    execution = {
        "build_loop_id": run_id,
        "crash_signal": "stop_hook",
        "crashed_at": "2026-07-29T01:06:26Z",
        "current_session_id": "019fab14-18dc-7ee2-b3a7-550e9f3d9934",
        "run_label": "codex#092307 2026-07-28T23:38:35.091646Z",
        "run_worktree_branch": "bl/run-092307",
        "run_worktree_path": str(run_worktree),
        "started_at": "2026-07-28T23:38:35.091646Z",
        "started_by_tool": "codex",
    }
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "execution": execution,
        "runs": [{"run_id": run_id, "outcome": "partial", "source": "append_run"}],
    }))
    child_state_path = run_worktree / ".build-loop" / "state.json"
    child_state_path.parent.mkdir(parents=True, exist_ok=True)
    child_state_path.write_text(json.dumps({
        "execution": dict(execution),
        "runs": [{"run_id": run_id, "outcome": "pass", "phases": {"6": {"status": "pass"}}}],
    }))
    return state_path, execution


def test_no_state_json_no_resume_returns_fresh(tmp_path):
    env = resolve(tmp_path, "")
    assert env["decision"] == "fresh"
    assert env["run_id"] is None


def test_no_state_json_with_resume_aborts(tmp_path):
    env = resolve(tmp_path, "run_doesnotexist")
    assert env["decision"] == "abort"


def test_malformed_state_json_aborts_instead_of_looking_absent(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"execution":')

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert "invalid JSON" in env["reason"]


@pytest.mark.parametrize("payload", [[], None, "state"])
def test_non_object_state_json_aborts(tmp_path, payload):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(payload))

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert "must contain a JSON object" in env["reason"]


def test_unreadable_state_path_aborts_instead_of_looking_absent(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.mkdir(parents=True)

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert "cannot read existing" in env["reason"]


def test_dangling_state_symlink_aborts_instead_of_looking_absent(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.symlink_to(tmp_path / "missing-state.json")

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert "dangling symlink" in env["reason"]


def test_non_object_execution_aborts(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"execution": ["not", "an", "object"]}))

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert "must be a JSON object" in env["reason"]


def test_empty_execution_without_resume_returns_fresh(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"execution": {}}))

    env = resolve(tmp_path, "")

    assert env["decision"] == "fresh"
    assert env["reason"] == "no incomplete run"


def test_empty_execution_with_resume_reports_no_execution_block(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"execution": {}}))

    env = resolve(tmp_path, "run_doesnotexist")

    assert env["decision"] == "abort"
    assert env["reason"] == "no execution block to resume from"


def test_terminal_schema_less_crash_requires_archive_before_fresh(tmp_path):
    state_path, execution = _write_terminal_legacy_crash(tmp_path)
    before = state_path.read_bytes()

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert env["run_id"] == execution["build_loop_id"]
    assert env["required_action"] == "archive_legacy_crash"
    assert env["fresh_ready"] is False
    assert env["archive_applied"] is False
    assert env["legacy_crash"]["classification"] == "terminal_legacy_crash"
    assert "run_worktree.state.runs records outcome=pass" in env["legacy_crash"]["evidence"]
    assert state_path.read_bytes() == before  # classification is read-only


def test_archive_terminal_schema_less_crash_clears_identity_atomically(tmp_path):
    state_path, execution = _write_terminal_legacy_crash(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "resume_resolver.py"),
            "--workdir", str(tmp_path),
            "--resume-arg", "",
            "--archive-terminal-legacy-crash",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["decision"] == "fresh"
    assert env["archive_applied"] is True
    assert env["fresh_ready"] is True
    state = json.loads(state_path.read_text())
    assert state["execution"] == {}
    assert state["historicalExecutions"][-1] == execution
    assert state["runs"][0]["outcome"] == "partial"


def test_schema_less_crash_with_live_worktree_remains_refused(tmp_path):
    run_worktree = tmp_path / ".build-loop" / "worktrees" / "run-live"
    run_worktree.mkdir(parents=True)
    _make_git_repo(run_worktree)
    state_path = tmp_path / ".build-loop" / "state.json"
    execution = {
        "build_loop_id": "bl-live-legacy",
        "crash_signal": "stop_hook",
        "crashed_at": "2026-07-29T01:06:26Z",
        "run_worktree_path": str(run_worktree),
    }
    state_path.write_text(json.dumps({"execution": execution}))

    env = resolve(tmp_path, "")
    apply_env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["classification"] == "ambiguous_or_potentially_active"
    assert apply_env["decision"] == "abort"
    assert json.loads(state_path.read_text())["execution"] == execution


def test_schema_less_crash_with_absent_managed_worktree_is_archivable(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "execution": {
            "build_loop_id": "bl-dead-legacy",
            "crashed_at": "2026-07-29T01:06:26Z",
            "crash_signal": "stop_hook",
            "run_worktree_path": str(tmp_path / ".build-loop" / "worktrees" / "gone"),
        },
        "runs": [{"run_id": "bl-dead-legacy", "outcome": "pass"}],
    }))

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert env["required_action"] == "archive_legacy_crash"
    assert "absent or dead" in " ".join(env["legacy_crash"]["evidence"])


def test_absent_legacy_resources_without_pass_remain_refused(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"execution": {
        "build_loop_id": "bl-dead-no-pass",
        "crashed_at": "2026-07-29T01:06:26Z",
        "crash_signal": "stop_hook",
        "run_worktree_path": str(tmp_path / ".build-loop" / "worktrees" / "gone"),
    }}))

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False


@pytest.mark.parametrize("manifest_payload", [None, "missing", "active", "mismatched"])
def test_absent_legacy_resources_require_terminal_matching_manifest(tmp_path, manifest_payload):
    run_id = "bl-dead-manifest"
    manifest_path = tmp_path / ".build-loop" / "data-manifests" / "dead.json"
    execution = {
        "build_loop_id": run_id,
        "crashed_at": "2026-07-29T01:06:26Z",
        "crash_signal": "stop_hook",
        "run_worktree_path": str(tmp_path / ".build-loop" / "worktrees" / "gone"),
        "data_manifest_path": "" if manifest_payload is None else str(manifest_path),
    }
    if manifest_payload in {"active", "mismatched"}:
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(json.dumps({
            "schema_version": 1,
            "run_id": "another-run" if manifest_payload == "mismatched" else run_id,
            "repository_path": str(tmp_path),
            "worktree_path": execution["run_worktree_path"],
            "branch": "bl/dead-manifest",
            "data_root": str(tmp_path / ".build-loop" / "data" / run_id),
            "created_at": "2026-07-29T00:00:00Z",
            "surfaces": [{
                "id": "production",
                "kind": "postgresql",
                "resource_key": "postgresql:production",
                "writable": True,
                "isolation": "shared_serialized",
                "authority": "canonical",
                "status": "active",
                "writer": "fixture",
            }],
        }))
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "execution": execution,
        "runs": [{"run_id": run_id, "outcome": "pass"}],
    }))

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False


def test_identified_legacy_residue_without_crash_signal_remains_refused(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "execution": {
            "build_loop_id": "bl-no-signal",
            "crashed_at": "2026-07-29T01:06:26Z",
        },
        "runs": [{"run_id": "bl-no-signal", "outcome": "pass"}],
    }))

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False


def _write_surviving_legacy_run(
    tmp_path: Path,
    *,
    merge_to_main: bool,
    outcome: str | None = "pass",
    manifest_status: str | None = "closed",
) -> tuple[Path, Path, dict]:
    """Create a real managed worktree whose run branch may be integrated."""
    _make_git_repo(tmp_path)
    (tmp_path / "base.txt").write_text("base\n")
    subprocess.check_call(["git", "add", "base.txt"], cwd=tmp_path)
    subprocess.check_call(["git", "commit", "-qm", "base"], cwd=tmp_path)
    subprocess.check_call(["git", "branch", "-M", "main"], cwd=tmp_path)
    run_worktree = tmp_path / ".build-loop" / "worktrees" / "run-surviving"
    run_worktree.parent.mkdir(parents=True)
    subprocess.check_call(
        ["git", "worktree", "add", "-qb", "bl/run-surviving", str(run_worktree)],
        cwd=tmp_path,
    )
    (run_worktree / "result.txt").write_text("done\n")
    subprocess.check_call(["git", "add", "result.txt"], cwd=run_worktree)
    subprocess.check_call(["git", "commit", "-qm", "run result"], cwd=run_worktree)
    if merge_to_main:
        subprocess.check_call(["git", "merge", "--ff-only", "bl/run-surviving"], cwd=tmp_path)
    execution = {
        "build_loop_id": "bl-surviving-legacy",
        "crash_signal": "stop_hook",
        "crashed_at": "2026-07-29T01:06:26Z",
        "run_worktree_path": str(run_worktree),
        "run_worktree_branch": "bl/run-surviving",
    }
    if manifest_status is not None:
        manifest_path = tmp_path / ".build-loop" / "data-manifests" / "surviving.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        data_root = tmp_path / ".build-loop" / "data" / execution["build_loop_id"]
        manifest_path.write_text(json.dumps({
            "schema_version": 1,
            "run_id": execution["build_loop_id"],
            "repository_path": str(tmp_path),
            "worktree_path": str(run_worktree),
            "branch": execution["run_worktree_branch"],
            "data_root": str(data_root),
            "created_at": "2026-07-29T00:00:00Z",
            "surfaces": [{
                "id": "fixture",
                "kind": "filesystem",
                "resource_key": "fixture:surviving",
                "writable": True,
                "isolation": "shared_serialized",
                "authority": "derived",
                "status": manifest_status,
                "writer": "fixture",
            }],
        }))
        execution["data_manifest_path"] = str(manifest_path)
    state_path = tmp_path / ".build-loop" / "state.json"
    runs = [] if outcome is None else [{"run_id": execution["build_loop_id"], "outcome": outcome}]
    state_path.write_text(json.dumps({"execution": execution, "runs": runs}))
    return state_path, run_worktree, execution


def test_schema_less_crash_with_clean_integrated_worktree_is_archivable(tmp_path):
    state_path, _, execution = _write_surviving_legacy_run(tmp_path, merge_to_main=True)

    env = resolve(tmp_path, "")
    apply_env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["required_action"] == "archive_legacy_crash"
    assert "integrated into main" in " ".join(env["legacy_crash"]["evidence"])
    assert apply_env["decision"] == "fresh"
    assert apply_env["archive_applied"] is True
    assert json.loads(state_path.read_text())["historicalExecutions"][-1] == execution


def test_schema_less_crash_with_dirty_integrated_worktree_remains_refused(tmp_path):
    state_path, run_worktree, execution = _write_surviving_legacy_run(tmp_path, merge_to_main=True)
    (run_worktree / "result.txt").write_text("uncommitted\n")

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


def test_schema_less_crash_with_clean_unmerged_worktree_remains_refused(tmp_path):
    state_path, _, execution = _write_surviving_legacy_run(tmp_path, merge_to_main=False)

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


@pytest.mark.parametrize("outcome", [None, "partial", "fail"])
def test_schema_less_crash_without_durable_pass_remains_refused(tmp_path, outcome):
    state_path, _, execution = _write_surviving_legacy_run(
        tmp_path,
        merge_to_main=True,
        outcome=outcome,
    )

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


def test_explicit_abandonment_preserves_partial_legacy_run_resources(tmp_path):
    state_path, run_worktree, execution = _write_surviving_legacy_run(
        tmp_path,
        merge_to_main=True,
        outcome="partial",
    )
    data_root = tmp_path / ".build-loop" / "data" / execution["build_loop_id"]
    data_root.mkdir(parents=True)
    execution["data_root"] = str(data_root)
    state_path.write_text(json.dumps({
        "execution": execution,
        "runs": [{"run_id": execution["build_loop_id"], "outcome": "partial"}],
    }))
    branch = execution["run_worktree_branch"]

    env = resolve(
        tmp_path,
        "",
        abandon_legacy_crash=execution["build_loop_id"],
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    state = json.loads(state_path.read_text())
    archived = state["historicalExecutions"][-1]
    assert env["decision"] == "fresh"
    assert env["abandon_applied"] is True
    assert env["fresh_ready"] is True
    assert state["execution"] == {}
    assert archived["build_loop_id"] == execution["build_loop_id"]
    assert archived["archive_disposition"] == "explicitly_abandoned"
    assert archived["abandoned_at"] == "2026-07-30T00:00:00Z"
    assert run_worktree.exists()
    assert Path(execution["data_manifest_path"]).exists()
    assert Path(execution["data_root"]).exists()
    assert env["preserved_resources"] == {
        "run_worktree_path": execution["run_worktree_path"],
        "run_worktree_branch": branch,
        "data_manifest_path": execution["data_manifest_path"],
        "data_root": execution["data_root"],
    }
    assert subprocess.run(
        ["git", "-C", str(tmp_path), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    ).returncode == 0


def test_explicit_abandonment_requires_exact_run_id(tmp_path):
    state_path, _, _execution = _write_surviving_legacy_run(
        tmp_path,
        merge_to_main=True,
        outcome="partial",
    )
    before = state_path.read_bytes()

    env = resolve(tmp_path, "", abandon_legacy_crash="wrong-run-id")

    assert env["decision"] == "abort"
    assert env["abandon_applied"] is False
    assert "does not match" in env["reason"]
    assert state_path.read_bytes() == before


def test_explicit_abandonment_refuses_execution_without_crash_marker(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    execution = {"build_loop_id": "legacy-live", "started_at": "2026-07-29T00:00:00Z"}
    state_path.write_text(json.dumps({"execution": execution}))

    env = resolve(tmp_path, "", abandon_legacy_crash="legacy-live")

    assert env["decision"] == "abort"
    assert env["abandon_applied"] is False
    assert "crash marker" in env["reason"]
    assert json.loads(state_path.read_text())["execution"] == execution


@pytest.mark.parametrize("manifest_status", ["active", "error", "deferred"])
def test_schema_less_crash_with_nonterminal_data_manifest_remains_refused(tmp_path, manifest_status):
    state_path, _, execution = _write_surviving_legacy_run(
        tmp_path,
        merge_to_main=True,
        manifest_status=manifest_status,
    )

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


def test_schema_less_crash_with_missing_data_manifest_remains_refused(tmp_path):
    state_path, _, execution = _write_surviving_legacy_run(tmp_path, merge_to_main=True)
    Path(execution["data_manifest_path"]).unlink()

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


def test_schema_less_crash_with_mismatched_data_manifest_remains_refused(tmp_path):
    state_path, _, execution = _write_surviving_legacy_run(tmp_path, merge_to_main=True)
    manifest_path = Path(execution["data_manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    manifest["run_id"] = "another-run"
    manifest_path.write_text(json.dumps(manifest))

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


def test_schema_less_crash_with_locked_worktree_remains_refused(tmp_path):
    state_path, run_worktree, execution = _write_surviving_legacy_run(tmp_path, merge_to_main=True)
    subprocess.check_call(["git", "worktree", "lock", str(run_worktree)], cwd=tmp_path)

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


def test_schema_less_crash_with_live_worktree_owner_remains_refused(tmp_path, monkeypatch):
    state_path, _, execution = _write_surviving_legacy_run(tmp_path, merge_to_main=True)
    monkeypatch.setattr(
        resume_resolver,
        "inspect_worktree_safety",
        lambda *_args, **_kwargs: {"safe": False, "reason": "live process cwd is inside worktree"},
    )

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


def test_schema_less_crash_integrated_only_into_non_main_head_remains_refused(tmp_path):
    state_path, _, execution = _write_surviving_legacy_run(tmp_path, merge_to_main=False)
    subprocess.check_call(["git", "checkout", "-qb", "feature-descendant"], cwd=tmp_path)
    subprocess.check_call(["git", "merge", "--ff-only", "bl/run-surviving"], cwd=tmp_path)

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


def test_schema_less_crash_with_moved_branch_ref_remains_refused(tmp_path):
    state_path, _, execution = _write_surviving_legacy_run(tmp_path, merge_to_main=True)
    main_head = subprocess.check_output(["git", "rev-parse", "HEAD^"], cwd=tmp_path, text=True).strip()
    subprocess.check_call(["git", "update-ref", "refs/heads/bl/run-surviving", main_head], cwd=tmp_path)

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["archive_safe"] is False
    assert json.loads(state_path.read_text())["execution"] == execution


def _write_identity_less_crash(tmp_path: Path, **extra) -> Path:
    """A stop_hook marker that recorded no run identity at all."""
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "execution": {"crashed_at": "2026-08-11T06:57:59Z", "crash_signal": "stop_hook", **extra},
        "runs": [],
    }))
    return state_path


def test_identity_less_crash_residue_is_archivable(tmp_path):
    """No run id means no chunks to resume, so the residue is terminal by construction.

    Without this arm the block is unarchivable forever and permanently blocks
    every fresh run in the repo.
    """
    _write_identity_less_crash(tmp_path)

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert env["required_action"] == "archive_legacy_crash"
    assert env["legacy_crash"]["classification"] == "terminal_legacy_crash"
    assert env["legacy_crash"]["archive_safe"] is True
    assert "identity-less" in " ".join(env["legacy_crash"]["evidence"])


def test_archiving_identity_less_residue_yields_fresh(tmp_path):
    state_path = _write_identity_less_crash(tmp_path)

    env = resolve(tmp_path, "", archive_terminal_legacy_crash=True)

    assert env["decision"] == "fresh"
    assert env["archive_applied"] is True
    assert env["fresh_ready"] is True
    state = json.loads(state_path.read_text())
    assert not state["execution"]
    assert state["historicalExecutions"][-1]["crash_signal"] == "stop_hook"


@pytest.mark.parametrize("ref", [
    {"run_worktree_path": "/tmp/some/run-1"},
    {"run_worktree_branch": "bl/run-1"},
])
def test_identity_less_residue_referencing_resources_stays_refused(tmp_path, ref):
    """A partially-written block still naming live resources must not auto-archive."""
    _write_identity_less_crash(tmp_path, **ref)

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert env["legacy_crash"]["classification"] == "ambiguous_or_potentially_active"
    assert env["legacy_crash"]["archive_safe"] is False


def test_no_resume_fresh_heartbeat_without_owner_aborts(tmp_path):
    _setup_started_run(tmp_path)
    # Heartbeat is fresh-ish — call resolve with a "now" only 30s after start
    now = datetime(2026, 5, 6, 10, 0, 30, tzinfo=timezone.utc)
    env = resolve(tmp_path, "", now=now)
    assert env["decision"] == "abort"
    assert "ownership is unproven" in env["reason"]


def test_no_resume_same_session_continues_existing_run_never_starts_fresh(tmp_path):
    state_path = _setup_started_run(tmp_path, run_id="run_owned")
    state = json.loads(state_path.read_text())
    state["execution"]["current_session_id"] = "session-current"
    state_path.write_text(json.dumps(state))

    env = resolve(
        tmp_path,
        "",
        now=datetime(2026, 5, 6, 10, 0, 30, tzinfo=timezone.utc),
        current_session_id="session-current",
    )
    wrong_owner = resolve(
        tmp_path,
        "",
        now=datetime(2026, 5, 6, 10, 0, 30, tzinfo=timezone.utc),
        current_session_id="session-other",
    )

    assert env["decision"] == "resume"
    assert env["session_continuity_verified"] is True
    assert env["ownership_verified"] is False
    assert env["run_id"] == "run_owned"
    assert {row["chunk_id"] for row in env["remaining_chunks"]} == {"c1", "c2", "c3", "c4"}
    assert wrong_owner["decision"] == "abort"
    assert wrong_owner["ownership_verified"] is False


@pytest.mark.parametrize(
    ("host_marker", "host_value"),
    [("CODEX_HOME", "/tmp/codex"), ("CURSOR_SESSION_ID", "cursor-test")],
)
def test_resume_cli_uses_host_neutral_runtime_root_without_claude_env(
    tmp_path, host_marker, host_value
):
    env = os.environ.copy()
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env["BUILD_LOOP_ROOT"] = str(REPO_ROOT)
    env[host_marker] = host_value

    result = subprocess.run(
        [
            sys.executable,
            str(Path(env["BUILD_LOOP_ROOT"]) / "scripts" / "resume_resolver.py"),
            "--workdir",
            str(tmp_path),
            "--resume-arg",
            "",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "fresh"


def test_resume_docs_use_runtime_root_and_list_every_decision():
    skill = (REPO_ROOT / "skills" / "build-loop" / "SKILL.md").read_text()
    protocol = (REPO_ROOT / "references" / "resume-protocol.md").read_text()
    for text in (skill, protocol):
        assert "${CLAUDE_PLUGIN_ROOT}/scripts/resume_resolver.py" not in text
        assert '$RUNTIME_PLUGIN_ROOT/scripts/resume_resolver.py' in text
    assert 'decision: "resume" | "abort" | "fresh" | "prompt_user"' in skill


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("iterate_attempt", "bad", "iterate_attempt"),
        ("queued_chunks", None, "queued_chunks"),
        ("in_flight_chunks", {"c1": True}, "in_flight_chunks"),
        ("completed_chunks", None, "completed_chunks"),
        ("file_ownership", [], "file_ownership"),
        ("last_heartbeat_at", 123, "last_heartbeat_at"),
    ],
)
def test_matching_session_malformed_schema_v1_aborts_without_traceback(
    tmp_path, field, value, reason
):
    state_path = _setup_started_run(tmp_path, run_id="run_malformed")
    state = json.loads(state_path.read_text())
    state["execution"]["current_session_id"] = "session-current"
    state["execution"][field] = value
    state_path.write_text(json.dumps(state))

    env = resolve(
        tmp_path,
        "",
        current_session_id="session-current",
        now=datetime(2026, 5, 6, 10, 0, 30, tzinfo=timezone.utc),
    )

    assert env["decision"] == "abort"
    assert "invalid schema-v1 execution" in env["reason"]
    assert reason in env["reason"]


def test_explicit_resume_malformed_budget_counter_aborts(tmp_path):
    state_path = _setup_started_run(tmp_path, run_id="run_bad_budget")
    state = json.loads(state_path.read_text())
    state["execution"]["budget"] = {
        "started_at": "2026-05-06T10:00:00Z",
        "deadline_at": "2026-05-06T12:00:00Z",
        "commits_since_push": "many",
    }
    state_path.write_text(json.dumps(state))

    env = resolve(tmp_path, "run_bad_budget")

    assert env["decision"] == "abort"
    assert "budget.commits_since_push" in env["reason"]


@pytest.mark.parametrize("heartbeat", [None, "not-an-iso-timestamp", "2026-05-06T10:00:00"])
def test_no_resume_missing_or_untrusted_heartbeat_aborts(tmp_path, heartbeat):
    state_path = _setup_started_run(tmp_path, run_id="run_unknown_heartbeat")
    state = json.loads(state_path.read_text())
    if heartbeat is None:
        state["execution"].pop("last_heartbeat_at")
    else:
        state["execution"]["last_heartbeat_at"] = heartbeat
    state_path.write_text(json.dumps(state))

    env = resolve(tmp_path, "")

    assert env["decision"] == "abort"
    assert "missing or unparseable" in env["reason"]
    assert env["ownership_verified"] is False


def test_no_resume_stale_heartbeat_prompts_user(tmp_path):
    _setup_started_run(tmp_path)
    # Now is 10 minutes after start — heartbeat is stale
    now = datetime(2026, 5, 6, 10, 10, 0, tzinfo=timezone.utc)
    env = resolve(tmp_path, "", now=now)
    assert env["decision"] == "prompt_user"
    assert env["run_id"] == "run_test_001"
    assert "incomplete build detected" in env["reason"]


def test_no_resume_phase_report_returns_fresh(tmp_path):
    _setup_started_run(tmp_path)
    update_execution_state(tmp_path / ".build-loop" / "state.json", "complete")
    # Even after "10 minutes" the phase=report sentinel says clean exit
    now = datetime(2026, 5, 6, 10, 10, 0, tzinfo=timezone.utc)
    env = resolve(tmp_path, "", now=now)
    assert env["decision"] == "fresh"


def test_resume_literal_match_succeeds(tmp_path):
    _setup_started_run(tmp_path, run_id="run_xyz")
    env = resolve(tmp_path, "run_xyz")
    assert env["decision"] == "resume"
    assert env["run_id"] == "run_xyz"
    # All 4 chunks queued and not yet dispatched → all remaining
    assert len(env["remaining_chunks"]) == 4
    assert {r["chunk_id"] for r in env["remaining_chunks"]} == {"c1", "c2", "c3", "c4"}


def test_resume_run_id_mismatch_aborts(tmp_path):
    _setup_started_run(tmp_path, run_id="run_abc")
    env = resolve(tmp_path, "run_def")
    assert env["decision"] == "abort"
    assert "does not match" in env["reason"]


def test_resume_phase_report_aborts(tmp_path):
    _setup_started_run(tmp_path, run_id="run_done")
    update_execution_state(tmp_path / ".build-loop" / "state.json", "complete")
    env = resolve(tmp_path, "run_done")
    assert env["decision"] == "abort"
    assert "already complete" in env["reason"]


def test_resume_schema_mismatch_aborts(tmp_path):
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "execution": {
            "schema_version": 999,
            "run_id": "run_old",
            "phase": "execute",
            "iterate_attempt": 0,
            "in_flight_chunks": [],
            "completed_chunks": [],
            "queued_chunks": ["c1"],
            "file_ownership": {"c1": ["a.py"]},
            "started_at": "2026-05-06T10:00:00Z",
            "last_heartbeat_at": "2026-05-06T10:00:00Z",
            "crashed_at": None,
        }
    }))
    env = resolve(tmp_path, "run_old")
    assert env["decision"] == "abort"
    assert "incompatible schema_version" in env["reason"]


def test_resume_with_some_returned_chunks_excludes_them(tmp_path):
    state_path = _setup_started_run(tmp_path, run_id="run_partial")
    # Dispatch + return c1 with status=fixed
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c1")
    update_execution_state(state_path, "return_chunk", chunk_id="c1", status="fixed")
    # Dispatch c2 but don't return (mid-execute crash)
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c2")
    env = resolve(tmp_path, "run_partial")
    assert env["decision"] == "resume"
    remaining = {r["chunk_id"] for r in env["remaining_chunks"]}
    # c1 is done (status=fixed → excluded); c2 is in_flight (no envelope → demoted); c3, c4 still queued
    assert remaining == {"c2", "c3", "c4"}


def test_in_flight_with_failed_envelope_demotes(tmp_path):
    state_path = _setup_started_run(tmp_path, run_id="run_failed")
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c1")
    # Subagent returned but with status=failed (M1 envelope present)
    write_subagent_result(tmp_path, "run_failed", {
        "chunk_id": "c1",
        "status": "failed",
        "files_changed": [],
        "verifications": [],
        "attempt": 1,
    })
    # State.json still shows c1 in in_flight (orchestrator crashed before update_execution_state for return_chunk)
    env = resolve(tmp_path, "run_failed")
    remaining = [r for r in env["remaining_chunks"] if r["chunk_id"] == "c1"]
    assert len(remaining) == 1
    assert remaining[0]["prior_status"] == "failed"


def test_resume_ignores_malformed_subagent_result_shapes_with_warnings(tmp_path):
    _setup_started_run(tmp_path, run_id="run_bad_results")
    results = tmp_path / ".build-loop" / "subagent-results" / "run_bad_results"
    results.mkdir(parents=True)
    (results / "array.json").write_text("[]", encoding="utf-8")
    (results / "null.json").write_text("null", encoding="utf-8")
    (results / "broken.json").write_bytes(b"\xff\xfe")
    (results / "mixed-attempt.json").write_text(
        json.dumps({"chunk_id": "c1", "status": "failed", "attempt": ["bad"]}),
        encoding="utf-8",
    )

    env = resolve(tmp_path, "run_bad_results")

    assert env["decision"] == "resume"
    assert env["envelopes"]["c1"][0]["status"] == "failed"
    assert len(env["state_warnings"]) == 4
    assert sum("ignored subagent result" in item for item in env["state_warnings"]) == 3
    assert any("non-integer attempt" in item for item in env["state_warnings"])


def test_resume_rejects_run_id_path_traversal_before_reading_results(tmp_path):
    state_path = _setup_started_run(tmp_path, run_id="run_safe")
    state = json.loads(state_path.read_text())
    state["execution"]["run_id"] = "../../outside"
    state_path.write_text(json.dumps(state))
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "leak.json").write_text(
        json.dumps({"chunk_id": "c1", "status": "fixed", "attempt": 1})
    )

    env = resolve(tmp_path, "../../outside")

    assert env["decision"] == "abort"
    assert "single safe path component" in env["reason"]
    assert env["envelopes"] == {}


def test_resume_bounds_subagent_result_file_size(tmp_path, monkeypatch):
    _setup_started_run(tmp_path, run_id="run_bounded_file")
    results = tmp_path / ".build-loop" / "subagent-results" / "run_bounded_file"
    results.mkdir(parents=True)
    monkeypatch.setattr(resume_resolver, "MAX_RESULT_FILE_BYTES", 128)
    (results / "oversize.json").write_text(
        json.dumps({"chunk_id": "c1", "status": "failed", "note": "x" * 256})
    )
    (results / "valid.json").write_text(
        json.dumps({"chunk_id": "c1", "status": "failed", "attempt": 2})
    )

    env = resolve(tmp_path, "run_bounded_file")

    assert len(env["envelopes"]["c1"]) == 1
    assert any("exceeds 128 bytes" in item for item in env["state_warnings"])


def test_resume_bounds_result_entries_and_attempts(tmp_path, monkeypatch):
    _setup_started_run(tmp_path, run_id="run_bounded_entries")
    results = tmp_path / ".build-loop" / "subagent-results" / "run_bounded_entries"
    results.mkdir(parents=True)
    monkeypatch.setattr(resume_resolver, "MAX_RESULT_FILES", 70)
    monkeypatch.setattr(resume_resolver, "MAX_RESULT_ATTEMPTS_PER_CHUNK", 8)
    for attempt in range(71):
        (results / f"{attempt:03d}.json").write_text(
            json.dumps({"chunk_id": "c1", "status": "failed", "attempt": attempt})
        )

    env = resolve(tmp_path, "run_bounded_entries")

    assert len(env["envelopes"]["c1"]) == 8
    retained_attempts = [row["attempt"] for row in env["envelopes"]["c1"]]
    assert retained_attempts == sorted(retained_attempts)
    assert all(0 <= attempt <= 70 for attempt in retained_attempts)
    assert any("truncated at 70 directory entries" in item for item in env["state_warnings"])
    assert any("retained the newest 8 attempts" in item for item in env["state_warnings"])


def test_resume_refuses_symlinked_results_directory(tmp_path):
    _setup_started_run(tmp_path, run_id="run_symlinked")
    outside = tmp_path / "outside-results"
    outside.mkdir()
    (outside / "result.json").write_text(
        json.dumps({"chunk_id": "c1", "status": "fixed", "attempt": 1})
    )
    results_root = tmp_path / ".build-loop" / "subagent-results"
    results_root.mkdir(parents=True)
    (results_root / "run_symlinked").symlink_to(outside, target_is_directory=True)

    env = resolve(tmp_path, "run_symlinked")

    assert env["decision"] == "resume"
    assert env["envelopes"] == {}
    assert any("symlinks are not trusted" in item for item in env["state_warnings"])


def test_resume_latest_resolves_to_actual_run_id(tmp_path):
    _setup_started_run(tmp_path, run_id="run_latest_test")
    now = datetime(2026, 5, 6, 10, 30, 0, tzinfo=timezone.utc)  # 30 min after start
    env = resolve(tmp_path, "latest", now=now)
    assert env["decision"] == "resume"
    assert env["run_id"] == "run_latest_test"


def test_resume_latest_when_no_stale_run_aborts(tmp_path):
    _setup_started_run(tmp_path)
    now = datetime(2026, 5, 6, 10, 0, 30, tzinfo=timezone.utc)  # 30s after start, fresh heartbeat
    env = resolve(tmp_path, "latest", now=now)
    assert env["decision"] == "abort"


def _make_git_repo(tmp_path: Path) -> None:
    """Initialize a git repo so concurrent-modification check has something to query."""
    subprocess.check_call(["git", "init", "-q"], cwd=tmp_path)
    subprocess.check_call(["git", "config", "user.email", "test@test"], cwd=tmp_path)
    subprocess.check_call(["git", "config", "user.name", "test"], cwd=tmp_path)


def test_concurrent_modification_demotes_completed_chunk(tmp_path):
    _make_git_repo(tmp_path)
    state_path = _setup_started_run(tmp_path, run_id="run_cm")
    # Create the file owned by c1, commit it, then complete c1
    (tmp_path / "c1.py").write_text("# v1\n")
    subprocess.check_call(["git", "add", "c1.py"], cwd=tmp_path)
    subprocess.check_call(["git", "commit", "-qm", "v1"], cwd=tmp_path)
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c1")
    update_execution_state(state_path, "return_chunk", chunk_id="c1", status="fixed")

    # Hand-modify c1.py AFTER the chunk completed
    time.sleep(0.05)
    (tmp_path / "c1.py").write_text("# v2 hand-edited\n")

    env = resolve(tmp_path, "run_cm")
    assert env["decision"] == "resume"
    flagged = [m for m in env["concurrent_modifications"] if m["chunk_id"] == "c1"]
    assert len(flagged) == 1
    assert "c1.py" in flagged[0]["files"]
    # Demoted into remaining_chunks with concurrent_modification_detected status
    remaining_c1 = [r for r in env["remaining_chunks"] if r["chunk_id"] == "c1"]
    assert remaining_c1
    assert remaining_c1[0]["prior_status"] == "concurrent_modification_detected"


def test_concurrent_modification_skipped_when_file_clean(tmp_path):
    _make_git_repo(tmp_path)
    state_path = _setup_started_run(tmp_path, run_id="run_clean")
    (tmp_path / "c1.py").write_text("# v1\n")
    subprocess.check_call(["git", "add", "c1.py"], cwd=tmp_path)
    subprocess.check_call(["git", "commit", "-qm", "v1"], cwd=tmp_path)
    update_execution_state(state_path, "dispatch_chunk", chunk_id="c1")
    update_execution_state(state_path, "return_chunk", chunk_id="c1", status="fixed")
    # Don't modify after — file is clean
    env = resolve(tmp_path, "run_clean")
    assert env["concurrent_modifications"] == []


def test_iterate_attempt_preserved_across_resume(tmp_path):
    state_path = _setup_started_run(tmp_path, run_id="run_iter")
    update_execution_state(state_path, "iterate_attempt")
    update_execution_state(state_path, "iterate_attempt")
    env = resolve(tmp_path, "run_iter")
    assert env["iterate_attempt"] == 2
