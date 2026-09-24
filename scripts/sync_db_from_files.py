#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rebuild Postgres state from canonical build-loop-memory markdown files.

Reads every `projects/<project>/decisions/*.md` (excluding `_history/` by
default; `--include-history` opts in), embeds the body via `embed_backend.embed`
(MLX default, Ollama fallback, 1024-dim), and upserts into
`agent_memory.<schema>.semantic_facts`.

Usage:
  python3 sync_db_from_files.py --workdir <repo>           # incremental upsert
  python3 sync_db_from_files.py --workdir <repo> --rebuild # truncate first

Idempotent: re-running without --rebuild keeps row count stable.

Exit codes: 0 success, 1 validation, 2 DB / FS error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _paths import default_schema as _default_schema  # type: ignore  # noqa: E402
from embed_backend import embed as _embed  # type: ignore  # noqa: E402
from write_decision import (  # type: ignore  # noqa: E402
    log,
    parse_frontmatter,
)

try:
    from db import execute, execute_many, execute_script, vector_literal  # type: ignore  # noqa: E402
    _DB_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001
    execute = execute_many = execute_script = vector_literal = None  # type: ignore[assignment]
    _DB_IMPORT_ERROR = exc


def _decision_dir_files(
    decisions_dir: Path,
    include_history: bool,
    include_review: bool,
) -> list[Path]:
    """Return the syncable markdown under ONE decisions directory.

    Written once and called from every branch of ``list_decision_files`` so a
    lane can never be included in one resolution path and silently dropped in
    another — the defect that kept `_review/` out of the database.
    """
    found: list[Path] = []
    if not decisions_dir.exists():
        return found
    found.extend(
        sorted(p for p in decisions_dir.glob("*.md") if not p.name.upper().startswith("INDEX"))
    )
    if include_history:
        history = decisions_dir / "_history"
        if history.exists():
            found.extend(sorted(history.glob("*.md")))
    if include_review:
        review = decisions_dir / "_review"
        if review.exists():
            found.extend(
                sorted(p for p in review.glob("*.md") if not p.name.upper().startswith("INDEX"))
            )
    return found


def list_decision_files(
    workdir: Path,
    include_history: bool,
    project: str | None = None,
    include_review: bool = True,
) -> list[Path]:
    """Find decision files to sync.

    Active mode reads the canonical build-loop-memory tree under
    ``projects/<project>/decisions``. Legacy sources are considered only
    when ``BUILD_LOOP_MEMORY_MIGRATION_MODE=1``.

    ``include_review`` defaults to True (2026-09-16). Quarantined tier-3
    captures under ``_review/`` used to be excluded here, which made them
    unreachable through recall no matter how relevant they were: 12,651
    records that only `rg` could find. They are synced with
    ``status='quarantined'`` so retrieval can RANK and LABEL them instead of
    pretending they do not exist. Exclusion was never a safety property —
    the trust signal is, and that now travels with the row.
    """
    import os

    from _paths import memory_store_root, project_decisions_dir, project_memory_root  # noqa: PLC0415
    from project_resolver import resolve_project  # noqa: PLC0415
    workdir = Path(workdir).resolve()
    files: list[Path] = []
    root = memory_store_root().resolve()
    projects_root = project_memory_root().resolve()
    if project:
        files.extend(
            _decision_dir_files(project_decisions_dir(project), include_history, include_review)
        )
        return files

    if workdir in {root, projects_root}:
        if not projects_root.exists():
            return []
        for project_dir in sorted(p for p in projects_root.iterdir() if p.is_dir()):
            files.extend(
                _decision_dir_files(project_dir / "decisions", include_history, include_review)
            )
        return files

    project = resolve_project(workdir)
    files.extend(
        _decision_dir_files(project_decisions_dir(project), include_history, include_review)
    )

    if os.environ.get("BUILD_LOOP_MEMORY_MIGRATION_MODE") == "1":
        legacy_dir = workdir / ".episodic" / "decisions"
        if legacy_dir.exists():
            files.extend(sorted(legacy_dir.glob("[0-9][0-9][0-9][0-9]-*.md")))
            if include_history:
                history = legacy_dir / "_history"
                if history.exists():
                    files.extend(sorted(history.glob("*.md")))
    return files


def _project_from_path(path: Path) -> str:
    """Return the project slug that OWNS this file, from its location.

    ``.../projects/<slug>/decisions/...`` -> ``<slug>``. Empty string when the
    path is not under a project lane, so the caller can fall back.
    """
    parts = path.resolve().parts
    if "projects" in parts:
        idx = len(parts) - 1 - parts[::-1].index("projects")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


def confidence_to_float(c: str) -> float:
    return {"assumed": 0.25, "inferred": 0.5, "confirmed": 0.75, "explicit": 1.0}.get(c, 0.5)


def upsert_decision(path: Path, schema: str, embed_model: str) -> bool:
    """Upsert ONE decision file. Thin wrapper over the batch path (one code path)."""
    return upsert_batch([path], schema) == 1


def prepare_row(path: Path, schema: str) -> dict | None:
    """Parse one decision file into the row we will insert. No DB, no embedding.

    Separated from the write so a batch can embed and insert many rows per
    round trip: per-file embed + DELETE + INSERT measured ~700ms/record, which
    is ~2.4h for this store and not a scalable sync.
    """
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        log(f"unsafe schema name: {schema!r}")
        return None
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text) or {}
    decision_id = str(fm.get("id") or "").strip()
    if not decision_id:
        log(f"skip: no id in frontmatter for {path}")
        return None
    # PROJECT COMES FROM THE PATH, not frontmatter. The tier-3 capture hook
    # writes `project: _unscoped` into most records even when the file sits in
    # `projects/<slug>/decisions/`, so trusting frontmatter left 1,459 of
    # atomize-ai's 1,836 records unreachable under a project-scoped search
    # (measured 2026-09-16: 377 matched by frontmatter vs 1,836 on disk).
    project = _project_from_path(path) or (fm.get("project") or "_unscoped").strip() or "_unscoped"
    subject_key = fm.get("canonical_id") or decision_id
    subject = f"decision:{project}:{subject_key}"
    # TRUST TIER travels with the row (2026-09-16). `_review/` holds tier-3
    # captures no human has confirmed; they stay retrievable but must never
    # read as a settled decision.
    is_quarantined = path.parent.name == "_review"
    row_status = "quarantined" if is_quarantined else "active"
    # KEYWORD LEG PAYLOAD -> the EXISTING `chunk_context` column, which the
    # existing `search_vector` generated column already indexes. `object` holds
    # only the title, so without this a keyword search cannot match anything
    # the title omits. Bounded, not a copy of the document.
    excerpt = str(fm.get("captured_turn_excerpt") or "").strip()
    if not excerpt:
        body = re.split(r"\n##\s", text.split("---", 2)[-1], maxsplit=2)
        excerpt = " ".join(" ".join(body[:2]).split())
    excerpt = excerpt[:400]
    metadata = {
        "tier": "quarantined" if is_quarantined else "curated",
        "source_path": str(path),
        "excerpt": excerpt,
        "decision_id": decision_id,
        "canonical_id": fm.get("canonical_id"),
        "entity": fm.get("entity"),
        "tags": fm.get("tags"),
        "status": fm.get("status"),
        "confidence": fm.get("confidence"),
        "source": fm.get("source"),
        "date": fm.get("date"),
        "file": str(path.name),
        "project": project,
        "project_frontmatter": fm.get("project"),
        "tool": fm.get("tool"),
        "model": fm.get("model"),
        "task_category": fm.get("task_category"),
        "author": fm.get("author"),
        "files_touched": fm.get("files_touched") or [],
        "closing_commit": fm.get("closing_commit"),
    }
    cc = fm.get("confirmation_count")
    try:
        confirmation_count_val = int(cc) if cc is not None else 0
    except (TypeError, ValueError):
        confirmation_count_val = 0
    files_touched = fm.get("files_touched") or []
    return {
        "subject": subject,
        "text": text,
        "values": [
            subject,
            fm.get("primary_tag") or "decision",
            fm.get("title") or "",
            confidence_to_float(fm.get("confidence")),
            row_status,
            None,  # embedding, filled per batch
            json.dumps(metadata, ensure_ascii=False),
            excerpt,
            project,
            fm.get("tool"),
            fm.get("model"),
            fm.get("task_category"),
            fm.get("author"),
            list(files_touched) if isinstance(files_touched, list) else [],
            fm.get("closing_commit"),
            fm.get("confidence_source"),
            confirmation_count_val,
            fm.get("valid_until"),
            fm.get("causal_parent_id"),
            fm.get("embedding_model_version"),
            fm.get("domain"),
            fm.get("goal"),
        ],
    }


def upsert_batch(paths: list[Path], schema: str, batch_size: int = 64) -> int:
    """Embed and upsert many decision files per round trip. Returns rows written."""
    if _DB_IMPORT_ERROR is not None or execute is None or vector_literal is None:
        log(f"db unavailable; cannot upsert: {_DB_IMPORT_ERROR}")
        return 0
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        log(f"unsafe schema name: {schema!r}")
        return 0
    insert_sql = (
        f"INSERT INTO {schema}.semantic_facts "  # nosec: schema is a validated identifier
        "(subject, predicate, object, confidence, status, embedding, metadata, chunk_context, "
        " project, tool, model, task_category, author, files_touched, closing_commit, "
        " confidence_source, confirmation_count, valid_until, causal_parent_id, "
        " embedding_model_version, domain, goal) "
        "VALUES (%s, %s, %s, %s, %s, %s::vector, %s::jsonb, %s, "
        " %s, %s, %s, %s, %s, %s, %s, "
        " %s, %s, %s, %s, "
        " %s, %s, %s)"
    )
    written = 0
    for start in range(0, len(paths), batch_size):
        chunk = paths[start : start + batch_size]
        prepared = []
        for path in chunk:
            try:
                row = prepare_row(path, schema)
            except Exception as e:  # noqa: BLE001
                log(f"skip: parse failed for {path}: {e}")
                row = None
            if row:
                prepared.append(row)
        if not prepared:
            continue
        try:
            vectors = _embed([row["text"] for row in prepared])
        except Exception as e:  # noqa: BLE001
            log(f"skip: embed failed for batch at {chunk[0]}: {e}")
            continue
        if len(vectors) != len(prepared):
            log(f"skip: embedder returned {len(vectors)} vectors for {len(prepared)} rows")
            continue
        params = []
        for row, vector in zip(prepared, vectors):
            values = list(row["values"])
            values[5] = vector_literal(vector)
            params.append(tuple(values))
        try:
            execute(
                f"DELETE FROM {schema}.semantic_facts WHERE subject = ANY(%s);",  # nosec: validated identifier
                ([row["subject"] for row in prepared],),
            )
            execute_many(insert_sql, params)
            written += len(params)
        except Exception as e:  # noqa: BLE001
            log(f"db error on batch at {chunk[0]}: {e}")
    return written


def truncate_facts(schema: str) -> None:
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"unsafe schema name: {schema!r}")
    if _DB_IMPORT_ERROR is not None or execute_script is None:
        raise RuntimeError(f"db unavailable: {_DB_IMPORT_ERROR}")
    execute_script(f"TRUNCATE TABLE {schema}.semantic_facts RESTART IDENTITY CASCADE;")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sync Postgres state from canonical markdown files")
    p.add_argument("--workdir", default=".", help="Project root")
    p.add_argument(
        "--schema",
        default=None,
        help="Postgres schema. Default: $AGENT_MEMORY_SCHEMA or 'personal_memory'.",
    )
    p.add_argument(
        "--embed-model",
        default="mxbai-embed-large",
        help="Legacy flag; ignored. Backend chosen via $EMBED_BACKEND.",
    )
    p.add_argument("--rebuild", action="store_true", help="TRUNCATE semantic_facts before upserting")
    p.add_argument("--include-history", action="store_true", help="Also upsert _history/ files")
    p.add_argument(
        "--no-review",
        dest="include_review",
        action="store_false",
        default=True,
        help="Skip _review/ quarantine captures (default: include them, status='quarantined').",
    )
    p.add_argument(
        "--project",
        default=None,
        help="Explicit canonical project tag to sync. Defaults to resolving from --workdir.",
    )
    args = p.parse_args(argv)
    if args.schema is None:
        args.schema = _default_schema()

    workdir = Path(args.workdir).resolve()
    files = list_decision_files(
        workdir, args.include_history, project=args.project, include_review=args.include_review
    )
    if not files:
        log(f"validation: no decision files under canonical memory store for {workdir}")
        return 1

    if args.rebuild:
        try:
            truncate_facts(args.schema)
            log(f"truncated {args.schema}.semantic_facts")
        except Exception as e:  # noqa: BLE001
            log(f"db error during truncate: {e}")
            return 2

    written = upsert_batch(files, args.schema)
    log(f"sync_db_from_files: upserted {written}/{len(files)} decision file(s)")
    return 0 if written == len(files) else 2


if __name__ == "__main__":
    sys.exit(main())
