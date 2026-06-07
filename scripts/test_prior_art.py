#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``prior_art`` (P4).

Seeds a temp ``build-loop-memory`` root with the target scenario from
``bl-memory-overhaul-plan``:

* ``atomize-news/lessons/semantic-search.md`` — prior impl write-up.
* ``atomize-news/decisions/postgres-pgvector.md`` — the "why".
* ``atomize-ai/lessons/rag-pipeline.md`` — second-project impl signal.
* ``atomize-ai/decisions/dense-over-keyword.md`` — second "why".
* ``aida/decisions/embedding-model-choice.md`` — third project, decision-only.

Then asserts that the prior-art digest for "build semantic search":
1. Surfaces ≥2 of those projects (cross-project scope).
2. Includes the decisions ("why") alongside the impls.
3. Stays under the configured digest size cap (compactness).
4. Empty memory → returns no prior art and does NOT block.
5. Excludes the current project from the digest.

Runnable via ``python3 scripts/test_prior_art.py``. Uses
``unittest.TestCase`` per the build-loop guardrails (pytest broken).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import prior_art  # noqa: E402


# --------------------------------------------------------------------------
# Helpers — seed a memory root with cross-project prior art.
# --------------------------------------------------------------------------

def _seed_decision(root: Path, project: str, slug: str, title: str, body: str, date: str) -> Path:
    d = root / "projects" / project / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"decision-project-{project}-{slug}-001.md"
    path.write_text(
        f"---\n"
        f"title: \"{title}\"\n"
        f"date: \"{date}\"\n"
        f"project: \"{project}\"\n"
        f"primary_tag: semantic-search\n"
        f"---\n"
        f"# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _seed_lesson(root: Path, project: str, slug: str, name: str, body: str) -> Path:
    d = root / "projects" / project / "lessons"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.md"
    path.write_text(
        f"---\n"
        f"name: \"{name}\"\n"
        f"description: \"{name}\"\n"
        f"type: lesson\n"
        f"created_at: \"2026-05-01T00:00:00Z\"\n"
        f"---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path


def _seed_target_scenario(root: Path) -> None:
    """Seed the 'build semantic search' scenario across 3 projects."""
    # atomize-news — full pair (impl + decision).
    _seed_lesson(
        root, "atomize-news", "semantic-search-impl",
        "atomize-news semantic search",
        "We built semantic search on top of pgvector for the article index. "
        "The vector index lives in Supabase Postgres; embeddings via bge-m3. "
        "RAG over the article corpus powers the article-recommend endpoint.",
    )
    _seed_decision(
        root, "atomize-news", "postgres-pgvector",
        "Use Postgres pgvector for semantic search",
        "We chose Postgres pgvector over a dedicated vector DB to keep one "
        "database. Tradeoffs: slower ANN at scale, but operational simplicity "
        "and SQL JOINs win for our < 5M-row corpus. Decision drives the "
        "semantic-search architecture.",
        "2026-03-15",
    )

    # atomize-ai — full pair.
    _seed_lesson(
        root, "atomize-ai", "rag-pipeline",
        "atomize-ai RAG pipeline",
        "RAG pipeline for chatbot grounding. Dense retrieval over knowledge "
        "base chunks, semantic search backed by an in-memory FAISS index "
        "rebuilt nightly from Postgres. retrieval-augmented generation.",
    )
    _seed_decision(
        root, "atomize-ai", "dense-over-keyword",
        "Dense retrieval beats keyword for RAG",
        "Earlier we used BM25 keyword search; switched to dense retrieval "
        "after A/B showed +40% answer-quality. semantic search now drives "
        "all chatbot grounding.",
        "2026-04-02",
    )

    # aida — decision-only project (still must surface).
    _seed_decision(
        root, "aida", "embedding-model-choice",
        "Pin embedding model to mxbai-embed-large-v1",
        "AIDA semantic search uses MLX mxbai-embed-large-v1 for 1024-dim "
        "embeddings. We pin the model so the vector index stays comparable "
        "across releases.",
        "2026-04-20",
    )

    # An unrelated project so we know cross-project filtering works.
    _seed_lesson(
        root, "wallpaper-maker", "color-picker",
        "wallpaper-maker color picker",
        "HSL color picker with eyedropper. No search involved.",
    )

    # Catch-all lane that MUST be excluded.
    _seed_lesson(
        root, "_unscoped", "junk",
        "noise",
        "should never appear in a cross-project digest",
    )


# --------------------------------------------------------------------------
# Tests.
# --------------------------------------------------------------------------

class TargetScenarioTests(unittest.TestCase):
    """The P4 acceptance scenario: cold 'build semantic search' task."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="bl_prior_art_")
        cls.root = Path(cls._tmp.name)
        _seed_target_scenario(cls.root)
        # Point the recall env so the dense tier (if present) uses this root.
        os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = str(cls.root)
        os.environ["AGENT_MEMORY_ROOT"] = str(cls.root)
        os.environ["BUILD_LOOP_MEMORY_ROOT"] = str(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()
        for var in ("BUILD_LOOP_MEMORY_STORE_ROOT", "AGENT_MEMORY_ROOT", "BUILD_LOOP_MEMORY_ROOT"):
            os.environ.pop(var, None)

    def _digest(self, query: str = "build semantic search across the docs site"):
        from capability_classifier import classify_envelope  # local import
        env = classify_envelope(query)
        return prior_art.build_prior_art(
            query=query,
            capabilities=env["capabilities"],
            current_project="some-new-cold-project",
            memory_root=self.root,
            terms=env["terms"],
        )

    def test_cross_project_scope_at_least_two_projects(self) -> None:
        digest = self._digest()
        projects = set(digest["stats"]["projects"])
        # ≥2 projects from the seed must surface (the DoD scenario).
        self.assertGreaterEqual(
            len(projects & {"atomize-news", "atomize-ai", "aida"}),
            2,
            f"expected ≥2 of atomize-news/atomize-ai/aida; got {projects}",
        )

    def test_decisions_surface_with_implementations(self) -> None:
        digest = self._digest()
        # The "why" — decisions list non-empty.
        self.assertGreater(
            len(digest["decisions"]), 0,
            "expected ≥1 prior decision; got none",
        )
        # Impls list non-empty (lessons or semantic).
        self.assertGreater(
            len(digest["implementations"]), 0,
            "expected ≥1 prior impl; got none",
        )

    def test_digest_size_under_cap(self) -> None:
        # Compact: under the configured total-char budget.
        cap = 1200
        digest = prior_art.build_prior_art(
            query="build semantic search across the docs site",
            capabilities=["semantic-search"],
            current_project="some-new-cold-project",
            memory_root=self.root,
            max_total_chars=cap,
        )
        self.assertLessEqual(len(digest["digest_text"]), cap + 64)

    def test_current_project_excluded(self) -> None:
        # Seed an entry IN atomize-news and treat it as the current project —
        # it must NOT appear in its own prior-art.
        digest = prior_art.build_prior_art(
            query="build semantic search",
            capabilities=["semantic-search"],
            current_project="atomize-news",
            memory_root=self.root,
        )
        projects = set(digest["stats"]["projects"])
        self.assertNotIn("atomize-news", projects)

    def test_unscoped_and_unsorted_excluded(self) -> None:
        digest = self._digest()
        projects = set(digest["stats"]["projects"])
        self.assertNotIn("_unscoped", projects)
        self.assertNotIn("_unsorted", projects)

    def test_digest_text_includes_why_header(self) -> None:
        digest = self._digest()
        self.assertIn("Prior Art Across Projects", digest["digest_text"])
        # Decisions block surfaces the "why" framing.
        self.assertIn("Decisions", digest["digest_text"])

    def test_decision_snippet_carries_why(self) -> None:
        """Each surfaced decision carries enough body to convey the 'why'."""
        digest = self._digest()
        joined = " ".join(d.get("snippet", "") for d in digest["decisions"])
        # The seeded decisions contain the rationale words: "chose", "switched",
        # "pin" — at least one rationale verb should land in the snippet text.
        rationale_signal = any(
            kw in joined.lower() for kw in ("chose", "tradeoff", "switched", "pin", "decision", "drive")
        )
        self.assertTrue(rationale_signal, f"decision snippets missing rationale signal: {joined[:200]}")


class AbsenceToleranceTests(unittest.TestCase):
    """Phase 1 NEVER blocks on missing prior art."""

    def test_empty_memory_returns_empty_digest_no_raise(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bl_pa_empty_") as tmp:
            os.environ["BUILD_LOOP_MEMORY_STORE_ROOT"] = tmp
            try:
                digest = prior_art.build_prior_art(
                    query="build semantic search",
                    capabilities=["semantic-search"],
                    current_project="some-cold-project",
                    memory_root=Path(tmp),
                )
            finally:
                os.environ.pop("BUILD_LOOP_MEMORY_STORE_ROOT", None)
        self.assertEqual([], digest["implementations"])
        self.assertEqual([], digest["decisions"])
        self.assertEqual(digest["digest_text"], "")
        # Reason recorded so the bootstrap can show the empty cause.
        self.assertIn("no_prior_art_found", digest["reasons"])

    def test_empty_capabilities_returns_empty_digest(self) -> None:
        # No classified capability → empty prior-art (the classifier missed).
        digest = prior_art.build_prior_art(
            query="buy groceries",
            capabilities=[],
            current_project="some-cold-project",
            memory_root=Path("/nonexistent/totally/missing/path"),
        )
        self.assertEqual([], digest["implementations"])
        self.assertEqual([], digest["decisions"])
        self.assertIn("no_capabilities_classified", digest["reasons"])

    def test_missing_memory_root_does_not_raise(self) -> None:
        # Even with a totally-bogus root, build_prior_art must not raise.
        digest = prior_art.build_prior_art(
            query="build semantic search",
            capabilities=["semantic-search"],
            current_project="some-cold-project",
            memory_root=Path("/nonexistent/totally/missing/path"),
        )
        # Empty payload is the expected graceful response.
        self.assertIsInstance(digest["implementations"], list)
        self.assertIsInstance(digest["decisions"], list)


class CapTests(unittest.TestCase):
    """Per-section + total caps are honored."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="bl_pa_cap_")
        self.root = Path(self._tmp.name)
        # Seed 6 lessons + 6 decisions across 3 projects so cap behavior fires.
        for proj in ("atomize-news", "atomize-ai", "aida"):
            for i in range(2):
                _seed_lesson(
                    self.root, proj, f"impl-{i}",
                    f"{proj} semantic search impl {i}",
                    "semantic search impl with vector index and dense retrieval, "
                    f"variant {i}. retrieval-augmented generation.",
                )
                _seed_decision(
                    self.root, proj, f"why-{i}",
                    f"Why {proj} chose semantic search {i}",
                    "Tradeoffs of vector search vs keyword for our corpus. "
                    f"variant {i}. semantic search rationale.",
                    "2026-04-01",
                )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_max_impls_caps_list(self) -> None:
        digest = prior_art.build_prior_art(
            query="build semantic search",
            capabilities=["semantic-search"],
            current_project="some-cold-project",
            memory_root=self.root,
            max_impls=3,
            max_per_capability=3,
        )
        self.assertLessEqual(len(digest["implementations"]), 3)

    def test_max_decisions_caps_list(self) -> None:
        digest = prior_art.build_prior_art(
            query="build semantic search",
            capabilities=["semantic-search"],
            current_project="some-cold-project",
            memory_root=self.root,
            max_decisions=2,
        )
        self.assertLessEqual(len(digest["decisions"]), 2)

    def test_total_chars_cap_truncation_signal(self) -> None:
        digest = prior_art.build_prior_art(
            query="build semantic search",
            capabilities=["semantic-search"],
            current_project="some-cold-project",
            memory_root=self.root,
            max_total_chars=400,
        )
        # The digest may be empty if the budget is tiny, but if non-empty it
        # must respect the budget within a small overhead.
        self.assertLessEqual(len(digest["digest_text"]), 480)


if __name__ == "__main__":
    unittest.main(verbosity=2)
