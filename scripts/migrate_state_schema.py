#!/usr/bin/env python3
"""migrate_state_schema.py — normalize .build-loop/state.json files to canonical schema.

Scans known locations, detects the schema shape of each file, migrates to the
canonical schema (schema_version: 1.0.0 + runs[]). Preserves all unknown
top-level keys. Idempotent: re-running on already-canonical files is a no-op.

Usage:
    python3 scripts/migrate_state_schema.py              # dry-run, prints summary
    python3 scripts/migrate_state_schema.py --apply      # writes changes
    python3 scripts/migrate_state_schema.py --apply --backup    # writes + .bak.<ts>
    python3 scripts/migrate_state_schema.py --root /path/to/projects
    python3 scripts/migrate_state_schema.py --path /full/path/state.json
    python3 scripts/migrate_state_schema.py --self-test  # inline tests

Detection table:

    Canonical                       runs[] present AND schema_version == "1.0.0"
                                    Action: skip (no-op).

    Has runs but no version         runs[] present, no schema_version field
                                    Action: stamp schema_version = "1.0.0".

    Wave-based                      completed_waves[] present (older Travel
                                    Planner / Chunk-and-Wave schema)
                                    Action: convert each wave into a run record;
                                    archive original under legacy_completed_waves.

    Phase-snapshot / unknown        anything else (mid-build snapshots, empty
                                    files, third-party shapes)
                                    Action: add empty runs[] + schema_version,
                                    preserve all existing keys.

Stdlib only. Atomic write via tmpfile + os.replace. JSON parse errors and
permission errors skip the file with a log line, never abort the run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from glob import glob
from pathlib import Path
from typing import Any

CANONICAL_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_shape(d: dict) -> str:
    """Return one of: canonical, needs_version, wave_based, phase_snapshot_or_unknown."""
    runs = d.get("runs")
    if isinstance(runs, list) and d.get("schema_version") == CANONICAL_VERSION:
        return "canonical"
    if isinstance(runs, list):
        return "needs_version"
    if isinstance(d.get("completed_waves"), list):
        return "wave_based"
    return "phase_snapshot_or_unknown"


# ---------------------------------------------------------------------------
# Wave-based conversion
# ---------------------------------------------------------------------------

def _file_paths_from_evidence(tasks: dict) -> list[str]:
    """Best-effort extraction of file paths from a wave's tasks dict."""
    files: list[str] = []
    for _tid, t in tasks.items():
        if not isinstance(t, dict):
            continue
        for fix in t.get("preexisting_fixes", []) or []:
            if isinstance(fix, str):
                # Take the part before the first colon (typical "path:line msg" form).
                head = fix.split(":", 1)[0].strip()
                if head and "/" in head:
                    files.append(head)
    return sorted(set(files))


def convert_wave_to_run(wave: dict, fallback_started: str | None, fallback_build: str | None) -> dict:
    """Convert one wave entry (Example-Web-App-style) to a canonical run record.

    Best-effort. Marks the result with migrated_from: "wave_based" so the
    forensic trail stays clear in Phase 6 Learn output.
    """
    wave_num = wave.get("wave")
    tasks = wave.get("tasks") if isinstance(wave.get("tasks"), dict) else {}
    task_count = len(tasks)
    done_count = sum(
        1 for t in tasks.values()
        if isinstance(t, dict) and t.get("status") == "done"
    )
    outcome = "pass" if task_count > 0 and done_count == task_count else (
        "partial" if done_count > 0 else "fail"
    )
    goal_text = f"Wave {wave_num}: {fallback_build}" if fallback_build else f"Wave {wave_num}"
    # Stable run_id from a content hash of the wave (idempotent re-runs).
    seed = json.dumps(wave, sort_keys=True)[:1024]
    rid = f"run_legacy_wave{wave_num}_{abs(hash(seed)) & 0xFFFFFFFF:08x}"
    return {
        "run_id": rid,
        "date": fallback_started or "1970-01-01T00:00:00Z",
        "goal": goal_text,
        "outcome": outcome,
        "phases": {
            f"wave_{wave_num}": {
                "status": outcome,
                "task_count": task_count,
                "done_count": done_count,
            }
        },
        "filesTouched": _file_paths_from_evidence(tasks),
        "diagnosticCommands": [],
        "manualInterventions": [],
        "active_experimental_artifacts": [],
        "migrated_from": "wave_based",
    }


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate(d: dict, shape: str) -> tuple[dict, dict]:
    """Return (new_state, change_log) for the given schema shape.

    new_state is a fresh dict; the input is not mutated.
    """
    new_d: dict[str, Any] = dict(d)
    log: dict[str, Any] = {"shape": shape, "added_keys": [], "removed_keys": []}

    if shape == "canonical":
        return new_d, log

    if shape == "needs_version":
        new_d["schema_version"] = CANONICAL_VERSION
        log["added_keys"].append("schema_version")
        return new_d, log

    if shape == "wave_based":
        waves = d.get("completed_waves") or []
        runs: list[dict] = []
        for w in waves:
            if isinstance(w, dict):
                runs.append(convert_wave_to_run(w, d.get("started"), d.get("build")))
        new_d["runs"] = runs
        new_d["schema_version"] = CANONICAL_VERSION
        new_d["legacy_completed_waves"] = waves
        new_d.pop("completed_waves", None)
        log["added_keys"] = ["runs", "schema_version", "legacy_completed_waves"]
        log["removed_keys"] = ["completed_waves"]
        log["converted_waves"] = len(runs)
        return new_d, log

    # phase_snapshot_or_unknown
    new_d["runs"] = []
    new_d["schema_version"] = CANONICAL_VERSION
    log["added_keys"] = ["runs", "schema_version"]
    return new_d, log


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def atomic_write(path: Path, data: dict, *, backup: bool) -> None:
    """Write data to path atomically. Optionally save .bak.<ts> first."""
    if backup and path.exists():
        bak = path.parent / f"{path.name}.bak.{int(time.time())}"
        bak.write_bytes(path.read_bytes())

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".tmp.",
        suffix=".json",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_state_files(root: Path | None, extra_path: Path | None) -> list[Path]:
    if extra_path:
        return [extra_path]
    if root is None:
        root = Path.home() / "dev" / "git-folder"
    files: list[Path] = []
    for p in glob(str(root / "*" / ".build-loop" / "state.json")):
        files.append(Path(p))
    global_state = Path.home() / ".build-loop" / "state.json"
    if global_state.exists():
        files.append(global_state)
    return sorted(files)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Normalize all .build-loop/state.json files to canonical schema."
    )
    p.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    p.add_argument("--backup", action="store_true", help="Save .bak.<timestamp> before each write.")
    p.add_argument("--root", type=str, help="Project parent directory (default: ~/dev/git-folder).")
    p.add_argument("--path", type=str, help="Migrate one specific state.json path.")
    p.add_argument("--quiet", action="store_true", help="Suppress per-file logs.")
    p.add_argument("--self-test", action="store_true", help="Run inline self-test and exit.")
    args = p.parse_args(argv)

    if args.self_test:
        return run_self_test()

    root = Path(args.root).expanduser() if args.root else None
    extra = Path(args.path).expanduser() if args.path else None
    files = discover_state_files(root, extra)

    summary: dict[str, Any] = {
        "scanned": 0,
        "canonical": 0,
        "needs_version": 0,
        "wave_based": 0,
        "phase_snapshot_or_unknown": 0,
        "applied": 0,
        "errors": [],
    }

    for fpath in files:
        summary["scanned"] += 1
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            d = json.loads(content) if content.strip() else {}
        except (OSError, json.JSONDecodeError) as e:
            summary["errors"].append({"path": str(fpath), "error": str(e)})
            if not args.quiet:
                print(f"[ERR ] {fpath}: {e}", file=sys.stderr)
            continue

        if not isinstance(d, dict):
            summary["errors"].append({"path": str(fpath), "error": "top-level is not an object"})
            if not args.quiet:
                print(f"[ERR ] {fpath}: top-level is not an object", file=sys.stderr)
            continue

        shape = detect_shape(d)
        summary[shape] += 1

        if shape == "canonical":
            if not args.quiet:
                print(f"[skip] {fpath} (already canonical)", file=sys.stderr)
            continue

        new_d, log = migrate(d, shape)

        if args.apply:
            try:
                atomic_write(fpath, new_d, backup=args.backup)
                summary["applied"] += 1
                if not args.quiet:
                    print(
                        f"[apply] {fpath} ({shape}): +{log['added_keys']}"
                        f"{' -' + str(log['removed_keys']) if log.get('removed_keys') else ''}",
                        file=sys.stderr,
                    )
            except OSError as e:
                summary["errors"].append({"path": str(fpath), "error": f"write: {e}"})
                if not args.quiet:
                    print(f"[ERR ] {fpath} write: {e}", file=sys.stderr)
        else:
            if not args.quiet:
                detail = f"would +{log['added_keys']}"
                if log.get("removed_keys"):
                    detail += f", -{log['removed_keys']}"
                if log.get("converted_waves") is not None:
                    detail += f", convert {log['converted_waves']} wave(s)"
                print(f"[dry ] {fpath} ({shape}): {detail}", file=sys.stderr)

    print(json.dumps(summary, indent=2))
    return 1 if summary["errors"] else 0


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def run_self_test() -> int:
    failures: list[str] = []

    # 1. Canonical: no-op
    canonical = {"runs": [{"run_id": "x"}], "schema_version": "1.0.0", "branch": "main"}
    new, log = migrate(canonical, detect_shape(canonical))
    if log["shape"] != "canonical":
        failures.append(f"canonical detection (got {log['shape']})")
    if new != canonical:
        failures.append("canonical preserved")

    # 2. Needs version: add schema_version, preserve runs
    nv = {"runs": [{"run_id": "x"}]}
    new, log = migrate(nv, detect_shape(nv))
    if log["shape"] != "needs_version":
        failures.append(f"needs_version detection (got {log['shape']})")
    if new.get("schema_version") != "1.0.0":
        failures.append("needs_version stamp")
    if new.get("runs") != [{"run_id": "x"}]:
        failures.append("needs_version preserves runs")

    # 3. Wave-based: convert
    wb = {
        "started": "2026-01-01T00:00:00Z",
        "build": "test",
        "completed_waves": [
            {"wave": 1, "tasks": {"a": {"status": "done"}, "b": {"status": "done"}}}
        ],
    }
    new, log = migrate(wb, detect_shape(wb))
    if log["shape"] != "wave_based":
        failures.append(f"wave_based detection (got {log['shape']})")
    if not isinstance(new.get("runs"), list) or len(new["runs"]) != 1:
        failures.append("wave_based converted to runs")
    if new["runs"][0]["outcome"] != "pass":
        failures.append(f"wave_based all-done outcome (got {new['runs'][0]['outcome']})")
    if "legacy_completed_waves" not in new:
        failures.append("wave_based archived")
    if "completed_waves" in new:
        failures.append("wave_based old key removed")

    # 3b. Wave with mixed status -> partial
    wbm = {
        "started": "2026-01-01T00:00:00Z",
        "build": "x",
        "completed_waves": [{"wave": 2, "tasks": {"a": {"status": "done"}, "b": {"status": "wip"}}}],
    }
    new, _ = migrate(wbm, detect_shape(wbm))
    if new["runs"][0]["outcome"] != "partial":
        failures.append(f"wave_based partial outcome (got {new['runs'][0]['outcome']})")

    # 4. Phase snapshot: add empty runs, preserve other keys
    ps = {"phase": "execute", "goal": "test", "completedPhases": ["assess"]}
    new, log = migrate(ps, detect_shape(ps))
    if log["shape"] != "phase_snapshot_or_unknown":
        failures.append(f"phase_snapshot detection (got {log['shape']})")
    if new.get("runs") != []:
        failures.append("phase_snapshot empty runs")
    if new.get("schema_version") != "1.0.0":
        failures.append("phase_snapshot version")
    if new.get("phase") != "execute":
        failures.append("phase_snapshot preserved phase")
    if new.get("goal") != "test":
        failures.append("phase_snapshot preserved goal")

    # 5. Unknown: same treatment as phase_snapshot
    unk = {"foo": "bar"}
    new, log = migrate(unk, detect_shape(unk))
    if log["shape"] != "phase_snapshot_or_unknown":
        failures.append(f"unknown detection (got {log['shape']})")
    if new.get("runs") != []:
        failures.append("unknown empty runs")
    if new.get("foo") != "bar":
        failures.append("unknown preserved")

    # 6. Idempotency: migrate twice, second is no-op
    once, _ = migrate(unk, detect_shape(unk))
    twice, log2 = migrate(once, detect_shape(once))
    if log2["shape"] != "canonical":
        failures.append("idempotent re-detection")
    if twice != once:
        failures.append("idempotent re-migrate")

    # 7. Empty file (size 0): treated as unknown
    empty = {}
    new, log = migrate(empty, detect_shape(empty))
    if log["shape"] != "phase_snapshot_or_unknown":
        failures.append("empty file detection")
    if new.get("runs") != []:
        failures.append("empty file runs[] init")

    if failures:
        print("migrate_state_schema self-test FAILED:", file=sys.stderr)
        for fmsg in failures:
            print(f"  - {fmsg}", file=sys.stderr)
        return 1
    print("migrate_state_schema self-test PASS (7 cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
