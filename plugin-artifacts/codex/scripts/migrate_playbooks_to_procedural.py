#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Copy `skills/debugging-memory/references/*-playbook.md` → `.procedural/<name>/procedure.md`.

Adds the procedural frontmatter scaffold per design §14.

Each source file becomes a directory:
  .procedural/<slug>/procedure.md
  .procedural/<slug>/incidents.jsonl   (empty placeholder)

The slug is derived from the source filename (`*-playbook.md` → `*`).

Idempotent: skips already-migrated names.

Exit codes: 0 success, 1 validation, 2 filesystem.
"""
from __future__ import annotations

import argparse
import re
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from write_decision import (  # type: ignore  # noqa: E402
    atomic_write_bytes,
    emit_frontmatter,
    parse_frontmatter,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Migrate playbooks → .procedural/")
    p.add_argument("--workdir", default=".", help="Project root")
    p.add_argument(
        "--source",
        default="skills/debugging-memory/references",
        help="Source dir (relative to workdir or absolute)",
    )
    p.add_argument(
        "--pattern",
        default="*-playbook.md",
        help="Glob pattern matching playbook files",
    )
    args = p.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    src_dir = Path(args.source)
    if not src_dir.is_absolute():
        src_dir = workdir / src_dir
    if not src_dir.exists():
        print(f"validation error: source dir not found at {src_dir}", file=sys.stderr)
        return 1

    proc_root = workdir / ".procedural"
    proc_root.mkdir(parents=True, exist_ok=True)

    matches = sorted(src_dir.glob(args.pattern))
    if not matches:
        print(f"validation error: no playbook files matching {args.pattern} in {src_dir}", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = 0
    skipped = 0
    for src in matches:
        slug = re.sub(r"-playbook\.md$", "", src.name)
        dest_dir = proc_root / slug
        dest_path = dest_dir / "procedure.md"
        if dest_path.exists():
            print(f"skip: already migrated {slug}", file=sys.stderr)
            skipped += 1
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)

        body_text = src.read_text(encoding="utf-8")
        # If the source file already has frontmatter, we'll preserve its body
        # and overlay our required procedural frontmatter.
        existing_fm = parse_frontmatter(body_text) or {}
        body_only = re.sub(r"^---\n.*?\n---\n", "", body_text, count=1, flags=re.DOTALL)

        # Heuristic trigger: pick the first H1/H2 heading text or filename.
        trigger = existing_fm.get("trigger")
        if not trigger:
            m = re.search(r"^#+\s+(.+)$", body_only, flags=re.MULTILINE)
            trigger = (m.group(1).strip() if m else slug.replace("-", " "))

        # Heuristic domains: split slug on dashes, drop common stop-words.
        domains = existing_fm.get("domains")
        if not domains:
            stops = {"playbook", "the", "a", "and", "of", "to"}
            domains = [w for w in slug.split("-") if w not in stops][:3] or [slug]

        fm = {
            "name": slug,
            "trigger": trigger,
            "domains": domains,
            "confidence": existing_fm.get("confidence", "medium"),
            "created": existing_fm.get("created", today),
            "last_applied": existing_fm.get("last_applied"),
            "incident_count": existing_fm.get("incident_count", 0),
            "depends_on": existing_fm.get("depends_on", []),
            "invalidation_signal": existing_fm.get("invalidation_signal"),
            "source_path": str(src.relative_to(workdir)),
            "migrated_on": today,
        }
        new_text = emit_frontmatter(fm) + body_only
        atomic_write_bytes(dest_path, new_text.encode("utf-8"))

        # Empty incidents.jsonl placeholder
        incidents = dest_dir / "incidents.jsonl"
        if not incidents.exists():
            atomic_write_bytes(incidents, b"")

        print(f"migrated: {src.name} → {dest_path.relative_to(workdir)}", file=sys.stderr)
        written += 1

    print(f"migrate_playbooks_to_procedural: wrote {written}, skipped {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
