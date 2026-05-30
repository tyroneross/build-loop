#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""self_review.py — deterministic data-gatherer for build-loop periodic self-review.

Mines recent activity for issues + efficiency signals, writes a human digest,
and enqueues candidate improvement items.  Does NO LLM calls and applies NO
code changes.  The host LLM (invoked separately by the cron wrapper) does the
reasoning and applying.

Frozen CLI (chunk G depends on this exactly):
  python3 scripts/self_review.py --mode {light|deep} [--workdir <repo>]
                                 [--days N] [--dry-run] --json

Output JSON shape:
  {
    "mode": "light"|"deep",
    "window_days": int,
    "mined": {
      "corrections": [...],
      "rituals": [...],
      "sequences": [...]
    },
    "efficiency_findings": [
      {
        "kind": str,
        "signal": str,
        "evidence": str,
        "suggested_action": str,
        "severity": "HIGH"|"MEDIUM"|"LOW"
      },
      ...
    ],
    "self_simplification": [
      {
        "kind": str,
        "signal": str,
        "evidence": str,
        "suggested_action": str,
        "severity": "HIGH"|"MEDIUM"|"LOW"
      },
      ...
    ],
    "digest_path": str | null,
    "queued": [str, ...],
    "errors": [str, ...],
    "dry_run": bool
  }

  ``self_simplification`` is only populated when the workdir IS the build-loop
  repo itself (self-recursive) AND mode == "deep".  It is always present as a
  list (possibly empty) in the output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent

# Heuristic thresholds for efficiency signals
_CHURN_THRESHOLD = 5       # files touched >= N times across window
_FAILURE_THRESHOLD = 2     # criterion failed in >= N distinct runs
_ITERATION_THRESHOLD = 3   # run with >= N iterate_attempt counts

# Proposal cap for light mode
_LIGHT_MODE_CAP = 10

# Self-simplification scan thresholds
_OVERSIZED_LINE_THRESHOLD = 600   # files > N lines → suggest split
_BUILD_LOOP_PLUGIN_NAME = "build-loop"

# Slug-safe characters
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, maxlen: int = 40) -> str:
    """Turn free text into a URL/filename-safe slug."""
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:maxlen].strip("-")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--mode",
        required=True,
        choices=["light", "deep"],
        help="light=7-day window, cap 10 proposals; deep=14-day, enqueue all",
    )
    p.add_argument(
        "--workdir",
        default=".",
        help="Project root containing .build-loop/ (default: cwd)",
    )
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override window in days (default: 7 for light, 14 for deep)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute everything but write nothing",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON to stdout (always implied; kept for compatibility)",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Transcript miner invocation
# ---------------------------------------------------------------------------

def _run_miner(
    workdir: Path,
    window_days: int,
    errors: list[str],
) -> dict[str, Any]:
    """Invoke transcript-pattern-miner.py as a subprocess; parse its output.

    Returns dict with keys: corrections, rituals, sequences.
    Fail-soft: any subprocess error is recorded in errors[] and empty results returned.
    """
    miner = HERE / "transcript-pattern-miner.py"
    empty: dict[str, Any] = {"corrections": [], "rituals": [], "sequences": []}

    if not miner.exists():
        errors.append(f"miner absent: {miner}")
        return empty

    # The miner writes its candidates JSON to a predictable path under its --out-dir.
    # We use a temp subdir under .build-loop/ to avoid polluting real transcript-patterns/.
    import tempfile
    tmp_out = workdir / ".build-loop" / "_self_review_miner_tmp"
    tmp_out.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(miner),
                "--days", str(window_days),
                "--out-dir", str(tmp_out),
                # Point at a non-existent sessions dir so the miner fast-exits
                # with empty results when transcripts aren't under the workdir.
                # The real sessions are at ~/.claude/projects/ which the miner
                # defaults to when --sessions-dir is not given.
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        errors.append(f"miner subprocess error: {exc}")
        return empty
    except subprocess.TimeoutExpired:
        errors.append("miner timed out after 120s")
        return empty
    except Exception as exc:  # noqa: BLE001
        errors.append(f"miner unexpected error: {exc}")
        return empty

    if result.returncode not in (0, 2):
        # returncode 2 = sessions dir not found (acceptable degradation)
        errors.append(
            f"miner exited {result.returncode}: "
            + (result.stderr or result.stdout or "")[:400]
        )
        return empty

    # Read candidates JSON if it was written
    candidates_path = tmp_out / ".candidates.json"
    if not candidates_path.exists():
        # Miner ran OK but produced no candidates (no sessions in window)
        return empty

    try:
        data = json.loads(candidates_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"could not parse miner candidates: {exc}")
        return empty

    candidates: list[dict[str, Any]] = data.get("candidates") or []
    corrections = [c for c in candidates if c.get("shape") == "user_correction_cluster"]
    rituals = [c for c in candidates if c.get("shape") == "bash_ritual"]
    sequences = [c for c in candidates if c.get("shape") == "repeated_tool_sequence"]

    return {"corrections": corrections, "rituals": rituals, "sequences": sequences}


# ---------------------------------------------------------------------------
# Efficiency scan (local, zero-LLM)
# ---------------------------------------------------------------------------

def _git_churn_files(workdir: Path, since_date: str, errors: list[str]) -> Counter[str]:
    """Return Counter(file -> touches) for files changed since since_date."""
    counter: Counter[str] = Counter()
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C", str(workdir),
                "log",
                f"--since={since_date}",
                "--name-only",
                "--pretty=format:",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        errors.append(f"git churn scan error: {exc}")
        return counter
    for line in out.splitlines():
        line = line.strip()
        if line:
            counter[line] += 1
    return counter


def _scan_state(
    workdir: Path,
    window_days: int,
    errors: list[str],
) -> list[dict[str, Any]]:
    """Read .build-loop/state.json runs[] and produce ranked efficiency_findings[]."""
    findings: list[dict[str, Any]] = []
    state_path = workdir / ".build-loop" / "state.json"

    if not state_path.exists():
        return findings

    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"state.json parse error: {exc}")
        return findings

    if not isinstance(state, dict):
        errors.append("state.json is not a JSON object")
        return findings

    runs: list[dict[str, Any]] = state.get("runs") or []
    if not isinstance(runs, list):
        errors.append("state.json.runs is not a list")
        return findings

    # Filter to window
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    window_runs: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        date_str = run.get("date") or run.get("created_at") or ""
        if date_str:
            try:
                parsed = dt.datetime.fromisoformat(
                    date_str.replace("Z", "+00:00") if date_str.endswith("Z") else date_str
                )
                if parsed < cutoff:
                    continue
            except (ValueError, TypeError):
                pass  # include if date unparseable
        window_runs.append(run)

    if not window_runs:
        return findings

    # --- Signal 1: phases that failed repeatedly across runs ---
    phase_failures: Counter[str] = Counter()
    for run in window_runs:
        phases = run.get("phases") or {}
        if not isinstance(phases, dict):
            continue
        for phase_name, phase_data in phases.items():
            if not isinstance(phase_data, dict):
                continue
            status = str(phase_data.get("status") or "").lower()
            if status in ("fail", "failed", "error"):
                phase_failures[phase_name] += 1

    for phase, count in phase_failures.most_common():
        if count >= _FAILURE_THRESHOLD:
            findings.append({
                "kind": "phase_repeated_failure",
                "signal": f"Phase '{phase}' failed in {count}/{len(window_runs)} runs",
                "evidence": f"phase={phase} failure_count={count} window_runs={len(window_runs)}",
                "suggested_action": (
                    f"Investigate recurring '{phase}' failures: review phase criteria, "
                    "tooling, or common root cause patterns"
                ),
                "severity": "HIGH" if count >= _FAILURE_THRESHOLD * 2 else "MEDIUM",
            })

    # --- Signal 2: criteria that recurred as failures ---
    criteria_failures: Counter[str] = Counter()
    for run in window_runs:
        phases = run.get("phases") or {}
        if not isinstance(phases, dict):
            continue
        for _phase_name, phase_data in phases.items():
            if not isinstance(phase_data, dict):
                continue
            criteria = phase_data.get("failed_criteria") or phase_data.get("criteria") or []
            if not isinstance(criteria, list):
                continue
            for criterion in criteria:
                if isinstance(criterion, str):
                    criteria_failures[criterion] += 1
                elif isinstance(criterion, dict):
                    label = criterion.get("name") or criterion.get("label") or ""
                    if label:
                        criteria_failures[str(label)] += 1

    for crit, count in criteria_failures.most_common(5):
        if count >= _FAILURE_THRESHOLD:
            findings.append({
                "kind": "criterion_recurring_failure",
                "signal": f"Criterion '{crit}' failed {count} times in window",
                "evidence": f"criterion={crit!r} failure_count={count}",
                "suggested_action": (
                    f"Add or strengthen automated check for criterion: {crit!r}"
                ),
                "severity": "HIGH" if count >= _FAILURE_THRESHOLD * 2 else "MEDIUM",
            })

    # --- Signal 3: long-running or repeatedly-iterating runs ---
    for run in window_runs:
        run_id = run.get("run_id", "(unknown)")
        execution = run.get("execution") or {}
        if isinstance(execution, dict):
            iterate_count = int(execution.get("iterate_attempt") or 0)
        else:
            # Some runs encode iterations at top level
            iterate_count = int(run.get("iterations") or 0)

        if iterate_count >= _ITERATION_THRESHOLD:
            findings.append({
                "kind": "high_iteration_run",
                "signal": f"Run {run_id} iterated {iterate_count} times",
                "evidence": f"run_id={run_id} iterate_attempt={iterate_count}",
                "suggested_action": (
                    "Review this run's manual interventions and failed criteria; "
                    "high iteration count signals unclear criteria or test gaps"
                ),
                "severity": "MEDIUM",
            })

    # --- Signal 4: escalations in runs ---
    escalation_count = 0
    for run in window_runs:
        escalations = run.get("escalations") or []
        if isinstance(escalations, list) and escalations:
            escalation_count += len(escalations)

    if escalation_count > 0:
        findings.append({
            "kind": "escalations_observed",
            "signal": f"{escalation_count} escalation(s) recorded in window",
            "evidence": f"total_escalations={escalation_count} window_runs={len(window_runs)}",
            "suggested_action": (
                "Review escalation contexts: ambiguous scope or missing decision rules "
                "are the most common root causes"
            ),
            "severity": "LOW",
        })

    return findings


def _scan_churn(
    workdir: Path,
    window_days: int,
    errors: list[str],
) -> list[dict[str, Any]]:
    """Scan git log for high-churn files."""
    findings: list[dict[str, Any]] = []
    since_date = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    ).strftime("%Y-%m-%d")
    churn = _git_churn_files(workdir, since_date, errors)
    for filepath, count in churn.most_common(5):
        if count >= _CHURN_THRESHOLD:
            findings.append({
                "kind": "high_churn_file",
                "signal": f"'{filepath}' changed {count} times in {window_days}d",
                "evidence": f"file={filepath!r} git_touches={count}",
                "suggested_action": (
                    f"Consider splitting or stabilising high-churn file: {filepath!r}. "
                    "Frequent edits often indicate unclear scope or missing tests."
                ),
                "severity": "LOW",
            })
    return findings


# ---------------------------------------------------------------------------
# Self-simplification scan (self-recursive deep mode only)
# ---------------------------------------------------------------------------

def _is_self_recursive(workdir: Path) -> bool:
    """Return True if workdir IS the build-loop repo itself.

    Two checks (either passing is sufficient to avoid false-negatives):
      1. .build-loop/state.json has selfRecursive.enabled == true
      2. .claude-plugin/plugin.json exists with name == "build-loop"
         AND scripts/self_review.py exists (canary)

    Fail-soft: any parse error → False.
    """
    # Check 1: explicit flag in state.json
    state_path = workdir / ".build-loop" / "state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            if isinstance(state, dict):
                sr = state.get("selfRecursive") or {}
                if isinstance(sr, dict) and sr.get("enabled") is True:
                    return True
        except (json.JSONDecodeError, OSError):
            pass

    # Check 2: plugin.json canary
    plugin_json = workdir / ".claude-plugin" / "plugin.json"
    if not plugin_json.exists():
        return False
    try:
        data = json.loads(plugin_json.read_text())
        if not isinstance(data, dict):
            return False
        name = data.get("name", "")
        if name != _BUILD_LOOP_PLUGIN_NAME:
            return False
    except (json.JSONDecodeError, OSError):
        return False
    # Canary: scripts/self_review.py must exist
    return (workdir / "scripts" / "self_review.py").exists()


def _get_changed_python_files(workdir: Path, window_days: int, errors: list[str]) -> list[str]:
    """Return Python files changed in the last window_days via git.

    Falls back to scripts/*.py if git is unavailable or returns nothing.
    """
    since_date = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    ).strftime("%Y-%m-%d")
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
        if files:
            return files
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: use git log --name-only across the window
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
        files = []
        for line in out.splitlines():
            line = line.strip()
            if line.endswith(".py") and line not in seen:
                p = workdir / line
                if p.exists():
                    seen.add(line)
                    files.append(str(p))
        if files:
            return files
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Final fallback: scan all scripts/*.py
    scripts_dir = workdir / "scripts"
    if scripts_dir.is_dir():
        return [str(f) for f in sorted(scripts_dir.glob("*.py"))]
    return []


def _run_complexity_detector(
    workdir: Path,
    py_files: list[str],
    errors: list[str],
) -> list[dict[str, Any]]:
    """Invoke complexity_detector.py on py_files; return hotspots list.

    Fail-soft: any error returns [].
    """
    detector = HERE / "complexity_detector.py"
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


def _scan_self_simplification(
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
    findings: list[dict[str, Any]] = []

    py_files = _get_changed_python_files(workdir, window_days, errors)

    # --- Complexity hotspots ---
    hotspots = _run_complexity_detector(workdir, py_files, errors)
    # Only surface "high" severity (not "advisory") to avoid noise
    high_hotspots = [h for h in hotspots if h.get("severity") == "high"]
    for h in high_hotspots:
        kind = h.get("kind", "complexity")
        file_ = h.get("file", "?")
        line = h.get("line", "?")
        reason = h.get("reason", "")
        # Map kind to severity tier
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

    # --- Oversized files ---
    scripts_dir = workdir / "scripts"
    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.glob("*.py")):
            if f.name.startswith("test_"):
                continue
            try:
                line_count = f.read_text(encoding="utf-8", errors="replace").count("\n")
            except OSError:
                continue
            if line_count > _OVERSIZED_LINE_THRESHOLD:
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

    # --- Missing tests ---
    if scripts_dir.is_dir():
        for f in sorted(scripts_dir.glob("*.py")):
            if f.name.startswith("test_") or f.name.startswith("_"):
                continue
            test_candidate = scripts_dir / f"test_{f.name}"
            if not test_candidate.exists():
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


def _rank_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by severity descending."""
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(findings, key=lambda f: order.get(f.get("severity", "LOW"), 2))


# ---------------------------------------------------------------------------
# classify_hint heuristic
# ---------------------------------------------------------------------------

def _classify_hint(finding: dict[str, Any]) -> str:
    """Heuristic classify_hint: SAFE for doc/test/lint; RISKY for runtime/multi-file."""
    kind = finding.get("kind", "")
    action = finding.get("suggested_action", "").lower()
    risky_signals = (
        "schema", "migration", "runtime", "multi-file", "split", "stabilising",
        "auth", "security", "production",
    )
    if any(s in action for s in risky_signals):
        return "RISKY"
    if kind in ("high_churn_file",):
        return "RISKY"
    if kind in ("phase_repeated_failure", "criterion_recurring_failure"):
        # Could be doc/test additions — lean SAFE
        return "SAFE"
    return "SAFE"


def _try_classify_action(workdir: Path, finding: dict[str, Any]) -> str:
    """Attempt to use classify_action.py's importable `classify()`. Fall back to heuristic."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "classify_action", HERE / "classify_action.py"
        )
        if spec is None or spec.loader is None:
            return _classify_hint(finding)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        result = mod.classify(
            workdir,
            command="",
            files_touched=[],
            envelope={},
        )
        # classify_action operates on commands/files; with no input it returns SAFE.
        # For our purposes the heuristic is more informative.
    except Exception:  # noqa: BLE001
        pass
    return _classify_hint(finding)


# ---------------------------------------------------------------------------
# Digest rendering
# ---------------------------------------------------------------------------

def _render_digest(
    *,
    mode: str,
    window_days: int,
    mined: dict[str, Any],
    efficiency_findings: list[dict[str, Any]],
    queued_paths: list[str],
    generated_at: dt.datetime,
    is_deep: bool,
) -> str:
    """Return the markdown digest string."""
    total = len(efficiency_findings) + len(mined.get("corrections") or [])
    # Determine top theme
    if efficiency_findings:
        top_theme = efficiency_findings[0].get("kind", "").replace("_", " ")
    elif mined.get("corrections"):
        top_theme = "recurring user corrections"
    else:
        top_theme = "no significant signals"

    lines: list[str] = []
    lines.append(
        f"# Self-Review Digest — {generated_at.date().isoformat()} ({mode})"
    )
    lines.append("")
    lines.append(
        f"**{total} finding(s)** | window: {window_days}d | top theme: {top_theme}"
    )
    lines.append(
        f"Generated: {generated_at.isoformat(timespec='seconds')} UTC"
    )
    lines.append("")

    # Mining findings
    lines.append("## Mining Findings")
    lines.append("")
    corrections = mined.get("corrections") or []
    rituals = mined.get("rituals") or []
    sequences = mined.get("sequences") or []
    if not (corrections or rituals or sequences):
        lines.append("_No mining findings in window._")
    else:
        for c in corrections:
            quote = (c.get("representative_quote") or "")[:200]
            lines.append(
                f"- **User correction cluster** (×{c.get('count', '?')}): {quote!r}"
            )
        for r in rituals:
            lines.append(
                f"- **Bash ritual** (×{r.get('count', '?')}): `{r.get('command_shape', '?')}`"
            )
        for s in sequences:
            seq = " → ".join(s.get("sequence") or [])
            lines.append(
                f"- **Tool sequence** ({s.get('session_count', '?')} sessions): `{seq}`"
            )
    lines.append("")

    # Efficiency findings
    lines.append("## Efficiency Findings")
    lines.append("")
    if not efficiency_findings:
        lines.append("_No efficiency signals detected._")
    else:
        for f in efficiency_findings:
            sev = f.get("severity", "?")
            signal = f.get("signal", "")
            evidence = f.get("evidence", "")
            lines.append(f"- [{sev}] {signal} — `{evidence}`")
    lines.append("")

    # Queued proposals
    lines.append("## Queued Proposals")
    lines.append("")
    if not queued_paths:
        lines.append("_No proposals enqueued._")
    else:
        for qp in queued_paths:
            lines.append(f"- `{qp}`")
    lines.append("")

    # Deep mode: Apply plan
    if is_deep and queued_paths:
        lines.append("## Apply plan")
        lines.append("")
        lines.append(
            "Items marked SAFE may be auto-applied by the host LLM. "
            "Items marked RISKY or DECISION require user confirmation."
        )
        lines.append("")
        lines.append("| Path | classify_hint |")
        lines.append("|---|---|")
        for qp in queued_paths:
            p = Path(qp)
            hint = "?"
            # Read hint from frontmatter
            try:
                content = p.read_text()
                for line in content.splitlines()[:10]:
                    if line.startswith("classify_hint:"):
                        hint = line.split(":", 1)[1].strip()
                        break
            except OSError:
                pass
            lines.append(f"| `{qp}` | {hint} |")
        lines.append("")

    lines.append("---")
    lines.append("Generated by `self_review.py`. No LLM. No network. Local stdlib only.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Proposal writing
# ---------------------------------------------------------------------------

def _make_frontmatter(
    *,
    source: str = "self-review",
    mode: str,
    severity: str,
    classify_hint: str,
    created_ts: str,
    target: str | None = None,
) -> str:
    lines = [
        "---",
        f"source: {source}",
        f"mode: {mode}",
        f"severity: {severity}",
        f"classify_hint: {classify_hint}",
        f"created_ts: {created_ts}",
    ]
    if target is not None:
        lines.append(f"target: {target}")
    lines += ["---", ""]
    return "\n".join(lines)


def _write_proposals(
    *,
    proposals_dir: Path,
    date_str: str,
    mode: str,
    efficiency_findings: list[dict[str, Any]],
    self_simplification: list[dict[str, Any]],
    mined: dict[str, Any],
    workdir: Path,
    created_ts: str,
    cap: int | None,
) -> list[str]:
    """Write one .md proposal file per actionable finding. Returns list of paths written."""
    proposals_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []

    # Combine: efficiency_findings first (already ranked), then mined corrections,
    # then self_simplification items (with target=self marker).
    items: list[dict[str, Any]] = list(efficiency_findings)
    for c in mined.get("corrections") or []:
        items.append({
            "kind": "user_correction_cluster",
            "signal": f"Recurring user correction (×{c.get('count', '?')})",
            "evidence": (c.get("representative_quote") or "")[:200],
            "suggested_action": (
                "Encode this correction pattern as a feedback_*.md note in CLAUDE.md "
                "or add a check/skill to prevent the need for future corrections."
            ),
            "severity": "MEDIUM",
            "_mined_data": c,
        })
    for r in mined.get("rituals") or []:
        items.append({
            "kind": "bash_ritual_candidate",
            "signal": f"Repeated bash ritual (×{r.get('count', '?')}): `{r.get('command_shape', '')}`",
            "evidence": r.get("command_shape", ""),
            "suggested_action": (
                "Consider wrapping this repeated shell shape in a script or adding it to /schedule."
            ),
            "severity": "LOW",
            "_mined_data": r,
        })
    # Self-simplification findings get target=self so deep-apply knows to pass them
    # through self_mod_verify before committing.
    for s in self_simplification:
        items.append({**s, "_target": "self"})

    if cap is not None:
        items = items[:cap]

    for idx, item in enumerate(items):
        severity = item.get("severity", "LOW")
        kind = item.get("kind", "finding")
        signal_text = item.get("signal", "")
        slug = _slug(f"{kind}-{signal_text}", 40)
        filename = f"self-review-{date_str}-{idx:02d}-{slug}.md"
        path = proposals_dir / filename
        # Self-simplification items are SAFE (no schema/runtime risk) by definition
        is_self = item.get("_target") == "self"
        hint = "SAFE" if is_self else _try_classify_action(workdir, item)

        frontmatter = _make_frontmatter(
            mode=mode,
            severity=severity,
            classify_hint=hint,
            created_ts=created_ts,
            target="self" if is_self else None,
        )
        body_lines = [
            f"## Finding: {item.get('signal', '')}",
            "",
            f"**Kind**: `{kind}`",
            f"**Severity**: {severity}",
            f"**Classify hint**: {hint}",
            "",
            "### Evidence",
            "",
            "```",
            item.get("evidence", ""),
            "```",
            "",
            "### Suggested fix",
            "",
            item.get("suggested_action", ""),
            "",
        ]
        if is_self:
            body_lines += [
                "### Self-modification gate",
                "",
                "This proposal modifies build-loop's own code. Before committing, run:",
                "```",
                "python3 scripts/self_mod_verify.py --workdir . --scope full "
                "--auto-revert --json",
                "```",
                "Commit ONLY if verdict == \"pass\".",
                "",
            ]
        # Include mined data if present
        mined_data = item.get("_mined_data")
        if mined_data:
            body_lines += [
                "### Mined data",
                "",
                "```json",
                json.dumps(
                    {k: v for k, v in mined_data.items() if k not in ("_mined_data", "_target")},
                    indent=2,
                ),
                "```",
                "",
            ]

        content = frontmatter + "\n".join(body_lines)
        path.write_text(content)
        paths.append(str(path))

    return paths


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(argv: list[str]) -> int:
    args = parse_args(argv)

    mode: str = args.mode
    is_deep: bool = mode == "deep"
    default_days = 14 if is_deep else 7
    window_days: int = args.days if args.days is not None else default_days
    dry_run: bool = args.dry_run
    workdir = Path(args.workdir).resolve()

    errors: list[str] = []
    now = dt.datetime.now(dt.timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    created_ts = now.isoformat(timespec="seconds")

    # Step 1: Mine transcripts (fail-soft)
    mined = _run_miner(workdir, window_days, errors)

    # Step 2: Efficiency scan
    efficiency_findings: list[dict[str, Any]] = []
    efficiency_findings.extend(_scan_state(workdir, window_days, errors))
    efficiency_findings.extend(_scan_churn(workdir, window_days, errors))
    efficiency_findings = _rank_findings(efficiency_findings)

    # Step 2b: Self-simplification scan (self-recursive + deep mode only)
    self_simplification: list[dict[str, Any]] = []
    if is_deep and _is_self_recursive(workdir):
        self_simplification = _scan_self_simplification(workdir, window_days, errors)

    # Step 3 + 4: Write digest + enqueue proposals (unless --dry-run)
    digest_path: str | None = None
    queued_paths: list[str] = []

    if not dry_run:
        # Proposals
        proposals_dir = workdir / ".build-loop" / "proposals"
        cap = _LIGHT_MODE_CAP if not is_deep else None
        try:
            queued_paths = _write_proposals(
                proposals_dir=proposals_dir,
                date_str=date_str,
                mode=mode,
                efficiency_findings=efficiency_findings,
                self_simplification=self_simplification,
                mined=mined,
                workdir=workdir,
                created_ts=created_ts,
                cap=cap,
            )
        except OSError as exc:
            errors.append(f"proposal write error: {exc}")

        # Digest
        review_dir = workdir / ".build-loop" / "self-review"
        review_dir.mkdir(parents=True, exist_ok=True)
        digest_filename = f"{date_str}-{mode}.md"
        digest_file = review_dir / digest_filename
        try:
            digest_content = _render_digest(
                mode=mode,
                window_days=window_days,
                mined=mined,
                efficiency_findings=efficiency_findings,
                queued_paths=queued_paths,
                generated_at=now,
                is_deep=is_deep,
            )
            digest_file.write_text(digest_content)
            digest_path = str(digest_file)
        except OSError as exc:
            errors.append(f"digest write error: {exc}")

    # Step 6: Emit JSON to stdout
    output: dict[str, Any] = {
        "mode": mode,
        "window_days": window_days,
        "mined": {
            "corrections": mined.get("corrections") or [],
            "rituals": mined.get("rituals") or [],
            "sequences": mined.get("sequences") or [],
        },
        "efficiency_findings": efficiency_findings,
        "self_simplification": self_simplification,
        "digest_path": digest_path,
        "queued": queued_paths,
        "errors": errors,
        "dry_run": dry_run,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))

    # Human summary to stderr
    n_findings = len(efficiency_findings)
    n_mined = (
        len(mined.get("corrections") or [])
        + len(mined.get("rituals") or [])
        + len(mined.get("sequences") or [])
    )
    print(
        f"self_review: mode={mode} window={window_days}d "
        f"efficiency_findings={n_findings} mined={n_mined} "
        f"self_simplification={len(self_simplification)} "
        f"queued={len(queued_paths)} errors={len(errors)} "
        f"dry_run={dry_run}",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
