#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Backend 2: canonical decision indexes + project decisions/*.md reader."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import (
    DECISION_FRONTMATTER_RE,
    _parse_iso,
    _q_match,
    _read_jsonl,
)


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


def _index_row_to_decision(
    row: Dict[str, Any], project: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Convert a single INDEX.jsonl row to a decision entry, or None to skip."""
    if str(row.get("type") or "") != "decision":
        return None
    row_project = str(row.get("project") or "_unscoped")
    # Exclude only rows that belong to a DIFFERENT, NAMED project. A row
    # that is itself _unscoped/global is a decision explicitly routed as
    # "applies to a different project too" (see the routing rule in
    # `skills/build-loop/references/memory.md`) and must stay visible for
    # every scoped project — it was previously dropped here, making the
    # global lane a write-only destination (17/47 indexed rows affected).
    if (
        project
        and project != "_unscoped"
        and row_project != "_unscoped"
        and row_project != project
    ):
        return None
    return {
        "_kind": "decisions",
        "_source": "index",
        "_recency_ts": _parse_iso(
            row.get("updated") or row.get("date") or row.get("created")
        ),
        "id": row.get("id") or row.get("canonical_id"),
        "canonical_id": row.get("canonical_id") or row.get("id"),
        "legacy_id": row.get("legacy_id"),
        "title": row.get("title") or "",
        "primary_tag": "",
        "project": row_project,
        "path": row.get("canonical_path") or "",
        "summary": row.get("title") or "",
    }


def _indexed_decisions(
    workdir: Path, query: str, limit: int
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read generated build-loop-memory index rows first."""
    try:
        from _paths import memory_indexes_dir  # type: ignore  # noqa: PLC0415
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return [], []

    rows, reasons = _read_jsonl(memory_indexes_dir() / "INDEX.jsonl")
    if not rows:
        return [], reasons

    project = resolve_project(workdir)
    out: List[Dict[str, Any]] = []
    for row in rows:
        searchable = " ".join(
            str(row.get(k) or "")
            for k in ("id", "canonical_id", "title", "status", "legacy_id", "legacy_path")
        )
        tags = row.get("tags") or []
        if isinstance(tags, list):
            searchable += " " + " ".join(str(t) for t in tags)
        if not _q_match(searchable, query):
            continue
        entry = _index_row_to_decision(row, project)
        if entry is not None:
            out.append(entry)
    out.sort(key=lambda x: x["_recency_ts"] or 0, reverse=True)

    # Crowding-out guard: now that global (_unscoped) rows survive the
    # per-project filter above, they compete with the project's OWN rows
    # for the same `limit` slots in a pure recency sort. Measured on the
    # live store (2026-08-14): querying project "build-loop" with limit=10
    # returned 10/10 _unscoped rows and dropped every build-loop row,
    # because 10 _unscoped decisions shared a more recent date than
    # build-loop's own newest. Reserve up to half of `limit` for the
    # project's own rows (recency order preserved within each half) so a
    # burst of newer global decisions can narrow, but never zero out, a
    # project's visibility into its own recent decisions. This is a
    # reservation on top of the existing recency sort, not a new ranking
    # dimension — ranking itself (recency, token-OR match) is unchanged.
    if project and project != "_unscoped":
        local = [e for e in out if e["project"] == project]
        if local and len(local) < len(out):
            reserved_n = min(len(local), max(1, limit // 2))
            reserved = local[:reserved_n]
            reserved_keys = {id(e) for e in reserved}
            rest = [e for e in out if id(e) not in reserved_keys]
            out = reserved + rest[: max(0, limit - reserved_n)]
            out.sort(key=lambda x: x["_recency_ts"] or 0, reverse=True)

    return out[:limit], reasons


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


def _decision_index_coverage_reason(dec_dirs: List[Path]) -> Optional[str]:
    """Observability-only signal: how many decision files on disk (across the
    resolved ``dec_dirs``) have no matching row in ``INDEX.jsonl``.

    Query-independent by design (unlike ``_indexed_decisions``, which is
    filtered by the caller's search terms) — this reports total index
    coverage, not coverage of the current search. Never raises, never
    treated as an error or a gate: returns ``None`` when paths can't be
    resolved or there's nothing on disk to report against. A prior audit
    found 45 of 61 on-disk decision files absent from the index; nothing
    previously surfaced that gap to a caller.
    """
    if not dec_dirs:
        return None
    try:
        from _paths import memory_indexes_dir  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None

    rows, _ = _read_jsonl(memory_indexes_dir() / "INDEX.jsonl")
    indexed_ids: set[str] = set()
    for row in rows:
        if str(row.get("type") or "") != "decision":
            continue
        for key in ("canonical_id", "id", "legacy_id"):
            val = row.get(key)
            if val:
                indexed_ids.add(str(val))

    on_disk = 0
    missing = 0
    seen_stems: set[str] = set()
    for dec_dir in dec_dirs:
        for p in dec_dir.glob("*.md"):
            stem = p.stem
            if stem.upper().startswith("INDEX") or stem.startswith("_") or stem in seen_stems:
                continue
            seen_stems.add(stem)
            on_disk += 1
            if stem not in indexed_ids:
                missing += 1

    if on_disk == 0:
        return None
    return (
        f"decision_index_coverage: {missing}/{on_disk} decision files on disk "
        "are not represented in INDEX.jsonl (observability only)"
    )


def read_decisions(
    workdir: Path, query: str, limit: int
) -> Tuple[List[Dict[str, Any]], List[str]]:
    reasons: List[str] = []
    indexed, index_reasons = _indexed_decisions(workdir, query, limit)
    reasons.extend(index_reasons)

    dec_dirs = _resolve_decision_dirs(workdir)
    coverage_reason = _decision_index_coverage_reason(dec_dirs)
    if coverage_reason:
        reasons.append(coverage_reason)

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
