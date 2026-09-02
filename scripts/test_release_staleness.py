# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the release-cadence detector.

Every case is built against a synthetic git repo, never against this checkout.
Asserting on the live repo would make the test a mirror of today's release state:
it would be red until someone cut a release and green forever after, which is the
opposite of what a regression test is for.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "release_staleness", Path(__file__).parent / "release_staleness.py"
)
rs = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader
_SPEC.loader.exec_module(rs)


def _git(repo: Path, *args: str, when: datetime | None = None) -> None:
    env = None
    if when is not None:
        stamp = when.isoformat()
        env = {
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(repo),
        }
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=env,
    )


def _tag(repo: Path, name: str) -> None:
    """Annotated + explicitly unsigned.

    A bare `git tag <name>` inherits the ambient user config: `tag.forceSignAnnotated`
    turns it annotated and it dies with "no tag message?", and `tag.gpgSign` makes it
    reach for a key the runner does not have. Real release tags are annotated anyway.
    """
    _git(repo, "-c", "tag.gpgSign=false", "tag", "-a", name, "-m", name)


def _commit(repo: Path, msg: str, when: datetime) -> None:
    (repo / "f.txt").write_text(msg)
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", msg, when=when)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    _git(r, "config", "commit.gpgsign", "false")
    _git(r, "config", "tag.gpgSign", "false")
    return r


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_quiet_week_is_not_stale(repo: Path) -> None:
    """The whole point of the AND: an old tag with nothing after it is CORRECT.

    Firing here would mean an alert every quiet week, and an alert that cries wolf
    is the reason a real one gets ignored.
    """
    _commit(repo, "feat: initial", NOW - timedelta(days=200))
    _tag(repo, "v1.0.0")
    verdict = rs.evaluate(str(repo), "main", now=NOW)
    assert verdict["stale"] is False
    assert verdict["commits_since_tag"] == 0


def test_unreleased_work_past_the_window_is_stale(repo: Path) -> None:
    """The exact shape of the two-month outage: work landed, no release followed."""
    _commit(repo, "feat: initial", NOW - timedelta(days=60))
    _tag(repo, "v1.0.0")
    _commit(repo, "fix: something users hit", NOW - timedelta(days=30))
    verdict = rs.evaluate(str(repo), "main", now=NOW)
    assert verdict["stale"] is True
    assert verdict["commits_since_tag"] == 1
    assert verdict["age_days"] > 10


def test_recent_work_inside_the_window_is_not_stale(repo: Path) -> None:
    """A release cut two days ago with a commit after it is a healthy cadence."""
    _commit(repo, "feat: initial", NOW - timedelta(days=3))
    _tag(repo, "v1.0.0")
    _commit(repo, "fix: minor", NOW - timedelta(days=2))
    verdict = rs.evaluate(str(repo), "main", now=NOW)
    assert verdict["stale"] is False


def test_commit_burst_is_stale_even_inside_the_age_window(repo: Path) -> None:
    """A fast cadence failure. 60 commits in three days is still 60 unreleased commits."""
    _commit(repo, "feat: initial", NOW - timedelta(days=3))
    _tag(repo, "v1.0.0")
    for i in range(60):
        _commit(repo, f"fix: change {i}", NOW - timedelta(days=2))
    verdict = rs.evaluate(str(repo), "main", max_commits=50, now=NOW)
    assert verdict["stale"] is True
    assert verdict["commits_since_tag"] == 60


def test_housekeeping_tags_are_not_release_tags(repo: Path) -> None:
    """Regression for the real reason this went unseen.

    This repo's newest tags by date are `archive/2026-08-25/...` and
    `rescue/peer-b0ad360`. Any check that treated the newest tag as the newest
    release would have reported FRESH through the entire outage.
    """
    _commit(repo, "feat: initial", NOW - timedelta(days=60))
    _tag(repo, "v1.0.0")
    _commit(repo, "fix: later work", NOW - timedelta(days=30))
    _tag(repo, "archive/2026-08-25/some-branch")
    _tag(repo, "rescue/peer-abc1234")
    # The one that actually exercises the anchor. The two above contain no
    # dotted triple, so an UNANCHORED regex still ignores them and the test
    # would pass against a broken matcher — a guard that only asserts the
    # working path. This tag looks exactly like a release to a loose regex.
    _tag(repo, "archive/2026-08-25/release-9.9.9")
    # A pre-release tag is not a release either. This is the case the trailing
    # `$` uniquely covers: `v9.9.9-rc1` starts with a valid triple, so only the
    # end-anchor keeps it out. Without it, cutting an rc would silence the
    # cadence alarm for a version that never shipped.
    _tag(repo, "v9.9.9-rc1")
    verdict = rs.evaluate(str(repo), "main", now=NOW)
    assert verdict["tag"] == "v1.0.0"
    assert verdict["stale"] is True


def test_newest_tag_is_chosen_by_version_not_by_date(repo: Path) -> None:
    """v0.9.0 created after v0.10.0 must not outrank it."""
    _commit(repo, "feat: initial", NOW - timedelta(days=60))
    _tag(repo, "v0.10.0")
    _commit(repo, "fix: more", NOW - timedelta(days=59))
    _tag(repo, "v0.9.0")
    assert rs.newest_release_tag(str(repo)) == "v0.10.0"


def test_no_release_tag_at_all_is_stale_once_work_exists(repo: Path) -> None:
    _commit(repo, "feat: initial", NOW - timedelta(days=1))
    verdict = rs.evaluate(str(repo), "main", now=NOW)
    assert verdict["stale"] is True
    assert verdict["tag"] is None


def test_cli_exit_codes(repo: Path) -> None:
    """Exit 1 must mean stale — the workflow branches on it."""
    _commit(repo, "feat: initial", NOW - timedelta(days=200))
    _tag(repo, "v1.0.0")
    assert rs.main(["--workdir", str(repo), "--branch", "main"]) == 0
    _commit(repo, "fix: later", NOW - timedelta(days=100))
    assert rs.main(["--workdir", str(repo), "--branch", "main"]) == 1


def test_a_non_repo_reports_error_not_fresh(tmp_path: Path) -> None:
    """Fail loud, never fail-green: a broken check must not read as 'no problem'."""
    assert rs.main(["--workdir", str(tmp_path / "nope"), "--json"]) == 2
