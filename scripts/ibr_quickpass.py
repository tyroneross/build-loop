#!/usr/bin/env python3
"""Run the project's existing IBR test suite (a "quick pass") for build-loop Review Sub-step B.

Discovers `**/*.ibr-test.json` (skipping node_modules, .build-loop, .ibr, _draft),
runs each via `ibr test --file <path> --json`, parallelizes up to 4 (matching the
hard cap from CLAUDE.md §Sub-Agents), and reports pass/fail plus untested-surface
hints derived from `git diff` against UI files that have no corresponding test.

Output: pure JSON to stdout. Exit 0 if all ran (even with failures — fails are in
the payload). Exit 1 if `ibr` CLI is unavailable or no test files exist (caller
decides — Sub-step B falls through to scanners). Exit 2 only on usage error.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SKIP_DIRS = {"node_modules", ".build-loop", ".ibr", "_draft", ".git", "dist", "build"}
UI_EXT = {".tsx", ".jsx", ".vue", ".svelte", ".swift", ".kt"}
MAX_PARALLEL = 4


def discover_tests(workdir: Path) -> list[Path]:
    found: list[Path] = []
    for path in workdir.rglob("*.ibr-test.json"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return sorted(found)


def changed_ui_files(workdir: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if proc.returncode != 0:
        return []
    return [
        line.strip()
        for line in (proc.stdout or "").splitlines()
        if line.strip() and Path(line.strip()).suffix in UI_EXT
    ]


def covered_targets(tests: list[Path]) -> set[str]:
    """Extract the `target`/`url`/`route` field from each test for coverage matching."""
    out: set[str] = set()
    for path in tests:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("target", "url", "route", "path"):
            val = data.get(key)
            if isinstance(val, str):
                out.add(val)
    return out


def run_one(test_path: Path, workdir: Path, output_dir: Path) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            [
                "ibr",
                "test",
                "--file",
                str(test_path),
                "--output-dir",
                str(output_dir),
                "--json",
                "--headless",
            ],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {
            "test": str(test_path.relative_to(workdir)),
            "status": "fail",
            "reason": "timeout",
            "duration_s": round(time.time() - started, 2),
        }

    duration = round(time.time() - started, 2)
    payload: dict[str, Any] = {"raw_stdout_tail": (proc.stdout or "")[-400:]}
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        pass

    status = "pass" if proc.returncode == 0 else "fail"
    return {
        "test": str(test_path.relative_to(workdir)),
        "status": status,
        "exit_code": proc.returncode,
        "duration_s": duration,
        "payload": payload,
        "stderr_tail": (proc.stderr or "")[-400:] if status == "fail" else "",
    }


def filter_changed_scope(tests: list[Path], workdir: Path) -> tuple[list[Path], list[str]]:
    """Keep tests whose JSON references any changed file. Skip the rest with a note."""
    changed = set(changed_ui_files(workdir))
    if not changed:
        return tests, []
    keep: list[Path] = []
    skipped: list[str] = []
    for path in tests:
        try:
            body = path.read_text()
        except OSError:
            continue
        if any(c in body for c in changed):
            keep.append(path)
        else:
            skipped.append(str(path.relative_to(workdir)))
    if not keep:
        return tests, []
    return keep, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="Run project IBR test suite for build-loop quick pass.")
    ap.add_argument("--workdir", required=True, help="Project root")
    ap.add_argument(
        "--scope",
        choices=("changed", "all"),
        default="changed",
        help="changed=run only tests whose target appears in git diff; all=every test",
    )
    args = ap.parse_args()

    workdir = Path(args.workdir).resolve()
    if not workdir.is_dir():
        print(json.dumps({"error": "workdir not a directory", "path": str(workdir)}))
        return 2

    if shutil.which("ibr") is None:
        print(json.dumps({"status": "ibr_unavailable", "suggested_action": "fall_through_to_scanners"}))
        return 1

    tests = discover_tests(workdir)
    if not tests:
        out = {
            "status": "no_tests",
            "suggested_action": "generate_for_changed_routes",
            "untested_surfaces": changed_ui_files(workdir),
        }
        print(json.dumps(out, indent=2))
        return 1

    if args.scope == "changed":
        tests, skipped = filter_changed_scope(tests, workdir)
    else:
        skipped = []

    output_dir = workdir / ".ibr" / "test-results"
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        for res in pool.map(lambda t: run_one(t, workdir, output_dir), tests):
            results.append(res)

    pass_results = [r for r in results if r["status"] == "pass"]
    fail_results = [r for r in results if r["status"] != "pass"]

    covered = covered_targets(tests)
    changed = changed_ui_files(workdir)
    untested = sorted(c for c in changed if not any(c in t for t in covered))

    summary_path = workdir / ".build-loop" / "ibr-quickpass.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "status": "ran",
        "ran": len(results),
        "pass": len(pass_results),
        "fail": fail_results,
        "skipped_for_scope": skipped,
        "untested_surfaces": untested,
        "duration_s": round(time.time() - started, 2),
    }
    try:
        summary_path.write_text(json.dumps(out, indent=2))
    except OSError:
        pass
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
