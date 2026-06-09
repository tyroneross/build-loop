# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Closeout status emit — the durable-signal classifier.

Reads three durable-signal sources written by the existing pipeline:

1. ``.build-loop/proposals/enforce-from-retro/`` — retrospective enforce-candidates.
   A non-empty directory is a HIGH durable signal — these are items the
   retro flagged for cross-run promotion. Closeout treats them as
   `wrote_memory` candidates if the retrospective also produced a durable
   path (``durable_path`` in `.build-loop/retrospectives/<date>/<run-id>.summary.md`).
2. ``.build-loop/pending-lessons/*.md`` — flat tier-1 captures from
   ``scan_corrections`` (Stop hook). Indicates raw candidate signal awaiting
   refinement. Maps to `queued_pending_lesson`.
3. ``.build-loop/pending-lessons/pending/*.json`` — structured
   ``memory_consolidate.intake`` queue. Also `queued_pending_lesson`.

Routing rule (strict — single source of truth):

    if retrospective produced a durable_path AND enforce_candidates >= 1 →
        wrote_memory   (the retro already wrote durable; pending-lessons are
                        secondary candidates the host agent will refine)

    elif raw_candidates (flat .md or queued .json) >= 1 →
        queued_pending_lesson

    else →
        no_durable_lesson

A "skipped" closeout (no status emitted) with raw_candidates >= 1 OR
retro_enforce_candidates >= 1 is the DETECTABLE FAILURE mode — the
test suite enforces it via :func:`detect_durable_signal`.

Atomic writes — `.build-loop/closeout/<run-id>.json` via tmpfile + os.replace.
Never raises (callers do not block on closeout); returns a degraded envelope
on internal error so the contract caller can log + continue.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLOSEOUT_STATUSES = ("wrote_memory", "queued_pending_lesson", "no_durable_lesson")
VALID_SOURCES = ("post-push", "post-push-armed", "phase-6-learn", "ad-hoc", "test")

CLOSEOUT_DIRNAME = "closeout"
PENDING_LESSONS_DIRNAME = "pending-lessons"
RETRO_ENFORCE_DIRNAME = "proposals/enforce-from-retro"
RETROSPECTIVES_DIRNAME = "retrospectives"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_count_glob(root: Path, pattern: str) -> int:
    """Count matches without raising on permission / missing-dir."""
    try:
        if not root.is_dir():
            return 0
        return sum(1 for _ in root.glob(pattern))
    except OSError:
        return 0


def _safe_list_glob(root: Path, pattern: str) -> list[str]:
    try:
        if not root.is_dir():
            return []
        return sorted(str(p) for p in root.glob(pattern))
    except OSError:
        return []


def _latest_retro_summary(workdir: Path) -> dict[str, Any]:
    """Find the most recent retrospective summary's durable_path, if any.

    Reads ``.build-loop/retrospectives/<YYYY-MM-DD>/<run-id>.summary.md`` or its
    sibling ``<run-id>.md`` — both are written atomically by
    ``scripts/retrospective/write.py``. We treat the presence of a durable
    promotion (file under ``build-loop-memory/projects/<slug>/retrospectives/``)
    as the "wrote_memory" signal from the retro side.
    """
    root = workdir / ".build-loop" / RETROSPECTIVES_DIRNAME
    if not root.is_dir():
        return {"present": False}

    summaries: list[Path] = []
    for date_dir in sorted(root.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for p in sorted(date_dir.glob("*.summary.md"), reverse=True):
            summaries.append(p)
        if summaries:
            break

    if not summaries:
        return {"present": False}

    latest = summaries[0]
    try:
        text = latest.read_text(encoding="utf-8")
    except OSError:
        return {"present": True, "summary_path": str(latest), "durable_path": None}

    durable: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("durable:") or s.lower().startswith("- durable:"):
            durable = s.split(":", 1)[1].strip() or None
            break
    return {
        "present": True,
        "summary_path": str(latest),
        "durable_path": durable,
    }


def detect_durable_signal(workdir: Path) -> dict[str, Any]:
    """Inspect the durable-signal sources.

    Returns::

        {
          "raw_candidates_flat": int,     # tier-1 scan_corrections .md files
          "raw_candidates_queued": int,   # memory_consolidate.intake pending JSON
          "retro_enforce_candidates": int,
          "retro_durable_path": str | None,
          "retro_summary_path": str | None,
        }
    """
    workdir = Path(workdir).resolve()
    pending_root = workdir / ".build-loop" / PENDING_LESSONS_DIRNAME

    raw_flat = _safe_count_glob(pending_root, "*.md")
    raw_queued = _safe_count_glob(pending_root / "pending", "*.json")
    retro_enforce_root = workdir / ".build-loop" / RETRO_ENFORCE_DIRNAME
    retro_enforce = _safe_count_glob(retro_enforce_root, "*.md")
    retro = _latest_retro_summary(workdir)

    return {
        "raw_candidates_flat": raw_flat,
        "raw_candidates_queued": raw_queued,
        "retro_enforce_candidates": retro_enforce,
        "retro_durable_path": retro.get("durable_path"),
        "retro_summary_path": retro.get("summary_path"),
    }


def _classify(signal: dict[str, Any]) -> tuple[str, str]:
    """Apply the routing rule. Returns (status, reason)."""
    raw_flat = int(signal.get("raw_candidates_flat") or 0)
    raw_queued = int(signal.get("raw_candidates_queued") or 0)
    retro_enforce = int(signal.get("retro_enforce_candidates") or 0)
    durable = signal.get("retro_durable_path")

    if retro_enforce >= 1 and durable:
        return (
            "wrote_memory",
            f"retro produced durable_path={durable} with {retro_enforce} enforce-candidate(s)",
        )
    if raw_flat + raw_queued >= 1 or retro_enforce >= 1:
        parts: list[str] = []
        if raw_flat:
            parts.append(f"{raw_flat} flat tier-1 candidate(s)")
        if raw_queued:
            parts.append(f"{raw_queued} queued intake candidate(s)")
        if retro_enforce:
            parts.append(f"{retro_enforce} retro enforce-candidate(s)")
        return ("queued_pending_lesson", "; ".join(parts) + " awaiting refinement")
    return ("no_durable_lesson", "no raw or retro signal this run")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".closeout-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _normalize_source(source: str | None) -> str:
    if not source:
        return "ad-hoc"
    return source if source in VALID_SOURCES else "ad-hoc"


def run(
    workdir: Path | str,
    *,
    run_id: str | None = None,
    source: str = "ad-hoc",
) -> dict[str, Any]:
    """Execute the closeout. Returns the envelope and writes it atomically.

    Args:
        workdir:  build-loop project root (the repo containing ``.build-loop/``).
        run_id:   stable run identifier. When omitted, derives ``ts-source``.
        source:   one of ``post-push | post-push-armed | phase-6-learn | ad-hoc | test``.

    Returns the envelope::

        {
          "closeout_status": "wrote_memory" | "queued_pending_lesson" | "no_durable_lesson",
          "reason": str,
          "source": str,
          "run_id": str,
          "ts": iso8601,
          "workdir": str,
          "signal": {raw_candidates_flat, raw_candidates_queued, retro_enforce_candidates, retro_durable_path, retro_summary_path},
          "written_to": str | None,        # path under .build-loop/closeout/
          "error": str | None,             # internal-error reason when degraded
        }
    """
    workdir = Path(workdir).resolve()
    source = _normalize_source(source)
    ts = _iso_now()
    rid = run_id or f"{ts.replace(':', '').replace('-', '')}-{source}"

    envelope: dict[str, Any] = {
        "closeout_status": "no_durable_lesson",
        "reason": "init",
        "source": source,
        "run_id": rid,
        "ts": ts,
        "workdir": str(workdir),
        "signal": {},
        "written_to": None,
        "error": None,
    }

    try:
        signal = detect_durable_signal(workdir)
        envelope["signal"] = signal
        status, reason = _classify(signal)
        envelope["closeout_status"] = status
        envelope["reason"] = reason
    except Exception as exc:  # noqa: BLE001 — closeout never raises
        envelope["error"] = f"{type(exc).__name__}: {exc}"
        envelope["reason"] = f"degraded: {envelope['error']}"

    # Atomic persist.
    out_path = workdir / ".build-loop" / CLOSEOUT_DIRNAME / f"{rid}.json"
    try:
        _atomic_write_json(out_path, envelope)
        envelope["written_to"] = str(out_path)
    except OSError as exc:
        envelope["error"] = (envelope.get("error") or "") + f" | write_failed: {exc}"

    return envelope
