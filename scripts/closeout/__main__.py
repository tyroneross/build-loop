#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""CLI: python3 -m closeout --workdir <repo> [--run-id <id>] [--source <s>] [--json]

Triggers
--------
- post-push (build-loop run)  → orchestrator Phase 4G calls this.
- post-push-armed (ad-hoc)    → session-start hook drains the armed baton.
- phase-6-learn               → orchestrator's ``## Learn`` line uses this.
- ad-hoc                      → human-invoked one-off.

Exit codes
----------
- 0 on success (closeout_status emitted).
- 0 on degraded (envelope.error set; the contract is "never block on closeout").
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path so ``import closeout.*`` works.

from closeout.status import VALID_SOURCES, run  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="closeout",
        description="Emit the build-loop closeout status (wrote_memory | queued_pending_lesson | no_durable_lesson).",
    )
    ap.add_argument("--workdir", required=True, help="build-loop project workdir")
    ap.add_argument("--run-id", default=None, help="stable run identifier")
    ap.add_argument(
        "--source",
        default="ad-hoc",
        choices=sorted(VALID_SOURCES),
        help="closeout trigger",
    )
    ap.add_argument("--memory-root", default=None, help="build-loop-memory root override")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    envelope = run(Path(args.workdir), run_id=args.run_id, source=args.source, memory_root=args.memory_root)
    if args.json:
        print(json.dumps(envelope, indent=2, sort_keys=True))
    else:
        status = envelope.get("closeout_status")
        print(f"closeout_status={status} source={envelope.get('source')} run={envelope.get('run_id')}")
        print(f"  reason: {envelope.get('reason')}")
        if envelope.get("written_to"):
            print(f"  wrote:  {envelope.get('written_to')}")
        if envelope.get("error"):
            print(f"  error:  {envelope.get('error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
