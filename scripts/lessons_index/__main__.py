#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""CLI for the lessons_index package.

Usage:
  python3 scripts/lessons_index ingest [--project X] [--db PATH]
  python3 scripts/lessons_index query --goal "<text>" [--project X] [--limit N] [--json]
  python3 scripts/lessons_index stats [--db PATH]

Default DB: $BUILD_LOOP_MEMORY_STORE_ROOT/indexes/lessons_index.db
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = HERE.parent
for _p in (str(HERE), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lessons_index as li  # noqa: E402  (intra-package via __init__)


def _cmd_ingest(args: argparse.Namespace) -> int:
    project = args.project or None
    db_path = args.db or None
    result = li.ingest(project=project, db_path=db_path)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(
            f"ingest complete: upserted={result['upserted']} "
            f"skipped={result['skipped']} errors={len(result['errors'])} "
            f"scanned={result['total_scanned']}"
        )
        if result["errors"]:
            for e in result["errors"]:
                print(f"  ERROR: {e['file']}: {e['error']}", file=sys.stderr)
    return 1 if result["errors"] else 0


def _cmd_query(args: argparse.Namespace) -> int:
    results = li.query(
        args.goal,
        project=args.project or None,
        limit=args.limit,
        db_path=args.db or None,
    )
    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        if not results:
            print("No results.")
            return 0
        for i, r in enumerate(results, 1):
            print(f"\n[{i}] {r['name']}")
            if r["description"]:
                print(f"    {r['description']}")
            if r["snippet"]:
                print(f"    {r['snippet']}")
            print(f"    lane={r['lane']}  score={r['score']:.4f}")
            print(f"    {r['source_path']}")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    s = li.stats(db_path=args.db or None)
    if args.json:
        json.dump(s, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"total_facts:    {s['total_facts']}")
        print(f"schema_version: {s['schema_version']}")
        print(f"last_ingest_ts: {s['last_ingest_ts']}")
    return 0


def _db_arg(parser: argparse.ArgumentParser) -> None:
    """Add --db and --json to a subcommand parser."""
    parser.add_argument(
        "--db", default=None, metavar="PATH",
        help="Override DB path (default: $MEMORY_STORE_ROOT/indexes/lessons_index.db)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m lessons_index",
        description="Portable SQLite/FTS5 lessons index for build-loop.",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    # ingest
    ing = sub.add_parser("ingest", help="Walk memory lanes and upsert into DB")
    ing.add_argument("--project", default=None,
                     help="Project tag (default: top-level lanes)")
    _db_arg(ing)

    # query
    qry = sub.add_parser("query", help="Query the index by goal text")
    qry.add_argument("--goal", required=True, help="Free-text retrieval goal")
    qry.add_argument("--project", default=None, help="Scope to project (+ _unscoped)")
    qry.add_argument("--limit", type=int, default=5, help="Max results (default 5)")
    _db_arg(qry)

    # stats
    sta = sub.add_parser("stats", help="Print index statistics")
    _db_arg(sta)

    return p


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # Propagate --db and --json to subcommand args (they're on the parent).
    dispatch = {
        "ingest": _cmd_ingest,
        "query": _cmd_query,
        "stats": _cmd_stats,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
