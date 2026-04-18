from __future__ import annotations

"""optimize_loop.py — Core loop mechanics for the build-loop optimize skill.

Responsibilities:
- Experiment state management (experiment.json)
- Result logging (results.tsv)
- Convergence detection (plateau, regression, budget)
- Metric execution coordination via metric_runner
- Target auto-detection (--detect)

NOT responsible for:
- Generating hypotheses (done by Claude agent)
- Editing code files (done by Claude agent)
- Running git commit / revert (done by Claude agent)
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
import datetime as _dt
from datetime import datetime
from pathlib import Path
from typing import Optional

from scripts.metric_runner import run_guard, run_metric


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class IterationResult:
    iteration: int
    commit: str
    metric_value: float
    delta: float
    status: str          # "keep" | "discard" | "error" | "baseline"
    description: str
    hypothesis: str = ""
    guard_passed: bool = True
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_LOOP_DIR = ".build-loop/optimize"
_EXPERIMENT_FILE = "experiment.json"
_RESULTS_FILE = "results.tsv"
_EXPERIMENTS_SUBDIR = "experiments"

_TSV_HEADER = "iteration\tcommit\tmetric\tdelta\tstatus\tdescription\thypothesis\n"


def _loop_dir(workdir: Path) -> Path:
    return workdir / _LOOP_DIR


def _experiment_path(workdir: Path) -> Path:
    return _loop_dir(workdir) / _EXPERIMENT_FILE


def _results_path(workdir: Path) -> Path:
    return _loop_dir(workdir) / _RESULTS_FILE


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _current_commit(workdir: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            check=False,
        )
        sha = result.stdout.strip()
        return sha if sha else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Experiment state
# ---------------------------------------------------------------------------

def load_experiment(workdir: Path) -> dict:
    return json.loads(_experiment_path(workdir).read_text())


def _save_experiment(workdir: Path, data: dict) -> None:
    path = _experiment_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Results log
# ---------------------------------------------------------------------------

def load_results(workdir: Path) -> list[dict]:
    path = _results_path(workdir)
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    if not lines:
        return []
    headers = lines[0].split("\t")
    rows: list[dict] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t", maxsplit=len(headers) - 1)
        row = dict(zip(headers, parts))
        rows.append(row)
    return rows


def log_iteration(workdir: Path, result: IterationResult) -> None:
    """Append one row to results.tsv.

    Format:
        iteration  commit  metric  delta  status  description  hypothesis
    """
    path = _results_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(_TSV_HEADER)

    delta_str = f"{result.delta:+.4f}" if result.iteration > 0 else "0.0"
    hypothesis = result.hypothesis.replace("\t", " ").replace("\n", " ")
    line = (
        f"{result.iteration}\t"
        f"{result.commit}\t"
        f"{result.metric_value:.4f}\t"
        f"{delta_str}\t"
        f"{result.status}\t"
        f"{result.description}\t"
        f"{hypothesis}\n"
    )
    with path.open("a") as fh:
        fh.write(line)


# ---------------------------------------------------------------------------
# Target auto-detection
# ---------------------------------------------------------------------------

def detect_targets(workdir: Path) -> list[dict]:
    """Scan workdir for optimization targets and return available profiles.

    Checks for: package.json scripts, test runners, skills/, docs/, build
    artifacts. Always includes 'simplify'.
    """
    targets: list[dict] = []

    # simplify is always available
    targets.append({
        "target": "simplify",
        "description": "Reduce total line count in recently changed files",
        "metric_cmd": "git diff --name-only HEAD~1 | xargs wc -l 2>/dev/null | tail -1 | awk '{print $1}'",
        "guard_cmd": None,
        "direction": "lower",
        "budget": 5,
        "scope": "git diff --name-only HEAD~1",
    })

    pkg_json = workdir / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            scripts = pkg.get("scripts", {})

            if "build" in scripts:
                targets.append({
                    "target": "optimize-build",
                    "description": "Reduce build time",
                    "metric_cmd": "/usr/bin/time -p npm run build 2>&1 | grep ^real | awk '{print $2}'",
                    "guard_cmd": "npm test -- --passWithNoTests",
                    "direction": "lower",
                    "budget": 5,
                    "scope": "*.config.*,tsconfig*.json",
                })

            if "test" in scripts:
                targets.append({
                    "target": "optimize-tests",
                    "description": "Increase test coverage",
                    "metric_cmd": "npm test -- --coverage --coverageReporters=text 2>&1 | grep 'All files' | awk '{print $4}'",
                    "guard_cmd": "npm test -- --passWithNoTests",
                    "direction": "higher",
                    "budget": 5,
                    "scope": "**/*.test.*,**/*.spec.*",
                })
        except (json.JSONDecodeError, KeyError):
            pass

    # Next.js / webpack bundle size
    next_static = workdir / ".next" / "static"
    if next_static.exists():
        targets.append({
            "target": "optimize-bundle",
            "description": "Reduce Next.js static bundle size",
            "metric_cmd": "du -sk .next/static 2>/dev/null | awk '{print $1}'",
            "guard_cmd": "npm run build",
            "direction": "lower",
            "budget": 5,
            "scope": "src/**/*",
        })

    return targets


# ---------------------------------------------------------------------------
# Experiment init
# ---------------------------------------------------------------------------

def init_experiment(workdir: Path, config: dict) -> Path:
    """Initialize a new experiment.

    Writes experiment.json with scope, metric_cmd, guard_cmd, and budget.
    Runs the metric command to establish a baseline.
    Writes iteration 0 (baseline) to results.tsv.

    Args:
        workdir: Root of the target repository.
        config: Dict with keys:
            - target (str): human name for the target
            - scope (str): glob pattern limiting which files change
            - metric_cmd (str): command whose stdout contains the numeric metric
            - guard_cmd (str|None): command that must exit 0 to accept a change
            - budget (int): maximum total iterations (kept + discarded + errors)
            - direction (str): "higher" or "lower"

    Returns:
        Path to the newly created experiment.json.
    """
    required = {"target", "metric_cmd"}
    missing = required - set(config)
    if missing:
        raise ValueError(f"config missing required keys: {missing}")

    loop_dir = _loop_dir(workdir)
    loop_dir.mkdir(parents=True, exist_ok=True)

    metric_result = run_metric(config["metric_cmd"], cwd=str(workdir))
    if not metric_result.success:
        raise RuntimeError(
            f"Baseline metric command failed: {metric_result.error}\n"
            f"Output: {metric_result.raw_output[:500]}"
        )

    baseline_commit = _current_commit(workdir)
    baseline_value = metric_result.value

    direction = config.get("direction", "higher")
    if direction not in ("higher", "lower"):
        raise ValueError(f"direction must be 'higher' or 'lower', got {direction!r}")

    experiment = {
        "target": config.get("target", "unnamed"),
        "scope": config.get("scope", "**/*"),
        "metric_cmd": config["metric_cmd"],
        "guard_cmd": config.get("guard_cmd"),
        "budget": int(config.get("budget", 5)),
        "direction": direction,
        "baseline_commit": baseline_commit,
        "baseline_value": baseline_value,
        "best_value": baseline_value,
        "prompt_version": config.get("prompt_version", "v1"),
        "started_at": datetime.now(_dt.timezone.utc).isoformat() + "Z",
        "iterations_kept": 0,
        "iterations_total": 0,
    }
    _save_experiment(workdir, experiment)

    results_path = _results_path(workdir)
    results_path.write_text(_TSV_HEADER)
    log_iteration(
        workdir,
        IterationResult(
            iteration=0,
            commit=baseline_commit,
            metric_value=baseline_value,
            delta=0.0,
            status="baseline",
            description="initial state",
        ),
    )

    return _experiment_path(workdir)


# ---------------------------------------------------------------------------
# Run one iteration
# ---------------------------------------------------------------------------

def run_iteration(workdir: Path, iteration: int) -> IterationResult:
    """Execute one iteration of the optimize loop.

    Steps:
    1. Run metric_cmd to get the current value.
    2. Compute delta from the running best (not baseline).
    3. Run guard_cmd (if configured) to check acceptability.
    4. Return an IterationResult — caller must do git commit/revert.

    NOTE: This function does NOT commit or revert. The Claude agent calling
    this function is responsible for all git operations.
    """
    experiment = load_experiment(workdir)
    # Compare against running best, not baseline.
    best = experiment.get("best_value", experiment["baseline_value"])
    metric_cmd = experiment["metric_cmd"]
    guard_cmd = experiment.get("guard_cmd")
    direction = experiment.get("direction", "higher")

    commit = _current_commit(workdir)

    metric_result = run_metric(metric_cmd, cwd=str(workdir))
    if not metric_result.success:
        result = IterationResult(
            iteration=iteration,
            commit=commit,
            metric_value=0.0,
            delta=0.0,
            status="error",
            description="metric command failed",
            guard_passed=False,
            error=metric_result.error,
        )
        _update_experiment_counters(workdir, kept=False, new_value=None)
        return result

    current_value = metric_result.value
    delta = current_value - best

    guard_passed = True
    if guard_cmd:
        guard_result = run_guard(guard_cmd, cwd=str(workdir))
        guard_passed = guard_result.passed

    improved = (direction == "higher" and delta > 0) or (direction == "lower" and delta < 0)
    if not guard_passed:
        status = "discard"
        description = "guard failed"
    elif improved:
        status = "keep"
        description = f"metric {current_value:.4f} (delta {delta:+.4f} vs best)"
    else:
        status = "discard"
        description = f"metric not improved {current_value:.4f} (delta {delta:+.4f} vs best)"

    kept = status == "keep"
    _update_experiment_counters(workdir, kept=kept, new_value=current_value if kept else None)

    return IterationResult(
        iteration=iteration,
        commit=commit,
        metric_value=current_value,
        delta=delta,
        status=status,
        description=description,
        guard_passed=guard_passed,
    )


def _update_experiment_counters(workdir: Path, kept: bool, new_value: float | None) -> None:
    experiment = load_experiment(workdir)
    experiment["iterations_total"] = experiment.get("iterations_total", 0) + 1
    if kept:
        experiment["iterations_kept"] = experiment.get("iterations_kept", 0) + 1
    if new_value is not None:
        experiment["best_value"] = new_value
    _save_experiment(workdir, experiment)


# ---------------------------------------------------------------------------
# Convergence detection
# ---------------------------------------------------------------------------

def check_convergence(workdir: Path) -> tuple[bool, str]:
    """Check whether the loop should stop.

    Stopping conditions (in priority order):
    1. budget exhausted — total iterations >= budget
    2. 5 consecutive discards/errors → "plateau"
    3. metric trending worse over the last 3 kept iterations → "regressing"

    Returns:
        (should_stop, reason) where reason is one of:
        "budget" | "plateau" | "regressing" | "" (continue)
    """
    experiment = load_experiment(workdir)
    budget = experiment.get("budget", 5)
    total = experiment.get("iterations_total", 0)

    # Budget = total iterations (kept + discarded + errors), not just kept.
    if total >= budget:
        return True, "budget"

    results = load_results(workdir)
    if not results:
        return False, ""

    non_baseline = [r for r in results if r.get("status") != "baseline"]

    # Plateau: 5 consecutive non-keep rows at the end.
    if len(non_baseline) >= 5:
        tail = non_baseline[-5:]
        if all(r.get("status") in ("discard", "error") for r in tail):
            return True, "plateau"

    # Regression: last 3 kept iterations show declining metric (direction-aware).
    direction = experiment.get("direction", "higher")
    kept_rows = [r for r in non_baseline if r.get("status") == "keep"]
    if len(kept_rows) >= 3:
        last_three = kept_rows[-3:]
        try:
            values = [float(r["metric"]) for r in last_three]
            if direction == "higher" and values[0] > values[1] > values[2]:
                return True, "regressing"
            if direction == "lower" and values[0] < values[1] < values[2]:
                return True, "regressing"
        except (KeyError, ValueError):
            pass

    return False, ""


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def get_experiment_summary(workdir: Path) -> dict:
    """Return a summary dict for the current experiment.

    Keys:
        total_iterations, kept, discarded, errors,
        baseline_value, current_best, improvement_pct,
        top_changes (list of up to 3 dicts with iteration/metric/delta/description)
    """
    experiment = load_experiment(workdir)
    results = load_results(workdir)

    baseline_value = experiment.get("baseline_value", 0.0)
    non_baseline = [r for r in results if r.get("status") != "baseline"]

    total = len(non_baseline)
    kept = [r for r in non_baseline if r.get("status") == "keep"]
    discarded = [r for r in non_baseline if r.get("status") == "discard"]
    errors = [r for r in non_baseline if r.get("status") == "error"]

    direction = experiment.get("direction", "higher")
    best_value = baseline_value
    for row in kept:
        try:
            v = float(row["metric"])
            if direction == "higher" and v > best_value:
                best_value = v
            elif direction == "lower" and v < best_value:
                best_value = v
        except (KeyError, ValueError):
            pass

    improvement_pct = 0.0
    if baseline_value != 0:
        improvement_pct = round((best_value - baseline_value) / abs(baseline_value) * 100, 2)

    # Top 3 kept changes by delta magnitude descending.
    top_changes: list[dict] = []
    for row in sorted(kept, key=lambda r: abs(float(r.get("delta", "0") or "0")), reverse=True)[:3]:
        try:
            top_changes.append(
                {
                    "iteration": int(row["iteration"]),
                    "metric": float(row["metric"]),
                    "delta": float(row.get("delta", "0") or "0"),
                    "description": row.get("description", ""),
                }
            )
        except (KeyError, ValueError):
            pass

    return {
        "target": experiment.get("target", "unnamed"),
        "total_iterations": total,
        "kept": len(kept),
        "discarded": len(discarded),
        "errors": len(errors),
        "baseline_value": baseline_value,
        "current_best": best_value,
        "improvement_pct": improvement_pct,
        "top_changes": top_changes,
    }


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def archive_experiment(workdir: Path) -> Path:
    """Move experiment.json and results.tsv to experiments/YYYY-MM-DD-<target>.*

    Returns:
        Path to the archived experiment.json.
    """
    experiment = load_experiment(workdir)
    target = experiment.get("target", "unnamed").lower().replace(" ", "-")
    date_str = datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    base_name = f"{date_str}-{target}"

    archive_dir = _loop_dir(workdir) / _EXPERIMENTS_SUBDIR
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Avoid silent clobber — append a counter suffix if the name exists.
    counter = 0
    suffix = ""
    while (archive_dir / f"{base_name}{suffix}.json").exists():
        counter += 1
        suffix = f"-{counter}"

    dest_json = archive_dir / f"{base_name}{suffix}.json"
    dest_tsv = archive_dir / f"{base_name}{suffix}.tsv"

    shutil.move(str(_experiment_path(workdir)), str(dest_json))

    results_path = _results_path(workdir)
    if results_path.exists():
        shutil.move(str(results_path), str(dest_tsv))

    return dest_json


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="optimize loop mechanics")
    parser.add_argument("--workdir", default=".", help="target repo root")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", action="store_true", help="initialize experiment")
    group.add_argument("--log", action="store_true", help="log an iteration result")
    group.add_argument("--check-convergence", action="store_true", help="check if loop should stop")
    group.add_argument("--archive", action="store_true", help="archive current experiment")
    group.add_argument("--summary", action="store_true", help="print experiment summary")
    group.add_argument("--detect", action="store_true", help="auto-discover optimization targets")

    parser.add_argument("--target", help="optimization target name")
    parser.add_argument("--scope", help="file scope glob")
    parser.add_argument("--metric-cmd", help="metric command")
    parser.add_argument("--guard-cmd", help="guard command")
    parser.add_argument("--budget", type=int, default=5)
    parser.add_argument("--direction", choices=["higher", "lower"], default="higher")

    parser.add_argument("--iteration", type=int, help="iteration number (for --log)")
    parser.add_argument("--commit", help="commit SHA (for --log)")
    parser.add_argument("--metric", type=float, help="metric value (for --log)")
    parser.add_argument("--delta", type=float, help="delta from best (for --log)")
    parser.add_argument("--status", choices=["keep", "discard", "error"], help="(for --log)")
    parser.add_argument("--description", help="change description (for --log)")
    parser.add_argument("--hypothesis", default="", help="hypothesis text (for --log)")
    parser.add_argument("--prompt-version", default="v1", help="prompt version tag (for --init)")

    args = parser.parse_args()
    workdir = Path(args.workdir).resolve()

    if args.detect:
        targets = detect_targets(workdir)
        print(json.dumps(targets, indent=2))

    elif args.init:
        config = {
            "target": args.target or "unnamed",
            "scope": args.scope or "**/*",
            "metric_cmd": args.metric_cmd,
            "guard_cmd": args.guard_cmd,
            "budget": args.budget,
            "direction": args.direction,
            "prompt_version": args.prompt_version,
        }
        if not config["metric_cmd"]:
            print("ERROR: --metric-cmd required for --init", file=sys.stderr)
            sys.exit(1)
        path = init_experiment(workdir, config)
        exp = load_experiment(workdir)
        print(f"Experiment initialized: {path}")
        print(f"Baseline: {exp['baseline_value']}")

    elif args.log:
        for field_name in ("iteration", "commit", "metric", "delta", "status", "description"):
            if getattr(args, field_name) is None:
                print(f"ERROR: --{field_name} required for --log", file=sys.stderr)
                sys.exit(1)
        result = IterationResult(
            iteration=args.iteration,
            commit=args.commit,
            metric_value=args.metric,
            delta=args.delta,
            status=args.status,
            description=args.description,
            hypothesis=args.hypothesis,
        )
        log_iteration(workdir, result)
        print(f"Logged iteration {args.iteration}: {args.status}")

    elif args.check_convergence:
        should_stop, reason = check_convergence(workdir)
        if should_stop:
            print(f"CONVERGED: {reason}")
            sys.exit(0)
        else:
            print("CONTINUE")
            sys.exit(1)

    elif args.archive:
        dest = archive_experiment(workdir)
        print(f"Archived to: {dest}")

    elif args.summary:
        summary = get_experiment_summary(workdir)
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    _cli()
