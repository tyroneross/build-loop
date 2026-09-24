#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""closeout_ready.py — merge ready run work, close owned worktrees, inventory the rest.

Phase D always runs this. Collapse never merges; this script is the merge step
that was previously prompt-only. Default is inventory-only. Mutation requires
``--owner-released`` (Phase D integrator). Stop is not owner release.

Ready means:
  * this run's branch can fast-forward or merge-risk-clear into local main, and
    the integrator worktree is on main and clean of tracked dirt;
  * leftover ``.build-loop/worktrees/run-*`` branches already on main, uniquely
    attributed, not active, and safe for ``collapse_run``.

Everything else is written to open-items. Pre-planned deletion point lives on
``state.execution.deletion_point`` (phase D; ``may_change: true``).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import collapse_run  # noqa: E402
import merge_risk  # noqa: E402
import worktree_inventory  # noqa: E402
from worktree_reaper.reaper import reap_worktrees  # noqa: E402

DEFAULT_DELETION_POINT: dict[str, Any] = {
    "phase": "D",
    "trigger": "review-g-pass+learn-complete",
    "may_change": True,
    "command": (
        "python3 scripts/closeout_ready.py --workdir \"$PWD\" "
        "--run-id <run-id> --owner-released --json"
    ),
    "reason": (
        "Merge ready run branches into local main, close owned worktrees, "
        "and inventory the rest. Slides if Iterate continues."
    ),
}

_OPEN_ITEMS_REL = Path(".build-loop") / "closeout"


def _git(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _read_state(workdir: Path) -> dict[str, Any]:
    path = workdir / ".build-loop" / "state.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def ensure_deletion_point(raw: Any) -> dict[str, Any]:
    """Return a complete deletion-point record; missing fields take the default."""
    out = dict(DEFAULT_DELETION_POINT)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if value is not None:
                out[key] = value
    return out


def _inventory(path: Path) -> dict[str, Any]:
    try:
        return worktree_inventory.inventory(path)
    except Exception as exc:  # noqa: BLE001 — inventory must not raise
        return worktree_inventory.error_result(path, str(exc))


def _list_worktrees(workdir: Path) -> tuple[list[dict[str, str]], str | None]:
    result = _git(workdir, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return [], (result.stderr or result.stdout).strip() or "git worktree list failed"
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}

    def finish() -> None:
        if current.get("path"):
            rows.append(dict(current))
        current.clear()

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            finish()
            current["path"] = str(Path(line[len("worktree "):].strip()).resolve())
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current["branch"] = (
                ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
            )
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line == "bare":
            current["bare"] = "1"
        elif line == "":
            finish()
    finish()
    return rows, None


def _integrator(worktrees: list[dict[str, str]], target: str) -> Path | None:
    for row in worktrees:
        if row.get("bare"):
            continue
        if row.get("branch") == target:
            return Path(row["path"])
    return None


def _tracked_dirty(path: Path) -> bool:
    result = _git(path, "status", "--porcelain=v1", "--untracked-files=no")
    return result.returncode != 0 or bool(result.stdout.strip())


def _can_ff(workdir: Path, branch: str, target: str) -> bool:
    return _git(workdir, "merge-base", "--is-ancestor", target, branch).returncode == 0


def _already_merged(workdir: Path, branch: str, target: str) -> bool:
    return collapse_run._is_ancestor(workdir, branch, target) is True


def _merge_readiness(
    workdir: Path, branch: str, target: str
) -> tuple[str, dict[str, Any]]:
    if _already_merged(workdir, branch, target):
        return "already_merged", {}
    try:
        risk = merge_risk.score(workdir, branch, target)
    except Exception as exc:  # noqa: BLE001 — score is advisory to a blocker
        return "blocked", {"verdict": "score_failed", "error": str(exc)}
    verdict = str(risk.get("verdict") or "")
    if verdict in {
        "stale_base_evidence_invalid",
        "conflict_likely",
        "evidence_failing",
    }:
        return "blocked", risk
    if _can_ff(workdir, branch, target):
        return "ff", risk
    if verdict in {"mergeable_evidence_current", "behind_but_disjoint"}:
        return "merge", risk
    return "blocked", risk


def _safe_to_remove(inv: dict[str, Any]) -> bool:
    if inv.get("error"):
        return False
    if inv.get("tracked_changes") or inv.get("untracked"):
        return False
    return bool(inv.get("caches_only_claim_supported"))


def _open_item(
    *,
    kind: str,
    disposition: str,
    reason: str,
    branch: str | None = None,
    path: str | None = None,
    run_id: str | None = None,
    inventory: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": kind,
        "disposition": disposition,
        "reason": reason,
        "branch": branch,
        "path": path,
        "run_id": run_id,
    }
    if inventory is not None:
        row["inventory"] = {
            "characterization": inventory.get("characterization"),
            "caches_only_claim_supported": inventory.get("caches_only_claim_supported"),
            "counts": inventory.get("counts"),
            "non_reproducible_ignored": inventory.get("non_reproducible_ignored"),
        }
    if extra:
        row.update(extra)
    return row


def _merge_branch(
    integrator: Path,
    branch: str,
    *,
    ff_only: bool,
) -> tuple[bool, str | None]:
    args = ["merge"]
    if ff_only:
        args.append("--ff-only")
    else:
        args.extend(["--no-ff", "-m", f"merge {branch} (closeout_ready)"])
    args.append(branch)
    result = _git(integrator, *args)
    if result.returncode == 0:
        return True, None
    return False, (result.stderr or result.stdout).strip() or f"git merge failed ({result.returncode})"


def _collapse_this_run(
    workdir: Path,
    run_id: str,
    branch: str,
    path: str | None,
) -> dict[str, Any]:
    return collapse_run.collapse(
        workdir,
        run_id=run_id,
        branch=branch,
        strict=True,
        merged_only=True,
        owner_released=True,
        require_run_root=True,
        release_source="phase-d-closeout-ready",
        expected_path=path,
    )


def closeout_ready(
    workdir: Path | str,
    *,
    run_id: str | None = None,
    target: str = "main",
    owner_released: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    workdir = Path(workdir).resolve()
    state = _read_state(workdir)
    execution = state.get("execution") if isinstance(state.get("execution"), dict) else {}
    deletion_point = ensure_deletion_point(execution.get("deletion_point"))
    this_branch = str(
        execution.get("run_worktree_branch")
        or execution.get("branch")
        or ""
    ).strip() or None
    this_path = str(
        execution.get("run_worktree_path")
        or execution.get("worktree")
        or ""
    ).strip() or None
    this_run = str(
        run_id
        or execution.get("build_loop_id")
        or execution.get("run_id")
        or "latest"
    )

    result: dict[str, Any] = {
        "run_id": this_run,
        "target": target,
        "deletion_point": deletion_point,
        "owner_released": bool(owner_released),
        "dry_run": bool(dry_run or not owner_released),
        "merged": [],
        "closed": [],
        "open_items": [],
        "errors": [],
        "inventory_path": None,
    }

    worktrees, list_error = _list_worktrees(workdir)
    if list_error:
        result["errors"].append({"reason": list_error})
        return result

    integrator = _integrator(worktrees, target)
    act = bool(owner_released and not dry_run)

    if this_branch:
        inv = _inventory(Path(this_path)) if this_path else {}
        readiness, risk = _merge_readiness(workdir, this_branch, target)
        extra = {"merge_verdict": risk.get("verdict"), "readiness": readiness}
        if readiness == "already_merged":
            if act:
                finalized = _collapse_this_run(workdir, this_run, this_branch, this_path)
                if finalized.get("strict_success"):
                    result["closed"].append(
                        {
                            "branch": this_branch,
                            "path": this_path,
                            "run_id": this_run,
                            "receipt": finalized.get("receipt_path"),
                        }
                    )
                else:
                    result["errors"].append(
                        {
                            "branch": this_branch,
                            "reason": "strict finalizer did not complete",
                            "details": finalized.get("errors") or [],
                        }
                    )
                    result["open_items"].append(
                        _open_item(
                            kind="worktree",
                            disposition="blocked",
                            reason="already merged; collapse did not finish",
                            branch=this_branch,
                            path=this_path,
                            run_id=this_run,
                            inventory=inv or None,
                            extra=extra,
                        )
                    )
            else:
                result["open_items"].append(
                    _open_item(
                        kind="worktree",
                        disposition="ready",
                        reason="already merged; Phase D --owner-released will close it",
                        branch=this_branch,
                        path=this_path,
                        run_id=this_run,
                        inventory=inv or None,
                        extra=extra,
                    )
                )
        elif readiness in {"ff", "merge"}:
            if this_path and _tracked_dirty(Path(this_path)):
                result["open_items"].append(
                    _open_item(
                        kind="worktree",
                        disposition="blocked",
                        reason="run worktree has uncommitted tracked changes",
                        branch=this_branch,
                        path=this_path,
                        run_id=this_run,
                        inventory=inv or None,
                        extra=extra,
                    )
                )
            elif integrator is None:
                result["open_items"].append(
                    _open_item(
                        kind="worktree",
                        disposition="blocked",
                        reason=f"no integrator worktree is on {target}",
                        branch=this_branch,
                        path=this_path,
                        run_id=this_run,
                        inventory=inv or None,
                        extra=extra,
                    )
                )
            elif _tracked_dirty(integrator):
                result["open_items"].append(
                    _open_item(
                        kind="worktree",
                        disposition="blocked",
                        reason=f"integrator {integrator} has uncommitted tracked changes",
                        branch=this_branch,
                        path=this_path,
                        run_id=this_run,
                        inventory=inv or None,
                        extra=extra,
                    )
                )
            elif act:
                ok, err = _merge_branch(
                    integrator, this_branch, ff_only=(readiness == "ff")
                )
                if not ok:
                    result["errors"].append({"branch": this_branch, "reason": err})
                    result["open_items"].append(
                        _open_item(
                            kind="worktree",
                            disposition="blocked",
                            reason=err or "merge failed",
                            branch=this_branch,
                            path=this_path,
                            run_id=this_run,
                            inventory=inv or None,
                            extra=extra,
                        )
                    )
                else:
                    result["merged"].append(
                        {
                            "branch": this_branch,
                            "mode": readiness,
                            "run_id": this_run,
                        }
                    )
                    if this_path and not _safe_to_remove(inv):
                        result["open_items"].append(
                            _open_item(
                                kind="worktree",
                                disposition="preserve-only",
                                reason="merged; worktree kept because inventory is not caches-only",
                                branch=this_branch,
                                path=this_path,
                                run_id=this_run,
                                inventory=inv or None,
                                extra=extra,
                            )
                        )
                    else:
                        finalized = _collapse_this_run(
                            workdir, this_run, this_branch, this_path
                        )
                        if finalized.get("strict_success"):
                            result["closed"].append(
                                {
                                    "branch": this_branch,
                                    "path": this_path,
                                    "run_id": this_run,
                                    "receipt": finalized.get("receipt_path"),
                                }
                            )
                        else:
                            result["errors"].append(
                                {
                                    "branch": this_branch,
                                    "reason": "merged but collapse did not finish",
                                    "details": finalized.get("errors") or [],
                                }
                            )
                            result["open_items"].append(
                                _open_item(
                                    kind="worktree",
                                    disposition="blocked",
                                    reason="merged; collapse did not finish",
                                    branch=this_branch,
                                    path=this_path,
                                    run_id=this_run,
                                    inventory=inv or None,
                                    extra=extra,
                                )
                            )
            else:
                result["open_items"].append(
                    _open_item(
                        kind="worktree",
                        disposition="ready",
                        reason=f"{readiness} into {target}; Phase D --owner-released will merge and close",
                        branch=this_branch,
                        path=this_path,
                        run_id=this_run,
                        inventory=inv or None,
                        extra=extra,
                    )
                )
        else:
            result["open_items"].append(
                _open_item(
                    kind="worktree",
                    disposition="blocked",
                    reason=f"not merge-ready ({risk.get('verdict') or readiness})",
                    branch=this_branch,
                    path=this_path,
                    run_id=this_run,
                    inventory=inv or None,
                    extra=extra,
                )
            )

    leftover = reap_worktrees(
        workdir,
        min_age_hours=0.0,
        dry_run=True,
        act=False,
        owner_released=False,
    )
    if act:
        for row in leftover.candidates:
            path = row.get("path")
            if this_path and path and str(Path(str(path)).resolve()) == str(
                Path(this_path).resolve()
            ):
                continue
            if any(closed.get("path") == path for closed in result["closed"]):
                continue
            inv = row.get("inventory") or {}
            if not _safe_to_remove(inv):
                result["open_items"].append(
                    _open_item(
                        kind="worktree",
                        disposition="preserve-only",
                        reason="leftover merged run worktree kept; inventory is not caches-only",
                        branch=row.get("branch"),
                        path=path,
                        run_id=row.get("run_id"),
                        inventory=inv,
                    )
                )
                continue
            finalized = collapse_run.collapse(
                workdir,
                run_id=str(row.get("run_id") or "latest"),
                branch=str(row.get("branch")),
                strict=True,
                merged_only=True,
                owner_released=True,
                require_run_root=True,
                release_source="phase-d-closeout-ready-leftover",
                expected_path=path,
            )
            if finalized.get("strict_success"):
                result["closed"].append(
                    {
                        "branch": row.get("branch"),
                        "path": path,
                        "run_id": row.get("run_id"),
                        "receipt": finalized.get("receipt_path"),
                        "source": "leftover",
                    }
                )
            else:
                result["errors"].append(
                    {
                        "path": path,
                        "reason": "leftover collapse did not finish",
                        "details": finalized.get("errors") or [],
                    }
                )
        for row in leftover.errors:
            result["errors"].append(row)
        for row in leftover.skipped_unmerged:
            if this_path and str(Path(str(row.get("path"))).resolve()) == str(
                Path(this_path).resolve()
            ):
                continue
            result["open_items"].append(
                _open_item(
                    kind="worktree",
                    disposition="preserve-only",
                    reason="leftover run worktree is not merged into main",
                    branch=row.get("branch"),
                    path=row.get("path"),
                    run_id=row.get("run_id"),
                    inventory=row.get("inventory"),
                )
            )
        for row in leftover.skipped_unattributed:
            result["open_items"].append(
                _open_item(
                    kind="worktree",
                    disposition="preserve-only",
                    reason=str(row.get("reason") or "unattributed leftover worktree"),
                    branch=row.get("branch"),
                    path=row.get("path"),
                )
            )
        for row in leftover.skipped_active:
            if this_branch and row.get("branch") == this_branch:
                continue
            result["open_items"].append(
                _open_item(
                    kind="worktree",
                    disposition="preserve-only",
                    reason="worktree is still the active execution branch",
                    branch=row.get("branch"),
                    path=row.get("path"),
                )
            )
    else:
        for row in leftover.candidates:
            if this_path and str(Path(str(row.get("path"))).resolve()) == str(
                Path(this_path).resolve()
            ):
                continue
            result["open_items"].append(
                _open_item(
                    kind="worktree",
                    disposition="ready",
                    reason="leftover merged run worktree; Phase D will close it",
                    branch=row.get("branch"),
                    path=row.get("path"),
                    run_id=row.get("run_id"),
                    inventory=row.get("inventory"),
                )
            )
        for row in leftover.skipped_unmerged:
            if this_path and str(Path(str(row.get("path"))).resolve()) == str(
                Path(this_path).resolve()
            ):
                continue
            result["open_items"].append(
                _open_item(
                    kind="worktree",
                    disposition="preserve-only",
                    reason="leftover run worktree is not merged into main",
                    branch=row.get("branch"),
                    path=row.get("path"),
                    run_id=row.get("run_id"),
                    inventory=row.get("inventory"),
                )
            )

    seen_paths = {
        str(Path(str(item["path"])).resolve())
        for item in (*result["open_items"], *result["closed"])
        if item.get("path")
    }
    run_root = str((workdir / ".build-loop" / "worktrees").resolve())
    for row in worktrees:
        path = row.get("path")
        if not path:
            continue
        resolved = str(Path(path).resolve())
        if resolved == str(workdir.resolve()):
            continue
        if integrator is not None and resolved == str(integrator.resolve()):
            continue
        if resolved in seen_paths:
            continue
        if this_path and resolved == str(Path(this_path).resolve()):
            continue
        if resolved.startswith(run_root) and Path(resolved).name.startswith("run-"):
            continue
        reason = (
            "build-loop worktree without run-* attribution"
            if resolved.startswith(run_root)
            else "not a build-loop run worktree"
        )
        result["open_items"].append(
            _open_item(
                kind="worktree",
                disposition="preserve-only",
                reason=reason,
                branch=row.get("branch"),
                path=resolved,
                inventory=_inventory(Path(resolved)),
            )
        )

    inventory_path = workdir / _OPEN_ITEMS_REL / f"{this_run}-open-items.json"
    _write_json(inventory_path, result)
    result["inventory_path"] = str(inventory_path)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workdir", default=".", help="Repository root")
    parser.add_argument("--run-id", help="Run id (default: state.execution.build_loop_id)")
    parser.add_argument("--target", default="main", help="Integration branch (default: main)")
    parser.add_argument(
        "--owner-released",
        action="store_true",
        help="Phase D integrator authority to merge ready work and close owned worktrees",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inventory only, even with --owner-released",
    )
    parser.add_argument("--json", dest="json_output", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = closeout_ready(
        Path(args.workdir).resolve(),
        run_id=args.run_id,
        target=args.target,
        owner_released=args.owner_released,
        dry_run=args.dry_run,
    )
    tag = " [REPORT-ONLY]" if result["dry_run"] else " [ACT]"
    print(
        f"closeout_ready{tag} "
        f"merged={len(result['merged'])} "
        f"closed={len(result['closed'])} "
        f"open_items={len(result['open_items'])} "
        f"errors={len(result['errors'])}",
        file=sys.stderr,
    )
    for item in result["open_items"]:
        print(
            f"  · {item['disposition']}: {item.get('branch') or item.get('path')} — {item['reason']}",
            file=sys.stderr,
        )
    if args.json_output:
        print(json.dumps(result, indent=2))
    if result["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
