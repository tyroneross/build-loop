# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math
import re
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from complexity_detector import analyze_source
except ImportError:  # pragma: no cover - only when imported outside scripts/
    analyze_source = None


@dataclass
class MetricResult:
    value: float
    raw_output: str
    elapsed_seconds: float
    success: bool
    error: Optional[str] = None
    samples_run: int = 1
    warmups_run: int = 0
    aggregate: str = "last"
    sample_values: list[float] = field(default_factory=list)
    summary: dict[str, float] = field(default_factory=dict)


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

_ALLOWED_AGGREGATES = {"last", "min", "max", "mean", "median", "p95"}
_DEPENDENCY_FILES = {
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements.in",
    "cargo.toml",
    "go.mod",
}

_ABSTRACTION_PATTERNS = [
    re.compile(r"^\+\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\+\s*(?:export\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    re.compile(r"^\+\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>"),
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


def _run_metric_once(cmd: str, timeout: int, cwd: str | None) -> MetricResult:
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


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    lower_value = ordered[lower]
    upper_value = ordered[upper]
    weight = index - lower
    return lower_value + (upper_value - lower_value) * weight


def _summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    summary = {
        "count": float(len(values)),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p95": _percentile(values, 0.95),
    }
    if len(values) > 1:
        summary["stdev"] = statistics.stdev(values)
    return {key: round(value, 6) for key, value in summary.items()}


def _aggregate_value(values: list[float], aggregate: str) -> float:
    if aggregate not in _ALLOWED_AGGREGATES:
        raise ValueError(
            f"Unsupported aggregate {aggregate!r}. Expected one of {sorted(_ALLOWED_AGGREGATES)}"
        )
    if not values:
        raise ValueError("At least one measured sample is required")
    summary = _summarize(values)
    if aggregate == "last":
        return values[-1]
    if aggregate == "min":
        return summary["min"]
    if aggregate == "max":
        return summary["max"]
    if aggregate == "mean":
        return summary["mean"]
    if aggregate == "median":
        return summary["median"]
    return summary["p95"]


def run_metric(
    cmd: str,
    timeout: int = 300,
    cwd: str | None = None,
    *,
    samples: int = 1,
    warmups: int = 0,
    aggregate: str = "last",
) -> MetricResult:
    """Run a metric command and extract a numeric result.

    Args:
        cmd: Shell command whose stdout contains a parseable numeric value.
        timeout: Maximum seconds to wait per invocation (default 300).
        cwd: Working directory for the command (default: current directory).
        samples: Number of measured runs to aggregate (default 1).
        warmups: Number of warmup runs to discard before measuring.
        aggregate: How to combine measured samples.

    Returns:
        MetricResult with aggregate value, raw_output, elapsed_seconds, and
        per-sample summary metadata.
    """
    if samples < 1:
        raise ValueError("samples must be >= 1")
    if warmups < 0:
        raise ValueError("warmups must be >= 0")
    if aggregate not in _ALLOWED_AGGREGATES:
        raise ValueError(
            f"Unsupported aggregate {aggregate!r}. Expected one of {sorted(_ALLOWED_AGGREGATES)}"
        )

    total_elapsed = 0.0
    sample_values: list[float] = []
    output_chunks: list[str] = []
    total_runs = warmups + samples

    for run_index in range(total_runs):
        result = _run_metric_once(cmd, timeout=timeout, cwd=cwd)
        total_elapsed += result.elapsed_seconds
        phase = "warmup" if run_index < warmups else "sample"
        ordinal = run_index + 1 if phase == "warmup" else run_index - warmups + 1
        output_chunks.append(f"## {phase} {ordinal}\n{result.raw_output.strip()}")
        if not result.success:
            return MetricResult(
                value=0.0,
                raw_output="\n\n".join(chunk for chunk in output_chunks if chunk.strip()),
                elapsed_seconds=total_elapsed,
                success=False,
                error=f"{phase} {ordinal} failed: {result.error}",
                samples_run=len(sample_values),
                warmups_run=min(run_index, warmups),
                aggregate=aggregate,
                sample_values=sample_values,
                summary=_summarize(sample_values),
            )
        if run_index >= warmups:
            sample_values.append(result.value)

    value = _aggregate_value(sample_values, aggregate)
    return MetricResult(
        value=value,
        raw_output="\n\n".join(chunk for chunk in output_chunks if chunk.strip()),
        elapsed_seconds=total_elapsed,
        success=True,
        samples_run=len(sample_values),
        warmups_run=warmups,
        aggregate=aggregate,
        sample_values=sample_values,
        summary=_summarize(sample_values),
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


def _git(args: list[str], cwd: str | None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def _diff_args(base: str, head: str) -> list[str]:
    return [f"{base}..{head}"]


def _changed_files(base: str, head: str, cwd: str | None) -> list[str]:
    out = _git(["diff", "--name-only", *_diff_args(base, head)], cwd)
    return [line.strip() for line in out.splitlines() if line.strip()]


def _loc_delta(base: str, head: str, cwd: str | None) -> dict[str, int]:
    out = _git(["diff", "--numstat", *_diff_args(base, head)], cwd)
    added = 0
    deleted = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            added += int(parts[0])
            deleted += int(parts[1])
        except ValueError:
            continue
    return {"added": added, "deleted": deleted, "net": added - deleted}


def _dependency_delta(base: str, head: str, cwd: str | None, files: list[str]) -> dict[str, object]:
    manifest_files = [path for path in files if Path(path).name.lower() in _DEPENDENCY_FILES]
    added: list[str] = []
    removed: list[str] = []
    for path in manifest_files:
        diff = _git(["diff", *_diff_args(base, head), "--", path], cwd)
        for line in diff.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added.append(line[1:].strip())
            elif line.startswith("-"):
                removed.append(line[1:].strip())
    return {
        "manifest_files_changed": manifest_files,
        "added": added,
        "removed": removed,
    }


def _new_abstractions(base: str, head: str, cwd: str | None, files: list[str]) -> list[dict[str, str]]:
    abstractions: list[dict[str, str]] = []
    for path in files:
        diff = _git(["diff", *_diff_args(base, head), "--", path], cwd)
        for line in diff.splitlines():
            if line.startswith("+++") or not line.startswith("+"):
                continue
            for pattern in _ABSTRACTION_PATTERNS:
                match = pattern.match(line)
                if match:
                    abstractions.append({"file": path, "name": match.group(1)})
                    break
    return abstractions


def _show_or_empty(rev: str, path: str, cwd: str | None) -> str:
    proc = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _complexity_counts(source_by_file: dict[str, str]) -> dict[str, int]:
    counts = {"high": 0, "advisory": 0, "total": 0}
    if analyze_source is None:
        return counts
    for path, source in source_by_file.items():
        if not source.strip():
            continue
        try:
            hotspots = analyze_source(source, path)
        except SyntaxError:
            continue
        for hotspot in hotspots:
            severity = hotspot.get("severity")
            if severity in ("high", "advisory"):
                counts[severity] += 1
            counts["total"] += 1
    return counts


def _complexity_delta(base: str, head: str, cwd: str | None, files: list[str]) -> dict[str, object] | None:
    py_files = [path for path in files if path.endswith(".py")]
    if not py_files or analyze_source is None:
        return None
    before_sources = {path: _show_or_empty(base, path, cwd) for path in py_files}
    after_sources = {path: _show_or_empty(head, path, cwd) for path in py_files}
    before = _complexity_counts(before_sources)
    after = _complexity_counts(after_sources)
    return {
        "before": before,
        "after": after,
        "delta": {key: after[key] - before[key] for key in ("high", "advisory", "total")},
    }


def run_simplicity_metrics(base: str, head: str = "HEAD", cwd: str | None = None) -> dict[str, object]:
    """Return Review-G simplicity metrics for a git diff range."""
    files = _changed_files(base, head, cwd)
    loc = _loc_delta(base, head, cwd)
    return {
        "net_loc": loc["net"],
        "loc": loc,
        "complexity_delta": _complexity_delta(base, head, cwd, files),
        "dependency_delta": _dependency_delta(base, head, cwd, files),
        "new_abstractions": _new_abstractions(base, head, cwd, files),
        "files_changed": files,
    }


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
    group.add_argument("--simplicity-diff", metavar="BASE", help="emit Review-G simplicity metrics from BASE..HEAD")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--cwd", default=None, help="working directory for the command")
    parser.add_argument("--head", default="HEAD", help="head revision for --simplicity-diff")
    parser.add_argument("--samples", type=int, default=1, help="measured runs to aggregate")
    parser.add_argument("--warmups", type=int, default=0, help="warmup runs to discard")
    parser.add_argument(
        "--aggregate",
        choices=sorted(_ALLOWED_AGGREGATES),
        default="last",
        help="aggregation applied to measured runs",
    )

    args = parser.parse_args()

    if args.cmd:
        result = run_metric(
            args.cmd,
            timeout=args.timeout,
            cwd=args.cwd,
            samples=args.samples,
            warmups=args.warmups,
            aggregate=args.aggregate,
        )
        output = {
            "value": result.value,
            "success": result.success,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
            "error": result.error,
            "samples_run": result.samples_run,
            "warmups_run": result.warmups_run,
            "aggregate": result.aggregate,
            "sample_values": result.sample_values,
            "summary": result.summary,
        }
        print(json.dumps(output))
        sys.exit(0 if result.success else 1)
    if args.guard:
        result = run_guard(args.guard, timeout=args.timeout, cwd=args.cwd)
        output = {
            "passed": result.passed,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
        }
        print(json.dumps(output))
        sys.exit(0 if result.passed else 1)
    output = run_simplicity_metrics(args.simplicity_diff, head=args.head, cwd=args.cwd)
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    _cli()
