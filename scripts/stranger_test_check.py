#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""stranger_test_check.py — a gated-surface verdict must carry its stranger test.

Why (incident 2026-09-25): an iOS owner/admin panel gated by client-side
trust-on-first-use shipped because the security reviewer and the independent
auditor both checked "an existing owner cannot be displaced" and never asked
what a stranger's fresh install reaches. The reviewer prompts now require a
`stranger_test` object. This module makes the requirement checkable: a verdict
recorded on a change that touches an auth/admin/owner/debug/gated surface does
not count as review-complete unless `stranger_test.answer` and
`stranger_test.in_release` are filled in.

Scope rules:
  - `security-reviewer` verdicts: stranger_test is REQUIRED on a gated change.
  - `independent-auditor` verdicts: checked only when the entry records a
    `stranger_test` key (an older verdict that never carried one is not
    retroactively disqualified; the security-reviewer carries the requirement).
  - A change is "gated" when its ADDED lines match the same detector the commit
    packet uses (`audit_before_commit._find_gated_surface_files`, markers from
    `release_surface_scan.GATED_SURFACE_PATTERNS`). One source of truth.

Fail-open by design: an unresolvable range, a missing file, or an import error
yields `gated: None` (undetermined), never an exception and never a block of
unrelated work.

CLI::

    python3 scripts/stranger_test_check.py --workdir . --run-id <id> [--json]

Exit 0 when every applicable verdict is complete (or nothing applies), 1 when a
gated verdict is missing its stranger test.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

SECURITY_REVIEWER_MARKER = "security-reviewer"
AUDITOR_MARKER = "independent-auditor"
JUDGE_DECISIONS_RELPATH = Path(".build-loop") / "judge-decisions.json"


def stranger_test_rejection(entry: Any, *, required: bool) -> str | None:
    """Why this verdict's stranger test is incomplete, or None when it is fine.

    `required=False` checks only an object that is present (auditor rule).
    """
    if not isinstance(entry, dict):
        return None
    if "stranger_test" not in entry:
        if not required:
            return None
        return (
            "the change touches an auth/admin/owner/debug/gated surface and the "
            "verdict records no `stranger_test`; answer what a fresh install with "
            "the stranger's own account reaches, and whether it compiles into Release"
        )
    st = entry.get("stranger_test")
    if not isinstance(st, dict):
        return f"`stranger_test` is {type(st).__name__}, not an object"
    missing = [k for k in ("answer", "in_release") if not str(st.get(k) or "").strip()]
    if missing:
        return (
            "`stranger_test` has an empty " + " and ".join(f"`{k}`" for k in missing)
            + " on a gated-surface change; a blank answer is not a stranger test"
        )
    return None


def _git(workdir: Path, args: list[str], timeout: int = 15) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(workdir), capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def gated_files_for_diff(files: list[str], diff_body: str) -> list[str]:
    import audit_before_commit as abc  # deferred: heavy module, hook-path safe

    return abc._find_gated_surface_files(files, abc._diff_by_file(diff_body))


def gated_files_for_range(workdir: Path, diff_range: str) -> list[str] | None:
    """Gated files in `git diff <range>`; None when the range does not resolve."""
    rng = str(diff_range or "").strip()
    if not rng or rng == "unknown":
        return None
    body = _git(workdir, ["diff", "--no-color", rng])
    names = _git(workdir, ["diff", "--name-only", rng])
    if body is None or names is None:
        return None
    try:
        return gated_files_for_diff([n for n in names.splitlines() if n], body)
    except Exception:  # noqa: BLE001 — fail open
        return None


def gated_files_for_paths(workdir: Path, paths: list[str]) -> list[str] | None:
    """Fallback when no range resolves: treat each file's current text as added."""
    if not paths:
        return None
    parts: list[str] = []
    seen: list[str] = []
    for rel in paths:
        p = workdir / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen.append(rel)
        parts.append(
            f"diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n@@ -0,0 +1 @@\n"
            + "".join("+" + ln + "\n" for ln in text.splitlines())
        )
    if not seen:
        return None
    try:
        return gated_files_for_diff(seen, "".join(parts))
    except Exception:  # noqa: BLE001
        return None


def filter_decisions(
    decisions: list[Any], gated: bool, marker: str | None = None
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Drop verdicts whose stranger test is incomplete on a gated change.

    Returns (kept, rejections). Non-gated changes pass through untouched.
    `marker` restricts which judges are examined (others are always kept).
    """
    if not gated:
        return list(decisions), []
    kept: list[Any] = []
    rejections: list[dict[str, Any]] = []
    for item in decisions:
        jid = str(item.get("judge_id", "")) if isinstance(item, dict) else ""
        if marker and marker not in jid:
            kept.append(item)
            continue
        if SECURITY_REVIEWER_MARKER in jid:
            why = stranger_test_rejection(item, required=True)
        elif AUDITOR_MARKER in jid:
            why = stranger_test_rejection(item, required=False)
        else:
            why = None
        if why:
            rejections.append({
                "judge_id": item.get("judge_id"),
                "run_id": item.get("run_id"),
                "diff_range": item.get("diff_range"),
                "reason": why,
            })
        else:
            kept.append(item)
    return kept, rejections


def _load_decisions(workdir: Path, run_id: str) -> list[dict[str, Any]]:
    try:
        data = json.loads((workdir / JUDGE_DECISIONS_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = []
    if isinstance(data, dict):
        data = data.get("decisions") or data.get("judge_decisions") or []
    out = [d for d in data if isinstance(d, dict) and str(d.get("run_id") or "") == run_id] \
        if isinstance(data, list) else []
    record = _run_record(workdir, run_id)
    for d in (record or {}).get("judge_decisions") or []:
        if isinstance(d, dict) and d not in out:
            out.append(d)
    return out


def _run_record(workdir: Path, run_id: str) -> dict[str, Any] | None:
    try:
        state = json.loads((workdir / ".build-loop" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for run in reversed(state.get("runs") or []):
        if isinstance(run, dict) and run.get("run_id") == run_id:
            return run
    return None


def check_run(workdir: Path, run_id: str) -> dict[str, Any]:
    """Which of this run's security/auditor verdicts lack a required stranger test."""
    workdir = Path(workdir)
    result: dict[str, Any] = {"run_id": run_id, "status": "complete", "checked": 0,
                              "rejections": [], "undetermined": []}
    if not run_id:
        result["status"] = "skipped"
        return result
    try:
        decisions = [
            d for d in _load_decisions(workdir, run_id)
            if SECURITY_REVIEWER_MARKER in str(d.get("judge_id", ""))
            or AUDITOR_MARKER in str(d.get("judge_id", ""))
        ]
        record = _run_record(workdir, run_id) or {}
        files_touched = [f for f in record.get("filesTouched") or [] if isinstance(f, str)]
        cache: dict[str, list[str] | None] = {}
        for d in decisions:
            rng = str(d.get("diff_range") or "")
            if rng not in cache:
                gated = gated_files_for_range(workdir, rng)
                if gated is None:
                    gated = gated_files_for_paths(workdir, files_touched)
                cache[rng] = gated
            gated = cache[rng]
            result["checked"] += 1
            if gated is None:
                result["undetermined"].append({"judge_id": d.get("judge_id"), "diff_range": rng})
                continue
            _, rej = filter_decisions([d], bool(gated))
            for r in rej:
                r["gated_files"] = gated[:10]
            result["rejections"].extend(rej)
    except Exception as exc:  # noqa: BLE001 — never crash a run close
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    if result["rejections"]:
        result["status"] = "incomplete"
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = check_run(Path(args.workdir).resolve(), args.run_id)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"stranger_test_check: {result['status']} ({result['checked']} verdict(s) checked)")
        for r in result["rejections"]:
            print(f"  {r['judge_id']} ({r.get('diff_range') or 'no range'}): {r['reason']}")
    return 1 if result["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
