#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""dedupe_run_ledger.py — one-shot repair for `.build-loop/state.json.runs[]`.

`write_run_entry` now upserts on `run_id`, so new duplicates cannot be created.
Ledgers written before that fix may still carry two rows for one run_id, which
double-counts the run for every consumer that aggregates over `runs[]` — Phase 6
Learn's sample counter and recurring-pattern-detector's 3-run threshold.

This script collapses those rows using the same merge rule the writer uses: the
LATER row wins on value (it is the correction), the EARLIEST row's index is kept
(ledger ordering does not shuffle), and evidence blocks the later row omitted
carry forward rather than being erased.

Report-only by default. `--apply` writes, under the same lock + atomic replace
the writer uses.

CLI::

    python3 scripts/dedupe_run_ledger.py --workdir <repo> [--apply] [--json]
    python3 scripts/dedupe_run_ledger.py --state <path/to/state.json> --apply --json

Exit 0 = inspected or repaired (including "nothing to do").
Exit 1 = bad arguments or unreadable/unparseable state.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
for _d in (str(_SCRIPTS_DIR), str(_SCRIPTS_DIR / "write_run_entry")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

try:
    from atomic_io import LockedFile, atomic_write_bytes  # type: ignore
except ImportError:  # package import: python3 -m scripts.dedupe_run_ledger
    from scripts.atomic_io import LockedFile, atomic_write_bytes  # type: ignore

try:
    from iohelpers import dedupe_runs  # type: ignore
except ImportError:  # package import
    from scripts.write_run_entry.iohelpers import dedupe_runs  # type: ignore


def _encode(state: object) -> bytes:
    return (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def repair(state_path: Path, *, apply: bool = False) -> dict:
    """Inspect (and optionally repair) duplicate run_id rows in one state.json."""
    result: dict = {
        "path": str(state_path),
        "applied": False,
        "rows_before": 0,
        "rows_after": 0,
        "duplicate_run_ids": [],
        "rows_removed": 0,
        "error": None,
    }
    if not state_path.exists():
        result["error"] = "state.json not found"
        return result
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = f"unreadable state.json: {exc}"
        return result
    if not isinstance(state, dict):
        result["error"] = "state.json root is not a JSON object"
        return result

    runs = state.get("runs")
    if not isinstance(runs, list):
        result["error"] = "state.json has no runs[] list"
        return result

    deduped, duplicates = dedupe_runs(runs)
    result["rows_before"] = len(runs)
    result["rows_after"] = len(deduped)
    result["duplicate_run_ids"] = duplicates
    result["rows_removed"] = len(runs) - len(deduped)

    if apply and result["rows_removed"]:
        with LockedFile(state_path):
            # Re-read under the lock: another writer may have landed since the
            # inspection read above.
            current = json.loads(state_path.read_text(encoding="utf-8"))
            current_runs = current.get("runs")
            if not isinstance(current_runs, list):
                result["error"] = "runs[] disappeared under the lock"
                return result
            fresh, fresh_dupes = dedupe_runs(current_runs)
            current["runs"] = fresh
            atomic_write_bytes(state_path, _encode(current))
            result["rows_before"] = len(current_runs)
            result["rows_after"] = len(fresh)
            result["rows_removed"] = len(current_runs) - len(fresh)
            result["duplicate_run_ids"] = fresh_dupes
            result["applied"] = True
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Collapse duplicate run_id rows in state.json.runs[].")
    p.add_argument("--workdir", help="Project root containing .build-loop/state.json")
    p.add_argument("--state", help="Explicit path to a state.json (overrides --workdir)")
    p.add_argument("--apply", action="store_true", help="Write the repair (default: report only)")
    p.add_argument("--json", action="store_true", help="Emit the report as JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.state:
        state_path = Path(args.state).resolve()
    elif args.workdir:
        state_path = Path(args.workdir).resolve() / ".build-loop" / "state.json"
    else:
        print("error: one of --workdir or --state is required", file=sys.stderr)
        return 1

    result = repair(state_path, apply=args.apply)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["error"]:
            print(f"error: {result['error']} ({result['path']})", file=sys.stderr)
        else:
            verb = "repaired" if result["applied"] else "inspected"
            print(
                f"{verb} {result['path']}: rows {result['rows_before']} -> {result['rows_after']} "
                f"({result['rows_removed']} removed, duplicate ids: "
                f"{', '.join(result['duplicate_run_ids']) or 'none'})"
            )
    return 1 if result["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
