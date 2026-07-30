#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""One-way NavGator lessons -> local SQLite semantic-facts sync.

Reads project-local + global NavGator lessons.json files and upserts each
real (non-template) lesson into the local build-loop-memory SQLite index
with ``subject='lesson:nav:<id>'`` and ``domain='architecture'``.

Cross-project lesson recall happens through the existing recall pipeline;
this script only writes.

Inputs
------
* Project-local: ``<workdir>/.navgator/lessons/lessons.json``
* Global:        ``~/.navgator/lessons/global-lessons.json``

Both files share a common shape (``{"lessons": [...]}``) with an optional
``_template`` sibling object that is always skipped. Inside ``lessons``,
any entry whose ``id`` starts with ``_template`` or is empty is also
skipped.

Project identity
----------------
Project tag is derived once per run from:
1. ``git remote get-url origin`` (basename minus ``.git``)
2. ``Path.cwd().name`` fallback

For project-local lessons the row's ``project`` column holds this tag.
For global lessons ``project`` is NULL — that is the cross-project
sentinel ``recall.py`` already understands.

SQLite upsert
-------------
The local index lives at ``<memory-root>/indexes/semantic_facts.sqlite`` by
default. It has a real ``PRIMARY KEY(subject, project_key)`` and uses an
idempotent upsert.

Postgres mirror
---------------
Postgres is optional. Pass ``--postgres-mirror`` to also write
``agent_memory.<schema>.semantic_facts``. Postgres-side, ``semantic_facts``
has no UNIQUE constraint on ``(subject, project)``. We follow the
``sync_db_from_files.py`` pattern: DELETE-then-INSERT in a single transaction.

Embeddings
----------
The SQLite default does not require embeddings. When ``--postgres-mirror`` is
set, ``scripts/embed_backend.embed`` is attempted for the Postgres vector
column. On embedding failure the mirror row is still written with
``embedding = NULL`` and the JSON output records the per-lesson error.

Failure modes
-------------
* SQLite write failure -> log to ``.build-loop/sync_errors.log`` and continue
  with ``errors: ["sqlite_upsert_failed:<lesson_id>"]``.
* Postgres mirror unreachable -> log to ``.build-loop/sync_errors.log`` and
  exit 0 with SQLite counts intact plus ``errors: ["postgres_unavailable"]``.
* Missing/empty/template-only ``lessons.json`` → ``synced: 0`` cleanly.
* Embedding subsystem unavailable → row written with NULL embedding;
  ``errors`` lists ``embed_unavailable:<lesson_id>``.

Stdout (always JSON):
    {
      "synced":            <int>,   # project-local SQLite rows written
      "global_synced":     <int>,   # global SQLite rows written
      "postgres_mirrored": <int>,   # project-local Postgres mirror rows
      "global_postgres_mirrored": <int>,
      "skipped_templates": <int>,
      "errors":            [str],
      "schema_version":    "1.0.0"
    }

CLI
---
    --project-only      only sync project-local lessons
    --global-only       only sync ~/.navgator/lessons/global-lessons.json
    --dry-run           don't open a DB connection or write anything
    --postgres-mirror   also write Postgres semantic_facts
    --sqlite-db PATH    override local SQLite index path
    --workdir PATH      project root (default: cwd)
    --lessons-file PATH override the lessons source — treat the given file as the
                        single project-local lessons.json and skip both the
                        NavGator project-local + global discovery. Used by
                        Chunk 8's promotion pipeline to feed
                        ``.build-loop/architecture/lessons.json`` through the
                        same write path with a different subject prefix.
    --source-prefix STR override the ``subject = '<prefix><lesson_id>'`` mapping.
                        Default ``lesson:nav:`` (NavGator origin). Chunk 8's
                        promotion script passes ``lesson:bl:`` so build-loop-
                        native lessons land in distinct semantic_facts rows.

Postgres DSN resolution (mirror only; matches build-loop convention):
    1. $BUILD_LOOP_DATABASE_URL (preferred, plan-doc convention)
    2. $DATABASE_URL            (fallback, ``db.py`` default)
    3. ~/.config/agent-memory/connection.env
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
DOMAIN = "architecture"
TOOL = "navgator"
SOURCE = "migration"  # one-way external import per write_decision taxonomy
SYNC_ERRORS_LOG = ".build-loop/sync_errors.log"

# One-time-per-process guard for the psycopg-missing warning. The Postgres
# mirror is OPTIONAL (SQLite is the source of truth); a missing psycopg should
# degrade gracefully, but it had been failing SILENTLY — only a line in
# .build-loop/sync_errors.log, which recurred 2026-05-05..2026-06-03 unseen.
# Surface it ONCE on stderr with the actionable install hint, then stay quiet.
_PSYCOPG_WARNED = False


def _warn_psycopg_missing_once() -> None:
    global _PSYCOPG_WARNED
    if _PSYCOPG_WARNED:
        return
    _PSYCOPG_WARNED = True
    _log(
        "psycopg not installed — skipping the optional Postgres mirror "
        "(SQLite remains the source of truth). Install with "
        "`uv pip install -e .[db]` to enable the mirror. "
        "(This notice prints once per run; details in .build-loop/sync_errors.log.)"
    )

# Make scripts/ importable as a sibling module (mirrors capture_arch_violation.py).
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from semantic_index import upsert_lesson as _upsert_sqlite_lesson  # type: ignore  # noqa: E402


# ---------- helpers ----------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(f"[sync_navgator_lessons] {msg}", file=sys.stderr, flush=True)


def _append_sync_error(workdir: Path, message: str) -> None:
    """Best-effort append to .build-loop/sync_errors.log; never raises."""
    try:
        log_path = workdir / SYNC_ERRORS_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{_now_iso()}: {message}\n")
    except OSError as exc:
        _log(f"WARN: could not write sync_errors.log ({exc})")


def _detect_project_tag(workdir: Path) -> str:
    """Best-effort project identifier: git remote basename, then cwd name."""
    try:
        cp = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if cp.returncode == 0:
            url = cp.stdout.strip()
            if url:
                # Strip trailing .git, take final path segment.
                stem = url.rstrip("/")
                if stem.endswith(".git"):
                    stem = stem[: -len(".git")]
                # Handle both git@host:owner/repo and https://host/owner/repo
                for sep in (":", "/"):
                    if sep in stem:
                        stem = stem.rsplit(sep, 1)[-1]
                if stem:
                    return stem
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    name = workdir.name
    return name or "unknown"


def _read_lessons_file(path: Path) -> tuple[list[dict], int]:
    """Return (real_lessons, skipped_template_count). Missing/invalid → ([], 0)."""
    if not path.exists():
        return [], 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"WARN: lessons file unreadable ({path}): {exc}")
        return [], 0
    if not isinstance(data, dict):
        return [], 0
    raw_lessons = data.get("lessons", []) or []
    if not isinstance(raw_lessons, list):
        return [], 0
    real: list[dict] = []
    skipped = 0
    for entry in raw_lessons:
        if not isinstance(entry, dict):
            continue
        lid = str(entry.get("id", "") or "").strip()
        if not lid or lid.startswith("_template"):
            skipped += 1
            continue
        real.append(entry)
    return real, skipped


def _confidence_source_for(promoted: bool) -> str:
    """Map NavGator's ``promoted`` flag to write_decision's closed taxonomy.

    Aligns with Chunk 6 helper inside ``capture_arch_violation.py``: every
    automated capture uses the ``auto-*`` prefix. Promoted lessons have
    been hand-validated → ``auto-confirmed``; un-promoted are heuristic →
    ``auto-inferred``.
    """
    return "auto-confirmed" if promoted else "auto-inferred"


def _confidence_float_for(promoted: bool) -> float:
    """Mirror ``write_decision._confidence_to_float`` so DB readers see the
    same numeric scale they get for decisions."""
    return 0.75 if promoted else 0.5


def _is_safe_schema(schema: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9_]*$", schema))


# ---------- DB layer ----------


def _resolve_dsn() -> str | None:
    """Plan-doc DSN order: BUILD_LOOP_DATABASE_URL → DATABASE_URL → connection.env.

    Delegates to the shared resolver (`scripts/_db_url.py`). Returns None
    when nothing is configured (the shared resolver returns ""); caller
    treats None as 'postgres unavailable' only when --postgres-mirror is set.
    """
    from _db_url import resolve_db_url  # noqa: PLC0415

    return resolve_db_url() or None


def _open_connection():
    """Open a psycopg connection. Raises on any failure.

    DSN comes from `_resolve_dsn`, which delegates to the shared
    `scripts/_db_url.py` resolver (BUILD_LOOP_DATABASE_URL → DATABASE_URL →
    connection.env). We open psycopg directly here rather than via
    ``scripts.db`` to keep this script's soft-failure (return-None →
    exit-0) envelope instead of db.py's raise-on-missing contract.
    """
    import psycopg  # type: ignore  # noqa: PLC0415

    dsn = _resolve_dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL/BUILD_LOOP_DATABASE_URL not configured")
    return psycopg.connect(dsn, autocommit=False)


def _vector_literal(embedding: list[float] | None) -> str | None:
    if not embedding:
        return None
    return "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"


def _upsert_lesson(
    conn,
    *,
    schema: str,
    lesson: dict,
    project: str | None,
    embedding: list[float] | None,
    subject_prefix: str = "lesson:nav:",
) -> None:
    """DELETE-then-INSERT a single lesson row in one transaction.

    Mirrors ``sync_db_from_files.upsert_decision`` — the schema has no
    UNIQUE(subject, project) so we cannot use a real ON CONFLICT. The
    DELETE+INSERT pair runs inside the caller's transaction; commit is
    issued by the caller after all lessons are processed (or on error).
    """
    if not _is_safe_schema(schema):
        raise ValueError(f"unsafe schema name: {schema!r}")

    lesson_id = str(lesson.get("id", ""))
    subject = f"{subject_prefix}{lesson_id}"
    predicate = str(lesson.get("category", "") or "uncategorized")
    obj_summary = str(lesson.get("pattern", "") or "")
    promoted = bool(lesson.get("promoted", False))
    confidence_source = _confidence_source_for(promoted)
    confidence = _confidence_float_for(promoted)

    metadata = {
        "lesson_id": lesson_id,
        "promoted": promoted,
        "navgator_lesson": lesson,  # full original payload
        "synced_at": _now_iso(),
        "schema_version": SCHEMA_VERSION,
    }

    # Files affected → array column when present, else empty list.
    ctx = lesson.get("context") or {}
    files_affected = []
    if isinstance(ctx, dict):
        fa = ctx.get("files_affected") or []
        if isinstance(fa, list):
            files_affected = [str(x) for x in fa]

    # last_synced lives in metadata, not as its own column (semantic_facts
    # already has last_validated/last_accessed; we don't want to clobber
    # those for facts that pre-existed in the table).
    metadata["last_synced"] = _now_iso()

    vec_lit = _vector_literal(embedding)

    # DELETE + INSERT inside one cursor for transactional symmetry.
    with conn.cursor() as cur:
        if project is None:
            cur.execute(
                f"DELETE FROM {schema}.semantic_facts "  # nosec: schema is a validated identifier (^[a-z][a-z0-9_]*$); values bound as params
                "WHERE subject = %s AND project IS NULL;",
                (subject,),
            )
        else:
            cur.execute(
                f"DELETE FROM {schema}.semantic_facts "  # nosec: schema is a validated identifier (^[a-z][a-z0-9_]*$); values bound as params
                "WHERE subject = %s AND project = %s;",
                (subject, project),
            )
        if vec_lit is None:
            sql = (
                f"INSERT INTO {schema}.semantic_facts "  # nosec: schema is a validated identifier (^[a-z][a-z0-9_]*$); values bound as params
                "(subject, predicate, object, confidence, status, embedding, metadata, "
                " project, tool, task_category, files_touched, "
                " confidence_source, domain) "
                "VALUES (%s, %s, %s, %s, 'active', NULL, %s::jsonb, "
                " %s, %s, %s, %s, "
                " %s, %s);"
            )
            params = (
                subject,
                predicate,
                obj_summary,
                confidence,
                json.dumps(metadata, ensure_ascii=False),
                project,
                TOOL,
                "research",
                files_affected,
                confidence_source,
                DOMAIN,
            )
        else:
            sql = (
                f"INSERT INTO {schema}.semantic_facts "  # nosec: schema is a validated identifier (^[a-z][a-z0-9_]*$); values bound as params
                "(subject, predicate, object, confidence, status, embedding, metadata, "
                " project, tool, task_category, files_touched, "
                " confidence_source, domain) "
                "VALUES (%s, %s, %s, %s, 'active', %s::vector, %s::jsonb, "
                " %s, %s, %s, %s, "
                " %s, %s);"
            )
            params = (
                subject,
                predicate,
                obj_summary,
                confidence,
                vec_lit,
                json.dumps(metadata, ensure_ascii=False),
                project,
                TOOL,
                "research",
                files_affected,
                confidence_source,
                DOMAIN,
            )
        cur.execute(sql, params)


# ---------- main pipeline ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="One-way NavGator lessons -> local SQLite semantic-facts sync"
    )
    p.add_argument(
        "--workdir",
        default=".",
        help="Project root (default: cwd). Project-local lessons live under <workdir>/.navgator/lessons/lessons.json.",
    )
    p.add_argument(
        "--project-only",
        action="store_true",
        help="Sync only project-local lessons.json.",
    )
    p.add_argument(
        "--global-only",
        action="store_true",
        help="Sync only ~/.navgator/lessons/global-lessons.json.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not open SQLite or Postgres. Reports what would be synced.",
    )
    p.add_argument(
        "--sqlite-db",
        default=None,
        help="Override local SQLite semantic index path. Default: <memory-root>/indexes/semantic_facts.sqlite.",
    )
    p.add_argument(
        "--postgres-mirror",
        action="store_true",
        help="Also mirror rows into Postgres semantic_facts. Default: SQLite only.",
    )
    p.add_argument(
        "--schema",
        default=None,
        help="Postgres schema. Default: $AGENT_MEMORY_SCHEMA or 'personal_memory'.",
    )
    p.add_argument(
        "--lessons-file",
        default=None,
        help=(
            "Override lessons source. When set, the given file is treated as the "
            "single project-local lessons.json and the NavGator project-local + "
            "global discovery is skipped. Used by Chunk 8's build-loop-native "
            "promotion pipeline."
        ),
    )
    p.add_argument(
        "--source-prefix",
        default="lesson:nav:",
        help=(
            "Subject prefix written into semantic_facts.subject "
            "(default 'lesson:nav:'). Chunk 8's promotion script passes "
            "'lesson:bl:' to keep build-loop-native lessons in distinct rows."
        ),
    )
    return p.parse_args(argv)


def _default_schema() -> str:
    return os.environ.get("AGENT_MEMORY_SCHEMA") or "personal_memory"


def _embed_safely(text: str, errors: list[str], lesson_id: str) -> list[float] | None:
    """Call embed_backend; on any failure log to ``errors`` and return None."""
    if not text or not text.strip():
        return None
    try:
        from embed_backend import embed as _embed  # type: ignore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        errors.append(f"embed_unavailable:{lesson_id}")
        _log(f"WARN: embed_backend import failed for {lesson_id}: {exc}")
        return None
    try:
        return _embed(text)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"embed_failed:{lesson_id}")
        _log(f"WARN: embedding {lesson_id} failed: {exc}")
        return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workdir = Path(args.workdir).resolve()
    schema = args.schema or _default_schema()

    if args.project_only and args.global_only:
        print(
            json.dumps(
                {
                    "synced": 0,
                    "global_synced": 0,
                    "postgres_mirrored": 0,
                    "global_postgres_mirrored": 0,
                    "skipped_templates": 0,
                    "errors": ["invalid_flags:project_only_and_global_only_mutually_exclusive"],
                    "schema_version": SCHEMA_VERSION,
                },
                sort_keys=True,
            )
        )
        return 0

    project_tag = _detect_project_tag(workdir)
    subject_prefix = args.source_prefix or "lesson:nav:"

    project_lessons: list[dict] = []
    global_lessons: list[dict] = []
    skipped_templates = 0

    if args.lessons_file:
        # Override mode: treat --lessons-file as the sole project-local
        # source. Skip NavGator's project-local + global discovery so
        # promotion-fed lessons don't double-sync.
        path = Path(args.lessons_file)
        if not path.is_absolute():
            path = (workdir / path).resolve()
        real, skipped = _read_lessons_file(path)
        project_lessons = real
        skipped_templates += skipped
    else:
        if not args.global_only:
            path = workdir / ".navgator" / "lessons" / "lessons.json"
            real, skipped = _read_lessons_file(path)
            project_lessons = real
            skipped_templates += skipped

        if not args.project_only:
            path = Path.home() / ".navgator" / "lessons" / "global-lessons.json"
            real, skipped = _read_lessons_file(path)
            global_lessons = real
            skipped_templates += skipped

    errors: list[str] = []
    synced = 0
    global_synced = 0
    postgres_mirrored = 0
    global_postgres_mirrored = 0

    if args.dry_run:
        out = {
            "synced": len(project_lessons),
            "global_synced": len(global_lessons),
            "postgres_mirrored": 0,
            "global_postgres_mirrored": 0,
            "skipped_templates": skipped_templates,
            "errors": errors,
            "schema_version": SCHEMA_VERSION,
            "dry_run": True,
            "postgres_mirror": bool(args.postgres_mirror),
        }
        print(json.dumps(out, sort_keys=True))
        return 0

    if not project_lessons and not global_lessons:
        out = {
            "synced": 0,
            "global_synced": 0,
            "postgres_mirrored": 0,
            "global_postgres_mirrored": 0,
            "skipped_templates": skipped_templates,
            "errors": errors,
            "schema_version": SCHEMA_VERSION,
            "postgres_mirror": bool(args.postgres_mirror),
        }
        print(json.dumps(out, sort_keys=True))
        return 0

    sqlite_db = Path(args.sqlite_db).expanduser().resolve() if args.sqlite_db else None

    for lesson in project_lessons:
        lid = str(lesson.get("id", ""))
        promoted = bool(lesson.get("promoted", False))
        try:
            _upsert_sqlite_lesson(
                lesson=lesson,
                project=project_tag,
                subject_prefix=subject_prefix,
                confidence=_confidence_float_for(promoted),
                confidence_source=_confidence_source_for(promoted),
                db_path=sqlite_db,
                tool=TOOL,
                domain=DOMAIN,
            )
            synced += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"sqlite_upsert_failed:{lid}: {type(exc).__name__}: {exc}"
            errors.append(f"sqlite_upsert_failed:{lid}")
            _append_sync_error(workdir, msg)

    for lesson in global_lessons:
        lid = str(lesson.get("id", ""))
        promoted = bool(lesson.get("promoted", False))
        try:
            _upsert_sqlite_lesson(
                lesson=lesson,
                project=None,
                subject_prefix=subject_prefix,
                confidence=_confidence_float_for(promoted),
                confidence_source=_confidence_source_for(promoted),
                db_path=sqlite_db,
                tool=TOOL,
                domain=DOMAIN,
            )
            global_synced += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"sqlite_upsert_failed_global:{lid}: {type(exc).__name__}: {exc}"
            errors.append(f"sqlite_upsert_failed:{lid}")
            _append_sync_error(workdir, msg)

    if args.postgres_mirror:
        # Open Postgres. Soft-fail to graceful exit on any connection error.
        try:
            conn = _open_connection()
        except Exception as exc:  # noqa: BLE001 - psycopg.OperationalError, RuntimeError, etc.
            msg = f"postgres_unavailable: {type(exc).__name__}: {exc}"
            _append_sync_error(workdir, msg)
            errors.append("postgres_unavailable")
            conn = None
            # A MISSING psycopg module (vs. a reachable-but-down server) is the
            # silent recurring case. Surface it visibly, once per run, with the
            # install hint — don't bury it in the log file again.
            if isinstance(exc, ModuleNotFoundError) or "psycopg" in str(exc).lower():
                _warn_psycopg_missing_once()

        if conn is not None:
            try:
                for lesson in project_lessons:
                    lid = str(lesson.get("id", ""))
                    embedding = _embed_safely(
                        str(lesson.get("pattern", "") or ""), errors, lid
                    )
                    try:
                        _upsert_lesson(
                            conn,
                            schema=schema,
                            lesson=lesson,
                            project=project_tag,
                            embedding=embedding,
                            subject_prefix=subject_prefix,
                        )
                        postgres_mirrored += 1
                    except Exception as exc:  # noqa: BLE001 - psycopg + unforeseen
                        conn.rollback()
                        msg = f"upsert_failed:{lid}: {type(exc).__name__}: {exc}"
                        errors.append(f"upsert_failed:{lid}")
                        _append_sync_error(workdir, msg)
                for lesson in global_lessons:
                    lid = str(lesson.get("id", ""))
                    embedding = _embed_safely(
                        str(lesson.get("pattern", "") or ""), errors, lid
                    )
                    try:
                        _upsert_lesson(
                            conn,
                            schema=schema,
                            lesson=lesson,
                            project=None,
                            embedding=embedding,
                            subject_prefix=subject_prefix,
                        )
                        global_postgres_mirrored += 1
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        msg = f"upsert_failed_global:{lid}: {type(exc).__name__}: {exc}"
                        errors.append(f"upsert_failed:{lid}")
                        _append_sync_error(workdir, msg)
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    out = {
        "synced": synced,
        "global_synced": global_synced,
        "postgres_mirrored": postgres_mirrored,
        "global_postgres_mirrored": global_postgres_mirrored,
        "skipped_templates": skipped_templates,
        "errors": errors,
        "schema_version": SCHEMA_VERSION,
        "postgres_mirror": bool(args.postgres_mirror),
        "sqlite_db": str(sqlite_db) if sqlite_db else None,
    }
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
