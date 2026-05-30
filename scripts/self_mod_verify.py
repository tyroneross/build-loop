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
        [--scope full|changed|auto]
        [--changed-files f1 f2 ...]
        [--auto-revert]
        [--timeout SECONDS]
        --json

Output JSON::

    {
      "scope":              "full" | "changed" | "auto",
      "effective_scope":    "full" | "changed" | "broad",   # resolved from auto
      "ran":                [str, ...],     # test files that were discovered and run
      "passed":             int,
      "failed":             int,
      "failed_tests":       [str, ...],    # short names of failing tests
      "reverted":           bool,
      "verdict":            "pass" | "fail" | "no_tests" | "needs_human",
      "meta_modification":  bool,          # true when gate/test files were changed
      "meta_files":         [str, ...],    # which changed files triggered meta flag
      "timed_out":          bool,
      "errors":             [str, ...]
    }

Exit codes:
  0  — verdict "pass" or "no_tests"
  1  — verdict "fail" or "needs_human"

Fail-soft on infrastructure errors (no pytest, unreadable output):
  verdict = "no_tests", exit 0.  A missing test suite never blocks a deploy;
  the absence is itself surfaced in the JSON for the caller to act on.

Scope selection:
  full     — whole scripts/ test suite (slow on large repos; raises timeout to 900s)
  changed  — only the mapped test files for --changed-files
  auto     — recommended default:
               meta files present  → needs_human  (runs mapped tests for info)
               1–3 source files    → changed  (mapped tests only)
               4+ files OR core paths present → broad  (mapped + area tests)

Meta-modification detection:
  Any change to the gate itself, the test files that gate the gate, or the
  self-improvement/autonomy control surface triggers verdict=needs_human
  regardless of whether tests pass.  This closes the reward-hacking hole where
  a self-mod edits its own tests to fabricate a green signal.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Meta-path definitions (relative to workdir/scripts, or repo-root globs)
# ---------------------------------------------------------------------------

# Patterns relative to the *repo root* (unix-style, forward slashes).
# fnmatch is applied after normalising the changed-file path to a relative
# forward-slash string.
_META_PATTERNS: list[str] = [
    # The gate itself
    "scripts/self_mod_verify.py",
    # Test files (editing tests that gate you = fabricating green signal)
    "scripts/test_*.py",
    # Classifier and autonomy control surfaces
    "scripts/classify_action.py",
    "scripts/autonomy_gate.py",
    "scripts/self_review.py",
    # Self-improvement loop scripts
    "scripts/self_review_run.sh",
    "scripts/install_self_review.py",
    # Permission / auth-adjacent files
    "scripts/deployment_policy.py",
    "scripts/audit_before_commit.py",
]


def classify_self_mod(changed_files: list[str], workdir: Path | None = None) -> dict:
    """Classify a list of changed files for meta-modification risk.

    Args:
        changed_files: paths as strings (absolute or relative to workdir).
        workdir: repo root used to normalise relative paths.  Defaults to cwd.

    Returns:
        {
          "meta_modification": bool,
          "meta_files": [str, ...],   # subset of changed_files that matched
          "reason": str
        }
    """
    if workdir is None:
        workdir = Path.cwd()

    meta_files: list[str] = []

    for raw in changed_files:
        p = Path(raw)
        if not p.is_absolute():
            p = workdir / p
        try:
            rel = p.resolve().relative_to(workdir.resolve())
        except ValueError:
            # File outside workdir — can't match patterns; treat as non-meta
            continue
        # Normalise to forward-slash for fnmatch
        rel_str = rel.as_posix()
        for pattern in _META_PATTERNS:
            if fnmatch.fnmatch(rel_str, pattern):
                meta_files.append(raw)
                break  # one match is enough per file

    if meta_files:
        return {
            "meta_modification": True,
            "meta_files": meta_files,
            "reason": (
                f"Changed files touch the gate, test suite, or self-improvement "
                f"control surface: {meta_files}.  Human review required — "
                f"auto-apply is blocked regardless of test outcome."
            ),
        }
    return {
        "meta_modification": False,
        "meta_files": [],
        "reason": "No meta-paths detected in changed files.",
    }


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
    meta_info: dict,
) -> tuple[str, list[str]]:
    """Resolve 'auto' scope to an effective scope + test file list.

    Returns (effective_scope, test_files) where effective_scope is one of
    "changed", "broad", or "full" (never "auto").
    """
    if meta_info["meta_modification"]:
        # Run mapped tests for information, but verdict will be needs_human anyway
        test_files = _tests_for_changed(workdir, changed_files)
        return "changed", test_files

    # Count non-meta source files
    n = len(changed_files)

    # Check for core-path files
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

    # --- Meta-modification check ---
    meta_info = classify_self_mod(changed_files, workdir)

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
            "meta_modification": meta_info["meta_modification"],
            "meta_files": meta_info["meta_files"],
            "timed_out": False,
            "errors": ["pytest not available (uv run pytest and python3 -m pytest both failed)"],
        }
        # needs_human overrides no_tests even when pytest is unavailable
        if meta_info["meta_modification"]:
            result["verdict"] = "needs_human"
            result["errors"].append(meta_info["reason"])
            return result, 1
        return result, 0

    runner_base, has_xdist = runner_result

    # --- Scope resolution ---
    effective_scope = scope
    if scope == "auto":
        effective_scope, test_files = _resolve_auto_scope(workdir, changed_files, meta_info)
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
            "meta_modification": meta_info["meta_modification"],
            "meta_files": meta_info["meta_files"],
            "timed_out": False,
            "errors": errors,
        }
        if meta_info["meta_modification"]:
            result["verdict"] = "needs_human"
            errors.append(meta_info["reason"])
            return result, 1
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
            "meta_modification": meta_info["meta_modification"],
            "meta_files": meta_info["meta_files"],
            "timed_out": True,
            "errors": errors,
        }
        if meta_info["meta_modification"]:
            result["verdict"] = "needs_human"
            errors.append(meta_info["reason"])
            return result, 1
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
            "meta_modification": meta_info["meta_modification"],
            "meta_files": meta_info["meta_files"],
            "timed_out": False,
            "errors": errors,
        }
        if meta_info["meta_modification"]:
            result["verdict"] = "needs_human"
            errors.append(meta_info["reason"])
            return result, 1
        return result, 0

    passed, failed, failed_tests = _parse_pytest_output(r.stdout, r.stderr)

    # --- Determine base verdict from test results ---
    if r.returncode == 0 and failed == 0:
        test_verdict = "pass"
        exit_code = 0
    elif r.returncode == 5:
        # pytest exit 5 = no tests collected
        test_verdict = "no_tests"
        exit_code = 0
    else:
        test_verdict = "fail"
        exit_code = 1
        # Auto-revert if requested (only on fail, not on needs_human)
        if auto_revert and not meta_info["meta_modification"]:
            reverted = _revert_files(workdir, changed_files, errors)

    # --- Meta overrides verdict: tests still ran and are reported, but verdict=needs_human ---
    if meta_info["meta_modification"]:
        verdict = "needs_human"
        exit_code = 1
        errors.append(meta_info["reason"])
    else:
        verdict = test_verdict

    result = {
        "scope": scope,
        "effective_scope": effective_scope,
        "ran": test_files,
        "passed": passed,
        "failed": failed,
        "failed_tests": failed_tests,
        "reverted": reverted,
        "verdict": verdict,
        "meta_modification": meta_info["meta_modification"],
        "meta_files": meta_info["meta_files"],
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
        help="If the suite fails (not needs_human), revert --changed-files via git restore",
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
        f"meta_modification={result['meta_modification']} "
        f"timed_out={result['timed_out']}",
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
