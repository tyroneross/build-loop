#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""
apple_sourcekit_triage.py — classify SourceKit "Cannot find type" diagnostics
as false-positive (ghost) vs real, by consulting ground truth (xcodebuild).

Usage:
  apple_sourcekit_triage.py --project-root <path> [--diagnostics <json-file>] [--json]

Stdlib only: pathlib, subprocess, json, argparse, sys, re, shutil, tempfile.

Background (from skills/build-loop/references/apple-native-planning.md):
  On Xcode 26.x XcodeGen projects, editing .swift files and running
  `xcodegen generate` produces false-positive SourceKit errors of the form
  "Cannot find type 'X' in scope" for types defined in sibling files within
  the same module. The diagnostics arrive AFTER xcodebuild ships
  "** BUILD SUCCEEDED **". They are stale index output, not real errors.

A SourceKit "Cannot find type" diagnostic is REAL (not a ghost) when ANY apply:
  - xcodebuild produces the same error
  - The named type does not exist anywhere in the module (grep returns nothing)
  - The file was just created and xcodegen generate has not been run

Otherwise it's a ghost and the fix is patience.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Diagnostic = dict[str, Any]
BuildResult = dict[str, Any]

# Matches: "Cannot find type 'Foo' in scope"
_CANNOT_FIND_TYPE_RE = re.compile(r"Cannot find type '([^']+)' in scope")

# Matches error lines in xcodebuild stderr:
# path/to/file.swift:10:5: error: Cannot find type 'Foo' in scope
_XCODE_ERROR_RE = re.compile(
    r"(?P<file>[^:]+\.swift):(?P<line>\d+):\d+: error: (?P<message>.+)"
)


# ---------------------------------------------------------------------------
# Default xcodebuild runner (production)
# ---------------------------------------------------------------------------

def _default_xcodebuild_runner(project_root: Path) -> BuildResult:
    """Run xcodegen generate + xcodebuild, return exit code and combined stderr.

    Returns a dict with:
      exit_code: int
      stderr: str    (combined stderr from both commands)
      scheme: str | None
    """
    stderr_parts: list[str] = []

    # Step 1: xcodegen generate
    xcodegen = shutil.which("xcodegen")
    if xcodegen:
        try:
            result = subprocess.run(
                [xcodegen, "generate"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                stderr_parts.append(result.stderr)
        except (subprocess.TimeoutExpired, OSError) as exc:
            stderr_parts.append(f"xcodegen error: {exc}")
    # xcodegen absence is not fatal — continue to xcodebuild

    # Step 2: discover scheme
    scheme = _discover_scheme(project_root)

    # Step 3: xcodebuild build
    cmd = [
        "xcodebuild",
        "-destination", "generic/platform=iOS",
        "build",
        "CODE_SIGNING_ALLOWED=NO",
    ]
    if scheme:
        cmd += ["-scheme", scheme]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=600,
        )
        stderr_parts.append(result.stderr)
        exit_code = result.returncode
    except (subprocess.TimeoutExpired, OSError) as exc:
        stderr_parts.append(f"xcodebuild error: {exc}")
        exit_code = 1

    return {
        "exit_code": exit_code,
        "stderr": "\n".join(stderr_parts),
        "scheme": scheme,
    }


def _discover_scheme(project_root: Path) -> str | None:
    """Discover the primary scheme from project.yml or xcodebuild -list."""
    # Try project.yml first
    project_yml = project_root / "project.yml"
    if project_yml.exists():
        try:
            content = project_yml.read_text(encoding="utf-8")
            # Look for "schemes:" section and grab the first scheme name
            m = re.search(r"^schemes:\s*\n\s+(\S+):", content, re.MULTILINE)
            if m:
                return m.group(1)
            # Look for "name:" at the top level (project name often = scheme)
            m = re.search(r"^name:\s*(\S+)", content, re.MULTILINE)
            if m:
                return m.group(1)
        except OSError:
            pass

    # Fall back to xcodebuild -list
    try:
        result = subprocess.run(
            ["xcodebuild", "-list", "-json"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            schemes = (
                data.get("project", {}).get("schemes")
                or data.get("workspace", {}).get("schemes")
                or []
            )
            if schemes:
                return schemes[0]
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        pass

    return None


# ---------------------------------------------------------------------------
# Grep helper
# ---------------------------------------------------------------------------

def _type_exists_in_module(type_name: str, project_root: Path) -> bool:
    """Return True if ``type_name`` appears in any *.swift file under project_root."""
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include=*.swift", "--", type_name, str(project_root)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return True  # Assume it exists on error (conservative — don't over-flag)


# ---------------------------------------------------------------------------
# Error parsing helpers
# ---------------------------------------------------------------------------

def _extract_type_name(message: str) -> str | None:
    """Extract 'Foo' from "Cannot find type 'Foo' in scope"."""
    m = _CANNOT_FIND_TYPE_RE.search(message)
    return m.group(1) if m else None


def _xcodebuild_errors(stderr: str) -> list[dict[str, Any]]:
    """Parse xcodebuild stderr into a list of {file, line, message} error dicts."""
    errors = []
    for line in stderr.splitlines():
        m = _XCODE_ERROR_RE.match(line.strip())
        if m:
            errors.append({
                "file": m.group("file"),
                "line": int(m.group("line")),
                "message": m.group("message").strip(),
            })
    return errors


def _xcodebuild_reproduced_error(
    diagnostic: Diagnostic,
    xcode_errors: list[dict[str, Any]],
) -> bool:
    """Return True if xcodebuild reproduced the same 'Cannot find type X' error."""
    diag_type = _extract_type_name(diagnostic.get("message", ""))
    if diag_type is None:
        return False
    for err in xcode_errors:
        err_type = _extract_type_name(err.get("message", ""))
        if err_type == diag_type:
            return True
    return False


# ---------------------------------------------------------------------------
# Core triage function (dependency-injected)
# ---------------------------------------------------------------------------

def triage(
    project_root: Path | str,
    diagnostics: list[Diagnostic],
    *,
    build_runner: Callable[[Path], BuildResult] = _default_xcodebuild_runner,
) -> dict[str, Any]:
    """Classify SourceKit diagnostics as false_positive vs real.

    Args:
        project_root: Path to the XcodeGen project root.
        diagnostics: List of {"file": str, "line": int, "message": str} dicts.
        build_runner: Injectable runner for xcodebuild. Default runs the real
            xcodebuild; pass a fake runner in tests for hermetic execution.

    Returns:
        Full JSON envelope per the spec.
    """
    root = Path(project_root).resolve()

    # Gate 1: Must be an XcodeGen project
    if not (root / "project.yml").exists():
        return {
            "applicable": False,
            "reason": "not an XcodeGen project",
        }

    # Run ground truth
    build_result = build_runner(root)
    exit_code: int = build_result.get("exit_code", 1)
    stderr: str = build_result.get("stderr", "")
    xcode_succeeded: bool = exit_code == 0

    # Parse all errors xcodebuild actually emitted
    xcode_errors = _xcodebuild_errors(stderr)

    # Classify each diagnostic
    classified: list[dict[str, Any]] = []
    for diag in diagnostics:
        message = diag.get("message", "")
        file_ = diag.get("file", "")
        line_ = diag.get("line")

        if xcode_succeeded:
            # xcodebuild clean → ALL SourceKit errors are ghosts
            classified.append({
                "file": file_,
                "line": line_,
                "message": message,
                "verdict": "false_positive",
                "reason": "xcodebuild succeeded — SourceKit index lag",
            })
            continue

        # xcodebuild failed — check if it reproduced this specific error
        if _xcodebuild_reproduced_error(diag, xcode_errors):
            # xcodebuild confirmed the error. Dig deeper for classification.
            type_name = _extract_type_name(message)
            if type_name and not _type_exists_in_module(type_name, root):
                reason = "type_genuinely_missing"
            else:
                reason = "xcodebuild reproduced the same error"
            classified.append({
                "file": file_,
                "line": line_,
                "message": message,
                "verdict": "real",
                "reason": reason,
            })
        else:
            # xcodebuild failed for a DIFFERENT reason — this specific error
            # was not reproduced, so it's still a SourceKit ghost.
            classified.append({
                "file": file_,
                "line": line_,
                "message": message,
                "verdict": "false_positive",
                "reason": "xcodebuild did not reproduce this specific error",
            })

    # Build summary
    total = len(classified)
    real_count = sum(1 for d in classified if d["verdict"] == "real")
    fp_count = total - real_count

    if real_count > 0:
        recommendation = "real errors present — see verdict=real entries"
    else:
        recommendation = (
            "ignore SourceKit diagnostics — ground truth (xcodebuild) is clean"
        )

    return {
        "applicable": True,
        "xcodebuild_exit": exit_code,
        "xcodebuild_succeeded": xcode_succeeded,
        "diagnostics": classified,
        "summary": {
            "total": total,
            "false_positive": fp_count,
            "real": real_count,
        },
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_diagnostics(path: str | None) -> list[Diagnostic]:
    """Load diagnostics from a JSON file or stdin."""
    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    if not text.strip():
        return []
    data = json.loads(text)
    # Accept either a list of diagnostics or {"diagnostics": [...]}
    if isinstance(data, list):
        return data
    return data.get("diagnostics", [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify SourceKit ghost diagnostics vs real errors.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--project-root",
        required=True,
        help="Path to the XcodeGen project root (must contain project.yml).",
    )
    parser.add_argument(
        "--diagnostics",
        metavar="JSON_FILE",
        help=(
            "JSON file containing diagnostics as [{file, line, message}, ...]. "
            "If omitted, reads from stdin."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit result as JSON (default when stdout is not a TTY).",
    )
    args = parser.parse_args(argv)

    # Check applicability first (no build needed for non-XcodeGen)
    root = Path(args.project_root)
    if not (root / "project.yml").exists():
        result: dict[str, Any] = {
            "applicable": False,
            "reason": "not an XcodeGen project",
        }
        print(json.dumps(result, indent=2))
        return 0

    # Load diagnostics (may be empty)
    try:
        diags = _load_diagnostics(args.diagnostics)
    except (json.JSONDecodeError, OSError) as exc:
        err = {"error": f"Failed to load diagnostics: {exc}"}
        print(json.dumps(err, indent=2))
        return 2

    result = triage(root, diags)

    emit_json = args.json or not sys.stdout.isatty()
    if emit_json:
        print(json.dumps(result, indent=2))
    else:
        # Human-readable summary
        print(f"Applicable: {result.get('applicable')}")
        if result.get("applicable"):
            print(f"xcodebuild exit: {result.get('xcodebuild_exit')}")
            print(f"xcodebuild succeeded: {result.get('xcodebuild_succeeded')}")
            summary = result.get("summary", {})
            print(
                f"Diagnostics: {summary.get('total')} total, "
                f"{summary.get('false_positive')} false_positive, "
                f"{summary.get('real')} real"
            )
            print(f"Recommendation: {result.get('recommendation')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
