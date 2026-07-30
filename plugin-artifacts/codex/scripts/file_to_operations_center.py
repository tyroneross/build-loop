#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""file_to_operations_center.py — mechanically file a CROSS-REPO issue as a task
on the RossLabs Operations Center queue.

Standing policy (flagged-issue default route): when a build-loop run surfaces an
issue, the DEFAULT action is set by WHERE the issue lives —

  * build-loop's OWN repo  → execute the fix (self-heal / iterate); never file.
  * any OTHER repo         → file a task on Operations Center's queue (here).
  * PRODUCTION-class / genuinely ambiguous → surface to the user (do not file).

A cross-repo finding must never end a run as a prose flag with a "want me to?"
question. This helper makes the "file it" branch MECHANICAL rather than prose:
the orchestrator/agents call it, it shells out to the Operations Center CLI's
documented `add` subcommand, and returns a structured receipt.

It NEVER writes raw rows into the SQLite queue — the intake contract is the CLI.
If the CLI binary cannot be found, it returns a structured blocker
(`filed: false`, exit 1) so the caller surfaces the blocker as its own finding,
rather than silently dropping the issue or corrupting the store.

CLI::

    python3 scripts/file_to_operations_center.py
        --repo <repo>                 # target repo the issue lives in (required)
        --title <one-line title>      # required
        [--spec <fuller description>]
        [--urgency low|normal|high|critical]   # default: normal
        [--task-type build|fix|research|ui-draft|ops|question]  # default: fix
        [--oc-bin <path>]             # override binary discovery
        [--db <sqlite path>]          # override the queue db (tests; default central)
        [--dry-run]                   # print the argv that WOULD run, file nothing
        --json

Output JSON::

    {
      "filed":     bool,
      "task_id":   str | null,   # 8-char id echoed by `oc add` — the durable
                                 #   receipt shown by `oc list` and the board
      "full_id":   str | null,   # forward-compat; null today (the CLI `show`
                                 #   subcommand needs a full id, not a prefix)
      "repo":      str,
      "title":     str,
      "urgency":   str,
      "priority":  int,          # 0=P0 (highest) .. 3=P3
      "task_type": str,
      "binary":    str | null,
      "receipt":   str | null,   # raw stdout line from `oc add`
      "reason":    str | null,   # non-null iff filed=false
      "argv":      [str, ...]     # command that ran (or would, on --dry-run)
    }

Exit codes:
  0  — filed (task created) OR dry-run printed successfully
  1  — NOT filed (binary missing, or `oc add` failed) — a blocker for the caller
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# urgency label → Operations Center priority (0=P0 highest .. 3=P3).
PRIORITY_BY_URGENCY = {
    "critical": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
}
DEFAULT_URGENCY = "normal"
DEFAULT_TASK_TYPE = "fix"

# Conventional Operations Center checkout name (sibling of the build-loop repo).
_OC_REPO_DIRNAME = "RossLabs Operations Center"
_OC_BINARY_NAME = "rosslabs-operations-center"


def urgency_to_priority(urgency: str | None) -> int:
    """Map an urgency label to an Operations Center priority int (default P2)."""
    if not urgency:
        return PRIORITY_BY_URGENCY[DEFAULT_URGENCY]
    return PRIORITY_BY_URGENCY.get(urgency.strip().lower(), PRIORITY_BY_URGENCY[DEFAULT_URGENCY])


def _candidate_binaries(workdir: Path | None) -> list[Path]:
    """Ordered candidate paths for the Operations Center CLI binary.

    Order: $OC_BIN → sibling checkout target/release → target/debug → PATH.
    release is preferred (the README's canonical `cargo build --release`), debug
    is the common local-dev artifact. No absolute user path is hardcoded.
    """
    out: list[Path] = []
    env_bin = os.environ.get("OC_BIN")
    if env_bin:
        out.append(Path(env_bin))

    roots: list[Path] = []
    if workdir is not None:
        # build-loop repo root is workdir; the OC checkout is a sibling.
        roots.append(workdir.parent / _OC_REPO_DIRNAME)
    # Also try relative to THIS script's checkout (scripts/ -> repo -> siblings).
    here_repo = Path(__file__).resolve().parents[1]
    roots.append(here_repo.parent / _OC_REPO_DIRNAME)

    for root in roots:
        out.append(root / "target" / "release" / _OC_BINARY_NAME)
        out.append(root / "target" / "debug" / _OC_BINARY_NAME)

    for name in (_OC_BINARY_NAME, "oc"):
        found = shutil.which(name)
        if found:
            out.append(Path(found))
    return out


def find_oc_binary(explicit: str | None = None, workdir: Path | None = None) -> Path | None:
    """Return the first existing, executable Operations Center binary, or None."""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    for cand in _candidate_binaries(workdir):
        if cand.exists() and os.access(cand, os.X_OK):
            return cand
    return None


def build_add_argv(
    binary: str,
    *,
    title: str,
    repo: str,
    priority: int,
    spec: str | None = None,
    task_type: str | None = DEFAULT_TASK_TYPE,
    db: str | None = None,
) -> list[str]:
    """Build the `oc add` argv. Global flags (--db) precede the subcommand.

    The title is a clap positional (`add [OPTIONS] <TITLE>`). A caller-supplied
    title starting with '-' (e.g. "--db=/evil.db") would otherwise be
    flag-parsed by clap — worst case redirecting the queue db or failing intake
    with a missing-<TITLE> error. So every flag precedes a `--` end-of-options
    separator and the title is passed LAST: after `--`, clap takes the title
    literally. Verified against the real CLI: `add --repo R -- "--db=x"` files a
    task titled exactly `--db=x`.
    """
    argv: list[str] = [binary]
    if db:
        argv += ["--db", db]
    argv += ["add", "--repo", repo, "--priority", str(priority)]
    if spec:
        argv += ["--spec", spec]
    if task_type:
        argv += ["--task-type", task_type]
    argv += ["--", title]
    return argv


# `oc add` prints:  "added  <8char-id>  <title>"
_ADDED_RE = re.compile(r"^added\s+(\S+)\s+", re.MULTILINE)


def parse_add_output(stdout: str) -> str | None:
    """Extract the short task id from `oc add` stdout, or None."""
    m = _ADDED_RE.search(stdout or "")
    return m.group(1) if m else None


def file_task(
    *,
    repo: str,
    title: str,
    spec: str | None = None,
    urgency: str = DEFAULT_URGENCY,
    task_type: str = DEFAULT_TASK_TYPE,
    oc_bin: str | None = None,
    db: str | None = None,
    workdir: Path | None = None,
    dry_run: bool = False,
) -> tuple[dict, int]:
    """File one cross-repo task on the Operations Center queue.

    Returns (result_dict, exit_code). Exit 1 (blocker) when the CLI binary is
    missing or `oc add` fails — the caller surfaces that as its own finding.
    """
    priority = urgency_to_priority(urgency)
    base = {
        "filed": False,
        "task_id": None,
        "full_id": None,
        "repo": repo,
        "title": title,
        "urgency": (urgency or DEFAULT_URGENCY).lower(),
        "priority": priority,
        "task_type": task_type,
        "binary": None,
        "receipt": None,
        "reason": None,
        "argv": [],
    }

    binary = find_oc_binary(oc_bin, workdir)
    if binary is None:
        base["reason"] = (
            "operations-center CLI binary not found "
            f"(looked for $OC_BIN, sibling '{_OC_REPO_DIRNAME}/target/{{release,debug}}/"
            f"{_OC_BINARY_NAME}', and PATH). Build it with `cargo build --release` "
            "in the Operations Center repo, or set $OC_BIN. Issue NOT filed — "
            "surface this blocker to the caller; do not write the sqlite db directly."
        )
        return base, 1

    base["binary"] = str(binary)
    argv = build_add_argv(
        str(binary),
        title=title,
        repo=repo,
        priority=priority,
        spec=spec,
        task_type=task_type,
        db=db,
    )
    base["argv"] = argv

    if dry_run:
        base["reason"] = "dry-run: no task filed"
        return base, 0

    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        base["reason"] = f"oc add invocation failed: {exc}"
        return base, 1

    if r.returncode != 0:
        base["reason"] = (
            f"oc add exited {r.returncode}: {(r.stderr or r.stdout).strip()[:400]}"
        )
        return base, 1

    receipt = (r.stdout or "").strip().splitlines()
    base["receipt"] = receipt[0] if receipt else None
    task_id = parse_add_output(r.stdout or "")
    base["task_id"] = task_id
    if task_id is None:
        # Command succeeded but output was unexpected — treat as filed-but-unparsed.
        base["filed"] = True
        base["reason"] = "oc add succeeded but the task id could not be parsed from stdout"
        return base, 0

    base["filed"] = True
    return base, 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--repo", required=True, help="Target repo the issue lives in")
    p.add_argument("--title", required=True, help="One-line task title")
    p.add_argument("--spec", default=None, help="Fuller description / repro / fix hint")
    p.add_argument(
        "--urgency",
        default=DEFAULT_URGENCY,
        choices=list(PRIORITY_BY_URGENCY.keys()),
        help="Maps to priority: critical=P0, high=P1, normal=P2, low=P3",
    )
    p.add_argument("--task-type", default=DEFAULT_TASK_TYPE, dest="task_type")
    p.add_argument("--oc-bin", default=None, dest="oc_bin", help="Override binary discovery")
    p.add_argument("--db", default=None, help="Override queue sqlite path (default: central)")
    p.add_argument("--workdir", default=None, help="build-loop repo root (for sibling discovery)")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.add_argument("--json", action="store_true", help="Emit result JSON (always implied)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    workdir = Path(args.workdir).resolve() if args.workdir else None
    result, exit_code = file_task(
        repo=args.repo,
        title=args.title,
        spec=args.spec,
        urgency=args.urgency,
        task_type=args.task_type,
        oc_bin=args.oc_bin,
        db=args.db,
        workdir=workdir,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    status = "filed" if result["filed"] else ("dry-run" if args.dry_run else "NOT filed")
    print(
        f"file_to_operations_center: {status} "
        f"repo={result['repo']} task_id={result['task_id']} "
        f"priority=P{result['priority']} reason={result['reason']!r}",
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
