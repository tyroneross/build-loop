#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Raw-candidate queue contract for ``.build-loop/pending-lessons/``.

Agents drop candidates here — content + optional hint — never picking the
final memory path. A consolidator drains the queue (see classify.py + place.py).

On-disk layout (per-workdir, repo-local):

    .build-loop/pending-lessons/
        pending/   <run-id>-<seq>-<slug>.json   ← awaiting consolidation
        placed/    <id>.json                     ← consolidated + filed
        rejected/  <id>.json                     ← rejected (with reason)

Each ``<id>.json`` is a single JSON object:

    {
      "id": "<run-id>-<seq>-<slug>",
      "content": "verbatim candidate body (markdown)",
      "hint": null | "free-text agent hint about lane/type/why-durable",
      "type": null | "lesson" | "gotcha" | "decision" | ...,
      "name": null | "candidate slug; helps the consolidator pick a filename",
      "project": null | "<slug>",
      "submitted_at": "ISO8601 UTC",
      "source_run_id": "run_<UTC>_<hash>",
      "source_host": "claude_code | codex | gemini | other",
      "source_workdir": "<abs path>",
      "placement": null | { ...fields filled by place.execute() }
    }

Stdlib-only. Atomic writes (tmpfile + os.replace). Zero deps on the
recall/embed stack — that lives in classify.py.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path for stdlib siblings.

# Sub-directory contract — mirrored to disk only on first write.
PENDING_DIR = "pending"
PLACED_DIR = "placed"
REJECTED_DIR = "rejected"

# Default queue root inside the consumer repo. Callers pass ``workdir``
# explicitly; this is the spec the orchestrator agent docs cite.
DEFAULT_QUEUE_RELDIR = ".build-loop/pending-lessons"


def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "untitled"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class Candidate:
    """A raw lesson candidate awaiting consolidation."""
    id: str
    content: str
    hint: str | None = None
    type: str | None = None
    name: str | None = None
    project: str | None = None
    submitted_at: str = ""
    source_run_id: str = ""
    source_host: str = ""
    source_workdir: str = ""
    placement: dict | None = None
    # Used internally to write the file back at the right state-dir.
    _state: str = field(default=PENDING_DIR, repr=False)
    _path: str = field(default="", repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_state", None)
        d.pop("_path", None)
        return d


def queue_dir(workdir: str | Path, state: str = PENDING_DIR) -> Path:
    """Return ``<workdir>/.build-loop/pending-lessons/<state>``."""
    if state not in {PENDING_DIR, PLACED_DIR, REJECTED_DIR}:
        raise ValueError(f"unknown state {state!r}")
    return Path(workdir) / DEFAULT_QUEUE_RELDIR / state


def submit(
    content: str,
    *,
    workdir: str | Path = ".",
    run_id: str,
    host: str,
    hint: str | None = None,
    type_: str | None = None,
    name: str | None = None,
    project: str | None = None,
) -> Candidate:
    """Drop a candidate into the pending queue.

    Generates a stable id from ``<run-id>-<seq>-<slug>`` where ``<seq>`` is
    the next free integer in ``pending/`` and ``<slug>`` is derived from
    ``name`` (or the first 6 words of ``content``).
    """
    if not content or not content.strip():
        raise ValueError("content must not be empty")
    if not run_id:
        raise ValueError("run_id required")
    if not host:
        raise ValueError("host required")

    pdir = queue_dir(workdir, PENDING_DIR)
    pdir.mkdir(parents=True, exist_ok=True)

    # Next seq: count existing entries for this run_id.
    existing = list(pdir.glob(f"{run_id}-*.json"))
    seq = len(existing) + 1
    slug_basis = name or " ".join(content.split()[:6])
    slug = _slugify(slug_basis)
    cid = f"{run_id}-{seq}-{slug}"

    workdir_abs = str(Path(workdir).resolve())
    c = Candidate(
        id=cid,
        content=content,
        hint=hint,
        type=type_,
        name=name,
        project=project,
        submitted_at=_iso_utc(),
        source_run_id=run_id,
        source_host=host,
        source_workdir=workdir_abs,
        placement=None,
    )
    fpath = pdir / f"{cid}.json"
    c._path = str(fpath)
    c._state = PENDING_DIR
    _atomic_write_json(fpath, c.to_dict())
    return c


def list_pending(workdir: str | Path = ".") -> list[Candidate]:
    """Return all pending candidates, sorted by submitted_at."""
    pdir = queue_dir(workdir, PENDING_DIR)
    if not pdir.exists():
        return []
    out: list[Candidate] = []
    for f in sorted(pdir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        c = _candidate_from_dict(d)
        c._path = str(f)
        c._state = PENDING_DIR
        out.append(c)
    return out


def load_candidate(candidate_id: str, workdir: str | Path = ".") -> Candidate:
    """Load a candidate by id, searching pending/ then placed/ then rejected/."""
    for state in (PENDING_DIR, PLACED_DIR, REJECTED_DIR):
        f = queue_dir(workdir, state) / f"{candidate_id}.json"
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            c = _candidate_from_dict(d)
            c._path = str(f)
            c._state = state
            return c
    raise FileNotFoundError(f"candidate {candidate_id!r} not found under {workdir}")


def transition(
    candidate: Candidate,
    new_state: str,
    *,
    placement: dict | None = None,
    workdir: str | Path = ".",
) -> Candidate:
    """Move a candidate from its current state-dir into ``new_state``.

    ``placement`` is attached when transitioning to PLACED_DIR (records where
    on disk the consolidator filed it via the writer guard). Atomic: write new
    file then unlink old.
    """
    if new_state not in {PLACED_DIR, REJECTED_DIR}:
        raise ValueError(f"cannot transition to {new_state!r}")
    if placement is not None:
        candidate.placement = placement
    new_dir = queue_dir(workdir, new_state)
    new_dir.mkdir(parents=True, exist_ok=True)
    new_path = new_dir / f"{candidate.id}.json"
    _atomic_write_json(new_path, candidate.to_dict())
    if candidate._path and Path(candidate._path).exists() and candidate._path != str(new_path):
        try:
            Path(candidate._path).unlink()
        except OSError:
            pass
    candidate._path = str(new_path)
    candidate._state = new_state
    return candidate


def _candidate_from_dict(d: dict[str, Any]) -> Candidate:
    return Candidate(
        id=d["id"],
        content=d["content"],
        hint=d.get("hint"),
        type=d.get("type"),
        name=d.get("name"),
        project=d.get("project"),
        submitted_at=d.get("submitted_at", ""),
        source_run_id=d.get("source_run_id", ""),
        source_host=d.get("source_host", ""),
        source_workdir=d.get("source_workdir", ""),
        placement=d.get("placement"),
    )
