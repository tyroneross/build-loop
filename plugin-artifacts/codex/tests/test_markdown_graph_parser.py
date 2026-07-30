"""Tests for scripts/markdown_graph_parser.py.

Covers:
  - Wikilink parsing (square-bracket variants, missing targets, anchors,
    aliases, .md suffixes)
  - Path-mention parsing (relative, with line numbers like recall.py:483,
    skipping bare words without slashes)
  - decision:NNNN citations
  - Frontmatter id resolution + slug fallback + filename id fallback
  - Self-loop suppression
  - Broken-link tolerance (unknown wikilink targets dropped silently)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from markdown_graph_parser import (  # noqa: E402
    Edge,
    parse_decisions_dir,
    parse_file,
)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _decision(idx: str, slug: str, body: str) -> str:
    return (
        "---\n"
        f"id: '{idx}'\n"
        f"slug: {slug}\n"
        "title: test\n"
        "type: decision\n"
        "status: accepted\n"
        "confidence: explicit\n"
        "date: '2026-05-06'\n"
        "primary_tag: meta\n"
        "---\n\n" + body
    )


# ---------------------------------------------------------------------------
# Wikilinks
# ---------------------------------------------------------------------------

def test_wikilink_resolves_to_known_id(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision("0001", "a", "See [[0002]] for details."))
    _write(d / "0002-2026-05-06-b.md", _decision("0002", "b", "Body."))
    edges = parse_decisions_dir(d)
    assert Edge(source="0001", target="0002", edge_type="wikilink") in edges


def test_wikilink_with_alias_and_anchor(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a", "See [[0002#Alternatives|here]] for the alt analysis."
    ))
    _write(d / "0002-2026-05-06-b.md", _decision("0002", "b", "Body."))
    edges = parse_decisions_dir(d)
    assert Edge(source="0001", target="0002", edge_type="wikilink") in edges


def test_wikilink_with_md_suffix(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision("0001", "a", "Cross [[0002.md]]."))
    _write(d / "0002-2026-05-06-b.md", _decision("0002", "b", "Body."))
    edges = parse_decisions_dir(d)
    assert any(
        e.target == "0002" and e.edge_type == "wikilink" for e in edges
    )


def test_wikilink_to_unknown_target_dropped(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision("0001", "a", "See [[9999-doesnotexist]]."))
    edges = parse_decisions_dir(d)
    assert not any(e.edge_type == "wikilink" for e in edges)


def test_wikilink_self_loop_dropped(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision("0001", "a", "Look at [[0001]]."))
    edges = parse_decisions_dir(d)
    assert not any(e.edge_type == "wikilink" for e in edges)


def test_wikilink_resolves_via_slug(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a-slug", "See [[b-slug]] for context."
    ))
    _write(d / "0002-2026-05-06-b.md", _decision("0002", "b-slug", "Body."))
    edges = parse_decisions_dir(d)
    # Target may be either '0002' or 'slug:b-slug' depending on resolution
    # order; we accept the slug form.
    assert any(
        e.source == "0001"
        and e.edge_type == "wikilink"
        and e.target in ("0002", "slug:b-slug")
        for e in edges
    )


# ---------------------------------------------------------------------------
# Path mentions
# ---------------------------------------------------------------------------

def test_path_mention_extracted(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a", "Touched scripts/recall.py and src/build_loop/architecture/foo.py."
    ))
    edges = parse_decisions_dir(d)
    targets = {e.target for e in edges if e.edge_type == "path"}
    assert "path:scripts/recall.py" in targets
    assert "path:src/build_loop/architecture/foo.py" in targets


def test_path_mention_with_line_number(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a", "See scripts/recall.py:483 for the bug site."
    ))
    edges = parse_decisions_dir(d)
    targets = {e.target for e in edges if e.edge_type == "path"}
    # Line number must be stripped — target is the file path only.
    assert "path:scripts/recall.py" in targets


def test_bare_word_no_slash_not_a_path(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a", "The word recall is fine. recall.py too because no slash."
    ))
    edges = parse_decisions_dir(d)
    assert not any(e.edge_type == "path" for e in edges)


def test_path_mention_dedupe_within_file(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a",
        "Edit scripts/recall.py. Now scripts/recall.py again. And once more scripts/recall.py."
    ))
    edges = [e for e in parse_decisions_dir(d) if e.edge_type == "path"]
    targets = [e.target for e in edges]
    # One source, one target → exactly one edge.
    assert targets == ["path:scripts/recall.py"]


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

def test_decision_citation_extracted(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a", "Builds on decision:0002 from last week."
    ))
    _write(d / "0002-2026-05-06-b.md", _decision("0002", "b", "Body."))
    edges = parse_decisions_dir(d)
    assert Edge(source="0001", target="0002", edge_type="cite") in edges


def test_decision_id_alias_citation(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a", "Per decision_id:0002 we ship the change."
    ))
    _write(d / "0002-2026-05-06-b.md", _decision("0002", "b", "Body."))
    edges = parse_decisions_dir(d)
    assert Edge(source="0001", target="0002", edge_type="cite") in edges


def test_citation_to_unknown_id_dropped(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a", "See decision:9999 (which never existed)."
    ))
    edges = parse_decisions_dir(d)
    assert not any(e.edge_type == "cite" for e in edges)


# ---------------------------------------------------------------------------
# Frontmatter id resolution
# ---------------------------------------------------------------------------

def test_filename_id_fallback_when_no_frontmatter_id(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    body = (
        "---\n"
        "title: t\n"
        "type: decision\n"
        "---\n\n"
        "Touched scripts/foo.py."
    )
    _write(d / "0007-2026-05-06-no-id.md", body)
    edges = parse_decisions_dir(d)
    # Source id falls through to the filename's NNNN.
    assert any(e.source == "0007" for e in edges)


def test_index_md_skipped(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "INDEX.md", "# Some index\n\nTouched scripts/foo.py.\n")
    _write(d / "0001-2026-05-06-a.md", _decision("0001", "a", "Body."))
    edges = parse_decisions_dir(d)
    # INDEX.md must not contribute edges.
    assert not any("path:scripts/foo.py" == e.target for e in edges)


# ---------------------------------------------------------------------------
# Stability
# ---------------------------------------------------------------------------

def test_edge_list_is_sorted_and_deduped(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    _write(d / "0001-2026-05-06-a.md", _decision(
        "0001", "a", "[[0002]] [[0002]] decision:0002 scripts/x/y.py scripts/x/y.py"
    ))
    _write(d / "0002-2026-05-06-b.md", _decision("0002", "b", "."))
    edges = parse_decisions_dir(d)
    # Sorted by NamedTuple ordering and deduped.
    assert edges == sorted(set(edges))
    # Wikilink+cite both present (same target, different edge_type).
    assert sum(1 for e in edges if e.target == "0002") == 2


# ---------------------------------------------------------------------------
# Broken / unreadable file tolerance
# ---------------------------------------------------------------------------

def test_unreadable_file_does_not_crash(tmp_path: Path) -> None:
    d = tmp_path / "decisions"
    # Real, parseable file alongside a binary-looking file.
    _write(d / "0001-2026-05-06-a.md", _decision("0001", "a", "Body."))
    (d / "0002-binary.md").write_bytes(b"\xff\xfe\x00\x01")
    # Should not raise.
    edges = parse_decisions_dir(d)
    assert isinstance(edges, list)
