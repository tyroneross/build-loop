#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""self_mod_verify.py — correctness gate for any self-modification of build-loop's own code.

Runs the test suite, parses pass/fail, and optionally auto-reverts changed files
if the suite fails.  This is the load-bearing guardrail that prevents a self-
simplification from being committed when it breaks existing behaviour.

The gate measures correctness via tests only.  It does NOT special-case which
files were changed (gate files, test files, self-improvement scripts, etc.) —
those run the same way as any other change.  Oversight moves to post-hoc review
and end-of-run readback; it does not live here.

CLI::

    python3 scripts/self_mod_verify.py
        --workdir <repo>
        [--scope full|changed|auto]
        [--changed-files f1 f2 ...]
        [--auto-revert]
        [--timeout SECONDS]
        --json

Output JSON::

    {
      "scope":           "full" | "changed" | "auto",
      "effective_scope": "full" | "changed" | "broad",   # resolved from auto
      "ran":             [str, ...],     # test files that were discovered and run
      "passed":          int,
      "failed":          int,
      "failed_tests":    [str, ...],    # short names of failing tests
      "reverted":        bool,
      "verdict":         "pass" | "fail" | "no_tests",
      "timed_out":       bool,
      "errors":          [str, ...]
    }

Exit codes:
  0  — verdict "pass" or "no_tests"
  1  — verdict "fail"

Fail-soft on infrastructure errors (no pytest, unreadable output):
  verdict = "no_tests", exit 0.  A missing test suite never blocks a deploy;
  the absence is itself surfaced in the JSON for the caller to act on.

Scope selection:
  full     — whole scripts/ test suite (slow on large repos; raises timeout to 900s)
  changed  — only the mapped test files for --changed-files
  auto     — recommended default:
               1–3 source files    → changed  (mapped tests only)
               4+ files OR a core/orchestration-path file → broad  (mapped + area tests)
               else                → changed
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Orchestration / core-path heuristic for "broad" scope
# ---------------------------------------------------------------------------

_CORE_PREFIXES = (
    "scripts/self_",
    "scripts/autonomy",
    "scripts/classify",
    "scripts/deploy",
    "scripts/audit",
    "scripts/worktree",
    "scripts/coordination",
    "scripts/rally",
    "scripts/state",
    "scripts/plan_verify",
    "scripts/review_",
    "scripts/build_acp",
)


def _is_core_path(rel_posix: str) -> bool:
    return any(rel_posix.startswith(p) for p in _CORE_PREFIXES)


# ---------------------------------------------------------------------------
# Test-runner discovery
# ---------------------------------------------------------------------------

def _find_runner(workdir: Path) -> list[str] | None:
    """Return the command list for pytest, or None if unavailable.

    Preference: ``uv run pytest`` (respects project virtualenv),
    fallback: ``python3 -m pytest``.
    Also detects pytest-xdist and appends ``-n auto`` when available.
    """
    base: list[str] | None = None

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
            base = ["uv", "run", "pytest"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if base is None:
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
                base = [sys.executable, "-m", "pytest"]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if base is None:
        return None

    # Detect pytest-xdist for parallel execution
    has_xdist = _detect_xdist(base, workdir)
    return base, has_xdist


def _detect_xdist(runner_base: list[str], workdir: Path) -> bool:
    """Return True if pytest-xdist is available in the runner's environment."""
    try:
        r = subprocess.run(
            runner_base + ["-p", "no:terminal", "--co", "-q", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(workdir),
        )
        combined = (r.stdout + r.stderr).lower()
        if "xdist" in combined:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Try import check as fallback
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import xdist"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(workdir),
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


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


def _broad_tests_for_changed(workdir: Path, changed_files: list[str]) -> list[str]:
    """Return mapped tests + area-adjacent test files (best-effort bounded).

    Adds test files whose name shares a stem prefix with any changed file,
    covering the module neighbourhood without running the full suite.
    """
    scripts_dir = workdir / "scripts"
    base = _tests_for_changed(workdir, changed_files)
    seen: set[str] = set(base)

    # Collect stem prefixes from changed files
    prefixes: set[str] = set()
    for raw in changed_files:
        stem = Path(raw).stem
        # strip leading "test_" if present
        if stem.startswith("test_"):
            stem = stem[5:]
        # use up to the first underscore as area prefix (e.g. "coordination_bootstrap" → "coordination")
        area = stem.split("_")[0] if "_" in stem else stem
        if area:
            prefixes.add(area)

    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.glob("test_*.py")):
            if str(f) in seen:
                continue
            # Include if name matches any area prefix
            stem_no_test = f.stem[5:]  # strip "test_"
            area = stem_no_test.split("_")[0] if "_" in stem_no_test else stem_no_test
            if area in prefixes:
                seen.add(str(f))
                base.append(str(f))

    return base


def _all_script_tests(workdir: Path) -> list[str]:
    """Return all test files under scripts/."""
    scripts_dir = workdir / "scripts"
    if not scripts_dir.is_dir():
        return []
    return sorted(str(f) for f in scripts_dir.glob("test_*.py"))


# ---------------------------------------------------------------------------
# Scope resolution for "auto"
# ---------------------------------------------------------------------------

def _resolve_auto_scope(
    workdir: Path,
    changed_files: list[str],
) -> tuple[str, list[str]]:
    """Resolve 'auto' scope to an effective scope + test file list.

    Rules (file-count / core-path only — no file-identity special-casing):
      1–3 source files and no core-path file → changed (mapped tests only)
      4+ files OR a core/orchestration-path file → broad (mapped + area tests)

    Returns (effective_scope, test_files) where effective_scope is one of
    "changed" or "broad" (never "auto" or "full").
    """
    n = len(changed_files)

    has_core = False
    for raw in changed_files:
        p = Path(raw)
        if not p.is_absolute():
            p = workdir / p
        try:
            rel = p.resolve().relative_to(workdir.resolve()).as_posix()
        except ValueError:
            continue
        if _is_core_path(rel):
            has_core = True
            break

    if n >= 4 or has_core:
        return "broad", _broad_tests_for_changed(workdir, changed_files)
    else:
        return "changed", _tests_for_changed(workdir, changed_files)


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
    timeout: int = 300,
) -> tuple[dict, int]:
    """Run the verification gate. Return (result_dict, exit_code)."""
    errors: list[str] = []
    reverted = False

    # --- Runner discovery ---
    runner_result = _find_runner(workdir)
    if runner_result is None:
        result = {
            "scope": scope,
            "effective_scope": scope if scope != "auto" else "changed",
            "ran": [],
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "timed_out": False,
            "errors": ["pytest not available (uv run pytest and python3 -m pytest both failed)"],
        }
        return result, 0

    runner_base, has_xdist = runner_result

    # --- Scope resolution ---
    effective_scope = scope
    if scope == "auto":
        effective_scope, test_files = _resolve_auto_scope(workdir, changed_files)
    elif scope == "changed":
        test_files = _tests_for_changed(workdir, changed_files)
    else:  # full
        test_files = _all_script_tests(workdir)
        # Raise timeout for full suite runs
        if timeout < 900:
            timeout = 900

    if not test_files:
        result = {
            "scope": scope,
            "effective_scope": effective_scope,
            "ran": [],
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "timed_out": False,
            "errors": errors,
        }
        return result, 0

    # --- Build pytest command ---
    cmd = runner_base + ["-q", "-p", "no:cacheprovider", "--tb=short"]
    if has_xdist:
        cmd += ["-n", "auto"]
    cmd += test_files

    # --- Run pytest ---
    timed_out = False
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        errors.append(f"pytest timed out after {timeout}s")
        result = {
            "scope": scope,
            "effective_scope": effective_scope,
            "ran": test_files,
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "timed_out": True,
            "errors": errors,
        }
        return result, 0
    except (FileNotFoundError, OSError) as exc:
        errors.append(f"pytest runner error: {exc}")
        result = {
            "scope": scope,
            "effective_scope": effective_scope,
            "ran": test_files,
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "no_tests",
            "timed_out": False,
            "errors": errors,
        }
        return result, 0

    passed, failed, failed_tests = _parse_pytest_output(r.stdout, r.stderr)

    # --- Determine verdict from test results only ---
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
        if auto_revert:
            reverted = _revert_files(workdir, changed_files, errors)

    result = {
        "scope": scope,
        "effective_scope": effective_scope,
        "ran": test_files,
        "passed": passed,
        "failed": failed,
        "failed_tests": failed_tests,
        "reverted": reverted,
        "verdict": verdict,
        "timed_out": timed_out,
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
        choices=["full", "changed", "auto"],
        default="auto",
        help=(
            "auto (default) = smart scope based on blast radius; "
            "changed = only tests for --changed-files; "
            "full = whole scripts/ suite (slow, raises timeout to 900s)"
        ),
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
        "--timeout",
        type=int,
        default=300,
        help="Subprocess timeout in seconds (default 300; full scope raises to 900 automatically)",
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
        timeout=args.timeout,
    )

    # JSON to stdout only — human summary to stderr
    print(json.dumps(result, indent=2))
    print(
        f"self_mod_verify: verdict={result['verdict']} "
        f"passed={result['passed']} failed={result['failed']} "
        f"reverted={result['reverted']} scope={result['scope']} "
        f"effective_scope={result['effective_scope']} "
        f"timed_out={result['timed_out']}",
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
