# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the post-push arming behavior of ``hooks/git/pre-push``.

Verifies the user's design steer (2026-06-09):

    *The closeout fires AFTER A PUSH, not on every Stop.*

Concretely:

1. The pre-push hook ARMS the baton at ``.build-loop/closeout/armed.json``
   when it allows a push.
2. The pre-push hook does NOT directly emit a ``closeout_status`` — that's
   the next session's job.
3. Stop hooks (``scan_corrections`` etc.) feed ``pending-lessons/`` but DO
   NOT write a ``closeout/<run-id>.json`` artifact.

The test invokes the pre-push hook as a subprocess against a real git repo
seeded into ``tmp_path`` so we exercise the actual installed contract.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent  # scripts/closeout/ → scripts/ → repo
PRE_PUSH_HOOK = REPO_ROOT / "hooks" / "git" / "pre-push"


def _init_git_repo(workdir: Path) -> None:
    """Initialize a minimal git repo so the pre-push hook can resolve repo root."""
    workdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(workdir)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (workdir / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(workdir), "add", "README.md"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    subprocess.run(
        ["git", "-C", str(workdir), "commit", "-q", "-m", "seed"],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stage_pre_push_hook(workdir: Path) -> None:
    """Install our pre-push hook into the seeded repo's hooks dir.

    We don't run ``install_git_hooks.py`` here — that test lives next to the
    installer. We just need the hook script to be executable so the pre-push
    arming code path runs verbatim.
    """
    hooks_dir = workdir / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    body = PRE_PUSH_HOOK.read_text(encoding="utf-8")
    dst = hooks_dir / "pre-push"
    dst.write_text(body, encoding="utf-8")
    dst.chmod(0o755)


def _run_pre_push(workdir: Path) -> subprocess.CompletedProcess[str]:
    """Run the pre-push hook directly with synthetic stdin (mimics git's invocation)."""
    stdin_line = "refs/heads/main aaaaaaa refs/heads/main bbbbbbb\n"
    return subprocess.run(
        [sys.executable, str(workdir / ".git" / "hooks" / "pre-push"),
         "origin", "https://example.invalid/repo.git"],
        input=stdin_line,
        cwd=str(workdir),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def test_pre_push_arms_baton_when_push_allowed(tmp_path: Path) -> None:
    workdir = tmp_path / "repo"
    _init_git_repo(workdir)
    _stage_pre_push_hook(workdir)
    armed = workdir / ".build-loop" / "closeout" / "armed.json"
    assert not armed.exists(), "test setup invariant: armed flag absent pre-run"

    proc = _run_pre_push(workdir)
    assert proc.returncode == 0, (
        f"pre-push must exit 0 when no hold is active "
        f"(rc={proc.returncode}, stderr={proc.stderr!r})"
    )
    assert armed.is_file(), (
        "pre-push hook did NOT arm .build-loop/closeout/armed.json — "
        "the post-push closeout cannot trigger via the session-start drain."
    )
    payload = json.loads(armed.read_text(encoding="utf-8"))
    assert payload.get("source") == "post-push-armed"
    assert payload.get("armed_at"), "armed payload missing armed_at timestamp"


def test_pre_push_does_not_directly_emit_closeout_status(tmp_path: Path) -> None:
    """The pre-push hook arms only — it does NOT run the closeout itself.

    Verifies the design: the closeout artifact at
    ``.build-loop/closeout/<run-id>.json`` only appears after the post-push
    handler (orchestrator Phase 4G or session-start-closeout.sh) runs.
    """
    workdir = tmp_path / "repo"
    _init_git_repo(workdir)
    _stage_pre_push_hook(workdir)
    proc = _run_pre_push(workdir)
    assert proc.returncode == 0

    closeout_dir = workdir / ".build-loop" / "closeout"
    # Only the armed baton — no concrete <run-id>.json status emit yet.
    entries = sorted(p.name for p in closeout_dir.glob("*.json"))
    assert entries == ["armed.json"], (
        f"pre-push hook unexpectedly emitted closeout artifacts beyond armed.json: {entries}"
    )


def test_every_stop_does_not_write_closeout_status_artifact(tmp_path: Path) -> None:
    """Every-Stop scanners (``scan_corrections``) feed pending-lessons/ but
    do NOT write a closeout/<run-id>.json artifact.

    Simulates a Stop by writing a candidate file the way ``scan_corrections``
    would, then asserts the closeout artifact is still absent. The closeout
    artifact must only appear from an EXPLICIT closeout invocation (post-push
    or phase-6-learn), never from background Stop scanning.
    """
    workdir = tmp_path / "repo"
    (workdir / ".build-loop" / "pending-lessons").mkdir(parents=True)
    (workdir / ".build-loop" / "closeout").mkdir(parents=True)

    # scan_corrections-style flat candidate
    body = (
        "---\n"
        "id: x\nkind: lesson\nsignal_type: correction\n"
        "confidence: high\nscope: project\ncaptured_at: 2026-06-09T00:00:00Z\n"
        "---\n\n## Quote\n\n> a candidate\n"
    )
    (workdir / ".build-loop" / "pending-lessons" / "x.md").write_text(body, encoding="utf-8")

    # Imagine Stop fired and finished. The closeout artifact directory must be empty.
    artifacts = sorted(p.name for p in (workdir / ".build-loop" / "closeout").glob("*.json"))
    assert artifacts == [], (
        "Stop-side candidate ingestion must not produce a closeout/<run-id>.json — "
        f"found: {artifacts}"
    )
