# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the branch-head drift check (approved proposal P3).

Evidence: integration branch `bl/et-next` merged history at `301b179d` while
the history branch kept moving to `c955ee52` then `29eeacf5` — the
integration gate built a stale merge. These tests exercise `record`/`check`
against real tempdir git repos: no drift, drift, missing branch, no ledger,
and the CLI JSON shape.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import branch_head_drift as bhd  # noqa: E402


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
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    assert _git(repo, "add", "README.md").returncode == 0
    assert _git(repo, "commit", "-m", "init").returncode == 0
    return repo


def _commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content, encoding="utf-8")
    assert _git(repo, "add", name).returncode == 0
    assert _git(repo, "commit", "-m", f"add {name}").returncode == 0
    result = _git(repo, "rev-parse", "HEAD")
    return result.stdout.strip()


def _head(repo: Path, ref: str) -> str:
    result = _git(repo, "rev-parse", "--verify", ref)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class TestRecordAndCheck:
    def test_record_then_check_unchanged_reports_ok(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert _git(repo, "checkout", "-b", "history").returncode == 0
        _commit(repo, "a.txt", "a\n")
        ledger = repo / ".build-loop" / "integration-heads.json"

        record_result = bhd.record(repo, ["history"], [None], ledger, None)
        assert record_result["errors"] == []
        assert record_result["recorded"] == [
            {"branch": "history", "sha": _head(repo, "refs/heads/history")}
        ]
        assert ledger.is_file()

        check_result = bhd.check(repo, ledger, None)
        assert check_result["errors"] == []
        assert check_result["branches"] == [
            {
                "branch": "history",
                "recorded": _head(repo, "refs/heads/history"),
                "current": _head(repo, "refs/heads/history"),
                "status": "ok",
            }
        ]

    def test_short_sha_is_normalised_so_unchanged_branch_is_ok(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert _git(repo, "checkout", "-b", "history").returncode == 0
        _commit(repo, "a.txt", "a\n")
        full = _head(repo, "refs/heads/history")
        ledger = repo / ".build-loop" / "integration-heads.json"
        bhd.record(repo, ["history"], [full[:8]], ledger, None)
        assert bhd.check(repo, ledger, None)["errors"] == []

    def test_branch_advance_after_record_reports_drift(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert _git(repo, "checkout", "-b", "history").returncode == 0
        old_sha = _commit(repo, "a.txt", "a\n")
        ledger = repo / ".build-loop" / "integration-heads.json"
        bhd.record(repo, ["history"], [None], ledger, None)

        new_sha = _commit(repo, "b.txt", "b\n")
        assert new_sha != old_sha

        result = bhd.check(repo, ledger, None)
        assert len(result["branches"]) == 1
        row = result["branches"][0]
        assert row["status"] == "drift"
        assert row["recorded"] == old_sha
        assert row["current"] == new_sha
        assert result["errors"] == [f"re-merge: history recorded {old_sha} now {new_sha}"]

    def test_deleted_branch_reports_missing(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert _git(repo, "checkout", "-b", "history").returncode == 0
        _commit(repo, "a.txt", "a\n")
        ledger = repo / ".build-loop" / "integration-heads.json"
        bhd.record(repo, ["history"], [None], ledger, None)

        assert _git(repo, "checkout", "main").returncode == 0
        assert _git(repo, "branch", "-D", "history").returncode == 0

        result = bhd.check(repo, ledger, None)
        row = result["branches"][0]
        assert row["status"] == "missing"
        assert row["current"] is None
        assert result["errors"] == ["history: missing"]

    def test_no_ledger_returns_error(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        ledger = repo / ".build-loop" / "integration-heads.json"
        result = bhd.check(repo, ledger, None)
        assert result["branches"] == []
        assert result["errors"] == [f"no ledger at {ledger}"]

    def test_record_merges_by_branch_name(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert _git(repo, "checkout", "-b", "history").returncode == 0
        _commit(repo, "a.txt", "a\n")
        assert _git(repo, "checkout", "-b", "other").returncode == 0
        _commit(repo, "o.txt", "o\n")
        ledger = repo / ".build-loop" / "integration-heads.json"

        bhd.record(repo, ["history"], [None], ledger, None)
        bhd.record(repo, ["other"], [None], ledger, None)

        data = json.loads(ledger.read_text(encoding="utf-8"))
        assert set(data["branches"]) == {"history", "other"}

    def test_from_merge_infers_second_parent(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert _git(repo, "checkout", "-b", "history").returncode == 0
        history_sha = _commit(repo, "a.txt", "a\n")

        assert _git(repo, "checkout", "main").returncode == 0
        assert _git(repo, "checkout", "-b", "integration").returncode == 0
        assert (
            _git(repo, "merge", "--no-ff", "-m", "merge history", "history").returncode
            == 0
        )
        merge_sha = _head(repo, "refs/heads/integration")

        ledger = repo / ".build-loop" / "integration-heads.json"
        result = bhd.record(repo, ["history"], [None], ledger, merge_sha)
        assert result["errors"] == []
        assert result["recorded"] == [{"branch": "history", "sha": history_sha}]


class TestCli:
    def test_check_cli_json_shape_and_exit_codes(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        assert _git(repo, "checkout", "-b", "history").returncode == 0
        old_sha = _commit(repo, "a.txt", "a\n")
        ledger_rel = ".build-loop/integration-heads.json"

        record_exit = bhd.main(
            [
                "record",
                "--workdir",
                str(repo),
                "--branch",
                "history",
                "--file",
                ledger_rel,
                "--json",
            ]
        )
        assert record_exit == 0

        ok_exit = bhd.main(
            ["check", "--workdir", str(repo), "--file", ledger_rel, "--json"]
        )
        assert ok_exit == 0

        new_sha = _commit(repo, "b.txt", "b\n")
        assert new_sha != old_sha

        drift_exit = bhd.main(
            ["check", "--workdir", str(repo), "--file", ledger_rel, "--json"]
        )
        assert drift_exit == 1

        result = bhd.check(repo, repo / ledger_rel, None)
        assert set(result.keys()) == {"branches", "errors"}
        assert set(result["branches"][0].keys()) == {
            "branch",
            "recorded",
            "current",
            "status",
        }

    def test_check_cli_no_ledger_exits_2(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        exit_code = bhd.main(
            [
                "check",
                "--workdir",
                str(repo),
                "--file",
                ".build-loop/integration-heads.json",
                "--json",
            ]
        )
        assert exit_code == 2

    def test_record_cli_missing_branch_flag_exits_2(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        exit_code = bhd.main(["record", "--workdir", str(repo)])
        assert exit_code == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
