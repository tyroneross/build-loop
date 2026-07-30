#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""knowledge_review — surface review-needing items across episodic memory.

Backs the `/knowledge:review` slash command. Read-only — never auto-resolves.

Output: structured markdown to stdout with four sections:
  1. Review queue        — `_review/` decisions awaiting promotion
  2. Decision rot        — decisions older than --rot-threshold-days
  3. Open conflicts      — `fact_conflicts` rows where resolved=FALSE
  4. Stale procedures    — depends_on symbols missing from codebase

Each item carries a suggested action (promote / supersede / revoke /
mark-validated / dismiss). User takes the action via existing scripts.

Exit codes:
  0 success
  1 validation error
  2 filesystem error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _has_db_config() -> bool:
    if "DATABASE_URL" in os.environ:
        return True
    return (Path.home() / ".config" / "agent-memory" / "connection.env").exists()


# ---------- review queue ----------


def review_queue_items(workdir: Path) -> list[dict]:
    review_dir = workdir / ".episodic" / "decisions" / "_review"
    if not review_dir.exists():
        return []
    out: list[dict] = []
    from write_decision import parse_frontmatter  # type: ignore

    for f in sorted(review_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        fm = parse_frontmatter(text) or {}
        out.append({
            "id": fm.get("id"),
            "title": fm.get("title"),
            "confidence": fm.get("confidence"),
            "primary_tag": fm.get("primary_tag"),
            "entity": fm.get("entity"),
            "date": fm.get("date"),
            "path": str(f.relative_to(workdir)),
        })
    return out


# ---------- decision rot ----------


def rot_items(workdir: Path, threshold_days: int) -> list[dict]:
    """Delegate to detect_decision_rot.py via subprocess for parity."""
    rot_script = HERE / "detect_decision_rot.py"
    proc = subprocess.run(
        [sys.executable, str(rot_script), "--workdir", str(workdir),
         "--threshold-days", str(threshold_days)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []


# ---------- open conflicts ----------


def conflict_items(schema: str) -> list[dict]:
    from db import query  # type: ignore  # noqa: PLC0415

    if not re.match(r"^[a-z][a-z0-9_]*$", schema):
        raise ValueError(f"unsafe schema name: {schema!r}")
    sql = (
        f"SELECT c.id::text AS id, c.fact_id_a::text AS fact_id_a, "
        f"       c.fact_id_b::text AS fact_id_b, c.conflict_type, c.detected_at, "
        f"       a.subject AS a_subject, a.predicate AS a_predicate, a.object AS a_object, "
        f"       b.subject AS b_subject, b.predicate AS b_predicate, b.object AS b_object "
        f"FROM {schema}.fact_conflicts c "
        f"LEFT JOIN {schema}.semantic_facts a ON c.fact_id_a = a.id "
        f"LEFT JOIN {schema}.semantic_facts b ON c.fact_id_b = b.id "
        f"WHERE c.resolved = FALSE "
        f"ORDER BY c.detected_at DESC"
    )
    return query(sql)


# ---------- stale procedures ----------


def stale_procedure_items(workdir: Path, paths: list[str]) -> list[dict]:
    """Run procedural_governance.py --mode validate-symbols and filter stale."""
    gov_script = HERE / "procedural_governance.py"
    proc = subprocess.run(
        [sys.executable, str(gov_script), "--workdir", str(workdir),
         "--mode", "validate-symbols", "--paths", ",".join(paths)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return []
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return [r for r in rows if r.get("stale")]


# ---------- render ----------


def render(
    review: list[dict],
    rot: list[dict],
    conflicts: list[dict] | None,
    stale_procs: list[dict],
    rot_threshold_days: int,
    db_consulted: bool,
) -> str:
    lines: list[str] = []
    lines.append("# Knowledge Review")
    lines.append("")
    lines.append(
        "_Read-only surface for repo-local episodic memory. "
        "Each item lists a suggested action; take it via the named script._"
    )
    lines.append("")

    # Section 1: Review queue
    lines.append("## Review queue")
    lines.append("")
    lines.append("_Tier-3 / inferred captures awaiting promotion or dismissal._")
    lines.append("")
    if not review:
        lines.append("_(empty)_")
    else:
        lines.append("| id | confidence | primary_tag | title | path | suggested action |")
        lines.append("|---|---|---|---|---|---|")
        for r in review:
            lines.append(
                f"| {r.get('id','')} | {r.get('confidence','')} | "
                f"{r.get('primary_tag','')} | {r.get('title','')} | "
                f"`{r.get('path','')}` | "
                f"**promote** (mv to decisions/) or **dismiss** (rm) |"
            )
    lines.append("")

    # Section 2: Decision rot
    lines.append(f"## Decision rot")
    lines.append("")
    lines.append(
        f"_Decisions whose `last_validated` (or `date` if absent) is "
        f"older than {rot_threshold_days} days._"
    )
    lines.append("")
    if not rot:
        lines.append("_(none stale)_")
    else:
        lines.append("| id | age_days | primary_tag | entity | title | suggested action |")
        lines.append("|---|---|---|---|---|---|")
        for r in rot:
            lines.append(
                f"| {r.get('id','')} | {r.get('age_days','')} | "
                f"{r.get('primary_tag','')} | {r.get('entity','')} | "
                f"{r.get('title','')} | "
                f"**mark-validated** (set `last_validated`), **supersede**, or **revoke** |"
            )
    lines.append("")

    # Section 3: Open conflicts
    lines.append("## Open conflicts")
    lines.append("")
    if conflicts is None:
        lines.append("_DB not consulted (--no-db or DB unavailable)._")
    elif not conflicts:
        lines.append("_(none open)_")
    else:
        lines.append("_`fact_conflicts` rows where `resolved=FALSE`._")
        lines.append("")
        lines.append("| id | conflict_type | A: object | B: object | suggested action |")
        lines.append("|---|---|---|---|---|")
        for c in conflicts:
            lines.append(
                f"| {c.get('id','')[:8]}… | {c.get('conflict_type','')} | "
                f"{(c.get('a_object') or '')[:40]} | "
                f"{(c.get('b_object') or '')[:40]} | "
                f"**resolve** (UPDATE one fact's status to `superseded`; set `fact_conflicts.resolved=TRUE`) |"
            )
    lines.append("")

    # Section 4: Stale procedures
    lines.append("## Stale procedures")
    lines.append("")
    lines.append(
        "_Procedures whose `depends_on` symbols are no longer present in the codebase._"
    )
    lines.append("")
    if not stale_procs:
        lines.append("_(none stale)_")
    else:
        lines.append("| name | path | missing_symbols | suggested action |")
        lines.append("|---|---|---|---|")
        for p in stale_procs:
            missing = ", ".join(p.get("missing_symbols") or [])
            lines.append(
                f"| {p.get('name','')} | `{p.get('path','')}` | "
                f"{missing} | "
                f"**re-verify** (update `depends_on.last_verified`) or **revoke** (move to _archive/) |"
            )
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- main ----------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Knowledge review report")
    p.add_argument("--workdir", default=".")
    p.add_argument("--rot-threshold-days", type=int, default=90)
    p.add_argument(
        "--schema",
        default=None,
        help="Postgres schema. Default: $AGENT_MEMORY_SCHEMA or 'personal_memory'.",
    )
    p.add_argument("--symbol-paths", default="scripts,src,app",
                   help="Comma-separated paths to grep for procedure symbols")
    p.add_argument("--no-db", action="store_true",
                   help="Skip the Open conflicts section (no DB query)")
    args = p.parse_args(argv)
    if args.schema is None:
        HERE_LOCAL = Path(__file__).resolve().parent
        import sys as _sys
        if str(HERE_LOCAL) not in _sys.path:
            _sys.path.insert(0, str(HERE_LOCAL))
        from _paths import default_schema as _ds  # noqa: PLC0415
        args.schema = _ds()

    workdir = Path(args.workdir).resolve()

    review = review_queue_items(workdir)
    rot = rot_items(workdir, args.rot_threshold_days)

    paths = [s.strip() for s in args.symbol_paths.split(",") if s.strip()]
    stale = stale_procedure_items(workdir, paths)

    conflicts: list[dict] | None
    if args.no_db or not _has_db_config():
        conflicts = None
    else:
        try:
            conflicts = conflict_items(args.schema)
        except Exception as e:  # noqa: BLE001
            print(f"[knowledge_review] DB query failed: {e}", file=sys.stderr)
            conflicts = None

    out = render(review, rot, conflicts, stale, args.rot_threshold_days,
                 db_consulted=conflicts is not None)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
