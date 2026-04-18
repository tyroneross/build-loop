from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MetricResult:
    value: float
    raw_output: str
    elapsed_seconds: float
    success: bool
    error: Optional[str] = None


@dataclass
class GuardResult:
    passed: bool
    raw_output: str
    elapsed_seconds: float


# Patterns tried in order for parse_numeric.
# Each pattern returns the numeric text of the first/last/best match.
_NUMERIC_PATTERNS = [
    # Labeled values: "Coverage: 85.2%", "Score: 0.824", "Time: 19.1s"
    re.compile(r"(?:coverage|score|time|value|result|metric|accuracy|rate|percent)\s*[:=]\s*([\d]+(?:\.[\d]+)?)", re.IGNORECASE),
    # Bare percentages: "85.2%"
    re.compile(r"([\d]+(?:\.[\d]+)?)\s*%"),
    # time(1) output: "real\t0m19.123s" — captures seconds as float
    re.compile(r"real\s+(\d+)m([\d]+(?:\.[\d]+)?)s", re.IGNORECASE),
    # Trailing number with optional unit: "19.1s", "42 ms", "1234"
    re.compile(r"([\d]+(?:\.[\d]+)?)\s*(?:ms|s|sec|seconds?)?\s*$", re.MULTILINE),
    # Any standalone float or int
    re.compile(r"([\d]+(?:\.[\d]+)?)"),
]


def parse_numeric(output: str) -> float:
    """Extract the last meaningful numeric value from command output.

    Handles:
    - Plain numbers: "42", "3.14"
    - Percentages: "85.2%" (strips the %)
    - time(1) output: "real 0m19.123s" → 19.123
    - Labeled lines: "Coverage: 85.2%", "Score: 0.824", "Time: 19.1s"
    """
    text = output.strip()
    if not text:
        raise ValueError("Empty output — no numeric value to parse")

    # Try labeled patterns first (highest signal).
    for pattern in _NUMERIC_PATTERNS[:2]:
        matches = pattern.findall(text)
        if matches:
            # Use the last match to pick up summary lines at the end.
            raw = matches[-1]
            if isinstance(raw, tuple):
                raw = raw[-1]  # fallback for groups
            return float(raw)

    # time(1) pattern — converts Xm Y.Zs → total seconds.
    time_matches = _NUMERIC_PATTERNS[2].findall(text)
    if time_matches:
        minutes_str, seconds_str = time_matches[-1]
        return float(minutes_str) * 60.0 + float(seconds_str)

    # Trailing number (end of line).
    trailing_matches = _NUMERIC_PATTERNS[3].findall(text)
    if trailing_matches:
        return float(trailing_matches[-1])

    # Last resort: any number anywhere in the output.
    any_matches = _NUMERIC_PATTERNS[4].findall(text)
    if any_matches:
        return float(any_matches[-1])

    raise ValueError(f"No numeric value found in output: {text[:200]!r}")


def run_metric(cmd: str, timeout: int = 300, cwd: str | None = None) -> MetricResult:
    """Run a metric command and extract a numeric result.

    Args:
        cmd: Shell command whose stdout contains a parseable numeric value.
        timeout: Maximum seconds to wait (default 300).
        cwd: Working directory for the command (default: current directory).

    Returns:
        MetricResult with value, raw_output, elapsed_seconds, success, error.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        elapsed = time.monotonic() - start
        raw = proc.stdout + proc.stderr

        if proc.returncode != 0:
            return MetricResult(
                value=0.0,
                raw_output=raw,
                elapsed_seconds=elapsed,
                success=False,
                error=f"Command exited with code {proc.returncode}",
            )

        try:
            value = parse_numeric(raw)
        except ValueError as exc:
            return MetricResult(
                value=0.0,
                raw_output=raw,
                elapsed_seconds=elapsed,
                success=False,
                error=str(exc),
            )

        return MetricResult(
            value=value,
            raw_output=raw,
            elapsed_seconds=elapsed,
            success=True,
        )

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        return MetricResult(
            value=0.0,
            raw_output="",
            elapsed_seconds=elapsed,
            success=False,
            error=f"Command timed out after {timeout}s",
        )
    except Exception as exc:  # pragma: no cover
        elapsed = time.monotonic() - start
        return MetricResult(
            value=0.0,
            raw_output="",
            elapsed_seconds=elapsed,
            success=False,
            error=str(exc),
        )


def run_guard(cmd: str, timeout: int = 300, cwd: str | None = None) -> GuardResult:
    """Run a guard command. Pass = exit code 0. Fail = nonzero.

    Guards are binary checks (lint, tests, type-checks) that determine whether
    a change is acceptable regardless of metric improvement.

    Args:
        cmd: Shell command whose exit code signals pass/fail.
        timeout: Maximum seconds to wait (default 300).
        cwd: Working directory for the command (default: current directory).

    Returns:
        GuardResult with passed, raw_output, elapsed_seconds.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        elapsed = time.monotonic() - start
        raw = proc.stdout + proc.stderr
        return GuardResult(
            passed=proc.returncode == 0,
            raw_output=raw,
            elapsed_seconds=elapsed,
        )

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        return GuardResult(
            passed=False,
            raw_output=f"Guard timed out after {timeout}s",
            elapsed_seconds=elapsed,
        )
    except Exception as exc:  # pragma: no cover
        elapsed = time.monotonic() - start
        return GuardResult(
            passed=False,
            raw_output=str(exc),
            elapsed_seconds=elapsed,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Run metric or guard commands")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cmd", help="metric command to run")
    group.add_argument("--guard", help="guard command to run")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--cwd", default=None, help="working directory for the command")

    args = parser.parse_args()

    if args.cmd:
        result = run_metric(args.cmd, timeout=args.timeout, cwd=args.cwd)
        output = {
            "value": result.value,
            "success": result.success,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
            "error": result.error,
        }
        print(json.dumps(output))
        sys.exit(0 if result.success else 1)
    else:
        result = run_guard(args.guard, timeout=args.timeout, cwd=args.cwd)
        output = {
            "passed": result.passed,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
        }
        print(json.dumps(output))
        sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    _cli()
