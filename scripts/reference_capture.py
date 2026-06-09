#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Capture web/doc research findings as dated memory reference files; flag stale ones.
#   application: memory
#   status: active
"""Reference capture CLI — persist research findings as dated reference files.

Thin command-line wrapper over the ``reference_capture`` package. Any build-loop
phase that fetched external information (WebSearch / WebFetch / Context7 /
api-registry) and used it in a decision can persist the EXTRACTED findings as a
date-stamped ``reference-*.md`` in the central memory store, routed through the
canonical memory writer with a per-content-class staleness horizon.

Subcommands:
  capture     — write one reference file from findings + sources + decision.
  scan-stale  — list references in the project lane past their refresh horizon.

Examples:
  python3 scripts/reference_capture.py capture --workdir "$PWD" \\
    --run-id "$RUN_ID" --topic "Anthropic API pricing" \\
    --findings "opus 4.8 is $10/$50 per MTok" \\
    --source "https://docs.anthropic.com/pricing|T1" \\
    --decision "priced the orchestrator tier" --json

  python3 scripts/reference_capture.py scan-stale --workdir "$PWD" --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from reference_capture import capture_reference, scan_reference_lane  # noqa: E402


def _parse_source(raw: str) -> dict[str, str]:
    """Parse a ``url|tier`` or bare ``url`` source string into ``{url, tier}``."""
    if "|" in raw:
        url, tier = raw.split("|", 1)
        return {"url": url.strip(), "tier": tier.strip().upper() or "T?"}
    return {"url": raw.strip(), "tier": "T?"}


def _cmd_capture(args: argparse.Namespace) -> int:
    findings = args.findings
    if findings is None:
        findings = sys.stdin.read()
    sources = [_parse_source(s) for s in (args.source or [])]
    result = capture_reference(
        workdir=Path(args.workdir),
        topic=args.topic,
        findings=findings or "",
        source_urls=sources,
        informed_decision=args.decision or "",
        run_id=args.run_id,
        host=args.host,
        content_class=args.content_class,
        refresh_after_days=args.refresh_after_days,
        retrieved_at=args.retrieved_at,
        project=args.project,
    )
    if args.json:
        json.dump(result, sys.stdout, default=str)
        sys.stdout.write("\n")
    else:
        print(result["path"])
    return 0


def _cmd_scan_stale(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    if args.lane_dir:
        lane = Path(args.lane_dir)
    else:
        from _paths import project_research_dir  # type: ignore  # noqa: PLC0415
        from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415

        project = args.project or resolve_project(workdir)
        lane = project_research_dir(project)
    records = scan_reference_lane(lane)
    stale = [r for r in records if r["stale"]]
    payload = {
        "lane": str(lane),
        "total": len(records),
        "stale_count": len(stale),
        "stale": stale,
        "all": records if args.all else None,
    }
    if args.json:
        json.dump(payload, sys.stdout, default=str)
        sys.stdout.write("\n")
    else:
        if not stale:
            print(f"[REFERENCES OK] {len(records)} reference(s), none stale")
        else:
            print(f"[REFERENCES STALE] {len(stale)}/{len(records)} past refresh horizon:")
            for r in stale:
                overdue = -(r["days_remaining"] or 0)
                print(f"  - {r['file']} ({r['content_class']}, {overdue}d overdue)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="Persist a research finding as a reference file")
    c.add_argument("--workdir", required=True)
    c.add_argument("--run-id", required=True)
    c.add_argument("--topic", required=True)
    c.add_argument("--findings", default=None, help="Extracted findings (stdin if omitted)")
    c.add_argument("--source", action="append", default=[],
                   help="Source as 'url|TIER' or bare 'url'; repeatable")
    c.add_argument("--decision", default="", help="What decision this informed")
    c.add_argument("--content-class", default=None,
                   help="Override the inferred content class")
    c.add_argument("--refresh-after-days", type=int, default=None,
                   help="Override the per-class staleness horizon (days)")
    c.add_argument("--retrieved-at", default=None, help="ISO date (default: today)")
    c.add_argument("--project", default=None)
    c.add_argument("--host", default="claude_code",
                   choices=["claude_code", "codex", "gemini", "other"])
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_capture)

    s = sub.add_parser("scan-stale", help="List references past their refresh horizon")
    s.add_argument("--workdir", required=True)
    s.add_argument("--project", default=None)
    s.add_argument("--lane-dir", default=None, help="Override the reference lane dir (testing)")
    s.add_argument("--all", action="store_true", help="Include all records, not just stale")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_scan_stale)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
