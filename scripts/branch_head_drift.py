#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Detect branch-head drift between an integration merge and today's history.

Evidence (approved proposal P3): an integration branch `bl/et-next` merged
history at sha `301b179d` while the history branch kept moving to `c955ee52`
and then `29eeacf5` — the integration gate built a stale merge and nobody
noticed until the deploy was already live.

This script is read-only on git (only `rev-parse` / `log` style reads). It
records the exact sha of each branch that a merge pulled in, at merge time,
and later checks whether that branch's head has moved on since.

    record  --workdir <repo> --branch <name> [--branch <name> ...]
            [--sha <sha> ...] [--from-merge <merge-commit>]
            [--file .build-loop/integration-heads.json] --json
    check   --workdir <repo> [--file ...] [--branch <name> ...] --json

`record` merges by branch name into the ledger (last write wins per branch)
and writes atomically via a temp file + `os.replace`. `check` compares each
recorded sha against the branch's live head (`refs/heads/<branch>`, falling
back to the ref as given when it is not a local head) and exits 0 only when
every recorded branch still matches.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LEDGER = ".build-loop/integration-heads.json"


def _git(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_ledger_path(workdir: Path, file_arg: str | None) -> Path:
    raw = file_arg or DEFAULT_LEDGER
    path = Path(raw)
    return path if path.is_absolute() else (workdir / path)


def _load_ledger(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_ledger_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".integration-heads-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _rev_parse(workdir: Path, ref: str) -> str | None:
    result = _git(workdir, "rev-parse", "--verify", ref)
    return result.stdout.strip() if result.returncode == 0 else None


def _second_parent_of_merge(workdir: Path, merge_commit: str) -> str | None:
    """Return the second-parent sha of a merge commit, or None if not simple.

    "Simple" means exactly two parents. Anything else (a non-merge commit, an
    octopus merge with 3+ parents, or an unresolvable ref) is left to the
    caller to handle — this helper never guesses.
    """
    result = _git(workdir, "rev-parse", f"{merge_commit}^@")
    if result.returncode != 0:
        return None
    parents = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(parents) != 2:
        return None
    return parents[1]


def record(
    workdir: Path,
    branches: list[str],
    shas: list[str | None],
    ledger_path: Path,
    from_merge: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"recorded": [], "errors": []}
    existing = _load_ledger(ledger_path) or {"branches": {}}
    branches_map = existing.get("branches")
    if not isinstance(branches_map, dict):
        branches_map = {}
        existing["branches"] = branches_map

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for index, branch in enumerate(branches):
        sha = shas[index] if index < len(shas) else None
        if not sha and from_merge:
            sha = _second_parent_of_merge(workdir, from_merge)
        if sha:
            # Normalise an abbreviated or symbolic sha to the full object id so
            # `check` compares like with like (a short sha would read as drift).
            sha = _rev_parse(workdir, f"{sha}^{{commit}}") or sha
        else:
            sha = _rev_parse(workdir, f"refs/heads/{branch}") or _rev_parse(workdir, branch)
        if not sha:
            result["errors"].append(f"cannot resolve sha for {branch}")
            continue
        branches_map[branch] = {"sha": sha, "recorded_at": now}
        result["recorded"].append({"branch": branch, "sha": sha})

    if result["recorded"]:
        _write_ledger_atomic(ledger_path, existing)
    result["file"] = str(ledger_path)
    return result


def check(
    workdir: Path,
    ledger_path: Path,
    only_branches: list[str] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"branches": [], "errors": []}
    ledger = _load_ledger(ledger_path)
    if ledger is None:
        result["errors"].append(f"no ledger at {ledger_path}")
        return result
    branches_map = ledger.get("branches")
    if not isinstance(branches_map, dict) or not branches_map:
        result["errors"].append(f"ledger at {ledger_path} has no recorded branches")
        return result

    names = only_branches if only_branches else sorted(branches_map)
    for branch in names:
        entry = branches_map.get(branch)
        if not isinstance(entry, dict) or not entry.get("sha"):
            result["errors"].append(f"{branch}: not recorded in ledger")
            continue
        recorded_sha = str(entry["sha"])
        current_sha = _rev_parse(workdir, f"refs/heads/{branch}")
        if current_sha is None:
            current_sha = _rev_parse(workdir, branch)
        row = {
            "branch": branch,
            "recorded": recorded_sha,
            "current": current_sha,
        }
        if current_sha is None:
            row["status"] = "missing"
            result["errors"].append(f"{branch}: missing")
        elif current_sha != recorded_sha:
            row["status"] = "drift"
            result["errors"].append(
                f"re-merge: {branch} recorded {recorded_sha} now {current_sha}"
            )
        else:
            row["status"] = "ok"
        result["branches"].append(row)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    subparsers = parser.add_subparsers(dest="mode", required=True)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--workdir", default=".")
    record_parser.add_argument("--branch", action="append", default=[], dest="branches")
    record_parser.add_argument("--sha", action="append", default=[], dest="shas")
    record_parser.add_argument("--file", default=None)
    record_parser.add_argument("--from-merge", default=None)
    record_parser.add_argument("--json", action="store_true", dest="json_output")

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--workdir", default=".")
    check_parser.add_argument("--file", default=None)
    check_parser.add_argument("--branch", action="append", default=None, dest="branches")
    check_parser.add_argument("--json", action="store_true", dest="json_output")

    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve()
    ledger_path = _resolve_ledger_path(workdir, args.file)

    if args.mode == "record":
        if not args.branches:
            print("error: --branch is required at least once")
            return 2
        # Pad/truncate shas to align 1:1 with branches; missing entries fall
        # back to --from-merge inference or a live rev-parse.
        shas: list[str | None] = list(args.shas) + [None] * max(
            0, len(args.branches) - len(args.shas)
        )
        result = record(workdir, args.branches, shas, ledger_path, args.from_merge)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            for row in result["recorded"]:
                print(f"recorded {row['branch']} @ {row['sha']}")
            for error in result["errors"]:
                print(f"- {error}")
        return 0 if not result["errors"] else 2

    result = check(workdir, ledger_path, args.branches)
    if not result["branches"] and result["errors"]:
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            for error in result["errors"]:
                print(f"- {error}")
        return 2

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        for row in result["branches"]:
            print(f"{row['branch']}: {row['status']} (recorded {row['recorded']}, current {row['current']})")
        for error in result["errors"]:
            print(f"- {error}")

    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
