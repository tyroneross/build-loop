#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""io.py — JSON read/write primitives for write_run_entry."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Flat intra-package import (works when package dir is on sys.path, as set by __init__.py)
from atomic_io import LockedFile, atomic_write_bytes  # type: ignore  # noqa: E402,F401


# A run's row is written by MORE THAN ONE process: write_run_entry (Review-G),
# append_run (the Stop hook), scripts/learn/runner.py (`learn`), and
# stop_closeout.py (`auditor_status`, `branch_closeout`, `createdRefs`,
# `provenance`). An allowlist of keys to carry forward can only ever enumerate
# what THIS writer knows about, so it silently deletes every other writer's
# evidence on a correction. The merge therefore starts from the existing row and
# lets the incoming entry overwrite it, which preserves position, lets a
# correction win on every field it carries, and cannot lose a key this module
# has never heard of. A field that must be REMOVED is removed explicitly, never
# by omission.


# Keys that identify the WRITER rather than the run. `source: append_run` marks a
# thin Stop-hook record, and append_run.py and stop_closeout.py both refuse to
# overwrite a row whose source is anything else — so a Review-G write that kept
# the label would invite the Stop hook to clobber a record it must not.
# run_close_lint reads a SECOND writer signature that this merge cannot change:
# a `hook_` run-id prefix marks scripts/audit_before_commit.py, and run_id is the
# merge key, so a Review-G write over a `hook_*` row still reads as floor-grade.
# Dropping `source` is necessary, not sufficient.
_WRITER_OWNED_KEYS = ("source",)


# The runs[] fields a CLI fills with an EMPTY default when its flag is not
# supplied. Canonical here, next to the merge that acts on it — __main__ imports
# it rather than keeping a second copy that can drift.
OMISSION_SENSITIVE_FIELDS = (
    "phases",
    "filesTouched",
    "diagnosticCommands",
    "manualInterventions",
    "active_experimental_artifacts",
)


def empty_collection_fields(row: dict) -> set[str]:
    """Omission-sensitive fields that are falsy on a row.

    For a HISTORICAL row there is no caller left to ask what was supplied, and
    the pre-fix writer always wrote `filesTouched: []` and `phases: {}` on a
    goal-only correction. An empty collection on such a row therefore carries no
    information about that field and must not overwrite a recorded one.

    This deliberately DIVERGES from the live CLI, which marks a field defaulted
    only when its flag was absent and so honours an explicit `--files-touched ""`
    as a deliberate clear. A historical row records no such distinction, so the
    repair cannot honour it and biases toward preserving data. The cost is that a
    deliberate clear captured in a pre-fix duplicate row is not replayed; the
    alternative is deleting a recorded file set on a guess, which is the defect
    this tool repairs.
    """
    return {f for f in OMISSION_SENSITIVE_FIELDS if not row.get(f)}


def upsert_merge(existing: dict, entry: dict, defaulted: set[str] | None = None) -> dict:
    """Build the replacement row for an existing run_id.

    `defaulted` names fields the caller filled with an EMPTY default rather than
    a supplied value. A CLI cannot otherwise tell "not supplied this pass" from
    "deliberately empty", and the difference decides whether a correction that
    restates only --goal wipes the run's filesTouched, phases, and the auditor
    verdict scoped to them. An empty default never overwrites a non-empty
    recorded value; an explicitly supplied empty value still does.

    Returns a new dict — neither argument is mutated, so callers keep their own
    entry intact.
    """
    defaulted = defaulted or set()
    merged = dict(existing)
    for key, value in entry.items():
        if key in defaulted and not value and existing.get(key):
            continue
        merged[key] = value
    for key in _WRITER_OWNED_KEYS:
        if key not in entry:
            merged.pop(key, None)
    # A judge verdict is scoped to the file set it was rendered against. A
    # correction that CHANGES filesTouched and supplies no new verdict would
    # otherwise re-attribute the old one to files no judge ever saw, so the
    # stale verdict is dropped rather than silently re-scoped. A correction that
    # merely does not restate the file set has changed nothing and keeps it.
    if (
        "judge_decisions" not in entry
        and "filesTouched" not in defaulted
        and "filesTouched" in entry
        and entry.get("filesTouched") != existing.get("filesTouched")
    ):
        merged.pop("judge_decisions", None)
    return merged


def dedupe_runs(runs: list) -> tuple[list, list[str]]:
    """Collapse duplicate run_id rows, keeping each id at its FIRST position.

    Later rows for the same run_id win on value (they are the corrections) while
    the earliest row's index is preserved, so ledger ordering does not shuffle.
    Returns (deduped_rows, duplicate_run_ids).
    """
    index_of: dict[str, int] = {}
    out: list = []
    duplicates: list[str] = []
    for row in runs:
        run_id = row.get("run_id") if isinstance(row, dict) else None
        if not isinstance(run_id, str) or not run_id:
            out.append(row)
            continue
        if run_id in index_of:
            i = index_of[run_id]
            prior = out[i]
            # Pass the later row's empty collections as defaulted. Without this
            # the repair tool applies the PRE-FIX writer's own wipe: that writer
            # always emitted filesTouched [] and phases {} on a goal-only
            # correction, which is the duplicate shape this tool exists to heal.
            out[i] = (
                upsert_merge(prior, row, empty_collection_fields(row))
                if isinstance(prior, dict)
                else row
            )
            if run_id not in duplicates:
                duplicates.append(run_id)
            continue
        index_of[run_id] = len(out)
        out.append(row)
    return out, duplicates


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


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


def _encode(state: Any) -> bytes:
    return (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def append_run_entry(state_path: Path, entry: dict, defaulted: set[str] | None = None) -> str:
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
        # UPSERT on run_id: one run_id owns exactly one row. A prior row may be a
        # thin Stop-hook record (source: append_run, written before this Review-G
        # write) or an earlier Review-G record this write is CORRECTING. Either
        # way, replace it in place at its original index. Blind-appending a second
        # row double-counts the run for every consumer that aggregates over runs[]
        # — Phase 6 Learn's sample counter and recurring-pattern-detector's 3-run
        # threshold — and lets judgment_gate resolve the stale row.
        run_id = entry.get("run_id")
        if run_id:
            matches = [
                i for i, r in enumerate(runs)
                if isinstance(r, dict) and r.get("run_id") == run_id
            ]
            if matches:
                # Replace at the FIRST match and drop the rest. A ledger written
                # before this fix can already hold two rows for one run_id;
                # returning on the first match would correct one and leave the
                # stale twin, so the writer heals what it finds rather than
                # waiting for dedupe_run_ledger.py to be remembered.
                #
                # Fold the extra rows IN before applying the incoming entry.
                # Deleting them unmerged would discard whatever they alone carry
                # — a later row can hold a security_findings payload the first
                # row never had — and would make the writer's heal disagree with
                # dedupe_run_ledger.py on the same inputs. Reusing the repair's
                # own merge here makes the two identical by construction rather
                # than by coincidence.
                first = matches[0]
                healed = runs[first]
                for i in matches[1:]:
                    healed = upsert_merge(healed, runs[i], empty_collection_fields(runs[i]))
                runs[first] = upsert_merge(healed, entry, defaulted)
                for i in reversed(matches[1:]):
                    del runs[i]
                atomic_write_bytes(state_path, _encode(state))
                return "updated" if len(matches) == 1 else "deduplicated"
        runs.append(entry)
        atomic_write_bytes(state_path, _encode(state))
        return "appended"


def append_experiment_row(jsonl_path: Path, row: dict) -> None:
    """Record one experiment row, UPSERTING on (event, run_id).

    The run ledger upserts a run_id but this log used to append unconditionally,
    so re-running the closing writer for one run -- a correction, a retry, a
    Stop hook firing after Review-G -- manufactured N measured samples from one
    run. The sweep counts ROWS, so eight writes of the same run_id could satisfy
    an eight-run floor on its own, and a corrected outcome left the superseded
    value in the average. One run contributes one row; a rewrite replaces it.

    A row with no `run_id` cannot be identified, so it appends as before.
    """
    with LockedFile(jsonl_path):
        line = json.dumps(row, ensure_ascii=False) + "\n"
        run_id = row.get("run_id")
        event = row.get("event")
        if run_id and jsonl_path.exists():
            lines = jsonl_path.read_text(encoding="utf-8").splitlines(keepends=True)
            for index, raw in enumerate(lines):
                try:
                    prior = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if (
                    isinstance(prior, dict)
                    and prior.get("run_id") == run_id
                    and prior.get("event") == event
                ):
                    lines[index] = line
                    atomic_write_bytes(jsonl_path, "".join(lines).encode("utf-8"))
                    return
        existing = jsonl_path.read_bytes() if jsonl_path.exists() else b""
        atomic_write_bytes(jsonl_path, existing + line.encode("utf-8"))


def experiment_log_path(experiments_dir: Path, name: str) -> Path | None:
    """The artifact's log path, or None when the name escapes the directory.

    Artifact names reach here from a CLI flag and were concatenated straight
    into a path. `--active-experimental-artifacts ../../../outside/new` wrote
    outside the repo's experiments directory, and the new create-on-first-append
    branch made that a directory-creating write rather than a no-op.
    """
    candidate = experiments_dir / f"{name}.jsonl"
    try:
        resolved_dir = experiments_dir.resolve()
        resolved = candidate.resolve()
        resolved.relative_to(resolved_dir)
    except (OSError, ValueError):
        return None
    if resolved.parent != resolved_dir:
        return None
    return candidate


def _co_applied(all_names: list[str], exclude: str) -> list[str]:
    """Return names with one entry excluded — O(n), called per artifact."""
    return [n for n in all_names if n != exclude]


# The metric a production run actually produces for an APPLIED experimental
# artifact: the Review-G run outcome, on a 0..1 scale.
#
# The evidence-based promotion path -- the sample sweep -- was inert before
# this existed. This writer is the SOLE production writer of `applied` rows and
# it hardcoded `metric_value: None`;
# `scripts/learn/runner.py` keeps only rows where `isinstance(metric_value,
# (int, float))`, so `len(applied)` was always 0, always below the floor of
# `max(8, sample_size_target)`, and the sample-sweep promotion never fired from
# a real run. The tests missed it because they injected 0.9 into row fixtures no
# writer produced.
#
# Why the outcome: the run's Review-G outcome is the only numeric signal EVERY
# production run records, and the experiments schema's own `baseline_metric`
# examples are rates ("Review-B pass rate on middleware edits") that a 0..1
# scale compares against directly. `partial` is 0.5 rather than 0 or 1 because
# a partial run is evidence in both directions and collapsing it either way
# would bias the sweep. A caller holding a better metric passes it explicitly.
OUTCOME_METRIC_VALUES: dict[str, float] = {"pass": 1.0, "partial": 0.5, "fail": 0.0}


def outcome_metric_value(outcome: object) -> float | None:
    """The 0..1 metric for a run outcome, or None when the outcome is unknown.

    None is deliberate and is NOT the same as 0.0: a run that recorded no usable
    outcome produced no measurement, and scoring it as a total failure would
    invent evidence. The consumer counts those as `metric_missing` instead.
    """
    if isinstance(outcome, (int, float)) and not isinstance(outcome, bool):
        return float(outcome)
    return OUTCOME_METRIC_VALUES.get(str(outcome).strip().lower())


def append_experiment_rows(
    experiments_dir: Path,
    run_id: str,
    active: list[str],
    outcome: str,
    date: str,
    metric_value: float | None = None,
) -> None:
    """Append one `applied` row per active experimental artifact.

    `metric_value` defaults to the run outcome's numeric value, so the row is
    measurable without the caller remembering to supply anything; pass a value
    explicitly to override it with a better metric.
    """
    measured = metric_value if metric_value is not None else outcome_metric_value(outcome)
    source = "caller" if metric_value is not None else "run_outcome"
    for name in active:
        path = experiment_log_path(experiments_dir, name)
        if path is None:
            log(
                f"warn: experiment name {name!r} resolves outside {experiments_dir}; "
                "refusing to write its log"
            )
            continue
        if not path.exists():
            # Create it. Skipping meant most artifacts never got a row at all,
            # so their evidence was lost rather than deferred. The log is
            # append-only and the sweep finds the `created` row wherever it
            # lands in the file, so a baseline written later still pairs with
            # the rows recorded before it.
            log(
                f"note: no baseline row yet for experiment '{name}' at {path}; "
                "creating the log and recording the applied row (the sweep stays "
                "inert until a 'created' row supplies baseline/target)"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
        co_applied = _co_applied(active, name)
        row = {
            "event": "applied",
            "date": date,
            "run_id": run_id,
            "triggered": True,
            "metric_value": measured,
            # What the number MEANS. An outcome-derived 1.0 is only comparable
            # against a baseline on the same 0..1 scale; recording the source
            # lets the sweep refuse to grade "seconds to complete" (baseline 10,
            # target 5) against a pass/fail score instead of declaring the
            # target met on evidence it never collected.
            "metric_source": source,
            "metric_scale": [0.0, 1.0] if source == "run_outcome" else None,
            "outcome": outcome,
            "co_applied_experimental_artifacts": co_applied,
            "confounded": len(co_applied) > 0,
        }
        append_experiment_row(path, row)
        log(
            f"appended applied row to {path.name} "
            f"(metric_value={measured}, confounded={row['confounded']})"
        )
