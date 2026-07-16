# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for outcome_reconcile.py — a shipped run is never stamped fail (Item 3B)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import outcome_reconcile as orc  # noqa: E402


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("v1")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "c1")
    return r


def _head(repo) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_non_fail_outcomes_pass_through(repo):
    for o in ("pass", "partial"):
        r = orc.reconcile(repo, o, {"commit": _head(repo)})
        assert r["outcome"] == o and r["changed"] is False


def test_shipped_and_audited_fail_becomes_pass(repo, monkeypatch):
    # RIG: outcome=fail but the commit is on main AND auditor passed -> pass.
    monkeypatch.setattr(orc, "rally_success", lambda w, rid: (False, "n/a"))
    rec = {
        "commit": _head(repo),
        "run_id": "R1",
        "judge_decisions": [{"judge_id": "independent-auditor", "verdict": "pass"}],
    }
    out = orc.reconcile(repo, "fail", rec, run_id="R1")
    assert out["outcome"] == "pass" and out["changed"] is True
    assert out["evidence"]["merged"]["value"] is True


def test_shipped_only_fail_becomes_partial(repo, monkeypatch):
    monkeypatch.setattr(orc, "rally_success", lambda w, rid: (False, "n/a"))
    rec = {"commit": _head(repo), "run_id": "R2", "judge_decisions": []}
    out = orc.reconcile(repo, "fail", rec, run_id="R2")
    assert out["outcome"] == "partial" and out["changed"] is True


def test_real_failure_stays_fail(repo, monkeypatch):
    # RIG PASSING BASELINE: a commit NOT on any branch + no auditor + no rally -> fail.
    monkeypatch.setattr(orc, "rally_success", lambda w, rid: (False, "n/a"))
    # create a dangling commit not reachable from main
    (repo / "g.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "stash")  # keeps main unchanged; fabricate an unrelated sha
    fake = "0" * 40
    out = orc.reconcile(repo, "fail", {"commit": fake, "run_id": "R3"}, run_id="R3")
    assert out["outcome"] == "fail" and out["changed"] is False


def test_auditor_passed_detects_verdict_aliases():
    rec = {"judge_decisions": [{"judge": "independent-auditor", "decision": "commit_as_planned"}]}
    ok, _ = orc.auditor_passed(rec)
    assert ok is True


def test_never_raises_on_bad_record(repo):
    out = orc.reconcile(repo, "fail", {"commit": None}, run_id=None)
    assert out["outcome"] == "fail"  # degrades to proposed, no exception
