#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""CLI for memory_consolidate: submit / list / prepare / place / consolidate / async.

Hot-path subcommands (P2):
    python3 -m memory_consolidate submit \\
        --content-file /tmp/raw.md --hint "smells like a debug-incident" \\
        --run-id run_x --host claude_code --project demoproj

    python3 -m memory_consolidate list --workdir .
    python3 -m memory_consolidate prepare <candidate-id> --workdir . --json
    python3 -m memory_consolidate place <candidate-id> --decision-file decision.json
    python3 -m memory_consolidate consolidate <candidate-id> --deterministic-only

Async / off-hot-path (P3 — for cron / `consolidate-async` watchers):
    python3 -m memory_consolidate async --workdir . --min-projects 2 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path

from . import classify, intake, place  # noqa: E402


def _cmd_submit(args: argparse.Namespace) -> int:
    content = args.content
    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")
    if not content:
        content = sys.stdin.read()
    c = intake.submit(
        content,
        workdir=args.workdir,
        run_id=args.run_id,
        host=args.host,
        hint=args.hint,
        type_=args.type,
        name=args.name,
        project=args.project,
    )
    if args.json:
        json.dump(c.to_dict(), sys.stdout, sort_keys=True, indent=2)
        sys.stdout.write("\n")
    else:
        print(c.id)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    items = intake.list_pending(workdir=args.workdir)
    if args.json:
        json.dump([c.to_dict() for c in items], sys.stdout, sort_keys=True, indent=2)
        sys.stdout.write("\n")
    else:
        for c in items:
            print(f"{c.id}\t{c.submitted_at}\t{c.hint or ''}")
    return 0


def _cmd_prepare(args: argparse.Namespace) -> int:
    packet = classify.prepare(args.candidate_id, workdir=args.workdir)
    json.dump(packet.to_dict(), sys.stdout, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_place(args: argparse.Namespace) -> int:
    decision = json.loads(Path(args.decision_file).read_text(encoding="utf-8"))
    fm = place.place(
        args.candidate_id, decision, workdir=args.workdir,
        run_id=args.run_id, host=args.host,
    )
    if args.json:
        json.dump(fm, sys.stdout, sort_keys=True, default=str, indent=2)
        sys.stdout.write("\n")
    else:
        print(fm.get("name"))
    return 0


def _cmd_consolidate(args: argparse.Namespace) -> int:
    """End-to-end: prepare → heuristic_decision → place."""
    packet = classify.prepare(args.candidate_id, workdir=args.workdir)
    decision = packet.suggested_decision
    fm = place.place(
        args.candidate_id, decision, workdir=args.workdir,
        run_id=args.run_id, host=args.host,
    )
    out = {"packet": packet.to_dict(), "decision": decision, "frontmatter": fm}
    json.dump(out, sys.stdout, sort_keys=True, default=str, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_async(args: argparse.Namespace) -> int:
    """Run the off-hot-path async consolidation pass (P3).

    Lazy-imported so the hot-path subcommands stay cheap.
    """
    from . import async_runner  # noqa: PLC0415
    apply_lifecycle = not args.no_apply_lifecycle
    report = async_runner.run_async(
        workdir=args.workdir,
        memory_root=args.memory_root,
        min_projects=args.min_projects,
        similarity_threshold=args.threshold,
        write=not args.dry_run,
        apply_lifecycle=apply_lifecycle,
    )
    json.dump(report.to_dict(), sys.stdout, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0 if not report.errors else 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".",
                   help="Repo workdir (queue lives under .build-loop/pending-lessons)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="Drop a candidate into the pending queue")
    s.add_argument("--run-id", required=True)
    s.add_argument("--host", required=True, choices=["claude_code", "codex", "gemini", "other"])
    grp = s.add_mutually_exclusive_group()
    grp.add_argument("--content", default=None, help="Inline candidate body")
    grp.add_argument("--content-file", default=None, help="Read body from this file")
    s.add_argument("--hint", default=None, help="Free-text agent hint (lane/type/why-durable)")
    s.add_argument("--type", default=None, help="Optional type hint")
    s.add_argument("--name", default=None, help="Optional name slug")
    s.add_argument("--project", default=None, help="Optional project tag (scope=project)")
    s.add_argument("--json", action="store_true")

    li = sub.add_parser("list", help="List pending candidates")
    li.add_argument("--json", action="store_true")

    pr = sub.add_parser("prepare", help="Build host-LLM consolidation packet")
    pr.add_argument("candidate_id")

    pl = sub.add_parser("place", help="File a candidate per a decision JSON")
    pl.add_argument("candidate_id")
    pl.add_argument("--decision-file", required=True)
    pl.add_argument("--run-id", default=None)
    pl.add_argument("--host", default=None, choices=[None, "claude_code", "codex", "gemini", "other"])
    pl.add_argument("--json", action="store_true")

    co = sub.add_parser("consolidate", help="End-to-end deterministic-only flow")
    co.add_argument("candidate_id")
    co.add_argument("--deterministic-only", action="store_true", default=True)
    co.add_argument("--run-id", default=None)
    co.add_argument("--host", default=None, choices=[None, "claude_code", "codex", "gemini", "other"])

    ac = sub.add_parser(
        "async",
        help="Run distill/promote/lifecycle/backlinks (off-hot-path; cron-style).",
    )
    ac.add_argument("--memory-root", default=None,
                    help="Override build-loop-memory root (default: $BUILD_LOOP_MEMORY_STORE_ROOT).")
    ac.add_argument("--min-projects", type=int, default=2,
                    help="Recurrence-gate threshold (promotion requires ≥N distinct projects, default 2).")
    ac.add_argument("--threshold", type=float, default=0.55,
                    help="Cosine similarity threshold for clustering/dedup (default 0.55).")
    ac.add_argument("--dry-run", action="store_true",
                    help="Do not write distill/promote/lifecycle/backlinks output; report-only.")
    ac.add_argument(
        "--no-apply-lifecycle", action="store_true", default=False,
        help=(
            "Lifecycle state transitions (stale/archived) are written automatically; "
            "pass --no-apply-lifecycle to report-only (no lifecycle writes). "
            "Promotion and backlinks writes are unaffected by this flag."
        ),
    )

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return {
        "submit": _cmd_submit,
        "list": _cmd_list,
        "prepare": _cmd_prepare,
        "place": _cmd_place,
        "consolidate": _cmd_consolidate,
        "async": _cmd_async,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
