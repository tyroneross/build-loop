#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""CLI: python3 -m retrospective --workdir <repo> [--run-id <id>] [--transcript <path>] [--json]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path so `import retrospective.*` works

from retrospective.synthesize import run as synth_run  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="retrospective",
        description="Synthesize the post-push retrospective for a build-loop run.",
    )
    ap.add_argument("--workdir", required=True, help="build-loop project workdir")
    ap.add_argument("--run-id", default=None, help="override derived run id")
    ap.add_argument("--transcript", default=None, help="override located transcript path")
    ap.add_argument("--memory-root", default=None, help="override build-loop-memory root")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    transcript = Path(args.transcript) if args.transcript else None
    memory_root = Path(args.memory_root) if args.memory_root else None
    result = synth_run(
        workdir=Path(args.workdir),
        run_id=args.run_id,
        transcript=transcript,
        memory_root=memory_root,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = result.get("status")
        print(f"retrospective: status={status} run={args.run_id or '(derived)'}")
        if result.get("active_path"):
            print(f"  active:  {result['active_path']}")
        if result.get("summary_path"):
            print(f"  summary: {result['summary_path']}")
        if result.get("durable_path"):
            print(f"  durable: {result['durable_path']}")
        ec = result.get("enforce_candidates") or []
        if ec:
            print(f"  enforce-candidates: {len(ec)}")
        if result.get("reason"):
            print(f"  reason:  {result['reason']}")
    return 0 if result.get("status") == "ok" else 0  # never fail the run


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
