# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Compact, human-readable semantic annotations for code navigation."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


_COMMENT_PREFIXES = ("<!--", "//", "/*", "#", "--", "*")
_COMMENT_SUFFIXES = ("-->", "*/")
_KIND_RE = re.compile(r"[a-z][a-z0-9-]*\Z")
_QUERY_TOKEN_RE = re.compile(r"[a-z0-9_-]+")
_SOURCE_SUFFIXES = frozenset({
    ".bash", ".c", ".cc", ".cjs", ".cpp", ".cs", ".css", ".fish",
    ".go", ".h", ".hpp", ".java", ".js", ".jsx", ".kt", ".kts",
    ".less", ".mjs", ".php", ".py", ".pyi", ".rb", ".rs", ".sass",
    ".scss", ".sh", ".sql", ".svelte", ".swift", ".ts", ".tsx",
    ".vue", ".zsh",
})


@dataclass(frozen=True)
class CodeAnnotation:
    kind: str
    summary: str
    keywords: tuple[str, ...]
    file: str
    line: int

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["keywords"] = list(self.keywords)
        return payload


def parse_annotation_line(line: str, *, file: str, line_number: int) -> CodeAnnotation | None:
    """Parse a standalone `BL:<kind> | <summary> | <keywords>` comment."""
    stripped = line.strip()
    prefix = next((item for item in _COMMENT_PREFIXES if stripped.startswith(item)), None)
    if prefix is None:
        return None
    body = stripped[len(prefix):].strip()
    for suffix in _COMMENT_SUFFIXES:
        if body.endswith(suffix):
            body = body[:-len(suffix)].rstrip()
            break
    if not body.startswith("BL:"):
        return None
    parts = [part.strip() for part in body[3:].split("|", 2)]
    if len(parts) < 2:
        return None
    kind, summary = parts[:2]
    if not _KIND_RE.fullmatch(kind) or not summary:
        return None
    raw_keywords = parts[2] if len(parts) == 3 else ""
    keywords = tuple(dict.fromkeys(
        keyword.strip().casefold()
        for keyword in raw_keywords.split(",")
        if keyword.strip()
    ))
    return CodeAnnotation(
        kind=kind,
        summary=summary,
        keywords=keywords,
        file=file,
        line=line_number,
    )


def scan_annotations(repo_root: Path | str, files: Iterable[str]) -> list[CodeAnnotation]:
    """Index annotations from the architecture scanner's source inventory."""
    repo = Path(repo_root).resolve()
    found: list[CodeAnnotation] = []
    for relative in sorted(set(files)):
        rel_path = Path(relative)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            continue
        if rel_path.suffix.casefold() not in _SOURCE_SUFFIXES:
            continue
        path = repo / rel_path
        if path.is_symlink() or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            annotation = parse_annotation_line(
                line,
                file=relative.replace("\\", "/"),
                line_number=line_number,
            )
            if annotation is not None:
                found.append(annotation)
    return found


def build_annotation_index(annotations: Sequence[CodeAnnotation]) -> dict[str, object]:
    """Return deterministic JSON-ready annotation data."""
    ordered = sorted(annotations, key=lambda item: (item.file, item.line, item.kind))
    kinds: dict[str, int] = {}
    for annotation in ordered:
        kinds[annotation.kind] = kinds.get(annotation.kind, 0) + 1
    return {
        "format": "BL:<kind> | <human-readable summary> | <comma-separated keywords>",
        "annotation_count": len(ordered),
        "kinds": dict(sorted(kinds.items())),
        "annotations": [annotation.to_dict() for annotation in ordered],
    }


def find_annotations(
    annotations: Sequence[CodeAnnotation] | Sequence[dict[str, object]],
    query: str,
) -> list[dict[str, object]]:
    """Match all query terms against kind, summary, keywords, and file."""
    terms = _QUERY_TOKEN_RE.findall(query.casefold())
    if not terms:
        return []
    matches: list[dict[str, object]] = []
    for item in annotations:
        payload = item.to_dict() if isinstance(item, CodeAnnotation) else dict(item)
        keywords = payload.get("keywords", [])
        keyword_text = " ".join(str(value) for value in keywords) if isinstance(keywords, list) else ""
        haystack = " ".join((
            str(payload.get("kind", "")),
            str(payload.get("summary", "")),
            keyword_text,
            str(payload.get("file", "")),
        )).casefold()
        if all(term in haystack for term in terms):
            matches.append(payload)
    return sorted(matches, key=lambda item: (str(item.get("file", "")), int(item.get("line", 0))))


# BL:purpose | Index concise human intent that structural scanners cannot infer | annotations,navigation
