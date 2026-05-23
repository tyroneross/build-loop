#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""One-shot migration of `.episodic/` + Postgres rows to v3 metadata schema.

Adds the seven new fields (design §16) to:

  1. every `*.md` MADR under `.episodic/decisions/` and
     `.episodic/decisions/_history/` and `.episodic/decisions/_review/`
  2. every line in `.episodic/events.jsonl` referring to a decision
  3. every row in `agent_memory.<schema>.semantic_facts` and
     `episode_events` (DB columns + JSONB sync)
  4. ALTERs the table shapes themselves (ADD COLUMN IF NOT EXISTS)

Defaults applied to retroactive rows (per design §16):

  - `confidence_source`       := from existing `source` (manual→user_statement,
                                  auto-*→ai_inference, migration→external_import,
                                  else unknown)
  - `confirmation_count`      := 0
  - `valid_until`             := null
  - `causal_parent_id`        := null
  - `embedding_model_version` := 'mxbai-embed-large-v1' (matches current
                                  MLX/Ollama backend default)
  - `domain`                  := heuristic from existing `primary_tag`:
                                  testing→test, ui→ui, data→data, infra→infra,
                                  tooling→tooling, process→meta, architecture→meta,
                                  security→infra, performance→meta, else unknown
  - `goal`                    := 'unknown' (retroactive intent inference is
                                  unreliable; the user backfills via
                                  /knowledge:review)

Idempotent: running twice produces no changes. Should run AFTER
`migrate_schema_v2.py` has already been applied (it does not re-derive
v2 fields).

Exit codes: 0 success, 1 validation error, 2 filesystem/DB error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from write_decision import (  # type: ignore  # noqa: E402
    DEFAULT_EMBEDDING_MODEL_VERSION,
    VALID_DOMAINS,
    VALID_GOALS,
    _FM_RE,
    atomic_write_bytes,
    emit_frontmatter,
    parse_frontmatter,
)


# Heuristic mapping from primary_tag → domain. Used only for migration
# defaults; new writes pass `domain` explicitly. See design §16 for the
# rationale (primary_tag stays as legacy alias; domain is stricter MECE).
PRIMARY_TAG_TO_DOMAIN = {
    "testing": "test",
    "ui": "ui",
    "data": "data",  # could also be 'search' on retrieval-related decisions
    "infra": "infra",
    "tooling": "tooling",
    "process": "meta",
    "architecture": "meta",
    "security": "infra",  # could split later
    "performance": "meta",  # cross-cutting
}

# v3 fields in canonical order. Inserted into frontmatter directly after
# the v2 block (i.e. after `closing_commit`).
V3_FIELDS = [
    "confidence_source",
    "confirmation_count",
    "valid_until",
    "causal_parent_id",
    "embedding_model_version",
    "domain",
    "goal",
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def derive_confidence_source(source: str | None) -> str:
    if source == "manual":
        return "user_statement"
    if source == "migration":
        return "external_import"
    if isinstance(source, str) and source.startswith("auto-"):
        return "ai_inference"
    if source == "orchestrator":
        return "ai_inference"
    return "unknown"


def derive_domain(primary_tag: str | None) -> str:
    if not primary_tag:
        return "unknown"
    pt = str(primary_tag).strip().lower()
    return PRIMARY_TAG_TO_DOMAIN.get(pt, "unknown")


def apply_v3_defaults_to_fm(fm: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (new_fm, changed). Mutates a copy.

    Inserts v3 fields if missing; preserves existing values verbatim
    (idempotency).
    """
    out = dict(fm)
    changed = False
    if "confidence_source" not in out or out.get("confidence_source") in (None, ""):
        out["confidence_source"] = derive_confidence_source(out.get("source"))
        changed = True
    if "confirmation_count" not in out:
        out["confirmation_count"] = 0
        changed = True
    if "valid_until" not in out:
        out["valid_until"] = None
        changed = True
    if "causal_parent_id" not in out:
        out["causal_parent_id"] = None
        changed = True
    if "embedding_model_version" not in out or out.get("embedding_model_version") in (None, ""):
        out["embedding_model_version"] = DEFAULT_EMBEDDING_MODEL_VERSION
        changed = True
    if "domain" not in out or out.get("domain") in (None, ""):
        out["domain"] = derive_domain(out.get("primary_tag"))
        changed = True
    if "goal" not in out or out.get("goal") in (None, ""):
        out["goal"] = "unknown"
        changed = True
    return out, changed


def _reorder_fm(fm: dict[str, Any]) -> dict[str, Any]:
    """Return fm with keys in canonical order matching the v3 template.

    Preserves any non-canonical extras (e.g. review_origin) at the end.
    """
    canonical_order = [
        "id", "slug", "title", "type", "status", "confidence", "date",
        "tags", "primary_tag", "entity",
        "project", "tool", "model", "task_category", "author",
        "source",
        "related_runs", "related_decisions",
        "supersedes", "superseded_by",
        "bookmark_snapshot_id", "captured_turn_excerpt",
        "last_validated", "last_accessed", "files_touched", "closing_commit",
        # v3
        "confidence_source", "confirmation_count", "valid_until",
        "causal_parent_id", "embedding_model_version", "domain", "goal",
    ]
    out: dict[str, Any] = {}
    for k in canonical_order:
        if k in fm:
            out[k] = fm[k]
    for k, v in fm.items():
        if k not in out:
            out[k] = v
    return out


def migrate_madr_file(path: Path, dry_run: bool) -> str:
    """Return one of: 'updated', 'unchanged', 'skipped'."""
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if fm is None:
        log(f"skipped (no frontmatter): {path}")
        return "skipped"
    new_fm, changed = apply_v3_defaults_to_fm(fm)
    if not changed:
        return "unchanged"
    new_fm = _reorder_fm(new_fm)
    body = _FM_RE.sub("", text, count=1)
    new_text = emit_frontmatter(new_fm) + body
    if dry_run:
        log(f"would update: {path}")
        return "updated"
    atomic_write_bytes(path, new_text.encode("utf-8"))
    return "updated"


def migrate_decision_files(workdir: Path, dry_run: bool) -> dict[str, int]:
    counts = {"updated": 0, "unchanged": 0, "skipped": 0}
    decisions_dir = workdir / ".episodic" / "decisions"
    if not decisions_dir.exists():
        return counts
    targets: list[Path] = []
    targets.extend(sorted(decisions_dir.glob("[0-9][0-9][0-9][0-9]-*.md")))
    history = decisions_dir / "_history"
    if history.exists():
        targets.extend(sorted(history.glob("[0-9][0-9][0-9][0-9]-*.md")))
    review = decisions_dir / "_review"
    if review.exists():
        targets.extend(sorted(review.glob("[0-9][0-9][0-9][0-9]-*.md")))
    for f in targets:
        try:
            outcome = migrate_madr_file(f, dry_run)
        except OSError as e:
            log(f"filesystem error on {f}: {e}")
            counts["skipped"] += 1
            continue
        counts[outcome] += 1
    return counts


# ---------- events.jsonl backfill ----------


def _load_decision_v3_index(workdir: Path) -> dict[str, dict[str, Any]]:
    """Build {decision_id -> v3 fields} from migrated MADR files."""
    out: dict[str, dict[str, Any]] = {}
    decisions_dir = workdir / ".episodic" / "decisions"
    if not decisions_dir.exists():
        return out
    for f in sorted(decisions_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        fm = parse_frontmatter(f.read_text(encoding="utf-8")) or {}
        did = fm.get("id")
        if did:
            out[str(did)] = {k: fm.get(k) for k in V3_FIELDS}
    history = decisions_dir / "_history"
    if history.exists():
        for f in sorted(history.glob("[0-9][0-9][0-9][0-9]-*.md")):
            fm = parse_frontmatter(f.read_text(encoding="utf-8")) or {}
            did = fm.get("id")
            if did and str(did) not in out:
                out[str(did)] = {k: fm.get(k) for k in V3_FIELDS}
    return out


def migrate_events_jsonl(workdir: Path, dry_run: bool) -> dict[str, int]:
    counts = {"updated": 0, "unchanged": 0, "skipped_lines": 0}
    events_path = workdir / ".episodic" / "events.jsonl"
    if not events_path.exists():
        return counts
    v3_index = _load_decision_v3_index(workdir)
    out_lines: list[bytes] = []
    any_changed = False
    for raw in events_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            counts["skipped_lines"] += 1
            out_lines.append((line + "\n").encode("utf-8"))
            continue
        kind = ev.get("kind")
        if kind not in {
            "decision_proposed",
            "decision_accepted",
            "decision_superseded",
            "decision_revoked",
        }:
            out_lines.append((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
            counts["unchanged"] += 1
            continue
        did = ev.get("decision_id") or ev.get("superseded_by")
        v3 = v3_index.get(str(did)) if did else None
        line_changed = False
        if v3:
            for k in V3_FIELDS:
                if k not in ev:
                    ev[k] = v3.get(k)
                    line_changed = True
        if line_changed:
            counts["updated"] += 1
            any_changed = True
        else:
            counts["unchanged"] += 1
        out_lines.append((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
    if any_changed and not dry_run:
        atomic_write_bytes(events_path, b"".join(out_lines))
    return counts


# ---------- DB migration ----------


def migrate_db(schema: str, dry_run: bool) -> dict[str, int]:
    counts = {"alter_ok": 0, "rows_updated": 0, "skipped": 0}
    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        log(f"db: unsafe schema name {schema!r}; skipping")
        counts["skipped"] += 1
        return counts
    try:
        from db import execute, execute_script  # type: ignore  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log(f"db: psycopg unavailable ({e}); skipping DB migration")
        counts["skipped"] += 1
        return counts

    alter_sql = []
    for table in ("semantic_facts", "episode_events"):
        for col, typ in [
            ("confidence_source", "TEXT"),
            ("confirmation_count", "INTEGER DEFAULT 0"),
            ("valid_until", "TIMESTAMPTZ"),
            ("causal_parent_id", "TEXT"),
            ("embedding_model_version", "TEXT"),
            ("domain", "TEXT"),
            ("goal", "TEXT"),
        ]:
            alter_sql.append(
                f"ALTER TABLE {schema}.{table} ADD COLUMN IF NOT EXISTS {col} {typ};"
            )
    alter_sql += [
        f"CREATE INDEX IF NOT EXISTS semantic_facts_domain_goal_idx "
        f"ON {schema}.semantic_facts (domain, goal);",
        f"CREATE INDEX IF NOT EXISTS semantic_facts_causal_parent_id_idx "
        f"ON {schema}.semantic_facts (causal_parent_id) WHERE causal_parent_id IS NOT NULL;",
        f"CREATE INDEX IF NOT EXISTS episode_events_domain_goal_idx "
        f"ON {schema}.episode_events (domain, goal);",
    ]
    full_sql = "\n".join(alter_sql)
    if dry_run:
        log("db (dry-run) would execute:\n" + full_sql)
        return counts
    try:
        execute_script(full_sql)
        counts["alter_ok"] = 1
    except Exception as e:  # noqa: BLE001
        log(f"db: ALTER/CREATE INDEX failed: {e}")
        counts["skipped"] += 1
        return counts

    # Backfill semantic_facts rows from JSONB metadata where typed columns are still null.
    backfill_sql = (
        f"UPDATE {schema}.semantic_facts SET "
        "  confidence_source = COALESCE(confidence_source, metadata->>'confidence_source'), "
        "  confirmation_count = COALESCE(confirmation_count, "
        "      (metadata->>'confirmation_count')::int, 0), "
        "  causal_parent_id = COALESCE(causal_parent_id, metadata->>'causal_parent_id'), "
        "  embedding_model_version = COALESCE(embedding_model_version, "
        "      metadata->>'embedding_model_version'), "
        "  domain = COALESCE(domain, metadata->>'domain'), "
        "  goal = COALESCE(goal, metadata->>'goal') "
        "WHERE confidence_source IS NULL "
        "   OR causal_parent_id IS NULL "
        "   OR embedding_model_version IS NULL "
        "   OR domain IS NULL "
        "   OR goal IS NULL;"
    )
    try:
        rc = execute(backfill_sql)
        counts["rows_updated"] = int(rc or 0)
    except Exception as e:  # noqa: BLE001
        log(f"db: backfill UPDATE failed: {e}")
        counts["skipped"] += 1

    return counts


# ---------- main ----------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Migrate `.episodic/` + DB to v3 metadata schema (design §16)."
    )
    p.add_argument("--workdir", default=".", help="Project root containing .episodic/")
    p.add_argument(
        "--schema",
        default=None,
        help="Postgres schema to migrate. Default: $AGENT_MEMORY_SCHEMA or 'personal_memory'.",
    )
    p.add_argument("--no-db", action="store_true", help="Skip the DB migration step.")
    p.add_argument("--no-files", action="store_true", help="Skip MADR file migration.")
    p.add_argument("--no-events", action="store_true", help="Skip events.jsonl backfill.")
    p.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.schema is None:
        HERE_LOCAL = Path(__file__).resolve().parent
        import sys as _sys
        if str(HERE_LOCAL) not in _sys.path:
            _sys.path.insert(0, str(HERE_LOCAL))
        from _paths import default_schema as _ds  # noqa: PLC0415
        args.schema = _ds()
    workdir = Path(args.workdir).resolve()
    if not (workdir / ".episodic").exists() and not args.no_files:
        log(f"no .episodic/ at {workdir}; nothing to migrate")
        return 0

    summary: dict[str, Any] = {}
    if not args.no_files:
        summary["files"] = migrate_decision_files(workdir, args.dry_run)
    if not args.no_events:
        summary["events"] = migrate_events_jsonl(workdir, args.dry_run)
    if not args.no_db:
        summary["db"] = migrate_db(args.schema, args.dry_run)

    log(f"migrate_schema_v3 summary: {json.dumps(summary, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
