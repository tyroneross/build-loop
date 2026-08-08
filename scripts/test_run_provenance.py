# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for run_provenance.py — commit/goal corroboration before a runs[] write.

The anchor case is the real defect (agent-rally-point enforce-candidate E3,
2026-07-09): a run record whose commit `6616b71` was reachable from neither the
run's push range nor its branch, alongside a goal matching no intent on disk.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_provenance  # noqa: E402

SCRIPT = Path(__file__).parent / "run_provenance.py"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    """A three-commit repo. Returns (path, [sha1, sha2, sha3])."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    shas = []
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text(str(i))
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(tmp_path, "rev-parse", "HEAD"))
    return tmp_path, shas


def _validate(repo_root, **kw):
    kw.setdefault("run_id", "r1")
    kw.setdefault("goal", "")
    kw.setdefault("commit", None)
    return run_provenance.validate_run_provenance(repo_root=str(repo_root), **kw)


def _codes(result, severity=None):
    return [
        f["code"]
        for f in result["findings"]
        if severity is None or f["severity"] == severity
    ]


# --------------------------------------------------------------------------
# Commit reachability — the block arm
# --------------------------------------------------------------------------

def test_unreachable_commit_blocks(repo):
    """The defect itself: a SHA belonging to no commit in this history."""
    path, _ = repo
    result = _validate(path, commit="6616b71")
    assert result["ok"] is False
    assert _codes(result, "block") == ["commit_unreachable"]


def test_reachable_head_commit_passes(repo):
    """Acquittal. Without this the gate could block everything and look green."""
    path, shas = repo
    result = _validate(path, commit=shas[-1])
    assert result["ok"] is True and result["findings"] == []


def test_reachable_ancestor_commit_passes(repo):
    path, shas = repo
    assert _validate(path, commit=shas[0])["ok"] is True


def test_short_sha_is_accepted(repo):
    """append_run resolves HEAD with `rev-parse --short`, so short SHAs are the
    normal input — a prefix must not read as unreachable."""
    path, shas = repo
    assert _validate(path, commit=shas[-1][:7])["ok"] is True


@pytest.mark.parametrize("commit", [None, "", "pending", "PENDING"])
def test_pending_commit_is_allowed(repo, commit):
    """A mid-run append before the push has nothing to corroborate. `pending` is
    the honest value the block arm itself falls back to — it must never block."""
    path, _ = repo
    assert _validate(path, commit=commit)["ok"] is True


def test_commit_outside_push_range_blocks(repo):
    """Reachable from HEAD but not from the range the run claims it pushed —
    the exact shape of the 2026-07-09 record."""
    path, shas = repo
    result = _validate(path, commit=shas[0], push_range=f"{shas[1]}..{shas[2]}")
    assert result["ok"] is False and _codes(result, "block") == ["commit_unreachable"]


def test_commit_inside_push_range_passes(repo):
    path, shas = repo
    assert _validate(path, commit=shas[2], push_range=f"{shas[1]}..{shas[2]}")["ok"]


def test_unusable_push_range_falls_back_to_head(repo):
    """A bad range is the caller's bookkeeping error, not evidence the commit is
    wrong. Falling through to HEAD keeps the check honest instead of punitive."""
    path, shas = repo
    assert _validate(path, commit=shas[-1], push_range="no-such-ref..also-missing")["ok"]


def test_non_git_directory_blocks_a_supplied_commit(tmp_path):
    """Fail closed: with no history to corroborate against, a SHA is unverified,
    and an unverified SHA is what this exists to stop."""
    result = _validate(tmp_path, commit="deadbeef")
    assert result["ok"] is False
    assert result["derived_commit"] is None


def test_derived_commit_reports_head(repo):
    path, shas = repo
    assert _validate(path, commit=shas[-1])["derived_commit"] == shas[-1]


# --------------------------------------------------------------------------
# Goal corroboration — the warn arm
# --------------------------------------------------------------------------

def _intent(path, body):
    (path / "intent.md").write_text(body)
    return str(path / "intent.md")


def test_goal_mismatch_warns_but_never_blocks(repo):
    path, _ = repo
    intent = _intent(path, "# Intent — Fix the retrospective pipeline\n")
    result = _validate(path, goal="Bump the dependency cooldown allowlist",
                       intent_path=intent)
    assert result["ok"] is True, "a goal mismatch must never block a run record"
    assert _codes(result, "warn") == ["goal_mismatch"]


def test_matching_goal_is_silent(repo):
    path, _ = repo
    intent = _intent(path, "# Intent — Fix the retrospective pipeline\n")
    result = _validate(path, goal="Fix the retrospective pipeline", intent_path=intent)
    assert result["findings"] == []


def test_intent_label_prefix_is_stripped(repo):
    """`# Intent — <goal>` is build-loop's own template. Comparing the label as
    part of the headline drags every ratio down and warns on correct goals."""
    path, _ = repo
    intent = _intent(path, "# Intent — ship X\n")
    assert run_provenance._extract_headline(intent) == "ship X"


def test_stale_intent_for_another_run_is_not_compared(repo):
    """build-loop leaves the PREVIOUS run's intent.md on disk. Warning against it
    would fire on nearly every run, and a gate that always fires gets ignored."""
    path, _ = repo
    intent = _intent(
        path,
        "<!-- intent_run_id: bl-OLD-RUN -->\n# Intent — some other work entirely\n",
    )
    result = _validate(path, run_id="bl-THIS-RUN", goal="totally unrelated goal",
                       intent_path=intent)
    assert result["findings"] == []


def test_intent_for_this_run_is_compared(repo):
    """The acquittal for the rule above: a matching run_id still gets checked."""
    path, _ = repo
    intent = _intent(
        path,
        "<!-- intent_run_id: bl-THIS-RUN -->\n# Intent — some other work entirely\n",
    )
    result = _validate(path, run_id="bl-THIS-RUN", goal="totally unrelated goal",
                       intent_path=intent)
    assert _codes(result, "warn") == ["goal_mismatch"]


def test_missing_intent_file_is_not_a_finding(repo):
    path, _ = repo
    result = _validate(path, goal="anything", intent_path=str(path / "nope.md"))
    assert result["findings"] == []


def test_empty_goal_is_not_compared(repo):
    path, _ = repo
    intent = _intent(path, "# Intent — Fix the retrospective pipeline\n")
    assert _validate(path, goal="", intent_path=intent)["findings"] == []


def test_resolve_intent_path(tmp_path):
    assert run_provenance.resolve_intent_path(tmp_path) is None
    (tmp_path / ".build-loop").mkdir()
    (tmp_path / ".build-loop" / "intent.md").write_text("# Intent — x\n")
    assert run_provenance.resolve_intent_path(tmp_path).endswith(
        ".build-loop/intent.md")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def test_cli_exit_1_on_block(repo):
    path, _ = repo
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--run-id", "r1", "--commit", "6616b71",
         "--repo-root", str(path), "--json"],
        capture_output=True, text=True,
    )
    assert res.returncode == 1
    assert "commit_unreachable" in res.stderr
    assert '"ok": false' in res.stdout


def test_cli_exit_0_when_clean(repo):
    path, shas = repo
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--run-id", "r1", "--commit", shas[-1],
         "--repo-root", str(path)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0
