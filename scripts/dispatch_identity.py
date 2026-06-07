#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Generate and validate build-loop dispatch task ids.

Phase 3 uses one task id per subagent dispatch. The same id is written to the
dispatch cost-ledger row, prepended to the subagent brief, and echoed in the
return row so consumers can join the pair.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid

TASK_ID_RE = re.compile(r"^t-[0-9a-f]{8}$")


def new_task_id() -> str:
    return f"t-{uuid.uuid4().hex[:8]}"


def is_task_id(value: str) -> bool:
    return bool(TASK_ID_RE.match(value or ""))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--validate", default=None, help="Validate an existing task id.")
    p.add_argument("--plain", action="store_true", help="Print only the task id.")
    p.add_argument("--json", action="store_true", help="Print a JSON envelope.")
    args = p.parse_args(argv)

    if args.validate is not None:
        valid = is_task_id(args.validate)
        if args.plain:
            print("true" if valid else "false")
        else:
            print(json.dumps({"task_id": args.validate, "valid": valid}, indent=2))
        return 0 if valid else 1

    task_id = new_task_id()
    if args.plain and not args.json:
        print(task_id)
    else:
        print(json.dumps({"task_id": task_id, "valid": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
