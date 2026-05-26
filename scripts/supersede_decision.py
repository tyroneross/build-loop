#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Explicit supersession CLI for canonical build-loop memory.

Wraps `write_decision.py --supersedes <old-id>` so the existing
supersession path (file move to `_history/`, frontmatter update,
`decision_superseded` event, INDEX regen) is reused without duplication.

Verifies the `--old-id` exists in
`build-loop-memory/projects/<project>/decisions/` before delegating, and
surfaces a clear error otherwise.

Usage:
  supersede_decision.py \\
    --old-id 0042 \\
    --new-decision "Switch to ..." \\
    --new-title "..." \\
    --tags "tooling,testing" \\
    --primary-tag testing \\
    --entity build-loop \\
    --confidence explicit \\
    --rationale "Why we changed our mind"

Exit codes: 0 success | 1 validation error | 2 filesystem/delegate error.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WRITE_DECISION = HERE / "write_decision.py"
sys.path.insert(0, str(HERE))

from _paths import project_decisions_dir  # type: ignore  # noqa: E402
from project_resolver import resolve_project  # type: ignore  # noqa: E402
from write_decision import parse_frontmatter  # type: ignore  # noqa: E402


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def find_decision_file(decisions_dir: Path, old_id: str) -> Path | None:
    if not decisions_dir.exists():
        return None
    for f in decisions_dir.glob(f"{old_id}-*.md"):
        if f.is_file():
            return f
    for f in decisions_dir.glob("*.md"):
        if f.name == "INDEX.md" or not f.is_file():
            continue
        try:
            fm = parse_frontmatter(f.read_text(encoding="utf-8")) or {}
        except OSError:
            continue
        raw_id = str(fm.get("id") or fm.get("legacy_id") or "")
        if raw_id == old_id:
            return f
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Explicit supersession of a prior decision; delegates to write_decision.py."
    )
    p.add_argument("--workdir", default=".")
    p.add_argument(
        "--project",
        default=None,
        help="Project tag. Default: resolve from --workdir via project_resolver.",
    )
    p.add_argument("--old-id", required=True, help="4-digit decision ID to supersede")
    p.add_argument("--new-decision", required=True, help="One-sentence decision body")
    p.add_argument("--new-title", default=None, help="Title for the new decision (defaults to --new-decision)")
    p.add_argument("--tags", required=True, help="Comma-separated tag list")
    p.add_argument("--primary-tag", required=True)
    p.add_argument("--entity", required=True, help="Must match the prior decision's entity")
    p.add_argument(
        "--confidence",
        required=True,
        choices=["assumed", "inferred", "confirmed", "explicit"],
    )
    p.add_argument("--status", default="accepted", choices=["proposed", "accepted", "superseded", "rejected"])
    p.add_argument("--source", default="manual")
    p.add_argument("--rationale", default="", help="Why we changed our mind (becomes consequences)")
    p.add_argument("--context", default="")
    p.add_argument("--alternatives", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--db", dest="db", action="store_true", default=True)
    p.add_argument("--no-db", dest="db", action="store_false")
    p.add_argument(
        "--schema",
        default=None,
        help="Postgres schema. Default: $AGENT_MEMORY_SCHEMA or 'personal_memory'.",
    )
    p.add_argument("--embed-model", default="nomic-embed-text")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0

    if args.schema is None:
        HERE_LOCAL = Path(__file__).resolve().parent
        if str(HERE_LOCAL) not in sys.path:
            sys.path.insert(0, str(HERE_LOCAL))
        from _paths import default_schema as _ds  # noqa: PLC0415
        args.schema = _ds()

    workdir = Path(args.workdir).resolve()
    if not re.match(r"^\d{4}$", args.old_id):
        log(f"validation error: --old-id must be 4 digits, got {args.old_id!r}")
        return 1

    project_tag = args.project or resolve_project(workdir)
    decisions_dir = project_decisions_dir(project_tag)
    prior = find_decision_file(decisions_dir, args.old_id)
    if prior is None:
        log(
            f"validation error: no decision matching id={args.old_id} found in "
            f"{decisions_dir}"
        )
        return 1

    # Delegate to write_decision.py with --supersedes — its supersession path
    # is the single canonical implementation (avoids drift).
    title = args.new_title or args.new_decision[:120]
    delegate = [
        sys.executable, str(WRITE_DECISION),
        "--workdir", str(workdir),
        "--project", project_tag,
        "--title", title,
        "--decision", args.new_decision,
        "--context", args.context,
        "--alternatives", args.alternatives,
        "--consequences", args.rationale,
        "--notes", args.notes,
        "--tags", args.tags,
        "--primary-tag", args.primary_tag,
        "--entity", args.entity,
        "--confidence", args.confidence,
        "--status", args.status,
        "--source", args.source,
        "--supersedes", args.old_id,
        "--schema", args.schema,
        "--embed-model", args.embed_model,
    ]
    if not args.db:
        delegate.append("--no-db")

    cp = subprocess.run(delegate, capture_output=True, text=True)
    # Stream child stderr through so the user sees validation messages.
    if cp.stderr:
        sys.stderr.write(cp.stderr)
    if cp.returncode != 0:
        return 2 if cp.returncode == 2 else 1

    # write_decision.py prints the new id to stdout — relay it.
    new_id = cp.stdout.strip()
    if new_id:
        print(new_id)
        log(f"superseded {args.old_id} → {new_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
