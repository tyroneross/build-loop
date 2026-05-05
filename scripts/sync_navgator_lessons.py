#!/usr/bin/env python3
"""One-way NavGator lessons → Postgres semantic_facts sync (Chunk 7).

Reads project-local + global NavGator lessons.json files and upserts each
real (non-template) lesson into ``agent_memory.<schema>.semantic_facts``
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

Upsert
------
Postgres-side, ``semantic_facts`` has no UNIQUE constraint on
``(subject, project)``. We follow the ``sync_db_from_files.py`` pattern:
DELETE-then-INSERT in a single transaction — idempotent and safe.

Embeddings
----------
``scripts/embed_backend.embed`` (MLX default, Ollama fallback). On
embedding failure we still write the row with ``embedding = NULL`` and
record the per-lesson error in the JSON output's ``errors`` array.

Failure modes
-------------
* Postgres unreachable → log to ``.build-loop/sync_errors.log`` and exit
  0 with ``errors: ["postgres_unavailable"]``. Best-effort sync; the
  scout treats this as a soft failure.
* Missing/empty/template-only ``lessons.json`` → ``synced: 0`` cleanly.
* Embedding subsystem unavailable → row written with NULL embedding;
  ``errors`` lists ``embed_unavailable:<lesson_id>``.

Stdout (always JSON):
    {
      "synced":            <int>,   # project-local rows written
      "global_synced":     <int>,   # global rows written
      "skipped_templates": <int>,
      "errors":            [str],
      "schema_version":    "1.0.0"
    }

CLI
---
    --project-only      only sync project-local lessons
    --global-only       only sync ~/.navgator/lessons/global-lessons.json
    --dry-run           don't open a DB connection or write anything
    --workdir PATH      project root (default: cwd)
    --lessons-file PATH override the lessons source — treat the given file as the
                        single project-local lessons.json and skip both the
                        NavGator project-local + global discovery. Used by
                        Chunk 8's promotion pipeline to feed
                        ``.build-loop/architecture/lessons.json`` through the
                        same Postgres write path with a different subject prefix.
    --source-prefix STR override the ``subject = '<prefix><lesson_id>'`` mapping.
                        Default ``lesson:nav:`` (NavGator origin). Chunk 8's
                        promotion script passes ``lesson:bl:`` so build-loop-
                        native lessons land in distinct semantic_facts rows.

DSN resolution (matches build-loop convention from ``scripts/db.py``):
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

# Make scripts/ importable as a sibling module (mirrors capture_arch_violation.py).
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


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

    Returns None when nothing is configured. Caller treats None as
    'postgres unavailable' and exits 0 with the soft-failure envelope.
    """
    bl = os.environ.get("BUILD_LOOP_DATABASE_URL")
    if bl:
        return bl
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    conn_env = Path.home() / ".config" / "agent-memory" / "connection.env"
    if conn_env.exists():
        try:
            for line in conn_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            return None
    return None


def _open_connection():
    """Open a psycopg connection. Raises on any failure.

    DSN comes from `_resolve_dsn`. We do NOT delegate to ``scripts.db``
    because ``db.get_connection`` only reads ``DATABASE_URL`` /
    connection.env — the plan-doc explicitly asks us to honour
    ``BUILD_LOOP_DATABASE_URL`` first as a build-loop-namespaced override.
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
                f"DELETE FROM {schema}.semantic_facts "
                "WHERE subject = %s AND project IS NULL;",
                (subject,),
            )
        else:
            cur.execute(
                f"DELETE FROM {schema}.semantic_facts "
                "WHERE subject = %s AND project = %s;",
                (subject, project),
            )
        if vec_lit is None:
            sql = (
                f"INSERT INTO {schema}.semantic_facts "
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
                f"INSERT INTO {schema}.semantic_facts "
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
        description="One-way NavGator lessons → Postgres semantic_facts sync"
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
        help="Do not open a DB connection. Reports what would be synced.",
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

    if args.dry_run:
        out = {
            "synced": len(project_lessons),
            "global_synced": len(global_lessons),
            "skipped_templates": skipped_templates,
            "errors": errors,
            "schema_version": SCHEMA_VERSION,
            "dry_run": True,
        }
        print(json.dumps(out, sort_keys=True))
        return 0

    if not project_lessons and not global_lessons:
        out = {
            "synced": 0,
            "global_synced": 0,
            "skipped_templates": skipped_templates,
            "errors": errors,
            "schema_version": SCHEMA_VERSION,
        }
        print(json.dumps(out, sort_keys=True))
        return 0

    # Open Postgres. Soft-fail to graceful exit on any connection error.
    try:
        conn = _open_connection()
    except Exception as exc:  # noqa: BLE001 - psycopg.OperationalError, RuntimeError, etc.
        msg = f"postgres_unavailable: {type(exc).__name__}: {exc}"
        _append_sync_error(workdir, msg)
        out = {
            "synced": 0,
            "global_synced": 0,
            "skipped_templates": skipped_templates,
            "errors": ["postgres_unavailable"],
            "schema_version": SCHEMA_VERSION,
        }
        print(json.dumps(out, sort_keys=True))
        return 0

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
                synced += 1
            except Exception as exc:  # noqa: BLE001 - psycopg + unforeseen
                conn.rollback()
                msg = f"upsert_failed:{lid}: {type(exc).__name__}: {exc}"
                errors.append(f"upsert_failed:{lid}")
                _append_sync_error(workdir, msg)
                # Re-open implicit transaction by issuing a new statement on
                # next iteration; psycopg auto-begins.
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
                global_synced += 1
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
        "skipped_templates": skipped_templates,
        "errors": errors,
        "schema_version": SCHEMA_VERSION,
    }
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
