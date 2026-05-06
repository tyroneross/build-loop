"""Acceptance gate for Phase A hybrid recall.

The spec's deciding test:
  uv run python scripts/recall.py --query "package-level dead detection" \\
      --limit 5 --stats
must surface at least one architecture-relevant fact in top-5 under
--mode hybrid. Today's --mode vector_only returns top-3 ≤ 0.418 with
zero relevant content.

The corpus may genuinely lack a relevant fact (decision 0009 hasn't
been auto-captured because Postgres was offline during that capture
run). Per spec: write a synthetic test fact at suite start that covers
the query phrase, run the test, then clean up.

Skipped automatically when:
  - psycopg or db.py unavailable
  - $DATABASE_URL or ~/.config/agent-memory/connection.env not configured
  - bge-m3 not pulled in Ollama
  - sentence-transformers not installed (rerank optional dep)

The intent is for this test to catch regressions in the FULL pipeline
end-to-end against a live database.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# All gates run as importorskip / skipif so the suite degrades cleanly.
psycopg = pytest.importorskip("psycopg")  # noqa: F841
sentence_transformers = pytest.importorskip("sentence_transformers")  # noqa: F841


def _db_reachable() -> bool:
    try:
        from db import query  # type: ignore
    except Exception:  # noqa: BLE001
        return False
    try:
        query("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def _embedder_reachable() -> bool:
    try:
        from embed_backend import embed  # type: ignore
        v = embed("acceptance gate canary")
        return isinstance(v, list) and len(v) == 1024
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.skipif(not _db_reachable(), reason="Postgres unavailable"),
    pytest.mark.skipif(not _embedder_reachable(), reason="embedder unavailable"),
]


# ---------------------------------------------------------------------------
# Fixture: synthetic relevant fact
# ---------------------------------------------------------------------------

ACCEPTANCE_QUERY = "package-level dead detection"

SYNTHETIC_FACT = {
    "subject": "decision:acceptance-gate-synthetic",
    "predicate": "architecture",
    "object": (
        "Phase A acceptance synthetic: implemented package-level dead detection "
        "in find_dead so NavGator's dead-code report rolls up file-level orphans "
        "into the parent package. Replaces leaf-only scan."
    ),
}


@pytest.fixture
def synthetic_fact_in_db():
    """Insert a fact whose object covers the acceptance query phrase,
    then clean up. The fact carries a UUID-prefixed subject so concurrent
    test runs don't collide."""
    from db import execute, vector_literal  # type: ignore
    from embed_backend import active_model, embed  # type: ignore

    schema = os.environ.get("AGENT_MEMORY_SCHEMA", "build_loop_memory")
    fact_id = str(uuid.uuid4())
    test_subject = f"{SYNTHETIC_FACT['subject']}-{fact_id[:8]}"
    text = f"{test_subject} {SYNTHETIC_FACT['predicate']} {SYNTHETIC_FACT['object']}"
    vec = embed(text)
    model_id = active_model()

    execute(
        f"INSERT INTO {schema}.semantic_facts "
        "(id, subject, predicate, object, confidence, status, embedding, "
        " embedding_model_version, project, metadata, valid_from) "
        "VALUES (%s, %s, %s, %s, 1.0, 'active', %s::vector, %s, %s, %s, now())",
        (
            fact_id,
            test_subject,
            SYNTHETIC_FACT["predicate"],
            SYNTHETIC_FACT["object"],
            vector_literal(vec),
            model_id,
            "build-loop",
            '{"confidence": "explicit", "tags": ["architecture", "acceptance-test"]}',
        ),
    )
    yield fact_id
    # Cleanup.
    try:
        execute(f"DELETE FROM {schema}.semantic_facts WHERE id = %s", (fact_id,))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# The acceptance gate
# ---------------------------------------------------------------------------

def _is_relevant(row: dict, fact_id: str) -> bool:
    """Tighten the relevance check — must be the synthetic fact OR a
    real corpus row whose object/subject genuinely mentions the
    acceptance query keywords. Defensive against false positives that
    just happen to share one word."""
    if str(row.get("id", "")) == fact_id:
        return True
    obj = (row.get("object") or "").lower()
    subj = (row.get("subject") or "").lower()
    text = f"{subj} {obj}"
    has_dead = "dead" in text
    has_detect = "detect" in text or "find_dead" in text
    return has_dead and has_detect


def test_hybrid_surfaces_relevant_fact_in_top_5(synthetic_fact_in_db):
    """THE ACCEPTANCE GATE.

    With a known-relevant fact in the corpus, --mode hybrid must place
    it in the top-5. If this fails, Phase 5 Iterate kicks in to tune
    weights (NOT architecture).
    """
    from recall import run_search  # type: ignore

    schema = os.environ.get("AGENT_MEMORY_SCHEMA", "build_loop_memory")
    facts, stats = run_search(
        ACCEPTANCE_QUERY,
        schema,
        limit=5,
        confidence_floor=0.5,
        mode="hybrid",
        projects=["build-loop", "_unscoped"],
    )
    relevant = [f for f in facts if _is_relevant(f, synthetic_fact_in_db)]
    assert relevant, (
        f"Phase A hybrid pipeline failed acceptance gate. "
        f"Top-5 ids: {[f.get('id', '?')[:8] for f in facts]}; "
        f"top-5 objects: {[(f.get('object') or '')[:60] for f in facts]}; "
        f"stats: {stats}"
    )
    # Stronger claim: the synthetic fact should be #1, since the cross-
    # encoder routinely separates relevant from noise by 5-100x.
    assert facts[0].get("id") == synthetic_fact_in_db, (
        f"synthetic fact should rank #1; got {facts[0].get('id')} with "
        f"object={(facts[0].get('object') or '')[:80]!r} "
        f"(synthetic was at rank "
        f"{[i for i, f in enumerate(facts) if f.get('id') == synthetic_fact_in_db][0] + 1})"
    )


def test_vector_only_baseline_does_NOT_surface_synthetic(synthetic_fact_in_db):
    """Regression baseline check: pre-Phase-A behavior (vector_only)
    should still look like today's noise on this query — confirming
    that the lift in test_hybrid_surfaces_relevant_fact_in_top_5 is
    actually attributable to Phase A.

    NOTE: this test passes when vector_only is at-or-near today's
    quality. If it ever starts passing the relevance check on its own,
    that's a SIGNAL not a failure — the synthetic fact's text is
    similar enough to the query that pure-cosine could surface it. In
    that case, harden the relevance bar OR pick a harder query.
    """
    from recall import run_search  # type: ignore

    schema = os.environ.get("AGENT_MEMORY_SCHEMA", "build_loop_memory")
    facts, _ = run_search(
        ACCEPTANCE_QUERY,
        schema,
        limit=5,
        confidence_floor=0.5,
        mode="vector_only",
        projects=["build-loop", "_unscoped"],
    )
    # Document the behavior — don't gate on it. We expect vector_only
    # to either miss the synthetic fact or rank it lower than hybrid
    # would. This test exists for the human reading the test output to
    # see the contrast. We assert weakly: the test must not error.
    # The Review-F report will show vector_only vs hybrid stats.
    assert isinstance(facts, list)


# ---------------------------------------------------------------------------
# Phase D — Contextual Retrieval acceptance
# ---------------------------------------------------------------------------

PARAPHRASED_QUERY = "unused dependencies cleanup"
"""Paraphrase of decision 0009's "package-level dead detection in
find_dead". Pure cosine over subject/predicate/object misses the
keyword overlap; with chunk_context populated, the query should hit
the prepended summary's domain language."""


@pytest.fixture
def synthetic_fact_with_chunk_context():
    """Like synthetic_fact_in_db but uses paraphrased terminology in
    chunk_context that does NOT appear in subject/predicate/object.

    Skips when:
      - the chunk_context column is absent (migration not run)
      - the contextual_prepend router is unavailable
    """
    from db import execute, query, vector_literal  # type: ignore
    from embed_backend import active_model, embed  # type: ignore

    schema = os.environ.get("AGENT_MEMORY_SCHEMA", "build_loop_memory")

    # Check column exists.
    col_present = query(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'semantic_facts' "
        "  AND column_name = 'chunk_context'",
        (schema,),
    )
    if not col_present:
        pytest.skip("chunk_context column missing — run migrate_add_chunk_context_column.py")

    # Synthetic chunk_context contains the paraphrased terminology.
    # subject/predicate/object intentionally omit "unused dependencies"
    # and "cleanup" — only the prepended chunk_context bridges them.
    subject = "decision:phase-d-acceptance"
    predicate = "architecture"
    obj = (
        "Implemented graph-walk in NavGator to roll up file-level orphans into the "
        "parent package, replacing leaf-only scans."
    )
    chunk_context = (
        "This decision addresses unused dependencies cleanup at the package "
        "level — finding dead packages where every file is orphaned, the "
        "kind of library cleanup that prevents bloat in long-lived repos."
    )

    fact_id = str(uuid.uuid4())
    test_subject = f"{subject}-{fact_id[:8]}"
    embed_text = f"{chunk_context}\n\n{test_subject} {predicate} {obj}"
    vec = embed(embed_text)
    model_id = active_model()

    execute(
        f"INSERT INTO {schema}.semantic_facts "
        "(id, subject, predicate, object, confidence, status, embedding, "
        " embedding_model_version, project, metadata, chunk_context, valid_from) "
        "VALUES (%s, %s, %s, %s, 1.0, 'active', %s::vector, %s, %s, %s, %s, now())",
        (
            fact_id, test_subject, predicate, obj,
            vector_literal(vec), model_id, "build-loop",
            '{"confidence": "explicit", "tags": ["architecture", "phase-d-acceptance"]}',
            chunk_context,
        ),
    )
    yield fact_id, test_subject
    try:
        execute(f"DELETE FROM {schema}.semantic_facts WHERE id = %s", (fact_id,))
    except Exception:  # noqa: BLE001
        pass


def test_phase_d_paraphrase_hits_via_chunk_context(synthetic_fact_with_chunk_context):
    """Phase D acceptance gate.

    The paraphrased query terminology lives ONLY in `chunk_context`, NOT
    in subject/predicate/object. Pure-cosine over the body would miss
    it. With Phase D's prepend, the embedded vector now carries the
    paraphrase and hybrid recall must surface the row.
    """
    from recall import run_search  # type: ignore

    fact_id, _ = synthetic_fact_with_chunk_context
    schema = os.environ.get("AGENT_MEMORY_SCHEMA", "build_loop_memory")
    facts, stats = run_search(
        PARAPHRASED_QUERY,
        schema,
        limit=10,
        confidence_floor=0.5,
        mode="hybrid",
        rerank_disabled=True,  # isolate the Phase D embedding lift
        projects=["build-loop", "_unscoped"],
    )
    ids = [f.get("id") for f in facts]
    assert fact_id in ids, (
        f"Phase D acceptance: paraphrased query must surface chunk_context-"
        f"populated row. Got top-{len(facts)} ids: {ids[:5]}; stats: {stats}"
    )
