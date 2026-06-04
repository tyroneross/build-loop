#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Surface pending-lesson candidates for the HOST coding agent to refine.

Tier-3 of the three-tier capture stack: deterministic Stop-hook (tier 1)
captures raw candidates, optional Ollama distill (tier 2) clusters them
when context is large, and THIS surface (tier 3) is the host coding
agent — Claude Code, Codex, etc. — reading the queue and promoting items
to durable memory via the existing memory_writer / write_decision paths.

Inputs (all relative to --workdir):
  .build-loop/pending-lessons/             — raw tier-1 captures (this run)
  .build-loop/pending-lessons/promoted/    — already promoted (skip)
  .build-loop/pending-lessons/discarded/   — explicitly discarded (skip)
  build-loop-memory/projects/<slug>/decisions/_review/  — Ollama quarantine

Output (default: human-readable markdown to stdout for the host agent
to read; --json for structured consumption).

Usage:
  python3 scripts/surface_pending_lessons.py --workdir .
  python3 scripts/surface_pending_lessons.py --workdir . --json
  python3 scripts/surface_pending_lessons.py --workdir . --limit 5
  python3 scripts/surface_pending_lessons.py --workdir . --include-decisions-review

Exit codes: 0 always (never blocks a session).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_LIMIT = 10
PENDING_DIRNAME = "pending-lessons"


def log(msg: str) -> None:
    print(f"[surface_pending_lessons] {msg}", file=sys.stderr)


@dataclass
class PendingItem:
    """One queued candidate awaiting host-agent refinement."""

    source: str  # "tier1" | "decisions_review"
    path: str  # relative to workdir
    id_hash: str
    kind: str  # correction | preference | tradeoff | decision (for _review)
    signal_type: str
    confidence: str
    scope: str
    quote: str
    captured_at: str
    extras: dict


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Naive YAML-frontmatter parser (no PyYAML dep).

    Handles scalars + simple nested dicts (extras: ... indented).
    Returns (fm_dict, body_text).
    """
    if not text.startswith("---\n"):
        return {}, text

    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text

    fm_raw = text[4:end]
    body = text[end + 5 :]
    fm: dict = {}
    current_nested_key: str | None = None
    nested: dict = {}

    for raw_line in fm_raw.splitlines():
        if not raw_line.strip():
            continue
        if not raw_line.startswith((" ", "\t")) and raw_line.rstrip().endswith(":"):
            if current_nested_key is not None:
                fm[current_nested_key] = nested
                nested = {}
            current_nested_key = raw_line.rstrip()[:-1].strip()
            continue
        if raw_line.startswith((" ", "\t")) and current_nested_key:
            kv = raw_line.strip().split(":", 1)
            if len(kv) == 2:
                k, v = kv[0].strip(), kv[1].strip()
                try:
                    nested[k] = json.loads(v)
                except (ValueError, json.JSONDecodeError):
                    nested[k] = v
            continue
        if current_nested_key is not None:
            fm[current_nested_key] = nested
            nested = {}
            current_nested_key = None
        if ":" in raw_line:
            k, v = raw_line.split(":", 1)
            fm[k.strip()] = v.strip()

    if current_nested_key is not None:
        fm[current_nested_key] = nested

    return fm, body


def _extract_quote(body: str) -> str:
    """Pull the verbatim quote from the markdown body."""
    m = re.search(r"##\s+Quote\s*\n\n>\s*(.+?)(?:\n\n|$)", body, re.S)
    if m:
        return m.group(1).strip().replace("\n> ", "\n").strip()
    return ""


def _scan_tier1(pending_dir: Path, workdir: Path) -> list[PendingItem]:
    """Scan .build-loop/pending-lessons/ for tier-1 candidates."""
    if not pending_dir.is_dir():
        return []
    items: list[PendingItem] = []
    for p in sorted(pending_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = _parse_frontmatter(text)
        if not fm:
            continue
        items.append(
            PendingItem(
                source="tier1",
                path=str(p.relative_to(workdir)),
                id_hash=str(fm.get("id", "")),
                kind=str(fm.get("kind", "")),
                signal_type=str(fm.get("signal_type", "")),
                confidence=str(fm.get("confidence", "")),
                scope=str(fm.get("scope", "")),
                quote=_extract_quote(body),
                captured_at=str(fm.get("captured_at", "")),
                extras=fm.get("extras", {}) if isinstance(fm.get("extras"), dict) else {},
            )
        )
    return items


def _resolve_project_slug(workdir: Path) -> str:
    """Best-effort project slug — repo basename is the durable default."""
    return workdir.name


def _scan_decisions_review(workdir: Path) -> list[PendingItem]:
    """Scan build-loop-memory/projects/<slug>/decisions/_review/ if present."""
    slug = _resolve_project_slug(workdir)
    candidates = [
        Path.home() / "dev" / "git-folder" / "build-loop-memory" / "projects" / slug / "decisions" / "_review",
        workdir / "build-loop-memory" / "projects" / slug / "decisions" / "_review",
    ]
    items: list[PendingItem] = []
    for review_dir in candidates:
        if not review_dir.is_dir():
            continue
        for p in sorted(review_dir.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, body = _parse_frontmatter(text)
            items.append(
                PendingItem(
                    source="decisions_review",
                    path=str(p),
                    id_hash=str(fm.get("id", "")),
                    kind="decision",
                    signal_type=str(fm.get("primary_tag", "")) or "tier3-inferred",
                    confidence=str(fm.get("confidence", "")),
                    scope="project",
                    quote=(body[:200].strip()).replace("\n", " "),
                    captured_at=str(fm.get("created_at", "")),
                    extras={"original_path": str(p)},
                )
            )
        break
    return items


def render_markdown(items: list[PendingItem], *, limit: int) -> str:
    """Render for the HOST coding agent to read."""
    if not items:
        return "## Pending lesson candidates: 0\n\nNothing waiting for refinement.\n"

    lines: list[str] = [f"## Pending lesson candidates ({len(items)})", ""]
    lines.append(
        "These are unrefined captures from tier-1 (deterministic Stop-hook) and "
        "tier-2 (Ollama distill, when present). The HOST coding agent is the "
        "primary refinement layer. For each item below: decide whether it is "
        "a real lesson/feedback/decision, classify its scope (project|global), "
        "and either promote via `scripts/memory_writer.py` "
        "(for kind=lesson|feedback) or `scripts/write_decision/__main__.py` "
        "(for kind=decision), or move the file into `pending-lessons/discarded/` "
        "to silence it."
    )
    lines.append("")
    for i, it in enumerate(items[:limit], 1):
        lines.append(f"### {i}. {it.kind} · {it.signal_type} · scope={it.scope}")
        lines.append("")
        lines.append(f"- **Source:** `{it.path}`")
        lines.append(f"- **Confidence:** {it.confidence}")
        if it.extras.get("prior_assistant_acted"):
            lines.append("- **High-signal:** user reacted to assistant's just-taken action")
        lines.append(f"- **Captured:** {it.captured_at}")
        lines.append("")
        lines.append("**Quote:**")
        lines.append("")
        lines.append("> " + it.quote.replace("\n", "\n> "))
        lines.append("")
        target_lane = (
            "build-loop-memory/lessons/"
            if it.scope == "global"
            else "build-loop-memory/projects/<slug>/lessons/"
        )
        lines.append(f"**Suggested promotion target:** `{target_lane}`")
        lines.append("")
    if len(items) > limit:
        lines.append(f"_(showing {limit} of {len(items)})_")
    return "\n".join(lines) + "\n"


def render_json(items: list[PendingItem], *, limit: int, workdir: Path) -> str:
    payload = {
        "workdir": str(workdir),
        "total": len(items),
        "shown": min(limit, len(items)),
        "items": [asdict(it) for it in items[:limit]],
        "promotion_guide": {
            "kind=lesson|feedback": "scripts/memory_writer.py write --type lesson --workdir <repo> ...",
            "kind=decision": "scripts/write_decision/__main__.py --confidence <...> ...",
            "discard": "mv the file into .build-loop/pending-lessons/discarded/",
            "global_lane": "build-loop-memory/lessons/",
            "project_lane": "build-loop-memory/projects/<slug>/lessons/",
        },
    }
    return json.dumps(payload, indent=2) + "\n"


def collect_pending(workdir: Path, *, include_decisions_review: bool) -> list[PendingItem]:
    pending = workdir / ".build-loop" / PENDING_DIRNAME
    items = _scan_tier1(pending, workdir)
    if include_decisions_review:
        items.extend(_scan_decisions_review(workdir))
    return items


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", default=".", help="Project root")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max items to render")
    ap.add_argument(
        "--include-decisions-review",
        action="store_true",
        help="Also surface build-loop-memory/projects/<slug>/decisions/_review/",
    )
    ap.add_argument("--json", action="store_true", help="Render as JSON envelope")
    ap.add_argument("--quiet", action="store_true", help="Suppress output if zero pending")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    try:
        items = collect_pending(workdir, include_decisions_review=args.include_decisions_review)
    except Exception as e:  # noqa: BLE001
        log(f"collect error (swallowed): {e}")
        return 0

    if args.quiet and not items:
        return 0

    if args.json:
        sys.stdout.write(render_json(items, limit=args.limit, workdir=workdir))
    else:
        sys.stdout.write(render_markdown(items, limit=args.limit))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"unexpected error (swallowed): {e}")
        sys.exit(0)
