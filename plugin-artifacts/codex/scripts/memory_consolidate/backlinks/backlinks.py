#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Generate ``[[name]]`` backlinks between semantically related entries.

Single source of the backlink contract — every other capability calls
into this module instead of templating ``[[...]]`` themselves:

  * Discovery   — ``find_related_entries`` queries the P1 hybrid recall
                  tier for siblings of a given entry's body.
  * Suggestion  — ``propose_backlinks`` returns dedup'd
                  BacklinkSuggestion entries, skipping links to self and
                  any link already in the entry.
  * Write       — ``write_backlinks_footer`` appends or refreshes a
                  surgical ``## Related`` block. Idempotent: running
                  twice yields a single block with each link once.

The ``[[name]]`` form is the Karpathy LLM-Wiki backlink convention.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # scripts/

try:
    import memory_writer as _mw  # type: ignore  # noqa: E402
    _patch_frontmatter = _mw.patch_frontmatter
except Exception:  # noqa: BLE001
    _patch_frontmatter = None  # degraded mode — tests may inject directly

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_BACKLINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_RELATED_HEADING_RE = re.compile(r"^## Related\s*$", re.MULTILINE)


@dataclass
class BacklinkPair:
    """A directional link from ``source_name`` to ``target_name``."""
    source_name: str
    target_name: str
    score: float = 0.0
    target_path: str | None = None


@dataclass
class BacklinkSuggestion:
    """A backlink to be added to a specific entry."""
    target_name: str
    target_path: str | None = None
    score: float = 0.0

    def render(self) -> str:
        return f"[[{self.target_name}]]"


# ---------------------------------------------------------------------------
# Parsing helpers.
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip('"').strip("'")
    body = _FM_RE.sub("", text, count=1)
    return fm, body


def extract_existing_backlinks(text: str) -> set[str]:
    """Return every ``[[name]]`` already present in ``text``."""
    return {m.group(1).strip() for m in _BACKLINK_RE.finditer(text)}


# ---------------------------------------------------------------------------
# Discovery — P1 hybrid recall.
# ---------------------------------------------------------------------------


def find_related_entries(
    body: str,
    *,
    own_name: str | None,
    project: str | None,
    limit: int = 5,
    embed_fn: Any = None,
) -> list[dict]:
    """Query the P1 hybrid recall tier for related siblings. Absence-tolerant.

    Returns each match's ``subject`` / ``object`` / ``project`` /
    ``file_hint``. Self-matches are filtered out by ``own_name``.
    """
    if not body or not body.strip():
        return []
    try:
        from semantic_index import query_facts  # type: ignore  # noqa: PLC0415
    except (ImportError, ModuleNotFoundError):
        return []
    try:
        kwargs = {"query": body[:600], "limit": limit, "mode": "hybrid"}
        if project is not None:
            kwargs["project"] = project
        if embed_fn is not None:
            kwargs["embed_fn"] = embed_fn
        rows = query_facts(**kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: backlinks recall failed: {exc}", file=sys.stderr)
        return []
    out: list[dict] = []
    own = (own_name or "").lower()
    for row in rows:
        subj = str(row.get("subject") or "")
        if own and own in subj.lower():
            continue
        out.append({
            "subject": subj,
            "object": row.get("object"),
            "project": row.get("project"),
            "file_hint": row.get("file_hint") or subj,
        })
    return out


# ---------------------------------------------------------------------------
# Suggestion — dedup'd, with skip-list.
# ---------------------------------------------------------------------------


def propose_backlinks(
    entry_text: str,
    *,
    own_name: str | None,
    project: str | None,
    limit: int = 5,
    related_fn: Any = None,
    embed_fn: Any = None,
) -> list[BacklinkSuggestion]:
    """Build a dedup'd list of BacklinkSuggestion entries.

    ``related_fn`` is injectable for tests: ``(body, own_name, project) -> list[dict]``.
    """
    fm, body = _parse_frontmatter(entry_text)
    existing = extract_existing_backlinks(entry_text)
    if related_fn is not None:
        rows = list(related_fn(body, own_name, project) or [])
    else:
        rows = find_related_entries(
            body, own_name=own_name, project=project,
            limit=limit * 2, embed_fn=embed_fn,
        )
    seen: set[str] = set()
    out: list[BacklinkSuggestion] = []
    for row in rows:
        name = _row_to_name(row)
        if not name:
            continue
        if name in existing or name in seen:
            continue
        if own_name and name == own_name:
            continue
        seen.add(name)
        out.append(BacklinkSuggestion(
            target_name=name,
            target_path=row.get("file_hint"),
            score=float(row.get("score", 0.0) or 0.0),
        ))
        if len(out) >= limit:
            break
    return out


def _row_to_name(row: dict) -> str:
    """Best-effort: convert a recall row into a target_name slug."""
    # Prefer 'file_hint' basename without extension; fall back to subject.
    hint = row.get("file_hint") or row.get("subject") or ""
    if not hint:
        return ""
    name = Path(str(hint)).name
    # Strip leading date + type prefix (YYYY-MM-DD-<type>-) → bare slug
    name = re.sub(r"\.md$", "", name)
    return name


# ---------------------------------------------------------------------------
# Write — surgical, idempotent ``## Related`` block.
# ---------------------------------------------------------------------------


def write_backlinks_footer(
    path: str | Path,
    suggestions: list[BacklinkSuggestion],
    *,
    dry_run: bool = False,
) -> str:
    """Append (or refresh) a ``## Related`` block at the end of ``path``.

    Behaviour:
      * If a ``## Related`` block already exists, REPLACE its body with
        the union of the existing links + the new suggestions (dedup'd).
      * Otherwise, append a new ``## Related`` block at end of file.
      * Idempotent: running twice with the same suggestions yields the
        same on-disk file.
      * ``dry_run=True`` returns the would-be file text without writing.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    existing_block = _find_related_block(text)
    existing_links: list[str] = []
    if existing_block is not None:
        start, end, block_text = existing_block
        existing_links = sorted(extract_existing_backlinks(block_text))
        body_before = text[:start]
        body_after = text[end:]
    else:
        body_before = text.rstrip() + "\n"
        body_after = ""

    new_links_set = {s.target_name for s in suggestions}
    union = sorted(set(existing_links) | new_links_set)

    if not union:
        # Nothing to write.
        return text

    block_lines = ["## Related", ""]
    for name in union:
        block_lines.append(f"- [[{name}]]")
    block_lines.append("")
    new_block = "\n".join(block_lines)

    # Ensure body_before ends with a blank line so the block stays
    # visually separated.
    if not body_before.endswith("\n\n"):
        if not body_before.endswith("\n"):
            body_before += "\n"
        body_before += "\n"

    new_text = body_before + new_block + (body_after.lstrip("\n") if body_after else "")
    if not new_text.endswith("\n"):
        new_text += "\n"

    if not dry_run:
        # f1: route through canonical writer (provenance + ledger).
        # Compute the new body (everything after the frontmatter) from new_text.
        fm_block_end = new_text.find("\n---\n", 4) + 5 if new_text.startswith("---\n") else 0
        new_body_only = new_text[fm_block_end:] if fm_block_end > 0 else new_text
        if _patch_frontmatter is not None:
            _patch_frontmatter(p, {}, new_body=new_body_only)
        else:
            # Degraded fallback (memory_writer unavailable in restricted environments).
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(new_text, encoding="utf-8")
            os.replace(tmp, p)
    return new_text


def _find_related_block(text: str) -> tuple[int, int, str] | None:
    """Locate the existing ``## Related`` block boundaries in ``text``.

    Returns ``(start_offset, end_offset, block_text)`` or ``None`` when
    no block is present. The block runs from the heading line through
    the line BEFORE the next H2 heading (or EOF).
    """
    m = _RELATED_HEADING_RE.search(text)
    if not m:
        return None
    start = m.start()
    # Find next H2 after the heading.
    rest = text[m.end():]
    next_h2 = re.search(r"\n## ", rest)
    if next_h2:
        end = m.end() + next_h2.start() + 1  # include trailing newline
    else:
        end = len(text)
    return start, end, text[start:end]
