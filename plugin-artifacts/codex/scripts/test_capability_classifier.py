#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``capability_classifier``.

Runnable via ``python3 scripts/test_capability_classifier.py``. Uses
``unittest.TestCase`` because the project's pytest is broken (per the
build-loop guardrails). Bare ``def test_*`` functions would silently
skip — every assertion lives on a TestCase.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from capability_classifier import (  # noqa: E402
    CAPABILITY_SYNONYMS,
    classify,
    classify_envelope,
    extract_terms,
)


class ClassifyTests(unittest.TestCase):
    def test_semantic_search_query_classifies(self) -> None:
        tags = classify("build semantic search for the docs site")
        self.assertIn("semantic-search", tags)

    def test_rag_alias_classifies_as_semantic_search(self) -> None:
        # "rag" is a synonym for semantic-search in this fleet.
        tags = classify("add a RAG pipeline over our knowledge base")
        self.assertIn("semantic-search", tags)

    def test_unknown_intent_returns_empty(self) -> None:
        # Absence-tolerant: unknown -> [], never raises.
        self.assertEqual([], classify("buy groceries tomorrow"))

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual([], classify(""))
        self.assertEqual([], classify(None))  # type: ignore[arg-type]

    def test_multi_capability_emission(self) -> None:
        # A single intent can hit multiple capabilities — both must surface.
        tags = classify(
            "add OAuth login with rate limiting on the auth endpoints"
        )
        self.assertIn("auth", tags)
        self.assertIn("rate-limiting", tags)

    def test_max_capabilities_caps_output(self) -> None:
        text = (
            "build oauth login with rate limiting, observability tracing, "
            "and a vector search index over user content"
        )
        tags = classify(text, max_capabilities=2)
        self.assertLessEqual(len(tags), 2)

    def test_whole_word_boundary_prevents_research_match(self) -> None:
        # "research" must NOT trigger the "search" sub-phrase — only the
        # multi-word phrase "semantic search" should fire.
        tags = classify("research notes on what's new")
        self.assertNotIn("semantic-search", tags)

    def test_ranking_is_descending_then_alphabetical(self) -> None:
        # Build a text with one strong semantic-search hit + one weak auth hit.
        tags = classify(
            "vector search semantic search rag retrieval-augmented generation login"
        )
        self.assertIn("semantic-search", tags)
        # semantic-search should come first because of multiple long phrases.
        self.assertEqual("semantic-search", tags[0])


class ExtractTermsTests(unittest.TestCase):
    def test_dedupes_and_drops_short_tokens(self) -> None:
        terms = extract_terms("Build a semantic search for the the docs")
        # "a"/"to" dropped, deduped.
        self.assertIn("semantic", terms)
        self.assertIn("search", terms)
        self.assertEqual(len(terms), len(set(terms)))

    def test_empty_returns_empty(self) -> None:
        self.assertEqual([], extract_terms(""))


class EnvelopeTests(unittest.TestCase):
    def test_envelope_shape(self) -> None:
        env = classify_envelope("build semantic search across projects")
        self.assertIn("capabilities", env)
        self.assertIn("terms", env)
        self.assertIn("confidence", env)
        self.assertIn(env["confidence"], {"none", "low", "high"})

    def test_high_confidence_on_strong_signal(self) -> None:
        env = classify_envelope(
            "vector search semantic search rag retrieval-augmented generation"
        )
        self.assertEqual("high", env["confidence"])

    def test_none_confidence_on_unrelated_intent(self) -> None:
        env = classify_envelope("buy groceries tomorrow")
        self.assertEqual("none", env["confidence"])
        self.assertEqual([], env["capabilities"])


class CLITests(unittest.TestCase):
    def test_cli_returns_valid_json(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(HERE / "capability_classifier.py"),
             "build", "semantic", "search"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("semantic-search", payload["capabilities"])


class SynonymTableHygiene(unittest.TestCase):
    """Catch table-format breakage early — entries are str or (str, num)."""

    def test_synonyms_well_formed(self) -> None:
        for tag, entries in CAPABILITY_SYNONYMS.items():
            self.assertIsInstance(tag, str, tag)
            self.assertGreater(len(entries), 0, f"empty synonyms for {tag}")
            for e in entries:
                if isinstance(e, tuple):
                    self.assertEqual(2, len(e), f"{tag}: tuple must be (phrase, weight)")
                    phrase, weight = e
                    self.assertIsInstance(phrase, str)
                    self.assertIsInstance(weight, (int, float))
                else:
                    self.assertIsInstance(e, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
