#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Postgres dual-write path (Phase 2) + embedding/legacy shims.

Best-effort INSERT into ``agent_memory.<schema>.semantic_facts`` after the
canonical file write. Every failure is logged and swallowed — the file is
canonical and the DB is regenerable via ``sync_db_from_files.py``. Behaviour
is byte-for-byte identical to the historical flat-module ``db_dualwrite``;
the decomposition is purely structural (helpers for the metadata dict, the
INSERT column shapes, and the param tuple).

Named ``dbwrite`` rather than ``db`` so the intra-package module never shadows
the top-level ``scripts/db.py`` it imports from.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from constants import log

# Column lists for the two INSERT shapes. The Phase-D shape adds the trailing
# ``chunk_context`` column; the legacy shape is retried when that column is
# absent (migration not yet run).
_COLS_V3 = (
    "(subject, predicate, object, confidence, status, embedding, metadata, "
    " project, tool, model, task_category, author, files_touched, closing_commit, "
    " confidence_source, confirmation_count, valid_until, causal_parent_id, "
    " embedding_model_version, domain, goal, chunk_context) "
)
_VALUES_V3 = (
    "VALUES (%s, %s, %s, %s, 'active', %s::vector, %s::jsonb, "
    " %s, %s, %s, %s, %s, %s, %s, "
    " %s, %s, %s, %s, %s, %s, %s, %s);"
)
_COLS_LEGACY = (
    "(subject, predicate, object, confidence, status, embedding, metadata, "
    " project, tool, model, task_category, author, files_touched, closing_commit, "
    " confidence_source, confirmation_count, valid_until, causal_parent_id, "
    " embedding_model_version, domain, goal) "
)
_VALUES_LEGACY = (
    "VALUES (%s, %s, %s, %s, 'active', %s::vector, %s::jsonb, "
    " %s, %s, %s, %s, %s, %s, %s, "
    " %s, %s, %s, %s, %s, %s, %s);"
)


def _confidence_to_float(c: str | None) -> float:
    return {"assumed": 0.25, "inferred": 0.5, "confirmed": 0.75, "explicit": 1.0}.get(c or "", 0.5)


def _coerce_confirmation_count(cc_val: Any) -> int:
    """Coerce a frontmatter confirmation_count to int, defaulting to 0.

    YAML scalars sometimes parse as strings, so coerce defensively.
    """
    try:
        return int(cc_val) if cc_val is not None else 0
    except (TypeError, ValueError):
        return 0


def _build_metadata(decision_id: str, fm: dict[str, Any], chunk_context: str) -> dict[str, Any]:
    """Mirror v2/v3 frontmatter fields into the JSONB metadata blob so older
    readers (without typed columns) still see them."""
    return {
        "decision_id": decision_id,
        "canonical_id": fm.get("canonical_id"),
        "entity": fm.get("entity"),
        "tags": fm.get("tags"),
        "status": fm.get("status"),
        "confidence": fm.get("confidence"),
        "source": fm.get("source"),
        "date": fm.get("date"),
        # v2 fields mirrored into JSONB so older readers still see them.
        "project": fm.get("project"),
        "tool": fm.get("tool"),
        "model": fm.get("model"),
        "task_category": fm.get("task_category"),
        "author": fm.get("author"),
        "files_touched": fm.get("files_touched") or [],
        "closing_commit": fm.get("closing_commit"),
        # v3 mirrored into JSONB (design §16) so older readers still see them.
        "confidence_source": fm.get("confidence_source"),
        "confirmation_count": fm.get("confirmation_count"),
        "valid_until": fm.get("valid_until"),
        "causal_parent_id": fm.get("causal_parent_id"),
        "embedding_model_version": fm.get("embedding_model_version"),
        "domain": fm.get("domain"),
        "goal": fm.get("goal"),
        # Phase D: mirrored into JSONB so readers without the new
        # column still see it.
        "chunk_context": chunk_context or None,
    }


def _common_insert_params(
    *,
    subject: str,
    predicate: str,
    obj_summary: str,
    fm: dict[str, Any],
    embedding: Any,
    metadata_json: str,
    files: Any,
    cc_db: int,
    vector_literal: Any,
) -> tuple:
    """The 21-element param prefix shared by both the Phase-D and legacy
    INSERT shapes (everything up to and excluding the trailing
    ``chunk_context`` value)."""
    return (
        subject,
        predicate,
        obj_summary,
        _confidence_to_float(fm.get("confidence")),
        vector_literal(embedding),
        metadata_json,
        fm.get("project"),
        fm.get("tool"),
        fm.get("model"),
        fm.get("task_category"),
        fm.get("author"),
        list(files) if isinstance(files, list) else [],
        fm.get("closing_commit"),
        fm.get("confidence_source"),
        cc_db,
        fm.get("valid_until"),
        fm.get("causal_parent_id"),
        fm.get("embedding_model_version"),
        fm.get("domain"),
        fm.get("goal"),
    )


def _execute_insert(execute: Any, schema: str, base_params: tuple, chunk_context: str) -> None:
    """Run the Phase-D INSERT; on a missing ``chunk_context`` column, retry the
    legacy column shape so writers on un-migrated installs don't lose the row.

    The next ``sync_db_from_files`` run backfills ``chunk_context`` once the
    migration lands. Any other error propagates to the caller's broad handler.
    """
    # v2: write typed columns when present (graceful degrade if migration
    # hasn't run yet — caught by the broad except in the caller).
    # Phase D: include chunk_context in the column list.
    sql = f"INSERT INTO {schema}.semantic_facts " + _COLS_V3 + _VALUES_V3  # nosec: schema is a validated identifier (^[a-z][a-z0-9_]*$); values bound as params
    try:
        execute(sql, base_params + (chunk_context or None,))
        return
    except Exception as e:  # noqa: BLE001
        # Most likely cause: migrate_add_chunk_context_column.py hasn't run
        # yet (column missing). Retry without the new column.
        if "chunk_context" not in str(e):
            raise
        log(f"db dual-write: chunk_context column missing ({e}); retrying legacy shape")
    sql_legacy = f"INSERT INTO {schema}.semantic_facts " + _COLS_LEGACY + _VALUES_LEGACY  # nosec: schema is a validated identifier (^[a-z][a-z0-9_]*$); values bound as params
    execute(sql_legacy, base_params)


def _resolve_chunk_context(fm: dict[str, Any], body_text: str) -> str:
    """Caller-provided chunk_context wins; otherwise best-effort generate it.

    Returns "" when the local LLM router is unavailable (pre-Phase-D path).
    """
    chunk_context = (fm.get("chunk_context") or "").strip()
    if chunk_context:
        return chunk_context
    try:
        from contextual_prepend import (  # type: ignore  # noqa: PLC0415
            generate_chunk_context,
        )
        return generate_chunk_context(body_text, max_tokens=100)
    except Exception as e:  # noqa: BLE001
        log(f"db dual-write: chunk_context unavailable ({e}); proceeding without prepend")
        return ""


def db_dualwrite(
    decision_id: str,
    fm: dict[str, Any],
    body_text: str,
    workdir: Path,
    schema: str,
    embed_model: str,  # retained for back-compat; embed_backend reads its own env
) -> None:
    """Embed body and INSERT into agent_memory.<schema>.semantic_facts.

    Best-effort: any failure is logged and swallowed. The file is canonical.
    Uses the persistent psycopg connection from `db.py`. Embedding goes
    through `embed_backend.embed` (MLX default, Ollama fallback, 1024-dim).

    Phase D (Anthropic Contextual Retrieval): also generates a
    `chunk_context` ~80-token summary via
    `contextual_prepend.generate_chunk_context` and prepends it to the
    embedded text. The chunk_context is also passed through to the new
    `chunk_context` column when present (graceful no-op when the
    migration hasn't run). When the local LLM router is unavailable
    (no Ollama target pulled), chunk_context is empty and behaviour
    reverts to the pre-Phase-D path. Caller may pre-populate
    `fm['chunk_context']` (e.g. mirrored from frontmatter); when set,
    we use it verbatim instead of regenerating.
    """
    try:
        from embed_backend import embed as _embed  # type: ignore  # noqa: PLC0415

        # Phase D: best-effort chunk_context. Caller-provided wins to
        # avoid double-generation when the writer pre-populated fm.
        chunk_context = _resolve_chunk_context(fm, body_text)

        embed_text = (
            f"{chunk_context}\n\n{body_text}" if chunk_context else body_text
        )
        try:
            embedding = _embed(embed_text)
        except Exception as e:  # noqa: BLE001
            log(f"db dual-write: embed unavailable ({e}); skipping db row for decision {decision_id}")
            return
        # Local import keeps Phase 1 (`--no-db` test runs) from requiring psycopg.
        from db import execute, vector_literal  # type: ignore  # noqa: PLC0415

        # Subject namespaced by project to avoid cross-project ID collisions
        # (decision IDs are allocated per-project; without namespacing,
        # build-loop/0001 and example-app/0001 would clobber each other).
        _project_for_subject = (fm.get("project") or "_unscoped").strip() or "_unscoped"
        subject = f"decision:{_project_for_subject}:{decision_id}"
        predicate = fm.get("primary_tag") or "decision"
        obj_summary = fm.get("title") or ""
        metadata = _build_metadata(decision_id, fm, chunk_context)
        # Schema is operator-controlled (CLI flag), not user input. Validate shape
        # to keep the f-string interpolation safe; psycopg cannot bind table names.
        if not re.match(r"^[a-z][a-z0-9_]*$", schema):
            raise ValueError(f"unsafe schema name: {schema!r}")
        files = fm.get("files_touched") or []
        cc_db = _coerce_confirmation_count(fm.get("confirmation_count"))
        metadata_json = json.dumps(metadata, ensure_ascii=False)
        base_params = _common_insert_params(
            subject=subject,
            predicate=predicate,
            obj_summary=obj_summary,
            fm=fm,
            embedding=embedding,
            metadata_json=metadata_json,
            files=files,
            cc_db=cc_db,
            vector_literal=vector_literal,
        )
        _execute_insert(execute, schema, base_params, chunk_context)
        log(f"db dual-write: inserted semantic_facts row for decision {decision_id}")
    except Exception as e:  # noqa: BLE001
        log(f"db dual-write: error (file write succeeded; DB regenerable via sync_db_from_files.py): {e}")


def ollama_embed(text: str, model: str) -> "list[float] | None":
    """Deprecation shim. Delegates to `embed_backend.embed`.

    Kept for back-compat with code that previously imported this name
    from write_decision (e.g. recall.py, sync_db_from_files.py,
    scan_transcript_for_decisions.py). Returns None on any error so
    legacy callers' "if embedding is None: skip" branches still work.

    The `model` argument is ignored — embed_backend reads $EMBED_BACKEND
    and $EMBED_MODEL from the environment. This is intentional: the
    abstraction owns model selection now, not the caller.
    """
    try:
        from embed_backend import embed as _embed  # type: ignore  # noqa: PLC0415

        return _embed(text)
    except Exception as e:  # noqa: BLE001
        log(f"embed_backend (legacy ollama_embed shim): {e}")
        return None


# NOTE: `psql_run` was removed when production scripts migrated to psycopg
# (`scripts/db.py`) on 2026-05-04. Callers should `from db import execute,
# execute_script, query, query_one`. The legacy name is preserved here as
# a deprecation shim for any out-of-tree code that might still import it.


def psql_run(sql: str, workdir: Path) -> None:  # pragma: no cover - shim
    """Deprecated. Use `db.execute_script(sql)` for multi-statement SQL or
    `db.execute(sql, params)` for parameterized single statements.
    """
    from db import execute_script  # type: ignore  # noqa: PLC0415

    execute_script(sql)
