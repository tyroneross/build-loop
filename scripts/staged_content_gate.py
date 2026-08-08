#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""staged_content_gate.py — grade what will actually be committed, not what
sits in the working tree.

Root cause this closes (RossLabs-AI-Assistant commit 5066d1f): a pre-commit
hook ran a test suite against the WORKING TREE while `git commit` records the
INDEX. An implementer had reverted a fix in the working tree to prove a
mutation while the fixed version was already staged (or vice versa) — the
hook graded bytes that were never committed and reported a green suite on
content nobody was actually shipping. A green gate certified an artifact it
never examined.

Two modes:

``--check`` (Mode 1, advisory divergence signal)
    For every path with staged changes (``git diff --cached --name-only``),
    compares the INDEX blob against the current WORKING-TREE bytes. Emits a
    JSON report of any path where the two differ, or where a staged path has
    since been deleted from the working tree. Exits 0 by default — partial
    staging (``git add -p``) is legitimate and common, so a hard block here
    would be a noisy gate (see ``feedback_noisy_gate_is_worse_than_no_gate``).
    Pass ``--strict`` to exit 1 when anything has diverged.

``--run <command>`` (Mode 2, THE FIX — hermetic grading)
    Materializes the exact INDEX content into a throwaway temp directory via
    ``git checkout-index --all --force --prefix=<tmpdir>/`` and runs the given
    shell command there. This is the whole point of the module: it does NOT
    copy the working tree and does NOT use ``git stash`` (stash mutates the
    working tree and can itself diverge from what checkout-index writes). The
    command's stdout/stderr/returncode are captured and the tmpdir is always
    removed afterward (``--keep-tmpdir`` to inspect it).

Limitation (by design, not a bug): ``git checkout-index --all`` only writes
TRACKED index entries. That is exactly "a worktree of what is about to be
committed" — an untracked file is, correctly, not part of what git commit
would record. But a command that depends on an untracked file (a local
config, a venv, a generated lockfile that was never `git add`ed) will see it
missing and can fail for that reason alone. ``--copy-untracked`` is an
explicit opt-in (default OFF) that additionally copies untracked, non-ignored
files into the tmpdir for commands that need them; it does NOT copy ignored
files, and it does NOT affect ``--check``.

Importable API (the audit packet builder should import these rather than
shell out):

    check_divergence(repo: Path) -> dict
    run_against_index(repo: Path, command: str, timeout: int = 600,
                       copy_untracked: bool = False,
                       keep_tmpdir: bool = False,
                       expect_paths: list[str] | None = None) -> dict

CLI:
    staged_content_gate.py --check [--strict] [--repo <path>] [--json]
    staged_content_gate.py --run "<shell command>" [--repo <path>]
        [--timeout SECONDS] [--copy-untracked] [--keep-tmpdir]
        [--expect-path PATH ...] [--json]

Exit codes:
    --check:  0 aligned or diverged (non-strict) · 1 diverged (--strict)
    --run:    mirrors the command's own returncode · 2 setup failure
              (checkout-index failed, ZERO files materialized, or an
              --expect-path was absent from the graded index) · 3 timeout
    neither --check nor --run given: 2 (usage error)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TIMEOUT_S = 600


# ---------------------------------------------------------------------------
# Git plumbing helpers
# ---------------------------------------------------------------------------


def _repo_root_from_cwd() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except OSError:
        pass
    return Path.cwd()


def _staged_paths(repo: Path) -> list[str]:
    """Paths with staged changes relative to HEAD (``git diff --cached``).

    Uses ``-z`` NUL-separated output so filenames containing spaces or other
    unusual bytes round-trip correctly.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only", "-z"],
        capture_output=True, check=False,
    )
    raw = result.stdout.decode("utf-8", errors="surrogateescape")
    return [p for p in raw.split("\x00") if p]


def _index_blob_bytes(repo: Path, path: str) -> bytes | None:
    """Return the INDEX (stage 0) content of ``path`` as raw bytes, or None
    if the path has no stage-0 blob (e.g. it was removed from the index)."""
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f":{path}"],
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _untracked_nonignored_paths(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True, check=False,
    )
    raw = result.stdout.decode("utf-8", errors="surrogateescape")
    return [p for p in raw.split("\x00") if p]


# ---------------------------------------------------------------------------
# Mode 1 — --check (advisory divergence signal)
# ---------------------------------------------------------------------------


def check_divergence(repo: Path) -> dict:
    """Compare the INDEX content of every staged path against the current
    WORKING-TREE bytes. Never decodes — comparison is always byte-for-byte,
    so binary files are handled correctly.

    Returns:
        {"divergent": [{"path": str, "reason": str}, ...],
         "staged_count": int, "divergent_count": int,
         "verdict": "aligned" | "diverged"}
    """
    staged = _staged_paths(repo)
    divergent: list[dict] = []

    for path in staged:
        index_bytes = _index_blob_bytes(repo, path)
        if index_bytes is None:
            # No stage-0 blob for this path (e.g. staged removal from the
            # index itself) — nothing to compare against the worktree.
            continue

        worktree_path = repo / path
        try:
            worktree_bytes = worktree_path.read_bytes()
        except OSError:
            divergent.append({"path": path, "reason": "staged_but_deleted_in_worktree"})
            continue

        if worktree_bytes != index_bytes:
            divergent.append({"path": path, "reason": "index_differs_from_worktree"})

    return {
        "divergent": divergent,
        "staged_count": len(staged),
        "divergent_count": len(divergent),
        "verdict": "diverged" if divergent else "aligned",
    }


# ---------------------------------------------------------------------------
# Mode 2 — --run (hermetic grading of the INDEX)
# ---------------------------------------------------------------------------


def _copy_untracked_into(repo: Path, dest: Path) -> None:
    for rel in _untracked_nonignored_paths(repo):
        src = repo / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def _decode_maybe(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_against_index(
    repo: Path,
    command: str,
    timeout: int = DEFAULT_TIMEOUT_S,
    copy_untracked: bool = False,
    keep_tmpdir: bool = False,
    expect_paths: list[str] | None = None,
) -> dict:
    """Materialize the INDEX (not the working tree, not a stash) into a
    throwaway directory and run ``command`` there. This is the hermetic fix:
    whatever ``git commit`` would actually record is what gets graded.

    Returns a dict with ``mode: "run"``, ``tmpdir``, ``command``,
    ``returncode``, ``stdout``, ``stderr``, ``graded: "staged_index"``,
    ``materialized_files``, and ``timed_out``. On setup failure
    (``checkout-index`` fails, nothing was materialized, or an ``expect_paths``
    entry is absent), ``returncode`` is 2 and ``setup_error`` is True. On
    timeout, ``returncode`` is 3 and ``timed_out`` is True.

    **Why the materialization is asserted rather than assumed.**
    ``git checkout-index --all`` exits 0 when it writes ZERO files (an empty
    index, or an index with no tracked entries), and tolerant commands return 0
    on nothing: ``ruff check .``, ``mypy .``, and a shell loop over an empty
    glob all exit 0 in an empty directory. Without the count below, this gate
    reproduces its own thesis one level down — a green result certifying an
    artifact it never examined. ``expect_paths`` is the stronger form: name the
    file under test and the gate refuses to report a pass if that file was not
    actually part of what it graded. A test file created but not yet ``git
    add``-ed is absent by design (it is not being committed), and that absence
    should surface as a setup error rather than as a green run.
    """
    tmpdir = tempfile.mkdtemp(prefix="staged-content-gate-")
    result: dict = {
        "mode": "run",
        "tmpdir": tmpdir,
        "command": command,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "graded": "staged_index",
        "materialized_files": 0,
        "timed_out": False,
    }
    try:
        prefix = tmpdir if tmpdir.endswith(os.sep) else tmpdir + os.sep
        checkout = subprocess.run(
            ["git", "-C", str(repo), "checkout-index", "--all", "--force", f"--prefix={prefix}"],
            capture_output=True, text=True, check=False,
        )
        if checkout.returncode != 0:
            result["returncode"] = 2
            result["setup_error"] = True
            result["stderr"] = f"git checkout-index failed: {checkout.stderr.strip()}"
            return result

        if copy_untracked:
            _copy_untracked_into(repo, Path(tmpdir))

        # Assert we actually graded something (see the docstring). A zero-file
        # tmpdir means every downstream "pass" is vacuous.
        materialized = sum(1 for p in Path(tmpdir).rglob("*") if p.is_file())
        result["materialized_files"] = materialized
        if materialized == 0:
            result["returncode"] = 2
            result["setup_error"] = True
            result["stderr"] = (
                "nothing was materialized from the index — checkout-index wrote 0 files. "
                "A command run here would grade an empty tree and could exit 0 for that reason."
            )
            return result

        if expect_paths:
            missing = [p for p in expect_paths if not (Path(tmpdir) / p).exists()]
            if missing:
                result["returncode"] = 2
                result["setup_error"] = True
                result["missing_expected_paths"] = missing
                result["stderr"] = (
                    "expected path(s) absent from the graded index: "
                    + ", ".join(missing)
                    + " — they are not staged, so this run would not have graded them."
                )
                return result

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            result["returncode"] = proc.returncode
            result["stdout"] = proc.stdout
            result["stderr"] = proc.stderr
        except subprocess.TimeoutExpired as exc:
            result["timed_out"] = True
            result["returncode"] = 3
            result["stdout"] = _decode_maybe(exc.stdout)
            result["stderr"] = _decode_maybe(exc.stderr)
        return result
    finally:
        if not keep_tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=None, help="Repo root (default: git rev-parse --show-toplevel from cwd)")
    parser.add_argument("--check", action="store_true", help="Mode 1: report index/worktree divergence")
    parser.add_argument("--strict", action="store_true", help="With --check: exit 1 when divergence is found")
    parser.add_argument("--run", dest="run_command", default=None, metavar="COMMAND",
                         help="Mode 2: run COMMAND against a checkout of the staged index")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="Seconds before --run times out")
    parser.add_argument("--copy-untracked", action="store_true",
                         help="Also copy untracked, non-ignored files into the --run tmpdir (default OFF)")
    parser.add_argument("--keep-tmpdir", action="store_true", help="Do not delete the --run tmpdir on exit")
    parser.add_argument("--expect-path", dest="expect_paths", action="append", default=None,
                        metavar="PATH",
                        help="Repo-relative path that MUST be present in the graded index; "
                             "repeatable. Absent -> setup error (exit 2), so a pass cannot be "
                             "reported for a run that never contained the file under test.")
    parser.add_argument("--json", action="store_true", help="No-op — output is always JSON (kept for CLI parity)")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve() if args.repo else _repo_root_from_cwd()

    if args.run_command is not None:
        result = run_against_index(
            repo,
            args.run_command,
            timeout=args.timeout,
            copy_untracked=args.copy_untracked,
            keep_tmpdir=args.keep_tmpdir,
            expect_paths=args.expect_paths,
        )
        print(json.dumps(result, indent=2))
        return result["returncode"] if isinstance(result["returncode"], int) else 2

    if args.check:
        result = check_divergence(repo)
        print(json.dumps(result, indent=2))
        if args.strict and result["verdict"] == "diverged":
            return 1
        return 0

    parser.print_usage(sys.stderr)
    sys.stderr.write("staged_content_gate.py: one of --check or --run is required\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
