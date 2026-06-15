#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for db_substrate_lint. Zero deps. Run: python3 -m pytest scripts/test_db_substrate_lint.py

Fixtures are modeled on REAL observed shapes (atomize-ai, evidence sample 1):
the positive fixture mirrors atomize's actual ArticleEmbedding model — embedding
+ content_hash but NO embedding_model/embedding_version/index_version — which is
exactly the undetectable-stale-vector failure that seeded check (a). The clean
fixture is the same shape with version + metadata fields added, proving the
lint clears a compliant design (no false positive)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import db_substrate_lint as lint  # noqa: E402


# Positive fixture: atomize-ai's real ArticleEmbedding shape (no version field)
# + a SQL artifact table with no metadata + a version-less cache key.
POSITIVE_PRISMA = """\
model ArticleEmbedding {
  id          String   @id @default(dbgenerated("gen_random_uuid()")) @db.Uuid
  articleId   String   @unique @map("article_id") @db.Uuid
  embedding   Unsupported("vector")
  contentHash String   @map("content_hash")
  createdAt   DateTime? @default(now()) @map("created_at") @db.Timestamptz(6)

  @@index([contentHash])
  @@index([embedding])
  @@map("article_embeddings")
}
"""

POSITIVE_SQL = """\
CREATE TABLE ingested_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source TEXT NOT NULL,
  extracted_text TEXT
);
"""

POSITIVE_CACHE_TS = """\
export function buildRetrievalCacheKey(userId: string, query: string) {
  const cacheKey = `retrieval:${userId}:${query}`;
  return cacheKey;
}
"""

# Clean fixture: same embedding shape WITH version metadata + artifact table
# WITH a full metadata record + a versioned cache key.
CLEAN_PRISMA = """\
model ArticleEmbedding {
  id             String   @id @default(dbgenerated("gen_random_uuid()")) @db.Uuid
  articleId      String   @unique @map("article_id") @db.Uuid
  embedding      Unsupported("vector")
  contentHash    String   @map("content_hash")
  embeddingModel String   @map("embedding_model")
  indexVersion   Int      @map("index_version")
  createdAt      DateTime? @default(now()) @map("created_at") @db.Timestamptz(6)

  @@map("article_embeddings")
}
"""

CLEAN_SQL = """\
CREATE TABLE ingested_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source TEXT NOT NULL,
  checksum TEXT NOT NULL,
  version INT NOT NULL,
  permissions TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
);
"""

CLEAN_CACHE_TS = """\
export function buildRetrievalCacheKey(userId: string, query: string, indexVersion: number) {
  const cacheKey = `retrieval:${indexVersion}:${userId}:${query}`;
  return cacheKey;
}
"""


def _write_repo(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp


class PositiveFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _write_repo({
            "prisma/schema.prisma": POSITIVE_PRISMA,
            "migrations/001_artifacts.sql": POSITIVE_SQL,
            "lib/retrieval-cache.ts": POSITIVE_CACHE_TS,
        })
        self.result = lint.scan_repo(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_check_a_flags_versionless_embedding(self) -> None:
        hits = [f for f in self.result["findings"]
                if f["check"] == "missing_version_component"
                and f["block"] == "ArticleEmbedding"]
        self.assertEqual(len(hits), 1, "should flag the version-less embedding model exactly once")
        self.assertIn("Retrieval And Metadata Rules", hits[0]["constitution_rule"])
        self.assertIn("atomize-ai", hits[0]["seed_evidence"])

    def test_check_a_flags_versionless_cache_key(self) -> None:
        hits = [f for f in self.result["findings"]
                if f["check"] == "missing_version_component"
                and f["block"] == "cache/retrieval key"]
        self.assertGreaterEqual(len(hits), 1, "should flag the version-less cache key")

    def test_check_b_flags_artifact_without_metadata(self) -> None:
        hits = [f for f in self.result["findings"]
                if f["check"] == "artifact_without_metadata"
                and f["block"] == "ingested_documents"]
        self.assertEqual(len(hits), 1, "should flag the metadata-less artifact table once")
        self.assertIn("File And Artifact Rule", hits[0]["constitution_rule"])

    def test_all_findings_are_warn_and_advisory(self) -> None:
        self.assertTrue(self.result["advisory"])
        self.assertTrue(all(f["severity"] == "WARN" for f in self.result["findings"]))
        self.assertGreater(self.result["finding_count"], 0)


class CleanFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _write_repo({
            "prisma/schema.prisma": CLEAN_PRISMA,
            "migrations/001_artifacts.sql": CLEAN_SQL,
            "lib/retrieval-cache.ts": CLEAN_CACHE_TS,
        })
        self.result = lint.scan_repo(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_findings_on_compliant_design(self) -> None:
        self.assertEqual(
            self.result["finding_count"], 0,
            f"compliant design should produce zero findings, got: {self.result['findings']}",
        )


class CliContractTests(unittest.TestCase):
    def test_missing_workdir_is_advisory_exit_zero(self) -> None:
        rc = lint.main(["--workdir", "/nonexistent/path/xyz", "--json"])
        self.assertEqual(rc, 0, "advisory lint must never hard-fail (exit 0)")

    def test_clean_repo_exit_zero(self) -> None:
        tmp = _write_repo({"prisma/schema.prisma": CLEAN_PRISMA})
        try:
            rc = lint.main(["--workdir", tmp.name, "--json"])
            self.assertEqual(rc, 0)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
