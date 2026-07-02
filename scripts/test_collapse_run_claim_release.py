#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/collapse_run.py's claim-release-on-teardown behavior.

Root cause under test: ``_remove_worktree`` deleted the worktree folder via
``git worktree remove`` but made zero rally calls, so a dispatch worktree's
file-scope claims (``file:.claude/worktrees/agent-*``) orphaned the instant
their backing folder vanished (84 dead-worktree claims observed live for an
already-empty ``.claude/worktrees/``).

No real ``rally`` binary and no real ``git worktree`` are ever invoked here:
both ``subprocess.run`` and ``shutil.which`` are monkeypatched at the
``collapse_run`` module level, matching the fake-rally pattern already used
in ``scripts/test_stop_closeout.py``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Make scripts/ importable
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import collapse_run  # noqa: E402


_FAKE_RALLY_BINARY = "/fake/bin/rally"


class _FakeRally:
    """In-memory rally stand-in wired in as ``subprocess.run``.

    Models the verified live behavior: ``rally room --tool claude_code --json``
    lists live claims (each with a ``scope`` list of ``file:<path>`` entries),
    and ``rally say release --tool claude_code --ref <event_id>`` releases one
    claim by event id. Also handles the ``git worktree remove`` call so no
    real git process ever runs.
    """

    def __init__(self, claims: list[tuple[str, str]], *, room_raises: bool = False) -> None:
        # claims: list of (event_id, scope_path)
        self.claims = list(claims)
        self.calls: list[list[str]] = []
        self.room_raises = room_raises

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))

        # git worktree remove — always succeeds, never touches real git.
        if argv and argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        # rally room --tool claude_code --json
        if len(argv) >= 2 and argv[1] == "room":
            if self.room_raises:
                raise RuntimeError("simulated rally room failure")
            facts = [
                {"kind": "claim", "scope": [f"file:{p}"], "event_id": e}
                for (e, p) in self.claims
            ]
            envelope = {"data": {"room": {"active_claims": facts}}}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(envelope), stderr="")

        # rally say release --tool claude_code --ref <id> ...
        if len(argv) >= 3 and argv[1] == "say" and argv[2] == "release":
            ref = argv[argv.index("--ref") + 1]
            before = len(self.claims)
            self.claims = [c for c in self.claims if c[0] != ref]
            ok = len(self.claims) < before
            return subprocess.CompletedProcess(argv, 0 if ok else 3, stdout="{}", stderr="")

        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="unexpected")


def _make_worktree_dir(workdir: Path) -> tuple[Path, str]:
    """Create a plain directory standing in for a dispatch worktree folder.

    Returns (absolute path, repo-relative posix path) — the latter matches
    the rally claim scope format (``file:<relpath>``).
    """
    wt_dir = workdir / ".claude" / "worktrees" / "agent-abc123"
    wt_dir.mkdir(parents=True)
    relpath = wt_dir.resolve().relative_to(workdir.resolve()).as_posix()
    return wt_dir, relpath


def test_successful_removal_releases_only_matching_claim(tmp_path, monkeypatch):
    wt_dir, relpath = _make_worktree_dir(tmp_path)
    fake = _FakeRally(
        claims=[
            ("fact_match_1", f"{relpath}/scripts/foo.py"),   # under the removed worktree
            ("fact_other_1", "scripts/unrelated.py"),         # unrelated — must survive
        ]
    )
    monkeypatch.setattr(collapse_run.shutil, "which", lambda name: _FAKE_RALLY_BINARY)
    monkeypatch.setattr(collapse_run.subprocess, "run", fake)

    err = collapse_run._remove_worktree(tmp_path, str(wt_dir))

    assert err is None  # teardown reported success
    release_calls = [c for c in fake.calls if len(c) >= 3 and c[1] == "say" and c[2] == "release"]
    assert len(release_calls) == 1
    released_ref = release_calls[0][release_calls[0].index("--ref") + 1]
    assert released_ref == "fact_match_1"
    remaining_ids = {e for (e, _p) in fake.claims}
    assert "fact_match_1" not in remaining_ids   # matching claim released
    assert "fact_other_1" in remaining_ids       # unrelated claim untouched
    # Never touch a real rally reaper surface — surgical release only.
    assert not any("--reap-stale" in c for c in fake.calls)
    assert not any(c[:1] == ["reap"] or (len(c) > 1 and c[1] == "reap") for c in fake.calls)


def test_sibling_worktree_sharing_a_name_prefix_is_not_released(tmp_path, monkeypatch):
    """A sibling worktree whose path shares this one's prefix must survive.

    Removing ``.../agent-abc123`` must NOT release a claim scoped under
    ``.../agent-abc123-extra`` — a bare substring match would (the prefix
    collision); the path-boundary match must not.
    """
    wt_dir, relpath = _make_worktree_dir(tmp_path)  # .claude/worktrees/agent-abc123
    sibling_scope = f"{relpath}-extra/scripts/foo.py"  # shares the prefix, different worktree
    fake = _FakeRally(
        claims=[
            ("fact_match_1", f"{relpath}/scripts/foo.py"),  # genuinely under removed wt
            ("fact_sibling", sibling_scope),                 # prefix-collision — must survive
        ]
    )
    monkeypatch.setattr(collapse_run.shutil, "which", lambda name: _FAKE_RALLY_BINARY)
    monkeypatch.setattr(collapse_run.subprocess, "run", fake)

    err = collapse_run._remove_worktree(tmp_path, str(wt_dir))

    assert err is None
    release_calls = [c for c in fake.calls if len(c) >= 3 and c[1] == "say" and c[2] == "release"]
    assert len(release_calls) == 1
    released_ref = release_calls[0][release_calls[0].index("--ref") + 1]
    assert released_ref == "fact_match_1"
    remaining_ids = {e for (e, _p) in fake.claims}
    assert "fact_sibling" in remaining_ids  # prefix-collision sibling untouched


def test_rally_failure_does_not_break_teardown(tmp_path, monkeypatch):
    wt_dir, _relpath = _make_worktree_dir(tmp_path)
    fake = _FakeRally(claims=[("fact_x", "irrelevant.py")], room_raises=True)
    monkeypatch.setattr(collapse_run.shutil, "which", lambda name: _FAKE_RALLY_BINARY)
    monkeypatch.setattr(collapse_run.subprocess, "run", fake)

    err = collapse_run._remove_worktree(tmp_path, str(wt_dir))

    assert err is None  # worktree removal still reported as successful


def test_no_rally_call_when_rally_unavailable(tmp_path, monkeypatch):
    wt_dir, _relpath = _make_worktree_dir(tmp_path)
    calls: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv and argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call while rally is unavailable: {argv}")

    monkeypatch.setattr(collapse_run.shutil, "which", lambda name: None)  # rally not installed
    monkeypatch.setattr(collapse_run.subprocess, "run", _fake_run)

    err = collapse_run._remove_worktree(tmp_path, str(wt_dir))

    assert err is None
    assert all(c[0] == "git" for c in calls)  # only the git worktree remove call happened


def test_idempotent_already_gone_worktree_still_no_ops_cleanly(tmp_path, monkeypatch):
    """A worktree folder already removed (idempotent path) must not error."""
    wt_dir = tmp_path / ".claude" / "worktrees" / "agent-already-gone"
    # deliberately never created — _remove_worktree should treat this as success
    calls: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(collapse_run.shutil, "which", lambda name: _FAKE_RALLY_BINARY)
    monkeypatch.setattr(collapse_run.subprocess, "run", _fake_run)

    err = collapse_run._remove_worktree(tmp_path, str(wt_dir))

    assert err is None
    assert not any(c[0] == "git" for c in calls)  # no git call — already gone, idempotent
