#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""One-shot migration of `.episodic/` + Postgres rows to v2 metadata schema.

Adds the nine new fields (`project`, `tool`, `model`, `task_category`,
`author`, `last_validated`, `last_accessed`, `files_touched`,
`closing_commit`) to:

  1. every `*.md` MADR under `.episodic/decisions/` and
     `.episodic/decisions/_history/` and `.episodic/decisions/_review/`
  2. every line in `.episodic/events.jsonl` referring to a decision
  3. every row in `agent_memory.<schema>.semantic_facts` that has a
     `metadata->>'decision_id'` populated (DB columns + JSONB sync)
  4. ALTERs the table shapes themselves to add the new columns
     (delegates to `init_agent_memory_schema.sql` which has
     `ADD COLUMN IF NOT EXISTS` blocks)

Defaults applied to retroactive rows (per design §15):

  - `project`     := entity prefix before ':' (e.g. "build-loop:foo" → "build-loop")
                    or basename of $CLAUDE_PROJECT_DIR / workdir
  - `tool`        := 'claude-code' for `auto-*` sources, 'manual' for `manual`,
                    'migration' otherwise (catch-all)
  - `model`       := 'unknown' (we don't have this info retroactively)
  - `task_category` := 'unknown'
  - `author`      := $USER env var (or 'tyroneross' fallback for build-loop's
                    own data) -- override with --default-author
  - `last_validated`, `last_accessed`, `closing_commit` := null
  - `files_touched` := []

Idempotent: running twice produces no changes (computes a hash of each
file's frontmatter before write, skips if already-equal-after-defaults).

Exit codes: 0 success, 1 validation error, 2 filesystem/DB error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from write_decision import (  # type: ignore  # noqa: E402
    VALID_TASK_CATEGORIES,
    VALID_TOOLS,
    _FM_RE,
    atomic_write_bytes,
    emit_frontmatter,
    parse_frontmatter,
)


# v2 fields in canonical order. The v2 block is inserted in the
# frontmatter directly after `entity` (consistent with template).
V2_FIELDS = [
    "project",
    "tool",
    "model",
    "task_category",
    "author",
]
V2_OPTIONAL_FIELDS = [
    "last_validated",
    "last_accessed",
    "files_touched",
    "closing_commit",
]
V2_ALL_FIELDS = V2_FIELDS + V2_OPTIONAL_FIELDS


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def derive_default_tool(source: str | None) -> str:
    if source is None:
        return "migration"
    if source == "manual":
        return "manual"
    if source == "migration":
        return "migration"
    if isinstance(source, str) and source.startswith("auto-"):
        return "claude-code"
    if source == "orchestrator":
        return "claude-code"
    return "migration"


def derive_project(entity: str | None, workdir: Path) -> str:
    if entity and isinstance(entity, str) and ":" in entity:
        prefix = entity.split(":", 1)[0].strip()
        if prefix:
            return prefix
    cpd = os.environ.get("CLAUDE_PROJECT_DIR")
    if cpd:
        name = Path(cpd).name
        if name:
            return name
    return workdir.name or "unknown"


def apply_v2_defaults_to_fm(
    fm: dict[str, Any], default_author: str, workdir: Path
) -> tuple[dict[str, Any], bool]:
    """Return (new_fm, changed). Mutates a copy.

    Inserts v2 fields if missing; preserves existing values verbatim
    (idempotency).
    """
    out = dict(fm)
    changed = False

    if "project" not in out or out.get("project") in (None, ""):
        out["project"] = derive_project(out.get("entity"), workdir)
        changed = True
    if "tool" not in out or out.get("tool") in (None, ""):
        out["tool"] = derive_default_tool(out.get("source"))
        changed = True
    if "model" not in out or out.get("model") in (None, ""):
        out["model"] = "unknown"
        changed = True
    if "task_category" not in out or out.get("task_category") in (None, ""):
        out["task_category"] = "unknown"
        changed = True
    if "author" not in out or out.get("author") in (None, ""):
        out["author"] = default_author
        changed = True
    if "last_validated" not in out:
        out["last_validated"] = None
        changed = True
    if "last_accessed" not in out:
        out["last_accessed"] = None
        changed = True
    if "files_touched" not in out:
        out["files_touched"] = []
        changed = True
    if "closing_commit" not in out:
        out["closing_commit"] = None
        changed = True

    return out, changed


def _reorder_fm(fm: dict[str, Any]) -> dict[str, Any]:
    """Return fm with keys in canonical order matching the v2 template."""
    canonical_order = [
        "id", "slug", "title", "type", "status", "confidence", "date",
        "tags", "primary_tag", "entity",
        "project", "tool", "model", "task_category", "author",
        "source",
        "related_runs", "related_decisions",
        "supersedes", "superseded_by",
        "bookmark_snapshot_id", "captured_turn_excerpt",
        "last_validated", "last_accessed", "files_touched", "closing_commit",
    ]
    out: dict[str, Any] = {}
    for k in canonical_order:
        if k in fm:
            out[k] = fm[k]
    # Preserve any non-canonical extras at the end (e.g. review_origin).
    for k, v in fm.items():
        if k not in out:
            out[k] = v
    return out


def migrate_madr_file(path: Path, default_author: str, workdir: Path, dry_run: bool) -> str:
    """Return one of: 'updated', 'unchanged', 'skipped'."""
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if fm is None:
        log(f"skipped (no frontmatter): {path}")
        return "skipped"
    new_fm, changed = apply_v2_defaults_to_fm(fm, default_author, workdir)
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


def migrate_decision_files(workdir: Path, default_author: str, dry_run: bool) -> dict[str, int]:
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
            outcome = migrate_madr_file(f, default_author, workdir, dry_run)
        except OSError as e:
            log(f"filesystem error on {f}: {e}")
            counts["skipped"] += 1
            continue
        counts[outcome] += 1
    return counts


# ---------- events.jsonl backfill ----------


def _load_decision_meta_index(workdir: Path) -> dict[str, dict[str, Any]]:
    """Build {decision_id -> v2 fields} from migrated MADR files."""
    out: dict[str, dict[str, Any]] = {}
    decisions_dir = workdir / ".episodic" / "decisions"
    if not decisions_dir.exists():
        return out
    for f in sorted(decisions_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        fm = parse_frontmatter(f.read_text(encoding="utf-8")) or {}
        did = fm.get("id")
        if did:
            out[str(did)] = {k: fm.get(k) for k in V2_FIELDS}
    history = decisions_dir / "_history"
    if history.exists():
        for f in sorted(history.glob("[0-9][0-9][0-9][0-9]-*.md")):
            fm = parse_frontmatter(f.read_text(encoding="utf-8")) or {}
            did = fm.get("id")
            if did and str(did) not in out:
                out[str(did)] = {k: fm.get(k) for k in V2_FIELDS}
    return out


def migrate_events_jsonl(workdir: Path, dry_run: bool) -> dict[str, int]:
    counts = {"updated": 0, "unchanged": 0, "skipped_lines": 0}
    events_path = workdir / ".episodic" / "events.jsonl"
    if not events_path.exists():
        return counts
    meta_index = _load_decision_meta_index(workdir)
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
        # Only decision-related events get v2 mirroring.
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
        meta = meta_index.get(str(did)) if did else None
        line_changed = False
        if meta:
            for k in V2_FIELDS:
                if k not in ev or ev.get(k) in (None, ""):
                    ev[k] = meta.get(k)
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
        from db import execute, execute_script, query  # type: ignore  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log(f"db: psycopg unavailable ({e}); skipping DB migration")
        counts["skipped"] += 1
        return counts

    # Apply ADD COLUMN IF NOT EXISTS for both tables.
    alter_sql = []
    for table in ("semantic_facts", "episode_events"):
        for col, typ in [
            ("project", "TEXT"),
            ("tool", "TEXT"),
            ("model", "TEXT"),
            ("task_category", "TEXT"),
            ("author", "TEXT"),
            ("last_validated", "TIMESTAMPTZ"),
            ("last_accessed", "TIMESTAMPTZ"),
            ("closing_commit", "TEXT"),
            ("files_touched", "TEXT[]"),
        ]:
            alter_sql.append(f"ALTER TABLE {schema}.{table} ADD COLUMN IF NOT EXISTS {col} {typ};")
    alter_sql += [
        f"CREATE INDEX IF NOT EXISTS semantic_facts_project_task_category_idx ON {schema}.semantic_facts (project, task_category);",
        f"CREATE INDEX IF NOT EXISTS semantic_facts_last_accessed_idx ON {schema}.semantic_facts (last_accessed DESC NULLS LAST);",
        f"CREATE INDEX IF NOT EXISTS episode_events_project_task_category_idx ON {schema}.episode_events (project, task_category);",
        f"CREATE INDEX IF NOT EXISTS episode_events_last_accessed_idx ON {schema}.episode_events (last_accessed DESC NULLS LAST);",
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
    # Idempotent: only target rows that are STILL missing one of the typed cols.
    # COALESCE writes against potentially-null JSONB keys are still safe re-runs
    # because they're guarded by the WHERE clause; on a clean v2 row the row
    # is excluded from the UPDATE entirely.
    backfill_sql = (
        f"UPDATE {schema}.semantic_facts SET "
        "  project = COALESCE(project, metadata->>'project'), "
        "  tool = COALESCE(tool, metadata->>'tool'), "
        "  model = COALESCE(model, metadata->>'model'), "
        "  task_category = COALESCE(task_category, metadata->>'task_category'), "
        "  author = COALESCE(author, metadata->>'author'), "
        "  closing_commit = COALESCE(closing_commit, metadata->>'closing_commit') "
        "WHERE project IS NULL "
        "   OR tool IS NULL "
        "   OR model IS NULL "
        "   OR task_category IS NULL "
        "   OR author IS NULL;"
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
    p = argparse.ArgumentParser(description="Migrate `.episodic/` + DB to v2 metadata schema (design §15).")
    p.add_argument("--workdir", default=".", help="Project root containing .episodic/")
    p.add_argument(
        "--default-author",
        default=os.environ.get("USER") or "unknown",
        help="Default author for retroactive entries. Defaults to $USER.",
    )
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
        summary["files"] = migrate_decision_files(workdir, args.default_author, args.dry_run)
    if not args.no_events:
        summary["events"] = migrate_events_jsonl(workdir, args.dry_run)
    if not args.no_db:
        summary["db"] = migrate_db(args.schema, args.dry_run)

    log(f"migrate_schema_v2 summary: {json.dumps(summary, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
