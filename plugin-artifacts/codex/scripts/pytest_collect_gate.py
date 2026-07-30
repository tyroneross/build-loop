#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Pytest-collection gate for the build-loop plugin.

Runs ``pytest --collect-only`` under ``env -u PYTHONPATH`` semantics on the
repository's test surface and reports whether the full suite is importable.
This is the cheap collection-only sibling of ``runtime_smoke.py``: it does NOT
require the full suite to execute green — db/live tests still skip via their
own markers at execution time. Collection only checks that every test module
loads, which catches the class of silent breakage where a missing export or
broken import quietly removes whole modules from coverage.

CLI:
    python3 scripts/pytest_collect_gate.py \\
        [--workdir <path>] \\
        [--paths <path1> [<path2> ...]] \\
        [--python <interpreter>] \\
        [--json] \\
        [--dry-run]

Exit codes:
    0 — pass | skipped | dry_run
    1 — fail (at least one collection error)
    2 — runner error (pytest not found, malformed input, internal failure)

Envelope (JSON, written on stdout when --json):
    {
      "status": "pass" | "fail" | "skipped" | "dry_run",
      "findings": [{file, line, error_class, message}, ...],
      "reason": "...",                  # set when status == "skipped"
      "command": [...],                 # the pytest invocation
      "paths": [...],                   # the collection paths
      "tests_collected": <int>,         # parsed from pytest summary when available
      "errors_count": <int>             # length of findings (top-level convenience)
    }

Stdlib only (argparse, json, pathlib, subprocess, re, sys, os).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Defaults — kept aligned with build-loop's own test layout
# ---------------------------------------------------------------------------

DEFAULT_PATHS: tuple[str, ...] = ("scripts/", "tests/")

# Pattern matches the "ERROR collecting <path>" header pytest writes for each
# collection error, e.g.:
#   ___________ ERROR collecting tests/test_run_entry_execution_state.py ___________
_ERROR_HEADER_RE = re.compile(r"^_+ ERROR collecting (?P<file>\S+) _+\s*$")

# Pattern for the file:line marker pytest emits inside a collection traceback:
#   tests/test_run_entry_execution_state.py:19: in <module>
_TRACEBACK_LINE_RE = re.compile(r"^(?P<file>[^:\s]+\.py):(?P<line>\d+): in")

# Pattern for the final exception line pytest prints inside a collection block:
#   E   ImportError: cannot import name 'X' from 'Y'
_EXCEPTION_RE = re.compile(r"^E\s+(?P<cls>[A-Za-z_][\w.]*):\s*(?P<msg>.*)$")

# Pattern for the summary "ERROR <path>" lines pytest emits at the bottom:
#   ERROR tests/test_run_entry_execution_state.py
_SUMMARY_ERROR_RE = re.compile(r"^ERROR\s+(?P<file>\S+)\s*$")

# Pattern for the "X tests collected, Y errors" or "X tests collected" footer.
_TESTS_COLLECTED_RE = re.compile(r"(?P<count>\d+)\s+tests?\s+collected")


# ---------------------------------------------------------------------------
# Environment + invocation
# ---------------------------------------------------------------------------

def _build_env() -> dict[str, str]:
    """Return a copy of os.environ with PYTHONPATH stripped.

    Mirrors the build-loop spec's ``env -u PYTHONPATH`` discipline: tests run
    against the installed package, not a stale or rigged path injection.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _resolve_python(explicit: str | None, workdir: Path) -> str | None:
    """Pick the python interpreter to drive pytest.

    Resolution order:
      1. explicit --python value
      2. workdir-local ``.venv/bin/python``
      3. workdir-local ``venv/bin/python``
      4. ``sys.executable`` (the interpreter running this gate)
    """
    if explicit:
        return explicit
    for candidate in (workdir / ".venv" / "bin" / "python",
                      workdir / "venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable or None


def _should_skip(workdir: Path, paths: list[str]) -> tuple[bool, str]:
    """Return (skip, reason) when no pytest config or test surface exists.

    Library-only repos without pytest configuration and no tests/ directory
    have nothing to collect — return ``skipped`` rather than failing.
    """
    has_config = any((workdir / name).exists() for name in
                     ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"))
    has_any_path = any((workdir / p).exists() for p in paths)
    if not has_config and not has_any_path:
        return True, "no pytest config and no test paths present"
    if not has_any_path:
        if has_config:
            # Python-bearing repo (has config) but tests are not at the default
            # paths. Skip LOUDLY so the operator notices the bypass and can pass
            # --paths for a non-standard layout — silence here would hide whole
            # test trees from the gate.
            return True, (
                "pytest config present but neither default path ("
                + ", ".join(paths)
                + ") found — pass --paths for non-standard layouts"
            )
        return True, "no test paths present"
    return False, ""


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

def _parse_collection_errors(output: str) -> list[dict]:
    """Parse pytest --collect-only output into structured findings.

    One finding per ``ERROR collecting <file>`` block. Each finding carries:
      - file: the test module that failed to collect
      - line: first traceback line inside that file (best-effort)
      - error_class: e.g. "ImportError", "SyntaxError" (best-effort)
      - message: the exception message (best-effort)
    """
    lines = output.splitlines()
    findings: list[dict] = []

    # Pass 1: walk top-to-bottom and detect each error block by its header.
    # Within a block, capture the most-specific file:line we see (the
    # innermost traceback frame matching the failing test module) and the
    # last "E   <Class>: <msg>" line.
    i = 0
    in_block = False
    block_file: str | None = None
    block_line: int | None = None
    block_cls: str | None = None
    block_msg: str | None = None
    while i < len(lines):
        line = lines[i]
        header = _ERROR_HEADER_RE.match(line)
        if header:
            # Flush any prior block first
            if in_block and block_file:
                findings.append({
                    "file": block_file,
                    "line": block_line,
                    "error_class": block_cls,
                    "message": block_msg,
                })
            in_block = True
            block_file = header.group("file")
            block_line = None
            block_cls = None
            block_msg = None
            i += 1
            continue

        if in_block:
            # End of error section: pytest prints a "===" separator before
            # the summary or moves into "short test summary info".
            if line.startswith("====") or line.startswith("==="):
                findings.append({
                    "file": block_file,
                    "line": block_line,
                    "error_class": block_cls,
                    "message": block_msg,
                })
                in_block = False
                block_file = None
                block_line = None
                block_cls = None
                block_msg = None
                i += 1
                continue
            tb = _TRACEBACK_LINE_RE.match(line)
            if tb and tb.group("file") == block_file:
                # Most-specific frame inside the failing module
                block_line = int(tb.group("line"))
            exc = _EXCEPTION_RE.match(line)
            if exc:
                block_cls = exc.group("cls")
                block_msg = exc.group("msg").strip()
        i += 1

    # Flush trailing block
    if in_block and block_file:
        findings.append({
            "file": block_file,
            "line": block_line,
            "error_class": block_cls,
            "message": block_msg,
        })

    # Pass 2: pick up any "ERROR <file>" summary lines we missed (defensive —
    # the header pass should catch them all on real pytest output).
    seen_files = {f["file"] for f in findings}
    for line in lines:
        m = _SUMMARY_ERROR_RE.match(line)
        if m and m.group("file") not in seen_files:
            findings.append({
                "file": m.group("file"),
                "line": None,
                "error_class": None,
                "message": None,
            })

    return findings


def _parse_tests_collected(output: str) -> int | None:
    """Best-effort: return the integer from pytest's '<N> tests collected'."""
    for line in reversed(output.splitlines()):
        m = _TESTS_COLLECTED_RE.search(line)
        if m:
            try:
                return int(m.group("count"))
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Output emitter
# ---------------------------------------------------------------------------

def _emit(envelope: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(envelope, indent=2))
        return
    status = envelope.get("status", "unknown")
    count = envelope.get("errors_count", 0)
    collected = envelope.get("tests_collected")
    bits = [f"pytest-collect-gate: status={status}"]
    if collected is not None:
        bits.append(f"collected={collected}")
    if count:
        bits.append(f"errors={count}")
    reason = envelope.get("reason")
    if reason:
        bits.append(f"reason={reason}")
    print(" ".join(bits))
    for f in envelope.get("findings", []):
        loc = f.get("file") or "?"
        if f.get("line"):
            loc = f"{loc}:{f['line']}"
        cls = f.get("error_class") or "?"
        msg = f.get("message") or ""
        print(f"  {cls:16s}  {loc}  {msg}")


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def run_gate(
    workdir: Path,
    paths: list[str],
    python: str | None,
    dry_run: bool,
) -> tuple[dict, int]:
    """Execute the gate. Returns (envelope, exit_code)."""
    paths = paths or list(DEFAULT_PATHS)
    skip, reason = _should_skip(workdir, paths)
    if skip:
        return ({
            "status": "skipped",
            "findings": [],
            "errors_count": 0,
            "tests_collected": None,
            "reason": reason,
            "command": [],
            "paths": paths,
        }, 0)

    interpreter = _resolve_python(python, workdir)
    if not interpreter:
        return ({
            "status": "fail",
            "findings": [{
                "file": None,
                "line": None,
                "error_class": "RunnerError",
                "message": "no python interpreter resolvable",
            }],
            "errors_count": 1,
            "tests_collected": None,
            "command": [],
            "paths": paths,
        }, 2)

    # NOTE: --collect-only without -q so the parser can read the
    # "<N> tests collected" footer; -q suppresses that summary line.
    command = [interpreter, "-m", "pytest", *paths, "--collect-only"]

    if dry_run:
        return ({
            "status": "dry_run",
            "findings": [],
            "errors_count": 0,
            "tests_collected": None,
            "command": command,
            "paths": paths,
        }, 0)

    try:
        proc = subprocess.run(
            command,
            cwd=str(workdir),
            env=_build_env(),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        return ({
            "status": "fail",
            "findings": [{
                "file": None,
                "line": None,
                "error_class": "RunnerError",
                "message": f"failed to invoke pytest: {exc}",
            }],
            "errors_count": 1,
            "tests_collected": None,
            "command": command,
            "paths": paths,
        }, 2)

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    findings = _parse_collection_errors(combined)
    collected = _parse_tests_collected(combined)

    # pytest exit-code conventions: 0 = success, 1 = tests failed (n/a for
    # --collect-only with no execution), 2 = command-line usage error,
    # 3 = internal error, 4 = pytest usage error, 5 = no tests collected.
    # For our purposes:
    #   - rc=0 AND no parsed findings -> pass
    #   - rc=0 AND parsed findings -> defensive: still report fail (shouldn't happen)
    #   - rc>=1 AND parsed findings -> fail (the normal collection-error path)
    #   - rc>=1 AND no parsed findings -> runner error (rc=2)
    #   - rc=5 (no tests collected) treated as skipped, not a runner error
    if proc.returncode == 0 and not findings:
        status = "pass"
        exit_code = 0
    elif proc.returncode == 5 and not findings:
        return ({
            "status": "skipped",
            "findings": [],
            "errors_count": 0,
            "tests_collected": collected,
            "reason": "no tests collected (pytest exit 5)",
            "command": command,
            "paths": paths,
        }, 0)
    elif findings:
        status = "fail"
        exit_code = 1
    else:
        # Non-zero exit with no parsed findings — runner error.
        return ({
            "status": "fail",
            "findings": [{
                "file": None,
                "line": None,
                "error_class": "RunnerError",
                "message": f"pytest exited {proc.returncode} with no parseable findings",
            }],
            "errors_count": 1,
            "tests_collected": collected,
            "command": command,
            "paths": paths,
            "stderr_tail": (proc.stderr or "").splitlines()[-5:],
        }, 2)

    return ({
        "status": status,
        "findings": findings,
        "errors_count": len(findings),
        "tests_collected": collected,
        "command": command,
        "paths": paths,
    }, exit_code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Pytest-collection gate — verify every test module loads. "
            "Runs with PYTHONPATH stripped so collection reflects the "
            "installed package, not a rigged path."
        ),
    )
    parser.add_argument("--workdir", default=".", help="repo root (default: cwd)")
    parser.add_argument(
        "--paths",
        nargs="+",
        default=list(DEFAULT_PATHS),
        metavar="PATH",
        help="collection paths (default: scripts/ tests/)",
    )
    parser.add_argument(
        "--python",
        default=None,
        help="python interpreter (default: workdir .venv, else sys.executable)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON envelope on stdout")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the command that would run without executing pytest",
    )
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    envelope, exit_code = run_gate(
        workdir=workdir,
        paths=list(args.paths),
        python=args.python,
        dry_run=args.dry_run,
    )
    _emit(envelope, as_json=args.json)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
