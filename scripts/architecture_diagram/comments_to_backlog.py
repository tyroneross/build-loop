#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Ingest exported architecture-diagram comments into the build-loop backlog as OPEN items.

The diagram's ⬇ Backup button produces a JSON:
    { "<elementId>": { "title": "...", "comments": [ {"text": "...", "t": "..."} ] }, ... }

This turns each commented element into one backlog item (status: open) via scripts/backlog.py,
tagged provenance `architecture-diagram`, so feedback on the diagram becomes assessable open
items for the backlog / improvements pipeline.

Usage:
    python3 scripts/architecture_diagram/comments_to_backlog.py <backup.json> [--repo build-loop]
            [--type {feature,fix,debt,infra,decision,cleanup,research}] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BACKLOG = REPO / "scripts" / "backlog.py"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backup", help="path to the diagram's exported comments .json (⬇ Backup)")
    ap.add_argument("--repo", default="build-loop")
    ap.add_argument("--area", default="architecture-diagram")
    ap.add_argument("--type", default="decision",
                    choices=["feature", "fix", "debt", "infra", "decision", "cleanup", "research"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = json.loads(Path(args.backup).read_text(encoding="utf-8"))
    today = datetime.date.today().isoformat()
    created = 0
    for eid, rec in data.items():
        comments = (rec or {}).get("comments") or []
        if not comments:
            continue
        title = ("Diagram feedback: " + (rec.get("title") or eid))[:80]
        body = "\n".join(f"- {c['text']} ({c.get('t', '')})" for c in comments)
        cmd = ["python3", str(BACKLOG), "new", "--repo", args.repo, "--area", args.area,
               "--type", args.type, "--title", title, "--priority", "P2", "--status", "open",
               "--provenance-source", "architecture-diagram", "--provenance-ref", eid,
               "--context", body, "--today", today]
        if args.dry_run:
            print(f"DRY  {title}  ({len(comments)} comment(s))")
            created += 1
            continue
        r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
        if r.returncode == 0:
            created += 1
            print(f"created  {title}")
        else:
            print(f"FAILED   {title}: {r.stderr[:200]}", file=sys.stderr)

    if not args.dry_run and created:
        subprocess.run(["python3", str(BACKLOG), "sync", "--repo", args.repo], cwd=str(REPO))
    print(f"done: {created} backlog item(s) from {args.backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
