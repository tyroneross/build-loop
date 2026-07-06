"""Tests for scripts/wiki_client.py — Phase C wiki federation.

Two layers:
  1. Pure-parser unit tests over canned llmwiki output samples.
  2. Subprocess routing tests with the CLI mocked via monkeypatch.
  3. Real-CLI integration test gated on the llmwiki binary existing
     at the default vault path AND a query reliably hitting a known
     wiki page (concept-hybrid-search-pgvector-fts-rrf).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import wiki_client  # noqa: E402
from wiki_client import (  # noqa: E402
    DEFAULT_CLI,
    is_available,
    parse_search_output,
    wiki_search,
)


# ---------------------------------------------------------------------------
# Sample CLI output (captured 2026-04 from a working wiki)
# ---------------------------------------------------------------------------

SAMPLE_OUTPUT = """\
query: 'hybrid search BM25 vector RRF'
provider: ollama / nomic-embed-text  ·  --walk-graph (α=0.85)
top 3 pages from 1760 chunks  ·  graph: 198 nodes / 1114 edges

1. [ppr 0.013 | cos 0.622 | lex 2.470] concept-hybrid-search-pgvector-fts-rrf § Tradeoffs  (graph)
   wiki/concepts/semantic-search/concept-hybrid-search-pgvector-fts-rrf.md
   **What you gain by going hybrid (vs pure dense or pure sparse):** - Higher recall on mixed-intent queries — sparse catches exact entity / model-name / error-string hits that paraphrase embeddings miss; dense catches conc...

2. [ppr 0.012 | cos 0.580 | lex 1.930] concept-semantic-search § Tradeoffs  (graph)
   wiki/concepts/semantic-search/concept-semantic-search.md
   **What you gain by choosing semantic (dense) retrieval as the retrieval base:** - Paraphrase and conceptual tolerance — queries that share meaning with a document surface it even when no surface terms overlap. This is th...

3. [ppr 0.005 | cos 0.667 | lex 2.260] [seedling] concept-vector-vs-keyword-search § Implementation notes  (graph)
   wiki/concepts/concept-vector-vs-keyword-search.md
   Example-app news picks hybrid (vector + BM25 via pgvector + PostgreSQL FTS + RRF) because news articles combine topical prose (semantic query) with named entities (exact-match query). An article about "the Fed raising rates"...
"""

SAMPLE_OUTPUT_ROOT_PAGE = """\
query: 'foo'
provider: ollama / nomic-embed-text  ·  --walk-graph (α=0.85)
top 1 pages from 100 chunks

1. [ppr 0.001 | cos 0.500 | lex 1.000] page-no-section  (vector)
   wiki/page-no-section.md
   Bare excerpt without a section header.
"""


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_three_results_with_sections():
    results = parse_search_output(SAMPLE_OUTPUT)
    assert len(results) == 3
    r0 = results[0]
    assert r0["subject"] == "concept-hybrid-search-pgvector-fts-rrf"
    assert r0["predicate"] == "Tradeoffs"
    assert r0["id"] == "wiki:concept-hybrid-search-pgvector-fts-rrf#Tradeoffs"
    assert r0["wiki_path"] == "wiki/concepts/semantic-search/concept-hybrid-search-pgvector-fts-rrf.md"
    assert r0["source"] == "wiki"
    assert abs(r0["ppr"] - 0.013) < 1e-6
    assert abs(r0["cos"] - 0.622) < 1e-6
    assert abs(r0["lex"] - 2.470) < 1e-6
    assert r0["score"] == r0["cos"]
    assert "hybrid" in r0["object"].lower()


def test_parse_handles_seedling_prefix():
    results = parse_search_output(SAMPLE_OUTPUT)
    seedling = results[2]
    assert seedling["subject"] == "concept-vector-vs-keyword-search"


def test_parse_root_page_no_section():
    results = parse_search_output(SAMPLE_OUTPUT_ROOT_PAGE)
    assert len(results) == 1
    assert results[0]["predicate"] == ""
    assert results[0]["id"] == "wiki:page-no-section"


def test_parse_empty_output():
    assert parse_search_output("") == []
    assert parse_search_output("query: 'foo'\nprovider: x\n") == []


def test_parse_truncates_long_excerpt():
    long_excerpt = "x" * 1000
    text = (
        "1. [ppr 0.001 | cos 0.5 | lex 1.0] page  (vector)\n"
        "   wiki/page.md\n"
        f"   {long_excerpt}\n"
    )
    results = parse_search_output(text)
    assert len(results) == 1
    assert len(results[0]["object"]) <= wiki_client.EXCERPT_MAX_CHARS


# ---------------------------------------------------------------------------
# Subprocess routing
#
# Phase I added an in-process `wiki_local` short-circuit that runs first
# when the real vault is present on disk. These tests explicitly target
# the subprocess fallback path, so they pin WIKI_FORCE_SUBPROCESS=1 to
# bypass the in-process route. The wiki_local path has its own coverage
# in tests/test_wiki_local.py.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def _force_subprocess(monkeypatch):
    monkeypatch.setenv("WIKI_FORCE_SUBPROCESS", "1")


def test_wiki_search_returns_empty_on_missing_cli(monkeypatch, _force_subprocess):
    monkeypatch.setenv(wiki_client.ENV_CLI, "/nonexistent/path/to/llmwiki")
    monkeypatch.setattr(wiki_client.shutil, "which", lambda _: None)
    assert wiki_search("anything") == []


def test_wiki_search_returns_empty_on_nonzero_exit(monkeypatch, tmp_path, _force_subprocess):
    fake_cli = tmp_path / "fake_llmwiki"
    fake_cli.write_text("#!/bin/sh\nexit 3\n")
    fake_cli.chmod(0o755)
    monkeypatch.setenv(wiki_client.ENV_CLI, str(fake_cli))
    # Bypass the help-probe-based availability cache.
    wiki_client._AVAILABILITY_CACHE.clear()
    assert wiki_search("anything") == []


def test_wiki_search_returns_empty_on_empty_query():
    assert wiki_search("") == []
    assert wiki_search("   ") == []


def test_wiki_search_returns_empty_on_timeout(monkeypatch, tmp_path, _force_subprocess):
    fake_cli = tmp_path / "fake_llmwiki"
    fake_cli.write_text("#!/bin/sh\nsleep 5\n")
    fake_cli.chmod(0o755)
    monkeypatch.setenv(wiki_client.ENV_CLI, str(fake_cli))
    wiki_client._AVAILABILITY_CACHE.clear()
    assert wiki_search("query", timeout_s=0.1) == []


def test_wiki_search_parses_real_subprocess_output(monkeypatch, tmp_path, _force_subprocess):
    """End-to-end: a fake CLI that prints SAMPLE_OUTPUT must yield 3 results."""
    fake_cli = tmp_path / "fake_llmwiki"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"print({SAMPLE_OUTPUT!r})\n"
    )
    fake_cli.chmod(0o755)
    monkeypatch.setenv(wiki_client.ENV_CLI, str(fake_cli))
    wiki_client._AVAILABILITY_CACHE.clear()
    results = wiki_search("hybrid search BM25 vector RRF", k=3)
    assert len(results) == 3
    assert results[0]["subject"] == "concept-hybrid-search-pgvector-fts-rrf"


def test_is_available_false_for_missing_cli(monkeypatch, tmp_path):
    monkeypatch.setenv(wiki_client.ENV_CLI, str(tmp_path / "nope"))
    monkeypatch.setattr(wiki_client.shutil, "which", lambda _: None)
    wiki_client._AVAILABILITY_CACHE.clear()
    assert is_available() is False


def test_default_cli_uses_current_home(monkeypatch, tmp_path):
    monkeypatch.delenv(wiki_client.ENV_CLI, raising=False)
    monkeypatch.setattr(wiki_client.shutil, "which", lambda _: None)
    cli = tmp_path / "ObsidianVault" / "tools" / "scripts" / "llmwiki"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\nexit 0\n")
    cli.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    wiki_client._AVAILABILITY_CACHE.clear()
    assert wiki_client._resolve_cli() == str(cli)


# ---------------------------------------------------------------------------
# Integration test: real wiki CLI on the developer machine
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_real_wiki_cli_returns_hybrid_search_page():
    """Phase C acceptance gate: a query for 'hybrid search BM25 vector'
    against the real wiki CLI MUST surface the
    concept-hybrid-search-pgvector-fts-rrf page in the top results.
    """
    cli_path = Path(DEFAULT_CLI).expanduser()
    if not cli_path.exists() or not os.access(cli_path, os.X_OK):
        pytest.skip(f"wiki CLI not present at {cli_path}; integration skipped")
    results = wiki_search("hybrid search BM25 vector RRF", k=5)
    assert results, "wiki returned no results — vector store may be down"
    subjects = [r["subject"] for r in results]
    assert "concept-hybrid-search-pgvector-fts-rrf" in subjects, (
        f"expected concept-hybrid-search-pgvector-fts-rrf in top-5 wiki "
        f"results; got {subjects}"
    )
    # Provenance + path tags must be set so recall.py can render them.
    target = next(r for r in results if r["subject"] == "concept-hybrid-search-pgvector-fts-rrf")
    assert target["source"] == "wiki"
    assert target["wiki_path"].endswith(".md")
    assert target["score"] > 0
