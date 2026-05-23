#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Explicit revoke for a decision (Scenario 8 — different from supersede).

Revoke means: this decision should not have been recorded, OR it has been
withdrawn entirely without a replacement. Use cases:

  - User clarifies that something captured was venting, not a decision
  - Inferred capture was wrong and there is no superseding decision
  - Decision was made in error and the team has rolled back to the prior state

Differs from `supersede_decision.py`:
  - supersede: replaces 0042 with a NEW decision; old → _history/0042-vN.md
  - revoke:    withdraws 0042 with NO replacement; old → _history/0042-revoked.md

Effects:
  - Move file from `.episodic/decisions/0042-...md` to `.episodic/decisions/_history/0042-revoked.md`
  - Update frontmatter: `status: rejected`, `revoked: true`, `revoke_reason: "..."`
  - Append `decision_revoked` event to `events.jsonl`
  - Best-effort: UPDATE semantic_facts SET status = 'retracted' WHERE metadata->>'decision_id' = ...

Exit codes: 0 success | 1 validation error | 2 filesystem error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from db import execute  # type: ignore  # noqa: E402
from write_decision import (  # type: ignore  # noqa: E402
    LockedFile,
    _FM_RE,
    append_event,
    atomic_write_bytes,
    emit_frontmatter,
    log,
    parse_frontmatter,
    regenerate_index,
)


def find_decision_file(workdir: Path, decision_id: str) -> Path | None:
    decisions_dir = workdir / ".episodic" / "decisions"
    if not decisions_dir.exists():
        return None
    for f in decisions_dir.glob(f"{decision_id}-*.md"):
        if f.is_file():
            return f
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Revoke (withdraw) a decision with no replacement.")
    p.add_argument("--workdir", default=".")
    p.add_argument("--id", required=True, help="4-digit decision ID to revoke")
    p.add_argument("--reason", required=True, help="Human-readable reason (recorded in frontmatter + event)")
    p.add_argument("--db", dest="db", action="store_true", default=True)
    p.add_argument("--no-db", dest="db", action="store_false")
    p.add_argument(
        "--schema",
        default=None,
        help="Postgres schema. Default: $AGENT_MEMORY_SCHEMA or 'personal_memory'.",
    )
    return p.parse_args(argv)


def _iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0

    if args.schema is None:
        from _paths import default_schema as _ds  # noqa: PLC0415
        args.schema = _ds()

    workdir = Path(args.workdir).resolve()
    if not re.match(r"^\d{4}$", args.id):
        log(f"validation error: --id must be 4 digits, got {args.id!r}")
        return 1

    decisions_dir = workdir / ".episodic" / "decisions"
    history_dir = decisions_dir / "_history"
    events_path = workdir / ".episodic" / "events.jsonl"
    history_dir.mkdir(parents=True, exist_ok=True)

    target = find_decision_file(workdir, args.id)
    if target is None:
        log(f"validation error: no decision matching id={args.id} in {decisions_dir}")
        return 1

    # Take the same writer lock write_decision.py uses to avoid races with
    # concurrent supersede / write operations.
    writer_lock_target = decisions_dir / ".writer"
    try:
        with LockedFile(writer_lock_target):
            return _do_revoke(args, workdir, target, history_dir, events_path)
    except TimeoutError as e:
        log(f"filesystem error: {e}")
        return 2


def _do_revoke(
    args: argparse.Namespace,
    workdir: Path,
    target: Path,
    history_dir: Path,
    events_path: Path,
) -> int:
    # Read + parse + update frontmatter
    text = target.read_text(encoding="utf-8")
    fm = parse_frontmatter(text) or {}
    fm["status"] = "rejected"
    fm["revoked"] = True
    fm["revoke_reason"] = args.reason
    fm["revoked_at"] = _iso_utc()

    body_only = _FM_RE.sub("", text, count=1)
    new_text = emit_frontmatter(fm) + body_only

    # Move to _history/<id>-revoked.md
    dest = history_dir / f"{args.id}-revoked.md"
    try:
        atomic_write_bytes(dest, new_text.encode("utf-8"))
    except OSError as e:
        log(f"filesystem error writing {dest}: {e}")
        return 2

    try:
        target.unlink()
    except OSError as e:
        log(f"filesystem error removing {target}: {e}")
        return 2

    # Regenerate INDEX (the revoked decision drops out of the trusted view)
    try:
        decisions_dir = workdir / ".episodic" / "decisions"
        regenerate_index(decisions_dir)
    except Exception as e:  # noqa: BLE001
        log(f"warning: failed to regenerate INDEX (continuing): {e}")

    # Emit decision_revoked event
    try:
        append_event(
            events_path,
            {
                "ts": _iso_utc(),
                "kind": "decision_revoked",
                "decision_id": args.id,
                "primary_tag": fm.get("primary_tag"),
                "entity": fm.get("entity"),
                "reason": args.reason,
                "dedup_key": f"decision:{args.id}:revoked",
            },
        )
    except Exception as e:  # noqa: BLE001
        log(f"warning: failed to append revoke event (continuing): {e}")

    # Best-effort DB status update
    if args.db:
        try:
            if not re.match(r"^[a-z][a-z0-9_]*$", args.schema):
                raise ValueError(f"unsafe schema name: {args.schema!r}")
            sql = (
                f"UPDATE {args.schema}.semantic_facts "
                "SET status = 'retracted', valid_to = now() "
                "WHERE metadata->>'decision_id' = %s;"
            )
            execute(sql, (args.id,))
            log(f"db: marked semantic_facts row(s) for decision {args.id} as retracted")
        except Exception as e:  # noqa: BLE001
            log(
                f"db: revoke (file move + event succeeded; DB update best-effort skipped): {e}"
            )

    log(f"revoked decision {args.id} → {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
