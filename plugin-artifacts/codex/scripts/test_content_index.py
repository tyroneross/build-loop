"""Tests for the body-only markdown FTS index."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import content_index


class ContentIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Path(self.tempdir.name) / "memory"
        self.store.mkdir()
        self.db = self.store / "indexes" / "test-content.sqlite"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, relative: str, body: str) -> Path:
        path = self.store / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def build(self, **kwargs: object) -> dict:
        return content_index.build(self.store, db_path=self.db, **kwargs)

    def search(self, text: str, **kwargs: object) -> list[dict]:
        return content_index.query(text, db_path=self.db, **kwargs)

    def test_body_only_match_and_row_shape(self) -> None:
        self.write("plain-note.md", "The heliotropic marker appears only in this body.")
        self.build()
        rows = self.search("heliotropic")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "plain-note")

    def test_returned_row_has_exact_required_keys(self) -> None:
        self.write("shape.md", "shapeneedle")
        self.build()
        row = self.search("shapeneedle")[0]
        self.assertEqual(set(row), {
            "_kind", "_scope", "_recency_ts", "id", "name", "title", "path",
            "_relevance", "snippet",
        })

    def test_incremental_reindexes_only_touched_file(self) -> None:
        changed = self.write("changed.md", "incremental sample")
        self.write("unchanged.md", "incremental sample")
        self.build()
        self.assertEqual(self.build()["indexed"], 0)
        stat = changed.stat()
        os.utime(changed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertEqual(self.build()["indexed"], 1)

    def test_deleted_file_is_removed_and_counted(self) -> None:
        doomed = self.write("doomed.md", "vanishingneedle")
        self.build()
        doomed.unlink()
        result = self.build()
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(self.search("vanishingneedle"), [])

    def test_bm25_sign_is_positive_and_repeated_term_outranks(self) -> None:
        self.write("single.md", "rankneedle")
        self.write("repeated.md", " ".join(["rankneedle"] * 20))
        self.build()
        rows = self.search("rankneedle")
        self.assertTrue(all(row["_relevance"] > 0 for row in rows))
        self.assertEqual(rows[0]["id"], "repeated")
        self.assertGreater(rows[0]["_relevance"], rows[1]["_relevance"])

    def test_porter_stemming_matches_migrations(self) -> None:
        self.write("plural.md", "Completed several migrations successfully.")
        self.build()
        self.assertEqual([row["id"] for row in self.search("migration")], ["plural"])

    def test_skip_directories_are_not_indexed(self) -> None:
        self.write("archive/old.md", "forbiddenarchiveword")
        self.write("node_modules/pkg/readme.md", "forbiddennodeword")
        self.write("oversized.md", "x" * ((1 << 20) + 1))
        self.write("included.md", "includedword")
        result = self.build()
        self.assertGreaterEqual(result["skipped"], 1)
        self.assertEqual(self.search("forbiddenarchiveword"), [])
        self.assertEqual(self.search("forbiddennodeword"), [])
        self.assertEqual([row["id"] for row in self.search("includedword")], ["included"])

    def test_project_filter_includes_global_and_named_project(self) -> None:
        self.write("global.md", "scopedneedle global")
        self.write("projects/alpha/a.md", "scopedneedle alpha")
        self.write("projects/beta/b.md", "scopedneedle beta")
        self.build()
        rows = self.search("scopedneedle", project="alpha")
        self.assertEqual({row["_scope"] for row in rows}, {"global", "alpha"})

    def test_frontmatter_title_is_metadata_not_body(self) -> None:
        self.write(
            "frontmatter.md",
            "---\ntitle: Presentation Heading\ntags: frontmatterneedle\n---\nvisiblebodyneedle",
        )
        self.build()
        rows = self.search("visiblebodyneedle")
        self.assertEqual(rows[0]["title"], "Presentation Heading")
        self.assertEqual(self.search("frontmatterneedle"), [])

    def test_missing_database_is_empty(self) -> None:
        self.assertEqual(content_index.query("anything", db_path=self.store / "missing.sqlite"), [])

    def test_multiword_query_uses_or_semantics(self) -> None:
        terms = "deployment blockers migration ledger reconciliation dependency security"
        self.write("first.md", "deployment blockers migration")
        self.write("second.md", "ledger reconciliation dependency security")
        self.build()

        self.assertEqual({row["id"] for row in self.search(terms)}, {"first", "second"})

    def test_bm25_ranks_broader_query_coverage_first(self) -> None:
        terms = "deployment blockers migration ledger reconciliation dependency security"
        self.write("two-hits.md", "dependency security")
        self.write("five-hits.md", "deployment blockers migration ledger reconciliation")
        self.build()

        rows = self.search(terms)
        self.assertEqual([row["id"] for row in rows[:2]], ["five-hits", "two-hits"])

    def test_hyphenated_query_is_tokenized_without_fts_syntax_error(self) -> None:
        self.write("hyphen.md", "decision project build loop thing")
        self.build()

        self.assertIn(
            "hyphen",
            [row["id"] for row in self.search("decision-project-build-loop-thing")],
        )

    def test_double_quote_and_bare_operator_are_safe_terms(self) -> None:
        self.write("quoted.md", "quote marker plain operator sentinel")
        self.build()

        self.assertIn("quoted", [row["id"] for row in self.search('quote"marker')])
        self.assertIn("quoted", [row["id"] for row in self.search("operator AND sentinel")])

    def test_punctuation_or_whitespace_query_is_empty_without_database_access(self) -> None:
        with patch("content_index.sqlite3.connect") as connect:
            self.assertEqual(self.search("  \t\n  "), [])
            self.assertEqual(self.search("-\"*:^^"), [])
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
