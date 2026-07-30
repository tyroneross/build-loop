#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""selfscan.py — self-simplification scan for the self_review package.

Runs only when workdir IS the build-loop repo (self-recursive) AND mode == "deep".
No LLM calls, no network, stdlib only.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# scripts/ directory — one level above this package
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent

_OVERSIZED_LINE_THRESHOLD = 600
_BUILD_LOOP_PLUGIN_NAME = "build-loop"

# ---------------------------------------------------------------------------
# Self-recursive detection helpers
# ---------------------------------------------------------------------------

def _check_state_flag(workdir: Path) -> bool:
    """Return True if state.json has selfRecursive.enabled == true."""
    state_path = workdir / ".build-loop" / "state.json"
    if not state_path.exists():
        return False
    try:
        state = json.loads(state_path.read_text())
        if not isinstance(state, dict):
            return False
        sr = state.get("selfRecursive") or {}
        return isinstance(sr, dict) and sr.get("enabled") is True
    except (json.JSONDecodeError, OSError):
        return False


def _check_plugin_canary(workdir: Path) -> bool:
    """Return True if plugin.json matches build-loop AND the self_review package exists."""
    plugin_json = workdir / ".claude-plugin" / "plugin.json"
    if not plugin_json.exists():
        return False
    try:
        data = json.loads(plugin_json.read_text())
        if not isinstance(data, dict):
            return False
        if data.get("name") != _BUILD_LOOP_PLUGIN_NAME:
            return False
    except (json.JSONDecodeError, OSError):
        return False
    # Accept either the new package form or the legacy flat file (backwards-compat).
    scripts_dir = workdir / "scripts"
    return (
        (scripts_dir / "self_review" / "__main__.py").exists()
        or (scripts_dir / "self_review.py").exists()
    )


def is_self_recursive(workdir: Path) -> bool:
    """Return True if workdir IS the build-loop repo itself.

    Two checks (either passing is sufficient to avoid false-negatives):
      1. .build-loop/state.json has selfRecursive.enabled == true
      2. .claude-plugin/plugin.json exists with name == "build-loop"
         AND scripts/self_review/__main__.py exists (canary)

    Fail-soft: any parse error → False.
    """
    return _check_state_flag(workdir) or _check_plugin_canary(workdir)


# ---------------------------------------------------------------------------
# Python file discovery helpers
# ---------------------------------------------------------------------------

def _files_from_git_diff(workdir: Path, since_date: str) -> list[str]:
    """Try git diff HEAD~1..HEAD to get recently changed Python files."""
    try:
        out = subprocess.check_output(
            [
                "git", "-C", str(workdir),
                "diff", "--name-only",
                f"--since={since_date}",
                "HEAD~1", "HEAD",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
        files = [
            str(workdir / f.strip())
            for f in out.splitlines()
            if f.strip().endswith(".py") and f.strip()
        ]
        return files
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _files_from_git_log(workdir: Path, since_date: str) -> list[str]:
    """Fallback: git log --name-only across the window."""
    try:
        out = subprocess.check_output(
            [
                "git", "-C", str(workdir),
                "log", f"--since={since_date}",
                "--name-only", "--pretty=format:",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=20,
        )
        seen: set[str] = set()
        files: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line.endswith(".py") or line in seen:
                continue
            p = workdir / line
            if p.exists():
                seen.add(line)
                files.append(str(p))
        return files
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []


def get_changed_python_files(workdir: Path, window_days: int, errors: list[str]) -> list[str]:  # noqa: ARG001
    """Return Python files changed in the last window_days via git.

    Falls back to scripts/*.py if git is unavailable or returns nothing.
    """
    import datetime as dt
    since_date = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    ).strftime("%Y-%m-%d")

    files = _files_from_git_diff(workdir, since_date)
    if files:
        return files

    files = _files_from_git_log(workdir, since_date)
    if files:
        return files

    scripts_dir = workdir / "scripts"
    if scripts_dir.is_dir():
        return [str(f) for f in sorted(scripts_dir.glob("*.py"))]
    return []


# ---------------------------------------------------------------------------
# Complexity detector invocation
# ---------------------------------------------------------------------------

def run_complexity_detector(
    workdir: Path,  # noqa: ARG001
    py_files: list[str],
    errors: list[str],
) -> list[dict[str, Any]]:
    """Invoke complexity_detector.py on py_files; return hotspots list.

    Fail-soft: any error returns [].
    """
    detector = _SCRIPTS_DIR / "complexity_detector.py"
    if not detector.exists():
        errors.append(f"complexity_detector absent: {detector}")
        return []
    if not py_files:
        return []
    try:
        result = subprocess.run(
            [sys.executable, str(detector), "--changed-files"] + py_files + ["--json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        errors.append(f"complexity_detector error: {exc}")
        return []
    if result.returncode not in (0, 2):
        errors.append(
            f"complexity_detector exited {result.returncode}: "
            + (result.stderr or result.stdout or "")[:300]
        )
        return []
    try:
        data = json.loads(result.stdout)
        return data.get("hotspots") or []
    except (json.JSONDecodeError, ValueError) as exc:
        errors.append(f"complexity_detector parse error: {exc}")
        return []


# ---------------------------------------------------------------------------
# Finding generators
# ---------------------------------------------------------------------------

def _findings_from_hotspots(hotspots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert complexity_detector hotspots to self_simplification findings."""
    high_hotspots = [h for h in hotspots if h.get("severity") == "high"]
    findings: list[dict[str, Any]] = []
    for h in high_hotspots:
        kind = h.get("kind", "complexity")
        file_ = h.get("file", "?")
        line = h.get("line", "?")
        reason = h.get("reason", "")
        severity = "HIGH" if kind in ("accidental_quadratic", "high_complexity") else "MEDIUM"
        findings.append({
            "kind": f"self_complexity_{kind}",
            "signal": f"{Path(file_).name}:{line} — {kind}",
            "evidence": f"file={file_!r} line={line} reason={reason!r}",
            "suggested_action": (
                f"Simplify '{Path(file_).name}' at line {line}: {reason}. "
                "The host LLM should refactor after self_mod_verify confirms tests pass."
            ),
            "severity": severity,
        })
    return findings


def _findings_oversized_files(scripts_dir: Path) -> list[dict[str, Any]]:
    """Return findings for script files exceeding the line threshold."""
    findings: list[dict[str, Any]] = []
    for f in sorted(scripts_dir.glob("*.py")):
        if f.name.startswith("test_"):
            continue
        try:
            line_count = f.read_text(encoding="utf-8", errors="replace").count("\n")
        except OSError:
            continue
        if line_count <= _OVERSIZED_LINE_THRESHOLD:
            continue
        findings.append({
            "kind": "self_oversized_file",
            "signal": f"{f.name} is {line_count} lines (>{_OVERSIZED_LINE_THRESHOLD})",
            "evidence": f"file={str(f)!r} lines={line_count}",
            "suggested_action": (
                f"Split '{f.name}' into focused modules. "
                "Large files increase cognitive load and diff noise."
            ),
            "severity": "MEDIUM",
        })
    return findings


def _findings_missing_tests(scripts_dir: Path) -> list[dict[str, Any]]:
    """Return findings for scripts lacking a corresponding test file."""
    findings: list[dict[str, Any]] = []
    for f in sorted(scripts_dir.glob("*.py")):
        if f.name.startswith("test_") or f.name.startswith("_"):
            continue
        test_candidate = scripts_dir / f"test_{f.name}"
        if test_candidate.exists():
            continue
        findings.append({
            "kind": "self_missing_test",
            "signal": f"No test file for {f.name}",
            "evidence": f"script={str(f)!r} expected_test={str(test_candidate)!r}",
            "suggested_action": (
                f"Add 'scripts/test_{f.name}' with at least one smoke test "
                "covering the main entry point."
            ),
            "severity": "LOW",
        })
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_self_simplification(
    workdir: Path,
    window_days: int,
    errors: list[str],
) -> list[dict[str, Any]]:
    """Gather self-simplification findings when self-recursive + deep mode.

    Detects:
      - High-severity hotspots from complexity_detector (deep_nesting,
        accidental_quadratic, high_complexity, redundant_multipass)
      - Oversized files (>_OVERSIZED_LINE_THRESHOLD lines) → suggest split
      - Missing tests (scripts/foo.py with no scripts/test_foo.py)
    """
    py_files = get_changed_python_files(workdir, window_days, errors)
    hotspots = run_complexity_detector(workdir, py_files, errors)

    findings: list[dict[str, Any]] = []
    findings.extend(_findings_from_hotspots(hotspots))

    scripts_dir = workdir / "scripts"
    if scripts_dir.is_dir():
        findings.extend(_findings_oversized_files(scripts_dir))
        findings.extend(_findings_missing_tests(scripts_dir))

    return findings
