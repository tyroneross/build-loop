# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import run_status


NOW = datetime(2026, 9, 12, 2, 0, 0, tzinfo=timezone.utc)


def _write_state(workdir: Path, state: dict) -> None:
    target = workdir / ".build-loop" / "state.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state), encoding="utf-8")


def test_reports_fresh_heartbeat_without_overclaiming_process_liveness(tmp_path: Path):
    _write_state(tmp_path, {"execution": {
        "schema_version": 1,
        "run_id": "bl-live",
        "phase": "execute",
        "last_heartbeat_at": "2026-09-12T01:58:00Z",
    }})

    result = run_status.inspect_run(tmp_path, "bl-live", now=NOW)

    assert result["status"] == "unknown"
    assert result["execution"]["heartbeat_status"] == "fresh"
    assert result["cost"] == {
        "model_calls": 0,
        "tokens": 0,
        "method": "deterministic local JSON and bounded Git inspection",
    }


def test_reports_stale_run_from_heartbeat(tmp_path: Path):
    _write_state(tmp_path, {"execution": {
        "schema_version": 1,
        "run_id": "bl-stale",
        "phase": "review",
        "last_heartbeat_at": "2026-09-12T01:00:00Z",
    }})

    result = run_status.inspect_run(tmp_path, "bl-stale", now=NOW)

    assert result["status"] == "stale"
    assert result["execution"]["heartbeat_age_seconds"] == 3600


def test_reports_canonical_pass_run_as_completed(tmp_path: Path):
    _write_state(tmp_path, {"runs": [{"run_id": "bl-done", "outcome": "pass"}]})

    result = run_status.inspect_run(tmp_path, "bl-done", now=NOW)

    assert result["status"] == "completed"


def test_reports_terminal_branch_closeout_as_completed(tmp_path: Path):
    _write_state(tmp_path, {"historicalExecutions": [{
        "build_loop_id": "bl-done",
        "archive_disposition": "terminal_branch_closeout",
    }]})

    result = run_status.inspect_run(tmp_path, "bl-done", now=NOW)

    assert result["status"] == "completed"


def test_reports_pending_external_merge_as_awaiting_closeout(tmp_path: Path):
    _write_state(tmp_path, {"runs": [{
        "run_id": "bl-pending",
        "outcome": "pass",
        "branch_closeout": {"status": "pending_external_merge"},
    }]})

    result = run_status.inspect_run(tmp_path, "bl-pending", now=NOW)

    assert result["status"] == "awaiting_closeout"


def test_nonterminal_branch_closeout_suppresses_pass_outcome(tmp_path: Path):
    expected = {
        "prepared": "awaiting_closeout",
        "deferred": "awaiting_closeout",
        "error": "unknown",
    }
    for branch_status, lifecycle_status in expected.items():
        _write_state(tmp_path, {"runs": [{
            "run_id": "bl-open",
            "outcome": "pass",
            "branch_closeout": {"status": branch_status},
        }]})
        result = run_status.inspect_run(tmp_path, "bl-open", now=NOW)
        assert result["status"] == lifecycle_status


def test_non_object_state_is_unknown(tmp_path: Path):
    target = tmp_path / ".build-loop" / "state.json"
    target.parent.mkdir(parents=True)
    target.write_text("[]", encoding="utf-8")

    result = run_status.inspect_run(tmp_path, "bl-any", now=NOW)

    assert result["status"] == "unknown"
    assert result["reasons"] == ["state.json root must be a JSON object"]


def test_explicit_abandonment_wins_over_partial_run_row(tmp_path: Path):
    _write_state(tmp_path, {
        "execution": {"run_id": "other"},
        "historicalExecutions": [{
            "build_loop_id": "bl-old",
            "archive_disposition": "explicitly_abandoned",
            "abandoned_at": "2026-09-12T01:36:18Z",
            "crashed_at": "2026-08-21T01:14:06Z",
        }],
        "runs": [{"run_id": "bl-old", "outcome": "partial"}],
    })

    result = run_status.inspect_run(tmp_path, "bl-old", now=NOW)

    assert result["status"] == "abandoned"
    assert result["run_ledger"]["outcomes"] == ["partial"]


def test_reports_preserved_unmerged_branch(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    tracked = tmp_path / "base.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    default_branch = subprocess.run(
        ["git", "-C", str(tmp_path), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(tmp_path), "branch", "bl/run-old"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "bl/run-old"], check=True)
    tracked.write_text("branch\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qam", "branch"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", default_branch], check=True)
    _write_state(tmp_path, {"historicalExecutions": [{
        "build_loop_id": "bl-old",
        "archive_disposition": "explicitly_abandoned",
        "run_worktree_branch": "bl/run-old",
    }]})

    result = run_status.inspect_run(tmp_path, "bl-old", now=NOW)

    assert result["worktree"]["branch_exists"] is True
    assert result["worktree"]["integrated_into_current_head"] is False
    assert result["worktree"]["commits_ahead_of_current_head"] == 1


def test_plain_directory_under_managed_root_is_not_a_worktree(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    fake = tmp_path / ".build-loop" / "worktrees" / "fake"
    fake.mkdir(parents=True)
    _write_state(tmp_path, {"historicalExecutions": [{
        "build_loop_id": "bl-fake",
        "archive_disposition": "explicitly_abandoned",
        "run_worktree_path": str(fake),
        "run_worktree_branch": "main",
    }]})

    result = run_status.inspect_run(tmp_path, "bl-fake", now=NOW)

    assert result["worktree"]["path_is_managed"] is False
    assert result["worktree"]["clean"] is None
    assert result["worktree"]["head"] is None


def test_registered_worktree_must_match_recorded_branch(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    tracked = tmp_path / "base.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "branch", "expected"], check=True)
    managed = tmp_path / ".build-loop" / "worktrees" / "registered"
    managed.parent.mkdir(parents=True)
    subprocess.run(["git", "-C", str(tmp_path), "worktree", "add", "-q", str(managed), "expected"], check=True)
    _write_state(tmp_path, {"historicalExecutions": [{
        "build_loop_id": "bl-mismatch",
        "archive_disposition": "explicitly_abandoned",
        "run_worktree_path": str(managed),
        "run_worktree_branch": "main",
    }]})

    result = run_status.inspect_run(tmp_path, "bl-mismatch", now=NOW)

    assert result["worktree"]["path_is_managed"] is True
    assert result["worktree"]["registered_branch"] == "expected"
    assert result["worktree"]["branch_matches_worktree"] is False
    assert result["worktree"]["clean"] is None


def test_telemetry_is_append_only_jsonl(tmp_path: Path):
    envelope = run_status.inspect_run(tmp_path, "missing", now=NOW)

    path = run_status._append_telemetry(tmp_path, envelope)
    run_status._append_telemetry(tmp_path, envelope)

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["schema"] == run_status.TELEMETRY_SCHEMA
    assert rows[0]["attributes"]["cost"]["tokens"] == 0


def test_telemetry_refuses_symlinked_directory(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    build_loop = tmp_path / ".build-loop"
    build_loop.mkdir()
    (build_loop / "telemetry").symlink_to(outside, target_is_directory=True)
    envelope = run_status.inspect_run(tmp_path, "missing", now=NOW)

    try:
        run_status._append_telemetry(tmp_path, envelope)
    except OSError:
        pass
    else:
        raise AssertionError("symlinked telemetry directory must be refused")
    assert not (outside / "run-status.jsonl").exists()
