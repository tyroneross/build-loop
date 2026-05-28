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
    from db import execute, execute_script, vector_literal  # type: ignore  # noqa: E402
    _DB_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001
    execute = execute_script = vector_literal = None  # type: ignore[assignment]
    _DB_IMPORT_ERROR = exc


def list_decision_files(
    workdir: Path,
    include_history: bool,
    project: str | None = None,
) -> list[Path]:
    """Find decision files to sync.

    Active mode reads the canonical build-loop-memory tree under
    ``projects/<project>/decisions``. Legacy sources are considered only
    when ``BUILD_LOOP_MEMORY_MIGRATION_MODE=1``.
    """
    import os

    from _paths import memory_store_root, project_decisions_dir, project_memory_root  # noqa: PLC0415
    from project_resolver import resolve_project  # noqa: PLC0415
    workdir = Path(workdir).resolve()
    files: list[Path] = []
    root = memory_store_root().resolve()
    projects_root = project_memory_root().resolve()
    if project:
        decisions_dir = project_decisions_dir(project)
        if decisions_dir.exists():
            files.extend(
                sorted(
                    p for p in decisions_dir.glob("*.md")
                    if not p.name.upper().startswith("INDEX")
                )
            )
            if include_history:
                history = decisions_dir / "_history"
                if history.exists():
                    files.extend(sorted(history.glob("*.md")))
        return files

    if workdir in {root, projects_root}:
        if not projects_root.exists():
            return []
        for project_dir in sorted(p for p in projects_root.iterdir() if p.is_dir()):
            decisions_dir = project_dir / "decisions"
            files.extend(
                sorted(
                    p for p in decisions_dir.glob("*.md")
                    if not p.name.upper().startswith("INDEX")
                )
            )
            if include_history:
                history = decisions_dir / "_history"
                if history.exists():
                    files.extend(sorted(history.glob("*.md")))
        return files

    project = resolve_project(workdir)
    decisions_dir = project_decisions_dir(project)
    if decisions_dir.exists():
        files.extend(
            sorted(
                p for p in decisions_dir.glob("*.md")
                if not p.name.upper().startswith("INDEX")
            )
        )
        if include_history:
            history = decisions_dir / "_history"
            if history.exists():
                files.extend(sorted(history.glob("*.md")))

    if os.environ.get("BUILD_LOOP_MEMORY_MIGRATION_MODE") == "1":
        legacy_dir = workdir / ".episodic" / "decisions"
        if legacy_dir.exists():
            files.extend(sorted(legacy_dir.glob("[0-9][0-9][0-9][0-9]-*.md")))
            if include_history:
                history = legacy_dir / "_history"
                if history.exists():
                    files.extend(sorted(history.glob("*.md")))
    return files


def confidence_to_float(c: str) -> float:
    return {"assumed": 0.25, "inferred": 0.5, "confirmed": 0.75, "explicit": 1.0}.get(c, 0.5)


def upsert_decision(path: Path, schema: str, embed_model: str) -> bool:
    """Read MADR, embed body, upsert into semantic_facts.

    Uses (subject = 'decision:<project>:NNNN') as the natural key — the
    project tag is included so that decision IDs allocated independently
    in different projects don't collide on upsert. We DELETE then INSERT
    to keep the schema simple (no UNIQUE constraint added; a real upsert
    would use ON CONFLICT but we'd need a unique index on subject which
    would conflict with future multi-fact-per-decision use).
    """
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text) or {}
    decision_id = str(fm.get("id") or "").strip()
    if not decision_id:
        log(f"skip: no id in frontmatter for {path}")
        return False
    project = (fm.get("project") or "_unscoped").strip() or "_unscoped"
    if _DB_IMPORT_ERROR is not None or execute is None or vector_literal is None:
        log(f"db unavailable; cannot upsert {path}: {_DB_IMPORT_ERROR}")
        return False

    try:
        embedding = _embed(text)
    except Exception as e:  # noqa: BLE001
        log(f"skip: embed failed for {path}: {e}")
        return False

    subject_key = fm.get("canonical_id") or decision_id
    subject = f"decision:{project}:{subject_key}"
    predicate = fm.get("primary_tag") or "decision"
    obj_summary = fm.get("title") or ""
    metadata = {
        "decision_id": decision_id,
        "canonical_id": fm.get("canonical_id"),
        "entity": fm.get("entity"),
        "tags": fm.get("tags"),
        "status": fm.get("status"),
        "confidence": fm.get("confidence"),
        "source": fm.get("source"),
        "date": fm.get("date"),
        "file": str(path.name),
        # v2 fields mirrored into JSONB so older readers still see them.
        "project": fm.get("project"),
        "tool": fm.get("tool"),
        "model": fm.get("model"),
        "task_category": fm.get("task_category"),
        "author": fm.get("author"),
        "files_touched": fm.get("files_touched") or [],
        "closing_commit": fm.get("closing_commit"),
    }
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        log(f"unsafe schema name: {schema!r}")
        return False
    try:
        # DELETE + INSERT in one transaction (db.execute commits each call,
        # so issue them as a single multi-statement script).
        execute(
            f"DELETE FROM {schema}.semantic_facts WHERE subject = %s;",
            (subject,),
        )
        files_touched = fm.get("files_touched") or []
        # Coerce confirmation_count to int (YAML parser may return string)
        cc = fm.get("confirmation_count")
        try:
            confirmation_count_val = int(cc) if cc is not None else 0
        except (TypeError, ValueError):
            confirmation_count_val = 0
        execute(
            (
                f"INSERT INTO {schema}.semantic_facts "
                "(subject, predicate, object, confidence, status, embedding, metadata, "
                " project, tool, model, task_category, author, files_touched, closing_commit, "
                " confidence_source, confirmation_count, valid_until, causal_parent_id, "
                " embedding_model_version, domain, goal) "
                "VALUES (%s, %s, %s, %s, 'active', %s::vector, %s::jsonb, "
                " %s, %s, %s, %s, %s, %s, %s, "
                " %s, %s, %s, %s, "
                " %s, %s, %s);"
            ),
            (
                subject,
                predicate,
                obj_summary,
                confidence_to_float(fm.get("confidence")),
                vector_literal(embedding),
                json.dumps(metadata, ensure_ascii=False),
                fm.get("project"),
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
            ),
        )
        return True
    except Exception as e:  # noqa: BLE001
        log(f"db error on {path}: {e}")
        return False


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
        "--project",
        default=None,
        help="Explicit canonical project tag to sync. Defaults to resolving from --workdir.",
    )
    args = p.parse_args(argv)
    if args.schema is None:
        args.schema = _default_schema()

    workdir = Path(args.workdir).resolve()
    files = list_decision_files(workdir, args.include_history, project=args.project)
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

    written = 0
    for f in files:
        if upsert_decision(f, args.schema, args.embed_model):
            written += 1
    log(f"sync_db_from_files: upserted {written}/{len(files)} decision file(s)")
    return 0 if written == len(files) else 2


if __name__ == "__main__":
    sys.exit(main())
