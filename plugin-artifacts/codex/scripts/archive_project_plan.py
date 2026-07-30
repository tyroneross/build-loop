#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Archive ephemeral project plans into build-loop-memory before cleanup."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _paths import memory_store_root  # type: ignore  # noqa: E402
from project_resolver import resolve_project  # type: ignore  # noqa: E402


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _unique_dest(dest_dir: Path, name: str) -> Path:
    candidate = dest_dir / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for i in range(2, 1000):
        alt = dest_dir / f"{stem}-{i}{suffix}"
        if not alt.exists():
            return alt
    raise FileExistsError(f"too many archive collisions for {candidate}")


def archive_plan(
    *,
    plan_path: Path,
    workdir: Path,
    memory_root: Path | None = None,
    remove_source: bool = False,
) -> dict[str, Any]:
    source = plan_path.expanduser().resolve()
    wd = workdir.expanduser().resolve()
    root = (memory_root or memory_store_root()).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"plan not found: {source}")

    project = resolve_project(wd)
    archive_dir = root / "projects" / project / "archive" / "plans" / utc_day()
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(archive_dir, source.name)

    tmp = dest.with_name(f".{dest.name}.tmp")
    tmp.write_bytes(source.read_bytes())
    os.replace(tmp, dest)
    if remove_source:
        source.unlink()

    return {
        "action": "plan-archived",
        "project": project,
        "source": str(source),
        "archive": str(dest),
        "removed_source": remove_source,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("plan", help="Path to the local plan markdown to archive.")
    p.add_argument("--workdir", default=".")
    p.add_argument("--memory-root", default=None)
    p.add_argument(
        "--remove-source",
        action="store_true",
        help="Remove the local plan only after archive write succeeds.",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    try:
        result = archive_plan(
            plan_path=Path(args.plan),
            workdir=Path(args.workdir),
            memory_root=Path(args.memory_root) if args.memory_root else None,
            remove_source=args.remove_source,
        )
    except Exception as exc:  # noqa: BLE001
        payload = {"action": "plan-archive-error", "error": str(exc)}
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        else:
            print(f"archive_project_plan: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["archive"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
