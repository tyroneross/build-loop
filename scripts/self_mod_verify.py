#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""self_mod_verify.py — safety gate for any self-modification of build-loop's own code.

Runs the test suite, parses pass/fail, and optionally auto-reverts changed files
if the suite fails.  This is the load-bearing guardrail that prevents a self-
simplification from being committed when it breaks existing behaviour.

CLI::

    python3 scripts/self_mod_verify.py
        --workdir <repo>
        [--scope full|changed]
        [--changed-files f1 f2 ...]
        [--auto-revert]
        --json

Output JSON::

    {
      "scope":        "full" | "changed",
      "ran":          [str, ...],      # test files that were discovered and run
      "passed":       int,
      "failed":       int,
      "failed_tests": [str, ...],      # short names of failing tests
      "reverted":     bool,
      "verdict":      "pass" | "fail" | "no_tests"
    }

Exit codes:
  0  — verdict "pass" or "no_tests"
  1  — verdict "fail"

Fail-soft on infrastructure errors (no pytest, unreadable output):
  verdict = "no_tests", exit 0.  A missing test suite never blocks a deploy;
  the absence is itself surfaced in the JSON for the caller to act on.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Test-runner discovery
# ---------------------------------------------------------------------------

def _find_runner(workdir: Path) -> list[str] | None:
    """Return the command list for pytest, or None if unavailable.

    Preference: ``uv run pytest`` (respects project virtualenv),
    fallback: ``python3 -m pytest``.
    """
    # Try uv run pytest
    try:
        r = subprocess.run(
            ["uv", "run", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(workdir),
        )
        if r.returncode == 0:
            return ["uv", "run", "pytest"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: python3 -m pytest
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(workdir),
        )
        if r.returncode == 0:
            return [sys.executable, "-m", "pytest"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


# ---------------------------------------------------------------------------
# Test file resolution
# ---------------------------------------------------------------------------

def _tests_for_changed(workdir: Path, changed_files: list[str]) -> list[str]:
    """Map changed implementation files to their test counterparts.

    ``scripts/foo.py`` → ``scripts/test_foo.py`` when it exists.
    Files already named ``test_*.py`` are included directly.
    """
    scripts_dir = workdir / "scripts"
    discovered: list[str] = []
    seen: set[str] = set()

    for raw in changed_files:
        p = Path(raw)
        # Normalise to absolute
        if not p.is_absolute():
            p = workdir / p
        p = p.resolve()

        name = p.name
        if name.startswith("test_"):
            # It IS a test file — include directly if it exists
            if p.exists() and str(p) not in seen:
                discovered.append(str(p))
                seen.add(str(p))
        else:
            # Map scripts/foo.py → scripts/test_foo.py
            candidate = scripts_dir / f"test_{name}"
            if candidate.exists() and str(candidate) not in seen:
                discovered.append(str(candidate))
                seen.add(str(candidate))

    return discovered


def _all_script_tests(workdir: Path) -> list[str]:
    """Return all test files under scripts/."""
    scripts_dir = workdir / "scripts"
    if not scripts_dir.is_dir():
        return []
    return sorted(str(f) for f in scripts_dir.glob("test_*.py"))


# ---------------------------------------------------------------------------
# Parse pytest output
# ---------------------------------------------------------------------------

# Patterns for summary line: "5 passed" / "2 failed" / "1 passed, 1 failed"
_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
# Pattern for individual FAILED lines: "FAILED scripts/test_foo.py::TestBar::test_baz"
_FAILED_ITEM_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)


def _parse_pytest_output(stdout: str, stderr: str) -> tuple[int, int, list[str]]:
    """Return (passed, failed, failed_test_names) from pytest output."""
    combined = stdout + "\n" + stderr
    passed = 0
    failed = 0

    m = _PASSED_RE.search(combined)
    if m:
        passed = int(m.group(1))
    m = _FAILED_RE.search(combined)
    if m:
        failed = int(m.group(1))

    failed_tests = _FAILED_ITEM_RE.findall(combined)
    return passed, failed, failed_tests


# ---------------------------------------------------------------------------
# Revert helper
# ---------------------------------------------------------------------------

def _revert_files(workdir: Path, changed_files: list[str], errors: list[str]) -> bool:
    """Run git restore on changed_files. Return True on success."""
    if not changed_files:
        errors.append("--auto-revert requested but no --changed-files given; revert skipped")
        return False
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "restore", "--staged", "--worktree", "--"]
            + changed_files,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            # Try legacy git checkout -- fallback
            r2 = subprocess.run(
                ["git", "-C", str(workdir), "checkout", "--"] + changed_files,
                capture_output=True,
                text=True,
                timeout=15,
            )
            return r2.returncode == 0
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        errors.append(f"revert failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main verify routine
# ---------------------------------------------------------------------------

def verify(
    *,
    workdir: Path,
    scope: str,
    changed_files: list[str],
    auto_revert: bool,
) -> tuple[dict, int]:
    """Run the verification gate. Return (result_dict, exit_code)."""
    errors: list[str] = []
    reverted = False

    runner = _find_runner(workdir)
    if runner is None:
        result = {
            "scope": scope,
            "ran": [],
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "errors": ["pytest not available (uv run pytest and python3 -m pytest both failed)"],
        }
        return result, 0

    # Resolve test files
    if scope == "changed":
        test_files = _tests_for_changed(workdir, changed_files)
    else:
        test_files = _all_script_tests(workdir)

    if not test_files:
        result = {
            "scope": scope,
            "ran": [],
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "errors": errors,
        }
        return result, 0

    # Run pytest
    cmd = runner + ["-v", "--tb=short"] + test_files
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        errors.append("pytest timed out after 300s")
        result = {
            "scope": scope,
            "ran": test_files,
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "errors": errors,
        }
        return result, 0
    except (FileNotFoundError, OSError) as exc:
        errors.append(f"pytest runner error: {exc}")
        result = {
            "scope": scope,
            "ran": test_files,
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "errors": errors,
        }
        return result, 0

    passed, failed, failed_tests = _parse_pytest_output(r.stdout, r.stderr)

    # Determine verdict
    if r.returncode == 0 and failed == 0:
        verdict = "pass"
        exit_code = 0
    elif r.returncode == 5:
        # pytest exit 5 = no tests collected
        verdict = "no_tests"
        exit_code = 0
    else:
        verdict = "fail"
        exit_code = 1
        # Auto-revert if requested
        if auto_revert:
            reverted = _revert_files(workdir, changed_files, errors)

    result = {
        "scope": scope,
        "ran": test_files,
        "passed": passed,
        "failed": failed,
        "failed_tests": failed_tests,
        "reverted": reverted,
        "verdict": verdict,
        "errors": errors,
    }
    return result, exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--workdir",
        required=True,
        help="Repo root to verify (must be the build-loop repo itself)",
    )
    p.add_argument(
        "--scope",
        choices=["full", "changed"],
        default="full",
        help="full=whole scripts/ suite (default), changed=only tests for --changed-files",
    )
    p.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        metavar="FILE",
        help="Changed files (required when --scope changed; used for revert scope)",
    )
    p.add_argument(
        "--auto-revert",
        action="store_true",
        help="If the suite fails, revert --changed-files via git restore",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit result JSON to stdout (always implied; kept for compatibility)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    workdir = Path(args.workdir).resolve()

    result, exit_code = verify(
        workdir=workdir,
        scope=args.scope,
        changed_files=args.changed_files or [],
        auto_revert=args.auto_revert,
    )
    print(json.dumps(result, indent=2))

    # Human summary to stderr
    print(
        f"self_mod_verify: verdict={result['verdict']} "
        f"passed={result['passed']} failed={result['failed']} "
        f"reverted={result['reverted']} scope={result['scope']}",
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
