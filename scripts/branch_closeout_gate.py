#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Verify branch hygiene before a run can post its terminal closeout phase.

The gate is read-only. It accepts solo-on-main runs with no attributable refs.
Any run-created branch requires a terminal ledger projection plus a schema-v1
receipt whose exact bundle/OID and live Git disposition still verify.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


def _identities(row: dict[str, Any] | None) -> set[str]:
    if not isinstance(row, dict):
        return set()
    return {
        value
        for key in ("build_loop_id", "run_id", "id")
        if isinstance((value := row.get(key)), str) and value
    }


def _git(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _resolve_path(workdir: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (workdir / path).resolve()


def _branch_oid(workdir: Path, branch: str) -> str | None:
    result = _git(workdir, "rev-parse", "--verify", f"refs/heads/{branch}")
    return result.stdout.strip() if result.returncode == 0 else None


def _registered_branch_paths(workdir: Path, branch: str) -> tuple[list[str], str | None]:
    result = _git(workdir, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return [], (result.stderr or result.stdout).strip() or "git worktree list failed"
    paths: list[str] = []
    current_path: str | None = None
    current_branch: str | None = None

    def finish() -> None:
        nonlocal current_path, current_branch
        if current_path and current_branch == branch:
            paths.append(str(Path(current_path).resolve()))
        current_path = None
        current_branch = None

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            finish()
            current_path = line[len("worktree "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current_branch = (
                ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
            )
        elif line == "":
            finish()
    finish()
    return sorted(paths), None


def _bundle_has_expected_ref(
    workdir: Path,
    bundle_path: Path,
    branch: str,
    expected_oid: str,
) -> bool:
    if not bundle_path.is_file():
        return False
    verify = _git(workdir, "bundle", "verify", str(bundle_path))
    if verify.returncode != 0:
        return False
    heads = _git(workdir, "bundle", "list-heads", str(bundle_path))
    if heads.returncode != 0:
        return False
    wanted = [expected_oid, f"refs/heads/{branch}"]
    return any(line.split(maxsplit=1) == wanted for line in heads.stdout.splitlines())


def _find_run(state: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    runs = state.get("runs")
    if not isinstance(runs, list):
        return None
    for row in reversed(runs):
        if isinstance(row, dict) and run_id in _identities(row):
            return row
    return None


def _attributable_refs(
    state: dict[str, Any],
    run: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    refs: dict[str, dict[str, Any]] = {}
    ambiguous_paths: list[str] = []

    def add(branch: object, path: object, status: object = None) -> None:
        branch_name = str(branch or "").strip()
        path_text = str(path or "").strip()
        if not branch_name:
            if path_text:
                ambiguous_paths.append(path_text)
            return
        if branch_name == "main":
            return
        entry = refs.setdefault(branch_name, {"branch": branch_name})
        if path_text:
            entry["path"] = path_text
        if isinstance(status, str) and status:
            entry["ledger_status"] = status

    for ref in run.get("createdRefs") or []:
        if isinstance(ref, dict):
            add(
                ref.get("branch"),
                ref.get("path") or ref.get("worktree"),
                ref.get("status"),
            )

    run_ids = _identities(run)
    executions: list[dict[str, Any]] = []
    active = state.get("execution")
    if isinstance(active, dict) and active:
        executions.append(active)
    executions.extend(
        row
        for row in state.get("historicalExecutions") or []
        if isinstance(row, dict)
    )
    for execution in executions:
        if not (run_ids & _identities(execution)):
            continue
        add(
            execution.get("run_worktree_branch")
            or execution.get("branch")
            or execution.get("branch_name"),
            execution.get("run_worktree_path")
            or execution.get("worktree")
            or execution.get("worktree_path"),
        )
    return refs, ambiguous_paths


def check_branch_closeout(workdir: Path | str, run_id: str) -> dict[str, Any]:
    """Return a machine-readable terminal-closeout verdict for one exact run."""
    root = Path(workdir).resolve()
    result: dict[str, Any] = {
        "ready": False,
        "run_id": run_id,
        "receipt_path": None,
        "branches": [],
        "errors": [],
    }
    state_path = root / ".build-loop" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["errors"].append(f"state unavailable: {exc}")
        return result
    if not isinstance(state, dict):
        result["errors"].append("state root is not an object")
        return result

    run = _find_run(state, run_id)
    if run is None:
        result["errors"].append(f"no exact runs[] row for {run_id}")
        return result
    refs, ambiguous_paths = _attributable_refs(state, run)
    if ambiguous_paths:
        result["errors"].append(
            "run-owned worktree path has no attributable branch: "
            + ", ".join(sorted(set(ambiguous_paths)))
        )
        return result
    result["branches"] = sorted(refs)
    if not refs:
        result["ready"] = True
        result["reason"] = "no run-created branches/worktrees; solo-on-main closeout"
        return result

    projection = run.get("branch_closeout")
    if not isinstance(projection, dict) or projection.get("status") != "complete":
        result["errors"].append("runs[].branch_closeout is not complete")
        return result
    receipt_path = _resolve_path(root, projection.get("receipt_path"))
    if receipt_path is None:
        result["errors"].append("complete branch_closeout has no receipt_path")
        return result
    result["receipt_path"] = str(receipt_path)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["errors"].append(f"receipt unavailable: {exc}")
        return result
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        result["errors"].append("receipt is not schema version 1")
        return result
    if receipt.get("run_id") not in _identities(run):
        result["errors"].append("receipt run_id does not match the run ledger")
        return result
    if receipt.get("status") != "complete":
        result["errors"].append("receipt is not terminal complete")
        return result
    receipt_refs = {
        str(entry.get("branch")): entry
        for entry in receipt.get("refs") or []
        if isinstance(entry, dict) and entry.get("branch")
    }

    for branch, ref in refs.items():
        entry = receipt_refs.get(branch)
        if entry is None:
            result["errors"].append(f"receipt omits attributable branch {branch}")
            continue
        status = entry.get("status")
        ledger_status = ref.get("ledger_status")
        registered_paths, registration_error = _registered_branch_paths(root, branch)
        if registration_error:
            result["errors"].append(
                f"cannot verify worktree registration for {branch}: {registration_error}"
            )
        elif registered_paths:
            result["errors"].append(
                f"terminal branch is still checked out for {branch}: "
                + ", ".join(registered_paths)
            )
        if status == "closed":
            if ledger_status != "closed":
                result["errors"].append(
                    f"ledger for {branch} is {ledger_status!r}, not closed"
                )
            if _branch_oid(root, branch) is not None:
                result["errors"].append(f"closed branch still exists: {branch}")
        elif status == "retained":
            if ledger_status not in ("kept_for_review", "surfaced_unmerged"):
                result["errors"].append(
                    f"retained branch {branch} has nonterminal ledger status {ledger_status!r}"
                )
        else:
            result["errors"].append(f"receipt branch {branch} is nonterminal: {status!r}")
            continue

        expected_oid = entry.get("expected_oid")
        bundle_path = _resolve_path(root, entry.get("bundle_path"))
        if (
            entry.get("bundle_verified") is not True
            or not isinstance(expected_oid, str)
            or not expected_oid
            or bundle_path is None
            or not _bundle_has_expected_ref(root, bundle_path, branch, expected_oid)
        ):
            result["errors"].append(f"exact verified bundle is invalid for {branch}")
        if status == "retained" and _branch_oid(root, branch) != expected_oid:
            result["errors"].append(f"retained branch OID changed: {branch}")

        path = _resolve_path(root, entry.get("path") or ref.get("path"))
        if path is not None and path.exists():
            result["errors"].append(f"terminal worktree path still exists: {path}")

    result["ready"] = not result["errors"]
    if result["ready"]:
        result["reason"] = "terminal receipt, ledger, bundle, and Git disposition verified"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    result = check_branch_closeout(args.workdir, args.run_id)
    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print("ready" if result["ready"] else "blocked")
        for error in result["errors"]:
            print(f"- {error}")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
