#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""prepush_dist_gate.py — auto-rebuild the committed ``dist/`` before a push.

``dist/`` is the ``tsc`` build output of ``src/*.ts`` and is committed to the repo
(plugin policy). It drifts stale whenever a ``.ts`` source is edited but the build
isn't re-run, producing the recurring manual ``build: rebuild dist`` commit. This
gate makes that automatic: at pre-push it detects a stale ``dist/``, runs the build,
and — if the build produced changes — auto-commits them as ``build: rebuild dist``
then BLOCKS the push so the operator re-pushes WITH the freshly-built output. The
rebuild + commit is no longer manual; only the (one-key) re-push is.

Contract (mirrors ``prepush_test_gate``):
    evaluate(repo, stdin_lines, env) -> {"action": "allow"|"block", "exit_code", "reason", ...}
    format_block_message(verdict) -> str

Fail-OPEN on every internal/tooling error (missing tsc, build failure, git error):
a broken build tool must never permanently wedge the operator's ability to push.
The push's CORRECTNESS gate is ``prepush_test_gate`` (stage 2); this gate is purely
about keeping the committed build artifact in sync, so allowing a push when the
rebuild can't run is the safe default.

Opt out:  ``BUILDLOOP_DIST_GATE_SKIP=1``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return 127, repr(exc)


def _tsc_cmd(repo: Path) -> list[str] | None:
    local = repo / "node_modules" / ".bin" / "tsc"
    if local.exists():
        return [str(local)]
    # npx fallback (may hit network on first use; still fail-open on error).
    import shutil
    npx = shutil.which("npx")
    if npx:
        return [npx, "--no-install", "tsc"]
    return None


def _dist_dirty(repo: Path) -> bool:
    rc, out = _run(["git", "status", "--porcelain", "--", "dist/"], repo, timeout=20)
    return rc == 0 and bool(out.strip())


def _newest_mtime(root: Path, suffix: str) -> float:
    newest = 0.0
    if not root.is_dir():
        return newest
    for p in root.rglob(f"*{suffix}"):
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return newest


def _needs_build(repo: Path) -> bool:
    if _dist_dirty(repo):
        return True
    src_ts = _newest_mtime(repo / "src", ".ts")
    dist_js = _newest_mtime(repo / "dist", ".js")
    if src_ts == 0.0:
        return False  # no TS sources → nothing to build
    if dist_js == 0.0:
        return True  # sources exist, no built output
    return src_ts > dist_js


def evaluate(repo: Path, stdin_lines: list[str] | None = None, env: dict | None = None) -> dict:
    env = env if env is not None else os.environ
    if str(env.get("BUILDLOOP_DIST_GATE_SKIP", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return {"action": "allow", "exit_code": 0, "reason": "dist gate skipped (env)"}
    try:
        if not (repo / "src").is_dir() or not (repo / "tsconfig.json").exists():
            return {"action": "allow", "exit_code": 0, "reason": "no TS project"}
        if not _needs_build(repo):
            return {"action": "allow", "exit_code": 0, "reason": "dist fresh"}
        tsc = _tsc_cmd(repo)
        if not tsc:
            return {"action": "allow", "exit_code": 0, "reason": "tsc unavailable — allowing (fail-open)"}
        rc, out = _run(tsc, repo)
        if rc != 0:
            return {"action": "allow", "exit_code": 0,
                    "reason": f"tsc build failed (rc={rc}) — allowing (fail-open)",
                    "build_output": out[-800:]}
        if not _dist_dirty(repo):
            return {"action": "allow", "exit_code": 0, "reason": "rebuilt — dist already in sync"}
        # dist changed → stage + auto-commit, then block so the operator re-pushes.
        add_rc, _ = _run(["git", "add", "--", "dist/"], repo, timeout=30)
        if add_rc != 0:
            return {"action": "allow", "exit_code": 0, "reason": "git add dist failed — allowing (fail-open)"}
        commit_rc, commit_out = _run(
            ["git", "commit", "--no-verify", "-m", "build: rebuild dist [pre-push auto]", "--", "dist/"],
            repo, timeout=60,
        )
        if commit_rc != 0:
            return {"action": "allow", "exit_code": 0,
                    "reason": "git commit dist failed — allowing (fail-open)", "detail": commit_out[-400:]}
        head_rc, head = _run(["git", "rev-parse", "--short", "HEAD"], repo, timeout=10)
        return {
            "action": "block",
            "exit_code": 1,
            "reason": "dist was stale — rebuilt and auto-committed; re-push to include it",
            "commit": head.strip() if head_rc == 0 else "(unknown)",
        }
    except Exception as exc:  # noqa: BLE001 — never wedge a push
        return {"action": "allow", "exit_code": 0, "reason": f"dist gate internal error — allowing: {exc!r}"}


def format_block_message(verdict: dict) -> str:
    commit = verdict.get("commit", "(unknown)")
    return (
        "\n"
        "===============================================================\n"
        "  BUILD-LOOP DIST REBUILD — push BLOCKED (one time)\n"
        "===============================================================\n"
        "  dist/ was stale vs src/*.ts. It has been rebuilt and committed\n"
        f"  automatically as:  {commit}  \"build: rebuild dist [pre-push auto]\"\n"
        "  ---------------------------------------------------------------\n"
        "  Just re-run your push — the new commit is now on the branch:\n"
        "      git push <remote> <branch>\n"
        "\n"
        "  To skip this gate for one push:\n"
        "      BUILDLOOP_DIST_GATE_SKIP=1 git push <remote> <branch>\n"
        "===============================================================\n"
    )
