#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Promote recurring architecture violations into build-loop-native lessons.

Chunk 8 — final chunk of the architecture-awareness initiative.

Reads:
    .build-loop/state.json                                           — runs[] history
    build-loop-memory/projects/<project>/architecture/known_violations.json — Chunk 6 registry

For each violation ``id`` in known_violations:
    1. Count distinct ``runs[]`` entries that mention the same id under
       ``architecture.violations[]``.
    2. If count >= ``--threshold`` (default 3) and the registry entry is not
       already marked ``promoted: true``, build a Lesson and append it to
       ``.build-loop/architecture/lessons.json``.
    3. Mark the registry entry ``promoted: true`` + ``promoted_at: <ISO>``.
    4. Best-effort: invoke ``scripts/sync_navgator_lessons.py
       --lessons-file <lessons.json> --source-prefix lesson:bl:`` so the new
       lesson lands in Postgres semantic_facts. Postgres failures are
       logged to ``.build-loop/sync_errors.log`` but never block the local
       lesson — this is a best-effort dual-write.

Stdout (JSON, sorted keys):
    {
      "promoted":                  N,
      "skipped_already_promoted":  M,
      "below_threshold":           K,
      "schema_version":            "1.0.0"
    }

CLI flags:
    --workdir PATH       project root (default: cwd)
    --threshold N        promotion threshold (default 3, must be >= 2)
    --dry-run            don't write lessons.json, registry, or invoke sync
    --no-sync            skip sync_navgator_lessons.py invocation only

Stdlib-only. Reuses ``src/build_loop/architecture/lessons.py`` for the Lesson
dataclass + atomic write helper. Postgres deps stay optional.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCHEMA_VERSION = "1.0.0"
DEFAULT_THRESHOLD = 3
SYNC_ERRORS_LOG = ".build-loop/sync_errors.log"
SOURCE_PREFIX = "lesson:bl:"   # build-loop-native lessons (cf. NavGator's lesson:nav:)
SCRIPT_DIR = Path(__file__).resolve().parent

# Make src/ importable so we can reuse the Lesson dataclass + atomic_write_json.
_REPO_ROOT_GUESS = SCRIPT_DIR.parent
_SRC = (_REPO_ROOT_GUESS / "src").resolve()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from build_loop.architecture.schemas import Lesson, SCHEMA_VERSION as ENGINE_SCHEMA_VERSION  # noqa: E402
from build_loop.architecture.storage import atomic_write_json, read_json  # noqa: E402
from _paths import project_architecture_dir  # type: ignore  # noqa: E402
from project_resolver import resolve_project  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------

# Map architecture rule_id → lesson category. NavGator-aligned vocabulary so
# downstream consumers see a familiar shape.
_RULE_CATEGORY_MAP: Dict[str, str] = {
    "cycle": "data-flow",
    "circular-dep": "data-flow",
    "circular_dep": "data-flow",
    "circular-dependency": "data-flow",
    "layer-violation": "component-communication",
    "layer_violation": "component-communication",
    "hotspot": "infrastructure",
    "hub": "infrastructure",
    "orphan": "infrastructure",
    "dead-code": "infrastructure",
    "missing-test": "infrastructure",
}
_DEFAULT_CATEGORY = "infrastructure"


_SEVERITY_RANK = {"info": 0, "warn": 1, "warning": 1, "error": 2, "critical": 3, "blocker": 3}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_sync_error(workdir: Path, message: str) -> None:
    try:
        log_path = workdir / SYNC_ERRORS_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{_now_iso()}: {message}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _read_state_runs(workdir: Path) -> List[Dict[str, Any]]:
    state_path = workdir / ".build-loop" / "state.json"
    raw = read_json(state_path) or {}
    runs = raw.get("runs") or []
    if not isinstance(runs, list):
        return []
    return [r for r in runs if isinstance(r, dict)]


def _read_registry(workdir: Path) -> Tuple[Path, Dict[str, Any]]:
    path = project_architecture_dir(resolve_project(workdir)) / "known_violations.json"
    raw = read_json(path) or {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("schema_version", SCHEMA_VERSION)
    raw.setdefault("violations", {})
    if not isinstance(raw["violations"], dict):
        raw["violations"] = {}
    return path, raw


def _count_distinct_run_occurrences(
    runs: List[Dict[str, Any]], violation_id: str
) -> Tuple[int, List[Dict[str, Any]], List[str], str, str]:
    """Walk ``runs[]`` and collect occurrences of a given violation id.

    Returns (count, occurrences_data, all_components_seen, max_severity, first_seen).
    Each occurrence shape is loose: violations may live under ``architecture
    .violations[]`` (canonical) but we also accept top-level ``violations[]``
    as a defensive fallback. Each violation is matched on either ``id`` (the
    Chunk 6 stable hash) or the synthesized ``violation_id`` field.
    """
    distinct_runs = 0
    occurrences: List[Dict[str, Any]] = []
    components_seen: List[str] = []
    max_severity = "info"
    first_seen = ""

    for run in runs:
        # Pull the run's violation list (canonical: run.architecture.violations).
        viols = []
        arch_block = run.get("architecture") or {}
        if isinstance(arch_block, dict):
            v = arch_block.get("violations") or []
            if isinstance(v, list):
                viols = v
        if not viols:
            top = run.get("violations") or []
            if isinstance(top, list):
                viols = top

        run_matched = False
        for vraw in viols:
            if not isinstance(vraw, dict):
                continue
            vid = (
                str(vraw.get("id") or "")
                or str(vraw.get("violation_id") or "")
            )
            if vid != violation_id:
                continue
            run_matched = True
            occurrences.append(vraw)
            comps = vraw.get("components") or []
            if isinstance(comps, list):
                for c in comps:
                    cstr = str(c)
                    if cstr and cstr not in components_seen:
                        components_seen.append(cstr)
            sev = str(vraw.get("severity") or "info").lower()
            if _SEVERITY_RANK.get(sev, 0) > _SEVERITY_RANK.get(max_severity, 0):
                max_severity = sev
            ts = str(run.get("timestamp") or run.get("ts") or "")
            if ts and (not first_seen or ts < first_seen):
                first_seen = ts
        if run_matched:
            distinct_runs += 1

    return distinct_runs, occurrences, components_seen, max_severity, first_seen


# ---------------------------------------------------------------------------
# Signature derivation
# ---------------------------------------------------------------------------


def _signature_regexes_for_components(components: List[str]) -> List[str]:
    """Generate one anchored regex per component file path.

    Each component string is escaped via ``re.escape`` so dots, slashes, and
    other meta-characters can't introduce accidental wildcard matches. The
    resulting list is intended to live under the lesson's ``signature``
    extras (the dataclass field is a single string; we collapse to that
    below). For matching we test each entry independently — a hit on any
    member counts.
    """
    out: List[str] = []
    seen: set = set()
    for c in components:
        if not c:
            continue
        # Strip any leading "src/" or repo prefix? No — keep as-is for fidelity.
        escaped = re.escape(c)
        if escaped not in seen:
            seen.add(escaped)
            out.append(escaped)
    return out


def _combined_signature(component_regexes: List[str]) -> str:
    """Collapse the list into a single alternation regex for the dataclass field.

    The ``Lesson.signature`` field is a single string; multi-pattern data
    rides in the ``extra`` payload as ``signature_list``. The collapsed
    alternation lets read-only consumers (slice_acp, build_acp) treat the
    string as a single regex without needing schema awareness.
    """
    if not component_regexes:
        return ""
    if len(component_regexes) == 1:
        return component_regexes[0]
    return "(?:" + "|".join(component_regexes) + ")"


# ---------------------------------------------------------------------------
# Lesson construction
# ---------------------------------------------------------------------------


def _build_lesson(
    *,
    violation_id: str,
    registry_entry: Dict[str, Any],
    occurrences: int,
    occurrence_components: List[str],
    max_severity: str,
    earliest_run_ts: str,
) -> Lesson:
    rule_id = str(registry_entry.get("rule_id") or "")
    category = _RULE_CATEGORY_MAP.get(rule_id, _DEFAULT_CATEGORY)

    # Component union: registry components (Chunk 6 sorted, stable) + anything
    # only seen in runs[] occurrences.
    registry_components = list(registry_entry.get("components") or [])
    union_components: List[str] = list(registry_components)
    for c in occurrence_components:
        if c not in union_components:
            union_components.append(c)

    sig_list = _signature_regexes_for_components(union_components)
    signature_combined = _combined_signature(sig_list)

    pattern = str(registry_entry.get("message") or "(no message)")
    first_seen = (
        earliest_run_ts
        or str(registry_entry.get("first_seen") or "")
        or _now_iso()
    )
    last_seen = str(registry_entry.get("last_seen") or _now_iso())
    severity_norm = "warn" if max_severity in {"warn", "warning"} else max_severity

    context_payload = {
        "first_seen": first_seen,
        "last_seen": last_seen,
        "occurrences": int(occurrences),
        "files_affected": union_components,
        "resolution": None,
        "violation_id": violation_id,
        "rule_id": rule_id,
    }

    example_payload = {
        "bad": pattern,
        "good": None,
        "why": "Recurred 3+ times across builds",
    }

    validation_payload = {
        "last_validated": _now_iso(),
        "source": "auto-promoted",
        "status": "active",
    }

    lesson_id = f"lesson-build-loop-{violation_id}"

    # Build the dataclass. The schema's `signature` is a single string; we
    # also stash the raw list under `extra.signature_list` so consumers that
    # want per-component regexes can read them. Both `example` and
    # `validation` are stashed as full dicts in `extra` (the dataclass holds
    # string forms for backward compat).
    lesson = Lesson(
        id=lesson_id,
        category=category,
        pattern=pattern,
        signature=signature_combined,
        severity=severity_norm or "info",
        context=context_payload,
        example=json.dumps(example_payload, sort_keys=True),
        validation=json.dumps(validation_payload, sort_keys=True),
        promoted=True,
    )
    # Stash richer payloads + the raw list under extra so the on-disk JSON
    # carries them verbatim and downstream consumers (slice_acp's
    # _match_lessons) keep working with both string and list signatures.
    lesson.extra = {
        "signature_list": sig_list,
        "example_obj": example_payload,
        "validation_obj": validation_payload,
        "promoted_at": _now_iso(),
        "source": "auto-promoted",
    }
    return lesson


# ---------------------------------------------------------------------------
# Lessons.json read/write (atomic, schema-stamped)
# ---------------------------------------------------------------------------


def _lessons_path(workdir: Path) -> Path:
    return workdir / ".build-loop" / "architecture" / "lessons.json"


def _load_lessons_doc(workdir: Path) -> Dict[str, Any]:
    path = _lessons_path(workdir)
    raw = read_json(path)
    if not raw or not isinstance(raw, dict):
        return {
            "schema_version": SCHEMA_VERSION,
            "lessons": [],
        }
    raw.setdefault("schema_version", SCHEMA_VERSION)
    if not isinstance(raw.get("lessons"), list):
        raw["lessons"] = []
    return raw


def _persist_lessons_doc(workdir: Path, doc: Dict[str, Any]) -> Path:
    path = _lessons_path(workdir)
    # Refresh metadata fields each write.
    doc["schema_version"] = SCHEMA_VERSION
    doc["generated_at"] = int(time.time() * 1000)
    doc["count"] = len(doc.get("lessons") or [])
    atomic_write_json(path, doc)
    return path


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------


def _invoke_sync_navgator_lessons(
    *, workdir: Path, lessons_path: Path, dry_run: bool
) -> bool:
    """Invoke ``scripts/sync_navgator_lessons.py`` for the local lessons file.

    Returns True on success (or dry-run), False on any failure (logged to
    sync_errors.log). Best-effort: never raises, never aborts promotion.
    """
    script = SCRIPT_DIR / "sync_navgator_lessons.py"
    if not script.exists():
        _log_sync_error(workdir, f"sync_navgator_lessons.py missing at {script}")
        return False

    cmd = [
        sys.executable,
        str(script),
        "--workdir", str(workdir),
        "--lessons-file", str(lessons_path),
        "--source-prefix", SOURCE_PREFIX,
    ]
    if dry_run:
        cmd.append("--dry-run")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        _log_sync_error(workdir, f"sync_navgator_lessons invocation failed: {exc!r}")
        return False

    if result.returncode != 0:
        snippet = (result.stderr or "").strip().splitlines()[-3:]
        _log_sync_error(
            workdir,
            f"sync_navgator_lessons exited {result.returncode}: {' | '.join(snippet)}",
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Promote recurring architecture violations to build-loop-native lessons."
    )
    p.add_argument(
        "--workdir",
        default=".",
        help="Project root (default: cwd).",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"Distinct-runs threshold for promotion (default {DEFAULT_THRESHOLD}, min 2).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not mutate lessons.json, registry, or invoke sync.",
    )
    p.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip the sync_navgator_lessons.py invocation only (still write local).",
    )
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    workdir = Path(args.workdir).resolve()
    threshold = max(2, int(args.threshold))

    runs = _read_state_runs(workdir)
    registry_path, registry = _read_registry(workdir)
    violations_map = registry.get("violations") or {}

    promoted_count = 0
    skipped_already = 0
    below_threshold = 0

    if not violations_map:
        # Nothing to do; emit clean envelope.
        out = {
            "promoted": 0,
            "skipped_already_promoted": 0,
            "below_threshold": 0,
            "schema_version": SCHEMA_VERSION,
        }
        print(json.dumps(out, sort_keys=True))
        return 0

    lessons_doc = _load_lessons_doc(workdir)
    lessons_list: List[Dict[str, Any]] = list(lessons_doc.get("lessons") or [])
    by_id: Dict[str, int] = {
        str(l.get("id")): idx for idx, l in enumerate(lessons_list) if l.get("id")
    }

    promoted_lesson_ids: List[str] = []

    for vid, entry in violations_map.items():
        if not isinstance(entry, dict):
            continue
        already = bool(entry.get("promoted"))
        if already:
            skipped_already += 1
            continue

        distinct_runs, occurrences, occ_comps, max_sev, first_run_ts = (
            _count_distinct_run_occurrences(runs, vid)
        )
        if distinct_runs < threshold:
            below_threshold += 1
            continue

        # Severity is the max of (runs occurrences max, registry entry's
        # severity). Without this, builds that only carry the violation id
        # in runs without a severity field would clamp to 'info'.
        registry_sev = str(entry.get("severity") or "info").lower()
        if _SEVERITY_RANK.get(registry_sev, 0) > _SEVERITY_RANK.get(max_sev, 0):
            max_sev = registry_sev

        lesson = _build_lesson(
            violation_id=vid,
            registry_entry=entry,
            occurrences=distinct_runs,
            occurrence_components=occ_comps,
            max_severity=max_sev,
            earliest_run_ts=first_run_ts,
        )
        # Replace-by-id semantics: if a lesson with the same id is already
        # in the file (from a prior run we mis-tracked), overwrite it.
        ldict = lesson.to_dict()
        if lesson.id in by_id:
            lessons_list[by_id[lesson.id]] = ldict
        else:
            by_id[lesson.id] = len(lessons_list)
            lessons_list.append(ldict)

        # Mark registry entry. We mutate in place; persist below if not dry-run.
        entry["promoted"] = True
        entry["promoted_at"] = _now_iso()
        entry["lesson_id"] = lesson.id

        promoted_lesson_ids.append(lesson.id)
        promoted_count += 1

    # Persist artifacts only when something changed and we're not in dry-run.
    if promoted_count > 0 and not args.dry_run:
        lessons_doc["lessons"] = lessons_list
        lessons_path_actual = _persist_lessons_doc(workdir, lessons_doc)

        # Persist registry update atomically.
        try:
            atomic_write_json(registry_path, registry)
        except OSError as exc:
            # Local lesson is durable; registry write failure is logged + non-fatal.
            _log_sync_error(workdir, f"registry write failed: {exc}")

        # Best-effort dual-write to Postgres semantic_facts via the sync script.
        if not args.no_sync:
            _invoke_sync_navgator_lessons(
                workdir=workdir,
                lessons_path=lessons_path_actual,
                dry_run=False,
            )

    out = {
        "promoted": promoted_count,
        "skipped_already_promoted": skipped_already,
        "below_threshold": below_threshold,
        "schema_version": SCHEMA_VERSION,
    }
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
