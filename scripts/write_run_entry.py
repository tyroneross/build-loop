#!/usr/bin/env python3
"""Deterministic Review-F writer for build-loop.

Appends a run entry to `.build-loop/state.json.runs[]` and, for each active
experimental artifact, appends an `applied` row to
`.build-loop/experiments/<name>.jsonl` with correct confound tracking.

Contract:
  stdout      -> run_id on success, nothing else
  stderr      -> human-readable log lines
  exit 0      -> success
  exit 1      -> validation error (bad args, missing required field, wrong type)
  exit 2      -> filesystem error (permission denied, disk full, lock timeout)

Atomicity: fcntl.flock(LOCK_EX) on a sidecar .lock file + tmpfile + os.replace.
Additive migration: existing non-runs[] top-level keys are never touched.
Zero dependencies. Python 3.11+.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOCK_TIMEOUT_S = 10
REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "run_id": str,
    "date": str,
    "goal": str,
    "outcome": str,
    "phases": dict,
    "filesTouched": list,
    "diagnosticCommands": list,
    "manualInterventions": list,
    "active_experimental_artifacts": list,
}
VALID_OUTCOMES = {"pass", "fail", "partial"}
VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def iso_basic_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def compute_run_id(goal: str, now: datetime | None = None) -> str:
    goal_hash = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:8]
    return f"run_{iso_basic_utc(now)}_{goal_hash}"


def iso_utc(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class LockedFile:
    """Exclusive fcntl.flock on a sidecar lockfile. Auto-released on close."""

    def __init__(self, target: Path, timeout_s: float = LOCK_TIMEOUT_S) -> None:
        self.lock_path = target.with_suffix(target.suffix + ".lock")
        self.timeout_s = timeout_s
        self._fd: int | None = None

    def __enter__(self) -> "LockedFile":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise TimeoutError(f"Could not acquire lock on {self.lock_path} within {self.timeout_s}s")
                time.sleep(0.05)

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class CorruptStateError(ValueError):
    """Raised when an existing state.json is present but unparseable."""


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise CorruptStateError(f"{path} is not valid JSON: {e}") from e


def append_run_entry(state_path: Path, entry: dict) -> None:
    with LockedFile(state_path):
        state = read_json(state_path)
        if state is None:
            state = {}
        if not isinstance(state, dict):
            raise ValueError(f"{state_path} is not a JSON object at top level")
        runs = state.get("runs")
        if runs is not None and not isinstance(runs, list):
            log(f"warn: existing 'runs' is not a list (got {type(runs).__name__}); preserving as 'runs_legacy'")
            state["runs_legacy"] = runs
            runs = None
        if runs is None:
            runs = []
            state["runs"] = runs
        runs.append(entry)
        atomic_write_bytes(state_path, (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def append_experiment_row(jsonl_path: Path, row: dict) -> None:
    with LockedFile(jsonl_path):
        existing = jsonl_path.read_bytes() if jsonl_path.exists() else b""
        line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        atomic_write_bytes(jsonl_path, existing + line)


def append_experiment_rows(experiments_dir: Path, run_id: str, active: list[str], outcome: str, date: str) -> None:
    for name in active:
        path = experiments_dir / f"{name}.jsonl"
        if not path.exists():
            log(f"warn: no baseline for experiment '{name}' at {path}; skipping applied row (run a Phase 6 Learn scan first)")
            continue
        co_applied = [n for n in active if n != name]
        row = {
            "event": "applied",
            "date": date,
            "run_id": run_id,
            "triggered": True,
            "metric_value": None,
            "outcome": outcome,
            "co_applied_experimental_artifacts": co_applied,
            "confounded": len(co_applied) > 0,
        }
        append_experiment_row(path, row)
        log(f"appended applied row to {path.name} (confounded={row['confounded']})")


def validate_entry(entry: dict) -> None:
    for field, expected in REQUIRED_FIELDS.items():
        if field not in entry:
            raise ValueError(f"missing required field: {field}")
        if not isinstance(entry[field], expected):
            raise ValueError(f"field {field!r} must be {expected.__name__ if isinstance(expected, type) else expected}, got {type(entry[field]).__name__}")
    if entry["outcome"] not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(VALID_OUTCOMES)}, got {entry['outcome']!r}")


def files_touched_from_git(workdir: Path, pre_sha: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(workdir), "diff", "--name-only", f"{pre_sha}..HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deterministic Review-F writer for build-loop.")
    p.add_argument("--workdir", required=True, help="Project root containing .build-loop/")
    p.add_argument("--goal", required=True, help="Short goal text for this build")
    p.add_argument("--outcome", required=True, choices=sorted(VALID_OUTCOMES))
    p.add_argument("--phases-json", default="{}", help="Per-phase status dict as JSON string")
    p.add_argument("--files-touched", default="", help="Comma-separated list of files touched")
    p.add_argument("--files-touched-from-git", action="store_true", help="Derive from git diff <preBuildSha>..HEAD (preBuildSha read from state.json)")
    p.add_argument("--diagnostic-commands", default="", help="Newline-separated commands run during build")
    p.add_argument("--manual-interventions-json", default="[]", help="JSON list of {phase, note} objects")
    p.add_argument("--active-experimental-artifacts", default="", help="Comma-separated experimental artifact names that triggered this run")
    p.add_argument("--run-id", default=None, help="Override run_id (default: compute from goal + now)")
    p.add_argument(
        "--security-findings-json",
        default=None,
        help=(
            "Path to a JSON file containing a list of security-reviewer findings (or '-' for stdin). "
            "Each element must be an object with at minimum 'mapped_risks' (list of strings) and "
            "'severity' (CRITICAL|HIGH|MEDIUM|LOW). Other fields (id, title, evidence, snippet, "
            "recommendation) pass through. When omitted, no 'security_findings' key is written."
        ),
    )
    return p.parse_args(argv)


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def load_security_findings(source: str) -> list[dict] | None:
    """Read findings JSON from a path or stdin ('-').

    Returns None when the file is missing or empty (caller should not write a 'security_findings'
    key — semantically equivalent to omitting the flag). Returns a list (possibly empty if the
    user explicitly passed `[]`) when the file decoded to a list shape.

    Validates that the decoded value is a list of objects, each with a 'mapped_risks' list of
    strings and a 'severity' string in VALID_SEVERITIES. Other fields pass through unchanged.
    Raises ValueError on malformed input.
    """
    if source == "-":
        raw = sys.stdin.read()
    else:
        path = Path(source)
        if not path.exists():
            log(f"note: --security-findings-json path {source} does not exist; treating as no findings (no security_findings key will be written)")
            return None
        raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"--security-findings-json is not valid JSON: {e}") from e
    # Accept either a bare list or the reviewer's full envelope ({"findings": [...], ...}).
    if isinstance(data, dict) and "findings" in data:
        data = data["findings"]
    if not isinstance(data, list):
        raise ValueError("--security-findings-json must decode to a list (or an object with a 'findings' list)")
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"security_findings[{i}] must be an object, got {type(item).__name__}")
        if "mapped_risks" not in item or not isinstance(item["mapped_risks"], list):
            raise ValueError(f"security_findings[{i}].mapped_risks must be a list of strings")
        if not all(isinstance(r, str) for r in item["mapped_risks"]):
            raise ValueError(f"security_findings[{i}].mapped_risks must contain only strings")
        sev = item.get("severity")
        if not isinstance(sev, str) or sev not in VALID_SEVERITIES:
            raise ValueError(f"security_findings[{i}].severity must be one of {sorted(VALID_SEVERITIES)}, got {sev!r}")
    return data


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0

    workdir = Path(args.workdir).resolve()
    build_dir = workdir / ".build-loop"
    state_path = build_dir / "state.json"
    experiments_dir = build_dir / "experiments"

    try:
        phases = json.loads(args.phases_json)
        if not isinstance(phases, dict):
            raise ValueError("--phases-json must decode to an object")
        manual_interventions = json.loads(args.manual_interventions_json)
        if not isinstance(manual_interventions, list):
            raise ValueError("--manual-interventions-json must decode to a list")
        security_findings = (
            load_security_findings(args.security_findings_json)
            if args.security_findings_json
            else None
        )
    except (json.JSONDecodeError, ValueError) as e:
        log(f"validation error: {e}")
        return 1

    files_touched = _split_csv(args.files_touched)
    if args.files_touched_from_git:
        state_existing = read_json(state_path) if state_path.exists() else {}
        pre_sha = state_existing.get("preBuildSha") if isinstance(state_existing, dict) else None
        if pre_sha:
            files_touched.extend(f for f in files_touched_from_git(workdir, pre_sha) if f not in files_touched)
        else:
            log("warn: --files-touched-from-git set but state.json has no preBuildSha; skipping git diff")

    active = _split_csv(args.active_experimental_artifacts)
    diagnostic_commands = [c for c in args.diagnostic_commands.splitlines() if c.strip()]
    run_id = args.run_id or compute_run_id(args.goal)
    date = iso_utc()

    entry = {
        "run_id": run_id,
        "date": date,
        "goal": args.goal,
        "outcome": args.outcome,
        "phases": phases,
        "diagnosticCommands": diagnostic_commands,
        "filesTouched": files_touched,
        "manualInterventions": manual_interventions,
        "active_experimental_artifacts": active,
    }
    if security_findings is not None:
        entry["security_findings"] = security_findings

    try:
        validate_entry(entry)
    except ValueError as e:
        log(f"validation error: {e}")
        return 1

    try:
        append_run_entry(state_path, entry)
        log(f"appended run entry to {state_path} (run_id={run_id})")
        if active:
            append_experiment_rows(experiments_dir, run_id, active, args.outcome, date)
    except CorruptStateError as e:
        log(f"validation error: {e}")
        return 1
    except TimeoutError as e:
        log(f"filesystem error: {e}")
        return 2
    except OSError as e:
        log(f"filesystem error: {e}")
        return 2

    print(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
modified
