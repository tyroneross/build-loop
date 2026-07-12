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
MILESTONE_OWED_PREFIX = "milestone-owed-"
CLOSEOUT_PENDING_DIRNAME = "closeout-pending"
DEFAULT_MEMORY_ROOT = "~/dev/git-folder/build-loop-memory"
# Sources for which milestone enforcement + queue drain fire (a real shipped run).
ENFORCE_SOURCES = ("post-push", "post-push-armed", "phase-6-learn")


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



def _row_identities(row: Any) -> set[str]:
    if not isinstance(row, dict):
        return set()
    return {
        v.strip()
        for k in ("build_loop_id", "run_id", "id")
        if isinstance((v := row.get(k)), str) and v.strip()
    }


def _run_shipped(workdir: Path, run_id: str) -> tuple[bool, str | None, str]:
    """Did the run identified by ``run_id`` ship anything?

    Reads ``.build-loop/state.json.runs[]`` — the record Review-G writes. A run
    "shipped" when its record carries a non-empty commit or files_touched/
    files_changed. Returns (shipped, commit, summary). Fail-soft → (False, None, "").
    """
    state_path = workdir / ".build-loop" / "state.json"
    try:
        if not state_path.exists():
            return (False, None, "")
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return (False, None, "")
    runs = state.get("runs") if isinstance(state, dict) else None
    if not isinstance(runs, list):
        return (False, None, "")
    for r in runs:
        if not isinstance(r, dict) or run_id not in _row_identities(r):
            continue
        commit = str(r.get("commit") or "").strip() or None
        files = r.get("files_touched") or r.get("files_changed") or r.get("files")
        has_files = bool(files) and str(files).strip() not in ("", "0", "[]")
        shipped = bool(commit) or has_files
        summary = str(r.get("goal") or r.get("summary") or r.get("run_label") or "").strip()
        return (shipped, commit, summary[:300])
    return (False, None, "")


def _milestone_recorded(memory_root: Path, slug: str, run_id: str, commit: str | None) -> bool:
    """True when milestones.jsonl already carries this run_id or commit. Read-only."""
    try:
        path = memory_root / "projects" / Path(*slug.split("/")) / "milestones.jsonl"
        if not path.exists():
            return False
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if run_id and row.get("run_id") == run_id:
                return True
            if commit and row.get("commit") == commit:
                return True
    except OSError:
        return False
    return False


def _milestone_queued(workdir: Path, run_id: str, commit: str | None) -> bool:
    """True when a milestone for this run is already sitting in the promotion queue."""
    try:
        import sys as _sys
        _here = str(Path(__file__).resolve().parent.parent)
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        import promotion_queue as _pq  # noqa: PLC0415
        for r in _pq.list_pending(workdir):
            if r.get("kind") != "milestone":
                continue
            if run_id and r.get("run_id") == run_id:
                return True
            pl = r.get("payload") or {}
            if commit and pl.get("commit") == commit:
                return True
    except Exception:  # noqa: BLE001 — advisory; absence → not queued
        return False
    return False


def _drain_promotions(workdir: Path, memory_root: Path) -> dict[str, Any]:
    """Drain any queued durable promotions (FIX-2). Fail-soft → {}."""
    try:
        import sys as _sys
        _here = str(Path(__file__).resolve().parent.parent)
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        import promotion_queue as _pq  # noqa: PLC0415
        return _pq.drain(workdir, memory_root=str(memory_root))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _write_milestone_owed_marker(workdir: Path, run_id: str, commit: str | None, summary: str) -> Path:
    """Write a blocking owed-item marker, mirroring the closeout-pending markers.

    Reuses the existing SessionStart sweep (``stop_closeout.run_session_start``),
    which surfaces any ``.build-loop/closeout-pending/*.md`` carrying
    ``closeout_incomplete: true``. This is the "emit a blocking owed-item"
    branch of FIX-1 — the enforcement net that catches a shipped run whose
    milestone the orchestrator skipped.
    """
    marker = workdir / ".build-loop" / CLOSEOUT_PENDING_DIRNAME / f"{MILESTONE_OWED_PREFIX}{run_id}.md"
    marker.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"run_id: {run_id}\n"
        f"recorded_at: {_iso_now()}\n"
        "topic: milestone-owed\n"
        f"commit: {commit or ''}\n"
        "closeout_incomplete: true\n"
        "source: closeout.status\n"
        "---\n\n"
        f"# Milestone owed — {run_id}\n\n"
        "This run SHIPPED (a `runs[]` record carries a commit / changed files) but no\n"
        "milestone line was appended to `build-loop-memory/projects/<slug>/milestones.jsonl`.\n"
        "The permanent progress record must not be skipped. Append it now:\n\n"
        "```\n"
        f"python3 scripts/append_milestone.py --workdir . --summary {json.dumps(summary or '(what shipped)')} \\\n"
        f"  --commit {commit or '<HEAD>'} --run-id {run_id}\n"
        "```\n\n"
        "A peer-held store will QUEUE the append (drained at the next closeout); this\n"
        "marker clears once the milestone is recorded.\n"
    )
    tmp = marker.with_suffix(".md.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, marker)
    return marker


def ensure_milestone(
    workdir: Path,
    run_id: str,
    memory_root: Path | str | None = None,
) -> dict[str, Any]:
    """FIX-1: mechanically enforce the Review-G milestone append.

    1. Drain any queued durable promotions (writes previously-queued milestones
       now that the store may be free).
    2. If the run shipped and no milestone is recorded (and none is queued),
       write a blocking owed-item marker.

    Deterministic + fail-soft. Returns a status block for the envelope.
    """
    workdir = Path(workdir).resolve()
    mem_root = Path(os.path.expanduser(str(memory_root or DEFAULT_MEMORY_ROOT)))
    drain_result = _drain_promotions(workdir, mem_root)

    shipped, commit, summary = _run_shipped(workdir, run_id)
    if not shipped:
        return {"status": "not_shipped", "drain": drain_result}

    try:
        from _paths import derive_slug_from_cwd  # noqa: PLC0415
        slug = derive_slug_from_cwd(workdir)
    except Exception:  # noqa: BLE001
        slug = workdir.name

    if _milestone_recorded(mem_root, slug, run_id, commit):
        return {"status": "recorded", "commit": commit, "drain": drain_result}
    if _milestone_queued(workdir, run_id, commit):
        return {"status": "queued", "commit": commit, "drain": drain_result}

    marker = _write_milestone_owed_marker(workdir, run_id, commit, summary)
    return {"status": "owed", "commit": commit, "marker": str(marker), "drain": drain_result}


def run(
    workdir: Path | str,
    *,
    run_id: str | None = None,
    source: str = "ad-hoc",
    memory_root: str | None = None,
    enforce_milestone: bool | None = None,
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
        "milestone": None,
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

    # FIX-1: mechanically enforce the Review-G milestone append + drain the
    # durable-promotion queue. Fires on real shipped-run sources (or when the
    # caller forces it), never on ad-hoc/test unless asked. Fail-soft.
    do_enforce = enforce_milestone if enforce_milestone is not None else (source in ENFORCE_SOURCES)
    if do_enforce and run_id:
        try:
            envelope["milestone"] = ensure_milestone(workdir, run_id, memory_root)
        except Exception as exc:  # noqa: BLE001 — enforcement never blocks closeout
            envelope["milestone"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    # Atomic persist.
    out_path = workdir / ".build-loop" / CLOSEOUT_DIRNAME / f"{rid}.json"
    try:
        _atomic_write_json(out_path, envelope)
        envelope["written_to"] = str(out_path)
    except OSError as exc:
        envelope["error"] = (envelope.get("error") or "") + f" | write_failed: {exc}"

    return envelope
