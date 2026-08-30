#!/usr/bin/env python3
"""CLI for the executable Phase 6 Learn pipeline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from learn import runner  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="learn", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="execute deterministic Learn stages")
    run_parser.add_argument("--workdir", default=".")
    run_parser.add_argument("--run-id", required=True)
    run_parser.add_argument("--source", required=True)
    run_parser.add_argument("--defer-reason", default="")
    run_parser.add_argument("--budget-action", default="")
    run_parser.add_argument("--skip-accrue", action="store_true")
    run_parser.add_argument("--json", action="store_true")

    attest_parser = sub.add_parser("attest", help="attach agent evidence to a work order")
    attest_parser.add_argument("--workdir", default=".")
    attest_parser.add_argument("--run-id", required=True)
    attest_parser.add_argument("--work-order-id", required=True)
    attest_parser.add_argument("--status", required=True, choices=["complete", "failed"])
    attest_parser.add_argument("--artifact", default="")
    attest_parser.add_argument("--verdict", default="")
    attest_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = runner.run(
                args.workdir,
                run_id=args.run_id,
                source=args.source,
                defer_reason=args.defer_reason,
                budget_action=args.budget_action,
                accrue=not args.skip_accrue,
            )
        else:
            result = runner.attest(
                args.workdir,
                run_id=args.run_id,
                work_order_id=args.work_order_id,
                status=args.status,
                artifact=args.artifact,
                verdict=args.verdict,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"schema": runner.SCHEMA, "status": "error", "errors": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result.get("learn_line", "Learn: error"))
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
