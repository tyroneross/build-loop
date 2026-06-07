#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the embedding backfill capability.

Covers:
  - Idempotent: re-run on a fully-backfilled DB does zero work.
  - Picks up pre-existing NULL rows (the production-no-op fix surface).
  - Per-row failure isolation: one bad row doesn't abort the batch.
  - Backend unavailable returns a clean BackfillResult, no raise.
  - Batching: max_rows cap respected.
  - Non-existent DB: clean no-op.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from semantic_index import init, upsert_fact  # noqa: E402
from semantic_index.backfill import (  # noqa: E402
    BackfillResult,
    backfill_embeddings,
)


def _fixed_vec(text: str) -> list[float]:
    """Deterministic 4-dim embedder."""
    h = abs(hash(text)) % 1000
    return [h / 1000.0, (h * 7 % 1000) / 1000.0, 0.5, 0.25]


# ------------------------------------------------------------- core paths


def test_backfill_picks_up_null_rows(tmp_path: Path) -> None:
    """Rows written with ``auto_embed=False`` (== legacy upsert) carry NULL
    ``embedding_json``. Backfill must find and embed them."""
    db = tmp_path / "bf.sqlite"
    upsert_fact(
        subject="legacy-1",
        predicate="p",
        object_text="adapter boundary",
        project="proj",
        db_path=db,
        auto_embed=False,
    )
    upsert_fact(
        subject="legacy-2",
        predicate="p",
        object_text="other content",
        project="proj",
        db_path=db,
        auto_embed=False,
    )

    # Pre: both rows have NULL embedding_json.
    conn = sqlite3.connect(str(db))
    pre = conn.execute(
        "SELECT COUNT(*) FROM semantic_facts WHERE embedding_json IS NULL"
    ).fetchone()[0]
    conn.close()
    assert pre == 2

    result = backfill_embeddings(db_path=db, embed_fn=_fixed_vec)
    assert isinstance(result, BackfillResult)
    assert result.scanned == 2
    assert result.embedded == 2
    assert result.failed == 0
    assert result.backend_unavailable is False

    # Post: zero rows are NULL — and the persisted vector is parseable.
    conn = sqlite3.connect(str(db))
    post = conn.execute(
        "SELECT COUNT(*) FROM semantic_facts WHERE embedding_json IS NULL"
    ).fetchone()[0]
    vecs = conn.execute(
        "SELECT embedding_json FROM semantic_facts ORDER BY subject"
    ).fetchall()
    conn.close()
    assert post == 0
    for (raw,) in vecs:
        parsed = json.loads(raw)
        assert isinstance(parsed, list) and len(parsed) == 4


def test_backfill_idempotent_second_run_is_noop(tmp_path: Path) -> None:
    """Once backfilled, a second run must scan zero NULL rows."""
    db = tmp_path / "bf.sqlite"
    upsert_fact(
        subject="x",
        predicate="p",
        object_text="text",
        project="proj",
        db_path=db,
        auto_embed=False,
    )
    first = backfill_embeddings(db_path=db, embed_fn=_fixed_vec)
    assert first.embedded == 1
    second = backfill_embeddings(db_path=db, embed_fn=_fixed_vec)
    assert second.scanned == 0
    assert second.embedded == 0
    assert second.failed == 0


def test_backfill_per_row_failure_isolation(tmp_path: Path) -> None:
    """One bad row must not abort the batch — the rest still embed."""
    db = tmp_path / "bf.sqlite"
    for i in range(3):
        upsert_fact(
            subject=f"row-{i}",
            predicate="p",
            object_text=f"content {i}",
            project="proj",
            db_path=db,
            auto_embed=False,
        )

    def flaky_embed(text: str) -> list[float]:
        if "content 1" in text:
            raise RuntimeError("simulated row-1 failure")
        return _fixed_vec(text)

    result = backfill_embeddings(db_path=db, embed_fn=flaky_embed)
    assert result.scanned == 3
    assert result.embedded == 2
    assert result.failed == 1
    assert len(result.sample_failures) == 1
    # Sample format: "rowid=<N>: RuntimeError: simulated row-1 failure"
    assert "simulated row-1 failure" in result.sample_failures[0]
    assert "RuntimeError" in result.sample_failures[0]
    assert "rowid=" in result.sample_failures[0]

    # Critical regression guard: the failed row stays NULL and is NOT
    # re-fetched in a subsequent batch (would have caused an infinite
    # loop in the first cut of this code — the after_rowid cursor in
    # _iter_null_rows is what stops that).
    conn = sqlite3.connect(str(db))
    null_count = conn.execute(
        "SELECT COUNT(*) FROM semantic_facts WHERE embedding_json IS NULL"
    ).fetchone()[0]
    conn.close()
    assert null_count == 1  # the one flaky row remains NULL


def test_backfill_skips_empty_text(tmp_path: Path) -> None:
    """A row with empty subject+predicate+object must be skipped, not
    sent to the embedder (most embedders raise on empty strings)."""
    db = tmp_path / "bf.sqlite"
    # Direct-insert a row with empty fields (bypass upsert_fact validation).
    init(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            """
            INSERT INTO semantic_facts
              (subject, project_key, predicate, object, last_synced, schema_version)
            VALUES ('only-subject', 'proj', '', '', '2026-01-01T00:00:00Z', '1.0.0')
            """
        )
        conn.commit()
    finally:
        conn.close()

    calls = {"n": 0}

    def tracker(text: str) -> list[float]:
        calls["n"] += 1
        return _fixed_vec(text)

    result = backfill_embeddings(db_path=db, embed_fn=tracker)
    # Subject 'only-subject' alone is non-empty text — it embeds.
    assert result.scanned == 1
    assert calls["n"] == 1
    assert result.embedded == 1
    assert result.skipped_empty_text == 0


def test_backfill_backend_unavailable_clean_error(tmp_path: Path) -> None:
    """When no embed_fn supplied AND the default ``embed_backend`` import
    fails, return a clean BackfillResult — no raise — with the
    backend-unavailable flag set."""
    db = tmp_path / "bf.sqlite"
    upsert_fact(
        subject="x",
        predicate="p",
        object_text="text",
        project="proj",
        db_path=db,
        auto_embed=False,
    )

    # Force-fail the default resolution by patching the module's resolver
    # to return None — same effect as a real import error.
    from semantic_index import backfill as backfill_mod

    original = backfill_mod._resolve_default_embed_fn
    backfill_mod._resolve_default_embed_fn = lambda: None
    try:
        result = backfill_embeddings(db_path=db)
    finally:
        backfill_mod._resolve_default_embed_fn = original

    assert result.backend_unavailable is True
    assert result.error is not None
    assert result.scanned == 0


def test_backfill_max_rows_cap(tmp_path: Path) -> None:
    """``max_rows`` caps the total processed — used for time-bounded
    incremental runs."""
    db = tmp_path / "bf.sqlite"
    for i in range(5):
        upsert_fact(
            subject=f"r{i}",
            predicate="p",
            object_text=f"row {i}",
            project="proj",
            db_path=db,
            auto_embed=False,
        )

    result = backfill_embeddings(db_path=db, embed_fn=_fixed_vec, max_rows=2)
    assert result.scanned == 2
    assert result.embedded == 2

    # Three rows still NULL; next call drains them.
    result2 = backfill_embeddings(db_path=db, embed_fn=_fixed_vec, max_rows=10)
    assert result2.scanned == 3
    assert result2.embedded == 3

    # Third call is the idempotent no-op.
    result3 = backfill_embeddings(db_path=db, embed_fn=_fixed_vec)
    assert result3.scanned == 0


def test_backfill_nonexistent_db_is_clean_noop(tmp_path: Path) -> None:
    """Fresh installs hit this before any write. Don't crash; don't flag
    backend-unavailable; just return zeros."""
    db = tmp_path / "does-not-exist.sqlite"
    result = backfill_embeddings(db_path=db, embed_fn=_fixed_vec)
    assert result.scanned == 0
    assert result.embedded == 0
    assert result.failed == 0
    assert result.backend_unavailable is False


def test_backfill_does_not_touch_already_embedded_rows(tmp_path: Path) -> None:
    """Idempotency at the row level: rows with non-NULL embedding_json
    must not be re-embedded (preserves caller-supplied vectors)."""
    db = tmp_path / "bf.sqlite"
    fixed = [0.11, 0.22, 0.33, 0.44]
    upsert_fact(
        subject="has-vec",
        predicate="p",
        object_text="text",
        project="proj",
        db_path=db,
        embedding=fixed,
    )
    upsert_fact(
        subject="no-vec",
        predicate="p",
        object_text="text",
        project="proj",
        db_path=db,
        auto_embed=False,
    )

    calls = {"texts": []}

    def tracker(text: str) -> list[float]:
        calls["texts"].append(text)
        return _fixed_vec(text)

    result = backfill_embeddings(db_path=db, embed_fn=tracker)
    assert result.scanned == 1  # only the NULL row
    assert result.embedded == 1

    # has-vec's vector survived untouched.
    conn = sqlite3.connect(str(db))
    raw = conn.execute(
        "SELECT embedding_json FROM semantic_facts WHERE subject='has-vec'"
    ).fetchone()[0]
    conn.close()
    assert json.loads(raw) == fixed
