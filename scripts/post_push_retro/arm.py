# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Pre-push arming + detached spawn — the wired, non-blocking trigger.

Git has NO native client-side post-push hook. The repo's established pattern
(see ``hooks/git/pre-push`` ``_arm_post_push_closeout``) is to arm a baton at
pre-push and drain it later. We reuse that substrate: the pre-push hook is the
ONLY surface that fires for EVERY push regardless of initiator (ad-hoc git push,
Codex, launchd, a crashed automation), so the trigger never depends on an agent
"remembering" — it is wired.

``arm_and_spawn`` does TWO things and returns immediately (it must NEVER block or
slow the push):
  1. Writes a UNIQUE baton (``armed-<ts>-<pid>-<shortsha>.json``) recording the
     pushed ref-range. Unique per push so two concurrent pushes never collide
     (plan-critic WARN, adopted).
  2. Spawns a DETACHED, fire-and-forget background job (``python3 -m
     post_push_retro run --armed <baton>``) via ``start_new_session=True`` with
     stdio -> DEVNULL. The retro work happens in that child AFTER the push
     returns; the hook returns now.

Fail-open: any error is swallowed and returned in the receipt — arming must
never break a push.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from post_push_retro import coverage as _coverage


def parse_pushed_range(stdin_lines: list[str]) -> dict[str, str]:
    """Per ``man githooks`` pre-push stdin: ``<local_ref> <local_sha>
    <remote_ref> <remote_sha>`` per pushed ref. Return the first real ref's
    range as ``{branch, pushed_range, local_sha, remote_sha}``. A new branch
    (all-zero remote_sha) has no range -> pushed_range='' (coverage falls back
    to the checkpoint / first-run cap)."""
    zero = "0" * 40
    for raw in stdin_lines:
        parts = raw.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if local_sha == zero:
            continue  # a delete; nothing to retro
        branch = local_ref.replace("refs/heads/", "")
        if remote_sha == zero or not remote_sha:
            pushed_range = ""  # new branch on remote
        else:
            pushed_range = f"{remote_sha}..{local_sha}"
        return {"branch": branch, "pushed_range": pushed_range,
                "local_sha": local_sha, "remote_sha": remote_sha}
    return {"branch": "", "pushed_range": "", "local_sha": "", "remote_sha": ""}


def _default_spawn(scripts_dir: Path, baton: Path, repo: Path) -> None:
    """Detached, fire-and-forget child. start_new_session detaches it from the
    hook's process group so the push (parent) is never waited on."""
    subprocess.Popen(  # noqa: S603 — args are fixed, not user data
        [sys.executable or "python3", "-m", "post_push_retro", "run",
         "--workdir", str(repo), "--armed", str(baton)],
        cwd=str(scripts_dir),
        env={**os.environ, "PYTHONPATH": str(scripts_dir) + os.pathsep + os.environ.get("PYTHONPATH", "")},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def arm_and_spawn(
    repo: Path,
    stdin_lines: list[str],
    *,
    spawn_fn: Callable[[Path, Path, Path], None] | None = None,
) -> dict[str, Any]:
    """Write the baton + spawn the detached retro job. Returns a receipt. Does
    NO retro work synchronously (non-blocking contract). Never raises."""
    try:
        repo = Path(repo)
        info = parse_pushed_range(stdin_lines)
        state_dir = _coverage.retro_state_dir(repo)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        short = (info.get("local_sha") or "nosha")[:9]
        baton = state_dir / f"armed-{ts}-{os.getpid()}-{short}.json"
        import json
        baton.write_text(json.dumps({
            "armed_at": ts,
            "branch": info.get("branch"),
            "pushed_range": info.get("pushed_range"),
            "local_sha": info.get("local_sha"),
            "remote_sha": info.get("remote_sha"),
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        scripts_dir = Path(__file__).resolve().parent.parent
        (spawn_fn or _default_spawn)(scripts_dir, baton, repo)
        return {"armed": True, "baton": str(baton),
                "pushed_range": info.get("pushed_range"), "spawned": True}
    except Exception as exc:  # noqa: BLE001 — arming must never break a push
        return {"armed": False, "error": f"{type(exc).__name__}: {exc}", "spawned": False}
