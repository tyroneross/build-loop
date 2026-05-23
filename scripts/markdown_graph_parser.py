#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Markdown link/mention parser for the Phase B knowledge-graph leg.

Walks `.episodic/decisions/*.md`, `.build-loop/feedback.md`, and
`~/.claude/projects/-Users-tyroneross/memory/*.md` (when present) and
extracts cross-decision references as graph edges.

Three edge types:
  1. wikilink   — Obsidian-style `[[target]]` resolved against decision
                  frontmatter `id:` field (and slug as a secondary key)
  2. path       — file path mentions like `scripts/foo.py`,
                  `src/build_loop/architecture/bar.py`, optionally with
                  a line suffix `:483`
  3. cite       — explicit cross-decision references like
                  `decision:0004`, `decision_id:0004`, or bare
                  `0004-2026-...` filenames

Returned triples: `(source_id, target_id, edge_type)`.

Source IDs are the decision frontmatter `id` (zero-padded 4-digit);
fallback to slug when id is missing. Target IDs follow the same rule.
For path edges the target is the absolute-relative path string
(prefixed `path:` so it doesn't collide with decision ids).

Tolerance contract: broken links are dropped silently. The whole point
is *additive* graph signal; a missing target should never fail the
parse. Errors reading a single file are logged to stderr and that file
is skipped.

Public API:
    parse_decisions_dir(root: Path) -> list[Edge]
    parse_file(path: Path, known_ids: set[str]) -> list[Edge]

Edges:
    Edge = NamedTuple('Edge', [('source', str), ('target', str), ('edge_type', str)])

Used by `scripts/recall_graph.py`.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterable, NamedTuple


class Edge(NamedTuple):
    source: str
    target: str
    edge_type: str  # "wikilink" | "path" | "cite"


# Frontmatter `id:` value. We accept quoted ('0007') and unquoted (0007).
_ID_RE = re.compile(r"^id\s*:\s*['\"]?([A-Za-z0-9_-]+)['\"]?\s*$", re.MULTILINE)
# Frontmatter `slug:`.
_SLUG_RE = re.compile(r"^slug\s*:\s*['\"]?([A-Za-z0-9_.-]+)['\"]?\s*$", re.MULTILINE)
# Filename pattern like `0007-2026-05-05-some-slug.md` — the `0007` is the id.
_FILENAME_ID_RE = re.compile(r"^(\d{4})-(\d{4}-\d{2}-\d{2})-")

# Wikilink: [[target]] OR [[target|alias]] OR [[target#anchor]]. Target
# may contain dashes/underscores/dots but no spaces (we strip on whitespace).
_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")

# File-path mention: anything that looks like a relative path with at
# least one slash and a known extension. Optionally followed by `:NNN`
# line suffix. We deliberately keep this conservative — bare words like
# "scripts" without a slash don't match.
_PATH_RE = re.compile(
    r"\b(?P<path>[A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+\.(?:py|md|sql|ts|tsx|js|jsx|json|yaml|yml|toml|sh|swift))"
    r"(?::\d+)?\b"
)

# Cross-decision reference. Two shapes:
#   1. `decision:0004` (or `decision_id:0004`)
#   2. bare `0004-` at start of a wikilink target body or in plain text
#      adjacent to ".md" (caught by the filename id regex below).
_CITE_RE = re.compile(r"\bdecision(?:_id)?\s*:\s*['\"]?(\d{4})['\"]?")


def _safe_read(path: Path) -> str:
    """Read the file. On error, return empty string (silent skip)."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"[markdown_graph_parser] skip {path}: {e}", file=sys.stderr)
        return ""


def _frontmatter_block(text: str) -> str:
    """Return the YAML frontmatter as a string, or empty if none.

    Handles the `---\\nKEY: VAL\\n...\\n---` shape used by every
    .episodic/decisions/*.md. We don't need a full YAML parser here —
    only the `id` and `slug` lines, which are flat scalars.
    """
    if not text.startswith("---"):
        return ""
    # Find the closing fence on a line by itself.
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    return text[3:end]


def _file_id(path: Path, text: str) -> str | None:
    """Resolve a stable id for the file.

    Priority:
      1. frontmatter `id:` field (decisions)
      2. filename id pattern `NNNN-YYYY-MM-DD-...` (decisions, fallback)
      3. frontmatter `slug:` field (decisions, last resort)
      4. relative file stem prefixed `file:` (feedback.md, MEMORY.md, etc.)

    The id namespace is shared with target ids — wikilinks resolve into
    the same space.
    """
    fm = _frontmatter_block(text)
    if fm:
        m = _ID_RE.search(fm)
        if m:
            return m.group(1)
        m = _FILENAME_ID_RE.match(path.name)
        if m:
            return m.group(1)
        m = _SLUG_RE.search(fm)
        if m:
            return f"slug:{m.group(1)}"
    # Non-decision files (feedback.md, MEMORY.md, etc.) — namespace
    # under `file:` so they don't collide with decision ids.
    return f"file:{path.stem}"


def _strip_frontmatter(text: str) -> str:
    """Return body only (no YAML frontmatter)."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    body_start = end + 4  # past `\n---`
    return text[body_start:]


def parse_file(path: Path, known_ids: set[str]) -> list[Edge]:
    """Extract edges from a single markdown file.

    `known_ids` is the set of all source ids in the corpus, used to
    resolve wikilinks. Wikilinks pointing at unknown targets are dropped
    silently (per the broken-link tolerance contract).
    """
    text = _safe_read(path)
    if not text:
        return []

    source_id = _file_id(path, text)
    if not source_id:
        return []

    body = _strip_frontmatter(text)
    edges: list[Edge] = []

    # 1. Wikilinks.
    for m in _WIKILINK_RE.finditer(body):
        target_raw = m.group(1).strip()
        # Remove a `.md` suffix if present (Obsidian-style).
        if target_raw.endswith(".md"):
            target_raw = target_raw[:-3]
        # Wikilink may name a decision id directly (`[[0007]]`) or a slug.
        if target_raw in known_ids:
            target = target_raw
        elif f"slug:{target_raw}" in known_ids:
            target = f"slug:{target_raw}"
        else:
            # Unknown target — drop silently.
            continue
        if target == source_id:
            # Self-loop: drop.
            continue
        edges.append(Edge(source=source_id, target=target, edge_type="wikilink"))

    # 2. Path mentions. These don't need to resolve to a corpus id —
    # they're useful as a join surface for path-aware queries
    # ("find decisions touching scripts/recall.py"). We namespace
    # path targets so they never collide with decision ids.
    seen_paths: set[str] = set()
    for m in _PATH_RE.finditer(body):
        p = m.group("path").strip()
        # Skip absolute paths (likely log lines / URLs / etc.) and
        # anything starting with a dot-only prefix.
        if p.startswith("/") or p.startswith("./"):
            p = p.lstrip("./")
        target = f"path:{p}"
        if target in seen_paths:
            continue
        seen_paths.add(target)
        edges.append(Edge(source=source_id, target=target, edge_type="path"))

    # 3. Explicit decision citations.
    for m in _CITE_RE.finditer(body):
        target_id = m.group(1)
        if target_id == source_id:
            continue
        if target_id not in known_ids:
            # Citation might be from before the target was renumbered.
            continue
        edges.append(Edge(source=source_id, target=target_id, edge_type="cite"))

    return edges


def _expand_extra_paths(extra: Iterable[Path] | None) -> list[Path]:
    if not extra:
        return []
    out: list[Path] = []
    for p in extra:
        if p.is_dir():
            out.extend(sorted(p.glob("*.md")))
        elif p.is_file():
            out.append(p)
    return out


def parse_decisions_dir(
    root: Path,
    *,
    extra_paths: Iterable[Path] | None = None,
) -> list[Edge]:
    """Parse a `.episodic/decisions/` directory into a flat edge list.

    Args:
      root:        the decisions directory, e.g. `.episodic/decisions/`
      extra_paths: optional additional files or directories to include
                   (e.g. `.build-loop/feedback.md`, the user's MEMORY.md).
                   Directories are expanded to `*.md`.

    Returns the deduplicated edge list. Order is stable (sort key:
    source, edge_type, target) so callers that snapshot the edge set
    for tests get reproducible output.
    """
    files: list[Path] = []
    if root.is_dir():
        # Skip INDEX.md (it's a generated rollup, not a source decision)
        # and any underscore-prefixed dirs (history / review).
        for p in sorted(root.glob("*.md")):
            if p.name == "INDEX.md":
                continue
            files.append(p)
    files.extend(_expand_extra_paths(extra_paths))

    # Two-pass: first compute the id namespace, then resolve wikilinks
    # against it.
    file_texts: dict[Path, str] = {}
    known_ids: set[str] = set()
    for p in files:
        text = _safe_read(p)
        if not text:
            continue
        file_texts[p] = text
        fid = _file_id(p, text)
        if fid:
            known_ids.add(fid)
            # Also accept the slug as a secondary key when present.
            fm = _frontmatter_block(text)
            if fm:
                slug_m = _SLUG_RE.search(fm)
                if slug_m:
                    known_ids.add(f"slug:{slug_m.group(1)}")

    edges: list[Edge] = []
    for p, text in file_texts.items():
        edges.extend(parse_file(p, known_ids))

    # Dedup edges (source, target, edge_type) and sort for stability.
    unique = sorted(set(edges))
    return unique


def main(argv: list[str] | None = None) -> int:
    """Print parsed edges as TSV. Useful for spot-checking from the CLI.

    Usage:
      python3 scripts/markdown_graph_parser.py [.episodic/decisions]
    """
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(".episodic/decisions")
    extra: list[Path] = []
    feedback = Path(".build-loop/feedback.md")
    if feedback.exists():
        extra.append(feedback)
    # User-level memory dirs are *not* loaded by default to avoid
    # leaking cross-project content into the per-repo graph. Pass
    # --include-user-memory to opt in.
    if "--include-user-memory" in (argv or sys.argv[1:]):
        user_memory = Path(os.path.expanduser(
            "~/.claude/projects/-Users-tyroneross/memory"
        ))
        if user_memory.exists():
            extra.append(user_memory)

    edges = parse_decisions_dir(root, extra_paths=extra)
    for e in edges:
        print(f"{e.source}\t{e.target}\t{e.edge_type}")
    print(f"\n# {len(edges)} edges from {root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
