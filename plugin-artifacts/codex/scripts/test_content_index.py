"""Tests for the versioned markdown FTS index and structured query API."""
from __future__ import annotations

import os
import sqlite3
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

    def document(self, relative: str, *, title: str | None = None, body: str = "needle", **fields: str | list[str]) -> None:
        entries = []
        if title is not None:
            entries.append(f"title: {title}")
        for key, value in fields.items():
            if isinstance(value, list):
                entries.append(f"{key}:")
                entries.extend(f"  - {item}" for item in value)
            else:
                entries.append(f"{key}: {value}")
        self.write(relative, "---\n" + "\n".join(entries) + "\n---\n" + body)

    def build(self, **kwargs: object) -> dict:
        return content_index.build(self.store, db_path=self.db, **kwargs)

    def search(self, text: str, **kwargs: object) -> list[dict]:
        return content_index.query(text, db_path=self.db, **kwargs)

    def test_title_only_match_is_returned(self) -> None:
        self.document("title-only.md", title="Heliotropic migration", body="unrelated prose")
        self.build()
        self.assertEqual([row["id"] for row in self.search("heliotropic")], ["title-only"])

    def test_title_hit_outranks_body_hit(self) -> None:
        self.document("title.md", title="Rankneedle", body="ordinary prose")
        self.document("body.md", title="ordinary", body="rankneedle")
        self.build()
        self.assertEqual([row["id"] for row in self.search("rankneedle")][:2], ["title", "body"])

    def test_structured_type_filters_and_lists(self) -> None:
        self.document("decision.md", type="decision")
        self.document("lesson.md", type="lesson")
        self.build()
        self.assertEqual([row["id"] for row in self.search("needle", doc_type="decision")], ["decision"])
        self.assertEqual({row["id"] for row in self.search("needle", doc_type=["decision", "lesson"])}, {"decision", "lesson"})

    def test_status_and_type_are_anded(self) -> None:
        self.document("open-decision.md", type="decision", status="open")
        self.document("accepted-decision.md", type="decision", status="accepted")
        self.document("open-lesson.md", type="lesson", status="open")
        self.build()
        self.assertEqual([row["id"] for row in self.search("needle", doc_type="decision", status="open")], ["open-decision"])

    def test_dates_are_inclusive_and_fall_back_to_updated(self) -> None:
        self.document("start.md", created="2026-08-01")
        self.document("end.md", created="2026-08-31")
        self.document("updated.md", updated="2026-08-15")
        self.build()
        self.assertEqual({row["id"] for row in self.search("needle", since="2026-08-01", until="2026-08-31")}, {"start", "end", "updated"})
        self.assertEqual([row["id"] for row in self.search("needle", since="2026-08-31", until="2026-08-31")], ["end"])

    def test_null_metadata_is_excluded_by_positive_filter(self) -> None:
        self.write("plain.md", "needle")
        self.document("decision.md", type="decision")
        self.build()
        self.assertEqual([row["id"] for row in self.search("needle", doc_type="decision")], ["decision"])

    def test_mode_all_and_any(self) -> None:
        self.write("one.md", "migration")
        self.write("both.md", "migration ledger")
        self.build()
        self.assertEqual({row["id"] for row in self.search("migration ledger")}, {"one", "both"})
        self.assertEqual([row["id"] for row in self.search("migration ledger", mode="all")], ["both"])

    def test_exclude_removes_matching_document(self) -> None:
        self.write("keep.md", "migration ledger")
        self.write("drop.md", "migration deprecated")
        self.build()
        self.assertEqual([row["id"] for row in self.search("migration", exclude=["deprecated"])], ["keep"])

    def test_parse_query_covers_the_dsl_grammar(self) -> None:
        parsed = content_index.parse_query(
            'migration "exact phrase" type:decision type:lesson status:open project:build-loop '
            'tag:tooling since:2026-08-01 until:2026-08-31 -deprecated all: invented:value'
        )
        self.assertEqual(parsed["q"], 'migration "exact phrase" invented:value')
        self.assertEqual(parsed["doc_type"], ["decision", "lesson"])
        self.assertEqual(parsed["status"], "open")
        self.assertEqual(parsed["project"], "build-loop")
        self.assertEqual(parsed["tag"], "tooling")
        self.assertEqual(parsed["since"], "2026-08-01")
        self.assertEqual(parsed["until"], "2026-08-31")
        self.assertEqual(parsed["exclude"], ["deprecated"])
        self.assertEqual(parsed["mode"], "all")

    def test_cli_dsl_preserves_its_filters(self) -> None:
        with patch("content_index.query", return_value=[]) as query:
            self.assertEqual(content_index.main([
                "query", "--dsl", "migration type:decision status:open", "--limit", "5",
            ]), 0)
        self.assertEqual(query.call_args.kwargs["q"], "migration")
        self.assertEqual(query.call_args.kwargs["doc_type"], "decision")
        self.assertEqual(query.call_args.kwargs["status"], "open")
        self.assertEqual(query.call_args.kwargs["limit"], 5)

    def test_tag_filter_uses_list_frontmatter(self) -> None:
        self.document("tagged.md", tags=["tooling", "data"])
        self.document("other.md", tags=["process"])
        self.build()
        self.assertEqual([row["id"] for row in self.search("needle", tag="tooling")], ["tagged"])

    def test_facets_match_unlimited_query_count(self) -> None:
        self.document("one.md", type="decision", status="open", project="build-loop")
        self.document("two.md", type="lesson", status="open", project="build-loop")
        self.document("three.md", type="decision", status="accepted", project="other")
        self.build()
        rows = self.search("needle", limit=1000, status="open")
        result = content_index.facets("needle", db_path=self.db, status="open")
        self.assertEqual(result["total"], len(rows))
        self.assertEqual(result["doc_type"], {"decision": 1, "lesson": 1})
        self.assertEqual(result["status"], {"open": 2})

    def test_schema_version_bump_rebuilds(self) -> None:
        self.document("fresh.md", title="freshneedle", body="body")
        self.db.parent.mkdir(parents=True)
        connection = sqlite3.connect(self.db)
        connection.execute("CREATE VIRTUAL TABLE content USING fts5(path, body)")
        connection.execute("INSERT INTO content VALUES ('old.md', 'oldneedle')")
        connection.commit()
        connection.close()
        result = self.build()
        self.assertEqual(result["total_docs"], 1)
        self.assertEqual([row["id"] for row in self.search("freshneedle")], ["fresh"])
        self.assertEqual(self.search("oldneedle"), [])

    def test_existing_four_argument_scope_call_keeps_prior_results(self) -> None:
        self.write("global.md", "scopedneedle")
        self.write("projects/alpha/a.md", "scopedneedle")
        self.write("projects/beta/b.md", "scopedneedle")
        self.build()
        rows = content_index.query("scopedneedle", limit=20, project="alpha", db_path=self.db)
        self.assertEqual([(row["id"], row["_scope"]) for row in rows], [("global", "global"), ("a", "alpha")])

    def test_row_shape_preserves_old_keys_and_adds_requested_keys(self) -> None:
        self.document("shape.md", type="decision", status="open", created="2026-08-01")
        self.build()
        self.assertEqual(set(self.search("needle")[0]), {
            "_kind", "_scope", "_recency_ts", "id", "name", "title", "path", "_relevance", "snippet",
            "doc_type", "status", "created",
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
        self.assertEqual(self.build()["deleted"], 1)
        self.assertEqual(self.search("vanishingneedle"), [])

    def test_porter_stemming_and_or_regression(self) -> None:
        self.write("plural.md", "Completed several migrations successfully.")
        self.write("first.md", "deployment blockers migration")
        self.write("second.md", "ledger reconciliation dependency security")
        self.build()
        self.assertIn("plural", [row["id"] for row in self.search("migration")])
        terms = "deployment blockers migration ledger reconciliation dependency security"
        self.assertEqual({row["id"] for row in self.search(terms)}, {"plural", "first", "second"})

    def test_skip_directories_and_oversized_files_are_not_indexed(self) -> None:
        self.write("archive/old.md", "forbiddenarchiveword")
        self.write("node_modules/pkg/readme.md", "forbiddennodeword")
        self.write("oversized.md", "x" * ((1 << 20) + 1))
        self.write("included.md", "includedword")
        self.assertGreaterEqual(self.build()["skipped"], 1)
        self.assertEqual(self.search("forbiddenarchiveword"), [])
        self.assertEqual(self.search("forbiddennodeword"), [])
        self.assertEqual([row["id"] for row in self.search("includedword")], ["included"])

    def test_hyphen_quotes_and_operators_are_safe_terms(self) -> None:
        self.write("hyphen.md", "decision project build loop thing quote marker plain operator sentinel")
        self.build()
        self.assertIn("hyphen", [row["id"] for row in self.search("decision-project-build-loop-thing")])
        self.assertIn("hyphen", [row["id"] for row in self.search('quote"marker')])
        self.assertIn("hyphen", [row["id"] for row in self.search("operator AND sentinel")])

    def test_empty_or_missing_index_is_safe(self) -> None:
        self.assertEqual(content_index.query("anything", db_path=self.store / "missing.sqlite"), [])
        with patch("content_index.sqlite3.connect") as connect:
            self.assertEqual(self.search("  \t\n  "), [])
            self.assertEqual(self.search("-\"*:^^"), [])
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
