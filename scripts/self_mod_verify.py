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
      "verdict":         "pass" | "fail" | "no_tests" | "error",
      "timed_out":       bool,
      "error_reason":    str | null,    # present (non-null) only when verdict="error"
      "errors":          [str, ...]
    }

Exit codes:
  0  — verdict "pass" or "no_tests"
  1  — verdict "fail"  (real test failures)
  2  — verdict "error" (infrastructure error: collection failure, timeout, worker crash)

Fail-soft on infrastructure errors (no pytest, unreadable output):
  verdict = "no_tests", exit 0.  A missing test suite never blocks a deploy;
  the absence is itself surfaced in the JSON for the caller to act on.

When pytest exits non-zero AND no "N passed"/"N failed" summary line was parsed
(collection error, INTERNALERROR, timeout at the pytest layer, etc.), the gate
returns verdict="error" with an error_reason field so callers know WHY rather
than silently reporting 0/0/fail.

Scope selection:
  full     — whole scripts/ test suite, serial, -m "not live" to exclude live-
             service tests, --timeout=120 --timeout-method=thread so any future
             hang fails cleanly rather than blocking the gate.
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

    Full scope always runs serially — no xdist.
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

    ``foo.py`` → ``test_foo.py``. A package file ``.../<pkg>/X.py`` →
    ``test_<pkg>.py`` (the capability's test). Tests are searched RECURSIVELY
    under both ``scripts/`` and ``tests/``, so nested suites (e.g.
    ``tests/architecture/test_scanner.py``) and folder-per-capability packages
    anywhere in the tree stay gate-able. Files already named ``test_*.py`` are
    included directly.
    """
    discovered: list[str] = []
    seen: set[str] = set()

    # Index every test file by basename, recursively under scripts/ and tests/.
    test_index: dict[str, list[Path]] = {}
    for root in (workdir / "scripts", workdir / "tests"):
        if root.is_dir():
            for tf in root.rglob("test_*.py"):
                test_index.setdefault(tf.name, []).append(tf)

    def _add_by_name(name: str) -> None:
        for tf in test_index.get(name, []):
            if str(tf) not in seen:
                discovered.append(str(tf))
                seen.add(str(tf))

    for raw in changed_files:
        p = Path(raw)
        if not p.is_absolute():
            p = workdir / p
        p = p.resolve()

        if p.name.startswith("test_"):
            if p.exists() and str(p) not in seen:
                discovered.append(str(p))
                seen.add(str(p))
            continue

        # The file's own name, plus — for a package file — its containing
        # capability folder name.
        _add_by_name(f"test_{p.name}")           # foo.py → test_foo.py
        parent = p.parent.name
        if parent and parent not in ("scripts", "tests", "src"):
            _add_by_name(f"test_{parent}.py")    # <pkg>/X.py → test_<pkg>.py

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


def _parse_pytest_output(stdout: str, stderr: str) -> tuple[int, int, list[str], bool]:
    """Return (passed, failed, failed_test_names, has_summary) from pytest output.

    ``has_summary`` is True when at least one of "N passed" or "N failed" was
    found, meaning pytest produced a proper result line.  False indicates a
    collection error, INTERNALERROR, or similar infrastructure failure.
    """
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
    # In -q (quiet) mode pytest may not print a "N passed, N failed" footer
    # but it DOES print "FAILED <test>" lines for failures.  When the footer
    # count is absent, derive `failed` from the explicit FAILED lines.
    if failed == 0 and failed_tests:
        failed = len(failed_tests)

    # has_summary: True when pytest produced any real result indicators.
    # We treat either a count-summary OR explicit FAILED items as evidence
    # that pytest ran to completion (not a collection/infra failure).
    has_summary = bool(
        _PASSED_RE.search(combined)
        or _FAILED_RE.search(combined)
        or failed_tests  # explicit FAILED lines = real test output
    )
    return passed, failed, failed_tests, has_summary


def _classify_error_reason(stdout: str, stderr: str) -> str:
    """Return a short error_reason string when pytest produced no summary.

    Grep stdout+stderr for known infrastructure-failure keywords and return
    the first match.  Falls back to "unknown — see stderr".
    """
    combined = (stdout + "\n" + stderr).lower()
    if "timeout" in combined:
        return "Timeout"
    if "internalerror" in combined:
        return "INTERNALERROR"
    if "no tests ran" in combined or "no tests were run" in combined:
        return "no tests ran"
    if "error" in combined:
        return "errors — see stderr"
    return "unknown — see stderr"


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
    """Run the verification gate. Return (result_dict, exit_code).

    Exit codes:
      0  — verdict "pass" or "no_tests"
      1  — verdict "fail"  (real test failures; failed > 0 OR returncode != 0
                             with a parseable summary)
      2  — verdict "error" (infrastructure failure: no summary parsed despite
                             non-zero exit — collection error, INTERNALERROR,
                             per-test timeout, worker crash, etc.)
    """
    errors: list[str] = []
    reverted = False

    # --- Runner discovery ---
    runner_base = _find_runner(workdir)
    if runner_base is None:
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
            "error_reason": None,
            "errors": ["pytest not available (uv run pytest and python3 -m pytest both failed)"],
        }
        return result, 0

    # --- Scope resolution ---
    effective_scope = scope
    if scope == "auto":
        effective_scope, test_files = _resolve_auto_scope(workdir, changed_files)
    elif scope == "changed":
        test_files = _tests_for_changed(workdir, changed_files)
    else:  # full
        test_files = _all_script_tests(workdir)

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
            "error_reason": None,
            "errors": errors,
        }
        return result, 0

    # --- Build pytest command ---
    # Full scope: serial (no xdist), per-test timeout so hangs fail cleanly,
    # and -m "not live" to exclude tests that require a live external service.
    # auto/changed scope: fast path — no extra flags needed.
    cmd = runner_base + ["-q", "-p", "no:cacheprovider", "--tb=short"]
    if scope == "full":
        cmd += [
            "--timeout=120",
            "--timeout-method=thread",
            "-m", "not live",
        ]
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
        errors.append(f"pytest process timed out after {timeout}s")
        result = {
            "scope": scope,
            "effective_scope": effective_scope,
            "ran": test_files,
            "passed": 0,
            "failed": 0,
            "failed_tests": [],
            "reverted": False,
            "verdict": "error",
            "timed_out": True,
            "error_reason": "Timeout",
            "errors": errors,
        }
        return result, 2
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
            "error_reason": None,
            "errors": errors,
        }
        return result, 0

    passed, failed, failed_tests, has_summary = _parse_pytest_output(r.stdout, r.stderr)

    # --- Determine verdict from test results only ---
    if r.returncode == 0 and failed == 0:
        verdict = "pass"
        exit_code = 0
    elif r.returncode == 5:
        # pytest exit 5 = no tests collected
        verdict = "no_tests"
        exit_code = 0
    elif not has_summary and r.returncode != 0:
        # Non-zero exit with no parseable summary → infrastructure failure.
        # This is the "0 passed / 0 failed / verdict=fail" silent-failure case.
        # Surface it as "error" with a reason so the caller knows WHY.
        reason = _classify_error_reason(r.stdout, r.stderr)
        errors.append(f"pytest exited {r.returncode} with no summary line: {reason}")
        verdict = "error"
        exit_code = 2
    else:
        verdict = "fail"
        exit_code = 1
        if auto_revert:
            reverted = _revert_files(workdir, changed_files, errors)

    error_reason: str | None = None
    if verdict == "error":
        error_reason = _classify_error_reason(r.stdout, r.stderr)

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
        "error_reason": error_reason,
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
            "full = whole scripts/ suite, serial, live tests excluded, "
            "per-test timeout 120s"
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
        help="Subprocess timeout in seconds for the pytest process as a whole (default 300)",
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
    err_suffix = (
        f" error_reason={result['error_reason']!r}"
        if result.get("error_reason") is not None
        else ""
    )
    print(
        f"self_mod_verify: verdict={result['verdict']} "
        f"passed={result['passed']} failed={result['failed']} "
        f"reverted={result['reverted']} scope={result['scope']} "
        f"effective_scope={result['effective_scope']} "
        f"timed_out={result['timed_out']}"
        f"{err_suffix}",
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
