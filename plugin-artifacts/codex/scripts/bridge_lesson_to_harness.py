#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Mirror a build-loop-memory lesson/feedback into the harness auto-memory store.

Bridges the two memory systems the user cited as unsynced:

  source: build-loop-memory/lessons/<name>.md
       OR build-loop-memory/projects/<slug>/lessons/<name>.md
       OR (any markdown file with frontmatter type: lesson|feedback|preference)

  target: ~/.claude/projects/-Users-<user>/memory/<slug>_<name>.md
       AND a one-line index entry appended to <target_dir>/MEMORY.md

Why bridge instead of canonicalizing one of them: build-loop-memory has a
structured-frontmatter contract + scope routing + cross-project lanes;
harness auto-memory is a flat topic-named bag indexed by MEMORY.md (Claude
Code reads it automatically on session start). The two have different jobs
and different consumers. Bridging keeps each one canonical for its purpose
and gives durable lessons the discoverability of both stores.

Idempotent:
  - Re-running the bridge on an already-bridged file is a no-op (compares
    target SHA-256; only writes when source changes).
  - The MEMORY.md index entry is dedup'd on the (name, source-path) pair.

Frontmatter contract on the bridged file (preserved + augmented):
  - All source frontmatter copied verbatim
  - `bridged_from: <abs-or-relative source path>` added
  - `bridged_at: <iso-utc>` added
  - `source_store: build-loop-memory` added

Usage:
  # bridge a single file
  python3 scripts/bridge_lesson_to_harness.py --source <path-to-lesson.md>

  # bridge all lessons in a folder (recursive)
  python3 scripts/bridge_lesson_to_harness.py --source-dir build-loop-memory/lessons/

  # dry-run (print what would happen, write nothing)
  python3 scripts/bridge_lesson_to_harness.py --source <path> --dry-run

  # custom target (testing)
  python3 scripts/bridge_lesson_to_harness.py --source <path> --target-dir <dir>

Exit codes:
  0 — success or dry-run
  1 — explicit error (source missing, malformed; only when --strict)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl as _fcntl  # POSIX only
    _HAVE_FLOCK = hasattr(_fcntl, "flock")
except ImportError:
    _fcntl = None  # type: ignore[assignment]
    _HAVE_FLOCK = False

_home = Path.home()
DEFAULT_HARNESS_MEMORY_DIR = _home / ".claude" / "projects" / str(_home).replace("/", "-") / "memory"
INDEX_FILENAME = "MEMORY.md"
BRIDGED_SECTION_HEADER = "## Bridged from build-loop-memory"

# Frontmatter types that are bridge-eligible. Decisions live in their own
# system (write_decision.py) and are NOT bridged here — they belong in
# build-loop-memory/projects/<slug>/decisions/.
BRIDGEABLE_TYPES = ("lesson", "feedback", "preference", "convention", "gotcha")


def log(msg: str) -> None:
    print(f"[bridge_lesson_to_harness] {msg}", file=sys.stderr)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slugify(text: str, *, maxlen: int = 80) -> str:
    """Stable slug for filenames + index entries."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:maxlen] or "unnamed"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML frontmatter parser (scalars + nested mapping)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    fm_raw = text[4:end]
    body = text[end + 5 :]
    fm: dict = {}
    current_nested: str | None = None
    nested: dict = {}

    for raw_line in fm_raw.splitlines():
        if not raw_line.strip():
            continue
        if not raw_line.startswith((" ", "\t")) and raw_line.rstrip().endswith(":"):
            if current_nested is not None:
                fm[current_nested] = nested
                nested = {}
            current_nested = raw_line.rstrip()[:-1].strip()
            continue
        if raw_line.startswith((" ", "\t")) and current_nested:
            kv = raw_line.strip().split(":", 1)
            if len(kv) == 2:
                k, v = kv[0].strip(), kv[1].strip()
                try:
                    nested[k] = json.loads(v)
                except (ValueError, json.JSONDecodeError):
                    nested[k] = v
            continue
        if current_nested is not None:
            fm[current_nested] = nested
            nested = {}
            current_nested = None
        if ":" in raw_line:
            k, v = raw_line.split(":", 1)
            fm[k.strip()] = v.strip()
    if current_nested is not None:
        fm[current_nested] = nested
    return fm, body


def _emit_frontmatter(fm: dict) -> str:
    """Reverse of _parse_frontmatter for scalars + simple nested maps."""
    lines = ["---"]
    nested_blocks: list[tuple[str, dict]] = []
    for k, v in fm.items():
        if isinstance(v, dict):
            nested_blocks.append((k, v))
        else:
            lines.append(f"{k}: {v}" if not isinstance(v, str) else f"{k}: {v}")
    for k, v in nested_blocks:
        lines.append(f"{k}:")
        for nk, nv in v.items():
            lines.append(f"  {nk}: {json.dumps(nv) if not isinstance(nv, str) else nv}")
    lines.append("---")
    return "\n".join(lines)


def _extract_kind(fm: dict) -> str:
    """Pull the kind/type from frontmatter; tolerant of either field name."""
    t = fm.get("type")
    if isinstance(t, str) and t:
        return t
    md = fm.get("metadata")
    if isinstance(md, dict):
        mt = md.get("type")
        if isinstance(mt, str) and mt:
            return mt
    return ""


def _name_from_fm_or_path(fm: dict, source: Path) -> str:
    n = fm.get("name")
    if isinstance(n, str) and n.strip():
        return n.strip()
    return source.stem


def _description_from_fm(fm: dict) -> str:
    d = fm.get("description")
    if isinstance(d, str) and d.strip():
        return d.strip()
    return ""


def _resolve_target_basename(fm: dict, source: Path) -> str:
    """Build a deterministic target basename: <kind>_<name-slug>.md.

    Matches the harness convention (e.g. `feedback_no_cosmetic_dismissal.md`).
    Falls back to the source stem when frontmatter is missing.
    """
    kind = _extract_kind(fm) or "lesson"
    name = _name_from_fm_or_path(fm, source)
    return f"{kind}_{_slugify(name)}.md"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bridge_one(
    source: Path,
    target_dir: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Bridge a single source file. Returns a result dict.

    Result keys:
      status: "written" | "skipped_identical" | "skipped_not_bridgeable" | "error"
      target: str (target absolute path; only when written)
      index_updated: bool
      reason: str (explanation)
    """
    if not source.exists():
        return {"status": "error", "reason": f"source missing: {source}"}

    try:
        src_text = source.read_text(encoding="utf-8")
    except OSError as e:
        return {"status": "error", "reason": f"read error: {e}"}

    fm, body = _parse_frontmatter(src_text)
    kind = _extract_kind(fm)
    if kind not in BRIDGEABLE_TYPES:
        return {
            "status": "skipped_not_bridgeable",
            "reason": f"frontmatter type={kind!r} not in {BRIDGEABLE_TYPES}",
        }

    target_basename = _resolve_target_basename(fm, source)
    target = target_dir / target_basename

    # Augment frontmatter for the bridged copy.
    augmented = dict(fm)
    augmented["bridged_from"] = str(source)
    augmented["bridged_at"] = _iso_now()
    augmented["source_store"] = "build-loop-memory"

    new_text = _emit_frontmatter(augmented) + "\n" + body.lstrip("\n")

    # Idempotency: if target exists AND content (sans bridged_at) matches, skip.
    if target.exists() and not force:
        try:
            existing = target.read_text(encoding="utf-8")
            # Compare ignoring bridged_at (which always changes) by stripping it.
            existing_norm = re.sub(r"^bridged_at:.*\n", "", existing, flags=re.M)
            new_norm = re.sub(r"^bridged_at:.*\n", "", new_text, flags=re.M)
            if _sha256(existing_norm) == _sha256(new_norm):
                return {
                    "status": "skipped_identical",
                    "reason": "target up to date",
                    "target": str(target),
                    "index_updated": False,
                }
        except OSError:
            pass

    index_updated = False
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".md.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, target)
        index_updated = _update_index(target_dir, fm, target_basename, source)

    return {
        "status": "written",
        "target": str(target),
        "index_updated": index_updated,
        "reason": "bridged",
    }


def _update_index(target_dir: Path, fm: dict, target_basename: str, source: Path) -> bool:
    """Append a one-line entry to MEMORY.md under the Bridged section.

    Dedup'd on the target basename — re-runs don't add duplicate lines.
    Concurrent writes are serialised via an advisory fcntl.flock on a
    <MEMORY.md>.lock sidecar (POSIX only; silently skipped elsewhere).
    The final write remains atomic via tmp + os.replace.
    """
    index_path = target_dir / INDEX_FILENAME
    name = _name_from_fm_or_path(fm, source)
    # Sanitize: strip characters that would break Markdown link syntax.
    name = re.sub(r"[\[\]()\n]", "", name)
    desc = _description_from_fm(fm)
    desc_short = (desc[:140] + "…") if len(desc) > 140 else desc
    entry = f"- [{name}]({target_basename}) — {desc_short or '(no description)'}"

    lock_path = index_path.with_name(INDEX_FILENAME + ".lock")
    _lock_fh = None
    try:
        if _HAVE_FLOCK:
            target_dir.mkdir(parents=True, exist_ok=True)
            _lock_fh = lock_path.open("a")
            _fcntl.flock(_lock_fh, _fcntl.LOCK_EX)  # type: ignore[union-attr]

        if not index_path.exists():
            # Brand new index — start from scratch.
            body = (
                "# Project Memory\n\n"
                f"{BRIDGED_SECTION_HEADER}\n\n"
                "Bridged lesson/feedback entries from build-loop-memory; "
                "the source is canonical and these mirrors update on bridge runs.\n\n"
                f"{entry}\n"
            )
            tmp = index_path.with_suffix(".md.tmp")
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, index_path)
            return True

        text = index_path.read_text(encoding="utf-8")
        # Dedup: anchor on the unique target basename.
        if f"]({target_basename})" in text:
            return False

        if BRIDGED_SECTION_HEADER in text:
            # Insert after the section header (and any blank line + intro).
            # Look for the next blank line after the header; insert after it.
            idx = text.find(BRIDGED_SECTION_HEADER)
            end_of_header_line = text.find("\n", idx) + 1
            # Find the END of the bridged section — either next "## " or EOF.
            next_section = text.find("\n## ", end_of_header_line)
            section_end = next_section if next_section >= 0 else len(text)
            section_text = text[end_of_header_line:section_end]
            # Insert at the end of the section (just before the next "## " or EOF).
            if section_text and not section_text.endswith("\n"):
                new_text = text[:section_end] + "\n" + entry + "\n" + text[section_end:]
            else:
                new_text = text[:section_end] + entry + "\n" + text[section_end:]
        else:
            # Append the section at the end of the file.
            sep = "" if text.endswith("\n") else "\n"
            new_text = (
                text
                + sep
                + "\n"
                + BRIDGED_SECTION_HEADER
                + "\n\n"
                + "Bridged lesson/feedback entries from build-loop-memory; "
                + "the source is canonical and these mirrors update on bridge runs.\n\n"
                + entry
                + "\n"
            )

        tmp = index_path.with_suffix(".md.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, index_path)
        return True
    finally:
        if _lock_fh is not None:
            try:
                _fcntl.flock(_lock_fh, _fcntl.LOCK_UN)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
            _lock_fh.close()


def collect_sources(source: Path | None, source_dir: Path | None) -> list[Path]:
    """Resolve --source / --source-dir into a flat list of markdown files."""
    out: list[Path] = []
    if source:
        if source.is_file():
            out.append(source)
        else:
            log(f"--source missing or not a file: {source}")
    if source_dir:
        if source_dir.is_dir():
            for p in sorted(source_dir.rglob("*.md")):
                # Skip the conventional README + INDEX files.
                if p.name in ("README.md", "INDEX.md", "MEMORY.md"):
                    continue
                out.append(p)
        else:
            log(f"--source-dir missing or not a dir: {source_dir}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=None, help="Single source markdown file")
    ap.add_argument("--source-dir", type=Path, default=None, help="Recurse a directory of markdown files")
    ap.add_argument(
        "--target-dir",
        type=Path,
        default=DEFAULT_HARNESS_MEMORY_DIR,
        help=f"Harness auto-memory dir (default: {DEFAULT_HARNESS_MEMORY_DIR})",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print actions, write nothing")
    ap.add_argument("--force", action="store_true", help="Re-write even if target matches source")
    ap.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero on any error")
    args = ap.parse_args(argv)

    sources = collect_sources(args.source, args.source_dir)
    if not sources:
        log("no sources given; pass --source or --source-dir")
        return 1 if args.strict else 0

    results: list[dict] = []
    any_error = False
    for src in sources:
        try:
            r = bridge_one(src, args.target_dir, dry_run=args.dry_run, force=args.force)
        except Exception as e:  # noqa: BLE001
            r = {"status": "error", "reason": f"unexpected: {e}", "source": str(src)}
        r["source"] = str(src)
        if r["status"] == "error":
            any_error = True
        results.append(r)

    summary = {
        "target_dir": str(args.target_dir),
        "dry_run": bool(args.dry_run),
        "totals": {
            "sources": len(sources),
            "written": sum(1 for r in results if r["status"] == "written"),
            "skipped_identical": sum(1 for r in results if r["status"] == "skipped_identical"),
            "skipped_not_bridgeable": sum(1 for r in results if r["status"] == "skipped_not_bridgeable"),
            "errors": sum(1 for r in results if r["status"] == "error"),
        },
        "items": results,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        t = summary["totals"]
        log(
            f"done — sources={t['sources']} written={t['written']} "
            f"skipped_identical={t['skipped_identical']} "
            f"skipped_not_bridgeable={t['skipped_not_bridgeable']} "
            f"errors={t['errors']}"
        )

    return 1 if (any_error and args.strict) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"unexpected error (swallowed): {e}")
        sys.exit(0)
