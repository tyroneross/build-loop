#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backend 2: content-FTS decision index (doc_type='decision') + project
decisions/*.md reader.

This module's first leg used to read `indexes/INDEX.jsonl` directly; it now
reads the content-FTS body index instead. Measured on the live store
(2026-09-01): INDEX.jsonl held 53 decision-typed rows and ALL 53 were
also present in the content-FTS body index (`indexes/content_fts.sqlite`,
6,777 decision-typed docs) -- the JSONL leg contributed zero unique
documents. Nothing kept it current either: 26/41 on-disk decision files for
project `build-loop` and 21/22 for `build-loop-memory` were absent from it.
37 of the 53 JSONL rows pointed at `lessons/*.md` decision-typed docs, a lane
the dec_dirs file scan below cannot reach (lessons/ is not a decision
directory) -- that coverage is the reason this reads the FTS index instead
of retiring the leg outright. See `plan.md` "Path A vs Path B".
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import (
    DECISION_FRONTMATTER_RE,
    _parse_iso,
    _q_match,
)

# Scope values the content-FTS index (`content_index._scope`) assigns that
# mean "visible from every project", generalizing the JSONL leg's single
# "_unscoped" sentinel: FTS scopes by path shape alone, returning "global"
# for anything not under a `projects/<slug>/` tree (e.g. top-level
# `lessons/*.md`) and the literal slug for `projects/<slug>/...` paths --
# which makes `projects/_unscoped/decisions/*.md` come through as scope
# "_unscoped", not "global". Both must be treated as globally visible.
_GLOBAL_SCOPES = frozenset({"_unscoped", "global"})


def _resolve_decision_dirs(workdir: Path) -> List[Path]:
    """Return active decision directories for this project.

    Normal reads use ``build-loop-memory/projects/<project>/decisions``.
    Legacy ``.episodic`` and pre-cutover ``decisions/<project>`` paths are
    migration/diagnostic inputs only; enable with
    ``BUILD_LOOP_MEMORY_MIGRATION_MODE=1``.

    Global/``_unscoped`` decision lanes (``projects/_unscoped/decisions``
    and the top-level ``decisions/_unscoped`` lane) are scanned
    UNCONDITIONALLY, regardless of the resolved project and regardless of
    the migration-mode env var. A decision correctly routed as "would this
    apply to a different project? yes -> global" (`skills/build-loop/
    references/memory.md`) is written to one of these two lanes; before
    this fix nothing ever read them back for a scoped project (the
    project-scoped canonical dir was the only unconditional lane, and the
    legacy top-level lane was migration-mode-gated), making every
    globally-routed decision a write-only artifact. This is a read-only
    addition — it does not change where writers put files.
    """
    dirs: List[Path] = []
    try:
        from _paths import decisions_root, project_decisions_dir  # type: ignore  # noqa: PLC0415
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415

        proj = resolve_project(workdir)
        if proj:
            canonical_dir = project_decisions_dir(proj)
            if canonical_dir.is_dir():
                dirs.append(canonical_dir)
            if os.environ.get("BUILD_LOOP_MEMORY_MIGRATION_MODE") == "1":
                legacy_global = decisions_root() / proj
                if legacy_global.is_dir() and legacy_global not in dirs:
                    dirs.append(legacy_global)

        # Unconditional global lanes (see docstring above).
        unscoped_canonical = project_decisions_dir("_unscoped")
        if unscoped_canonical.is_dir() and unscoped_canonical not in dirs:
            dirs.append(unscoped_canonical)
        unscoped_legacy = decisions_root() / "_unscoped"
        if unscoped_legacy.is_dir() and unscoped_legacy not in dirs:
            dirs.append(unscoped_legacy)
    except Exception:  # noqa: BLE001 — best-effort path resolution
        pass
    if os.environ.get("BUILD_LOOP_MEMORY_MIGRATION_MODE") == "1":
        legacy = workdir / ".episodic" / "decisions"
        if legacy.is_dir() and legacy not in dirs:
            dirs.append(legacy)
    return dirs


def _yv(line: str) -> str:
    """Extract YAML scalar value from ``key: value`` line, stripping quotes."""
    return line.split(":", 1)[1].strip().strip('"').strip("'")


_DECISION_KEYS = ("title:", "date:", "primary_tag:", "canonical_id:", "id:")


def _parse_decision_frontmatter(
    text: str,
) -> Tuple[str, Optional[str], str, str, Optional[str]]:
    """Parse YAML frontmatter of a decision file.

    Returns (title, ts_raw, primary_tag, canonical_id, legacy_id).
    ``canonical_id`` defaults to empty string when absent (caller uses stem).
    """
    m = DECISION_FRONTMATTER_RE.match(text)
    if not m:
        return "", None, "", "", None
    fields: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        for key in _DECISION_KEYS:
            if line.startswith(key):
                fields[key[:-1]] = _yv(line)
                break
    return (
        fields.get("title", ""),
        fields.get("date") or None,
        fields.get("primary_tag", ""),
        fields.get("canonical_id", ""),
        fields.get("id") or None,
    )


def _content_row_to_decision(
    row: Dict[str, Any], project: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Convert a single content-FTS row (already filtered to doc_type='decision')
    to a decision entry, or None to skip.

    Generalizes the routing rule the retired `_index_row_to_decision` enforced
    against INDEX.jsonl's `project` field: exclude only rows that belong to a
    DIFFERENT, NAMED project. A row scoped "_unscoped" or "global" is a
    decision explicitly routed as "applies to a different project too" (see
    the routing rule in `skills/build-loop/references/memory.md`) and must
    stay visible for every scoped project.

    Recency: prefer the frontmatter `created`/`date` field (`meta.created`,
    already returned on every FTS row) over the row's `_recency_ts`, which
    `content_index.query` derives from `files.mtime_ns` -- checkout time, not
    authorship time. The retired `_index_row_to_decision` ranked by
    `updated or date or created`; ranking by mtime instead would silently
    flatten in exactly the situation recall matters most: a fresh clone or
    any fresh git worktree gives every file the SAME mtime (verified live in
    this run's own worktree -- every file carries mtime 2026-09-01 02:16),
    collapsing the FTS leg's recency signal to a constant. `_parse_iso`
    returns Unix seconds and `_recency_ts` is already `mtime_ns / 1e9` --
    same scale, so falling back to mtime when `created` is absent mixes
    safely into the merge sort.

    Dedup note: `read_decisions` seeds `seen_ids` from this leg's
    `canonical_id` (here, the file stem) and skips a file-scan entry whose
    frontmatter `canonical_id` collides with it. That agreement holds only
    while frontmatter `canonical_id` equals the file stem -- true for all 42
    on-disk decision files in the live store as of this writing, but not a
    contract either leg enforces; a decision file with a `canonical_id`
    that legitimately diverges from its stem would produce two entries
    instead of one.
    """
    row_project = str(row.get("_scope") or "_unscoped")
    if (
        project
        and project not in _GLOBAL_SCOPES
        and row_project not in _GLOBAL_SCOPES
        and row_project != project
    ):
        return None
    return {
        "_kind": "decisions",
        "_source": "content",
        "_recency_ts": _parse_iso(row.get("created")) or row.get("_recency_ts"),
        "id": row.get("id") or "",
        "canonical_id": row.get("id") or "",
        "legacy_id": None,
        "title": row.get("title") or "",
        "primary_tag": "",
        "project": row_project,
        "path": row.get("path") or "",
        "summary": row.get("snippet") or "",
    }


def _indexed_decisions(
    workdir: Path, query: str, limit: int
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read decision-typed docs (`doc_type='decision'`) from the content-FTS
    body index instead of the retired `INDEX.jsonl` leg (see module docstring
    for the measurements that justified the swap).

    Mirrors `read_content`'s degrade-quietly contract: an index that simply
    hasn't been built yet returns `([], [])` with no reason, same as the
    JSONL leg this replaces (`_read_jsonl` was silent on a missing file too;
    the file-scan leg in `read_decisions` unconditionally covers the same
    ground). A reason is only emitted for a genuine, unexpected failure
    (import error resolving the index path, or the query itself raising) --
    never for the ordinary case of no index on disk. Never raises either
    way.
    """
    try:
        try:
            from scripts import content_index  # type: ignore  # noqa: PLC0415
        except ImportError:
            import content_index  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return [], []

    try:
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
        project: Optional[str] = resolve_project(workdir)
    except Exception:  # noqa: BLE001
        project = None

    # Push the project scope into the SQL WHERE, never into a Python pass over
    # the result. `content_index.query` applies `LIMIT` inside the database, so
    # filtering afterwards means the limit is spent on rows this project cannot
    # see: with `limit` foreign decisions outranking every local one, the leg
    # returns ZERO local decisions while the store holds plenty. The retired
    # JSONL leg read the whole file, so it never had this failure mode.
    #
    # `content_index._filters` builds `(scope IN (project) OR scope IN
    # (_GLOBAL_SCOPES))`, which is exactly the visibility rule
    # `_content_row_to_decision` enforces below — the Python check stays as a
    # cross-check that the two cannot silently disagree. Scoped only when the
    # project is a NAMED one: for a caller already in a global lane the Python
    # rule admits every project, and passing that sentinel to SQL would invert
    # it into "global rows only".
    scoped_project = project if project and project not in _GLOBAL_SCOPES else None
    # Crowding-out guard: global rows are visible from every project by design,
    # so they compete with the project's OWN rows for the same `limit` slots.
    # Measured on the live store (2026-08-14): querying project "build-loop"
    # with limit=10 returned 10/10 global rows and dropped every build-loop row,
    # because 10 global decisions shared a more recent date than build-loop's
    # own newest. Reserve up to half of `limit` for the project's own rows.
    #
    # The reservation needs its own SQL query to be worth anything. A single
    # widened query cannot be post-filtered into one: `LIMIT` runs inside the
    # database, so if enough global rows outrank every local one, no local row
    # ever reaches Python to be reserved. `scope=` asks for exactly this
    # project's documents, with no implicit global OR.
    reserved_n = max(1, limit // 2) if scoped_project else 0
    kwargs: Dict[str, Any] = {
        "doc_type": "decision",
        # An empty query BROWSES, matching `_q_match`'s "empty query matches
        # everything" contract that the retired JSONL leg relied on. Without
        # it, `read_decisions(workdir, "", limit)` -- a bare "show me recent
        # decisions" -- returns nothing at all from the index leg.
        "browse_on_empty": True,
    }
    try:
        db_path = content_index.default_db_path()
        if not db_path.is_file():
            # Ordinary "not built yet" case -- quiet, matches the JSONL
            # leg's contract for a missing INDEX.jsonl.
            return [], []
        reserved_rows = (
            content_index.query(query, limit=reserved_n, scope=scoped_project, **kwargs)
            if scoped_project
            else []
        )
        rows = content_index.query(query, limit=limit, project=scoped_project, **kwargs)
    except Exception as exc:  # noqa: BLE001 — never raise on the recall hot path
        return [], [f"content_index_error: {exc}"]

    out: List[Dict[str, Any]] = []
    seen_paths: set = set()
    for row in [*reserved_rows, *rows]:
        path = str(row.get("path") or "")
        if path and path in seen_paths:
            continue
        entry = _content_row_to_decision(row, project)
        if entry is None:
            continue
        if path:
            seen_paths.add(path)
        out.append(entry)
    out.sort(key=lambda x: x["_recency_ts"] or 0, reverse=True)

    if scoped_project:
        local = [e for e in out if e["project"] == scoped_project]
        if local and len(local) < len(out):
            keep_n = min(len(local), reserved_n)
            reserved = local[:keep_n]
            reserved_keys = {id(e) for e in reserved}
            rest = [e for e in out if id(e) not in reserved_keys]
            out = reserved + rest[: max(0, limit - keep_n)]
            out.sort(key=lambda x: x["_recency_ts"] or 0, reverse=True)

    return out[:limit], []


def _file_decision_entry(
    p: Path, stem: str, workdir: Path, text: str
) -> Optional[Dict[str, Any]]:
    """Build a decision entry dict from already-read file text."""
    m = DECISION_FRONTMATTER_RE.match(text)
    title, ts_raw, primary_tag, canonical_id, legacy_id = _parse_decision_frontmatter(text)
    if not canonical_id:
        canonical_id = stem

    body = text[m.end():] if m else text
    summary_lines = [
        ln.strip() for ln in body.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    summary = summary_lines[0][:240] if summary_lines else ""
    try:
        rel_path = str(p.relative_to(workdir))
    except ValueError:
        rel_path = str(p)
    return {
        "_kind": "decisions",
        "_source": "file",
        "_recency_ts": _parse_iso(ts_raw),
        "id": canonical_id,
        "canonical_id": canonical_id,
        "legacy_id": legacy_id,
        "title": title,
        "primary_tag": primary_tag,
        "path": rel_path,
        "summary": summary,
    }


def _scan_decision_files(
    dec_dirs: List[Path],
    workdir: Path,
    query: str,
    seen_ids: set[str],
    reasons: List[str],
) -> List[Dict[str, Any]]:
    """Walk decision directories, skipping already-seen IDs.  Returns new entries."""
    out: List[Dict[str, Any]] = []
    for dec_dir in dec_dirs:
        for p in sorted(dec_dir.glob("*.md")):
            stem = p.stem
            if stem.upper().startswith("INDEX") or stem.startswith("_") or stem in seen_ids:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError as e:
                reasons.append(f"decision_read_error: {p.name} {e}")
                continue
            if not _q_match(text + " " + _parse_decision_frontmatter(text)[0], query):
                continue
            entry = _file_decision_entry(p, stem, workdir, text)
            out.append(entry)
            seen_ids.add(entry["canonical_id"])
    return out


def read_decisions(
    workdir: Path, query: str, limit: int
) -> Tuple[List[Dict[str, Any]], List[str]]:
    reasons: List[str] = []
    indexed, index_reasons = _indexed_decisions(workdir, query, limit)
    reasons.extend(index_reasons)

    dec_dirs = _resolve_decision_dirs(workdir)
    if not dec_dirs:
        return indexed, reasons

    seen_ids: set[str] = {
        str(item.get("canonical_id") or item.get("id"))
        for item in indexed
        if item.get("canonical_id") or item.get("id")
    }
    file_entries = _scan_decision_files(dec_dirs, workdir, query, seen_ids, reasons)
    file_entries.sort(key=lambda x: x["_recency_ts"] or 0, reverse=True)

    merged = indexed + file_entries
    merged.sort(key=lambda x: x["_recency_ts"] or 0, reverse=True)
    return merged[:limit], reasons
