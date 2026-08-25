"""Tests for scripts/memory_facade.py.

Locks priority 3 of the architecture-awareness follow-up: a single `recall()`
function that fans out to all four memory backends and degrades gracefully
when any (or all) are unavailable.

No live Postgres or external debugger dependency. The DB backend is forced into
an unavailable state by clearing env vars; the debugger backend uses local
incident files by default and can be injected with a stub callable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import memory_facade as mf  # type: ignore  # noqa: E402
from semantic_index import upsert_fact  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Synthetic project root with state.json runs and a couple of decisions."""
    # Full DB-URL isolation: the shared resolver also reads $DATABASE_URL
    # and ~/.config/agent-memory/connection.env, so clearing only
    # BUILD_LOOP_DATABASE_URL would let it fall through to the developer's
    # real DSN. Clear both env vars and point HOME at an empty tmp dir.
    monkeypatch.delenv("BUILD_LOOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "_no_home"))
    monkeypatch.setattr(mf, "_DEBUGGER_RUNNER_OVERRIDE", None)
    # Isolate from the live memory store during tests.
    isolated_root = tmp_path / "_agent_memory_root"
    isolated_root.mkdir()
    monkeypatch.delenv("BUILD_LOOP_MEMORY_STORE_ROOT", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_ROOT", raising=False)
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(isolated_root))

    bl = tmp_path / ".build-loop"
    bl.mkdir()
    state = {
        "runs": [
            {
                "run_id": "run_1",
                "goal": "wire architecture scout into Phase 1",
                "outcome": "pass",
                "date": "2026-05-01T10:00:00Z",
                "filesTouched": ["agents/build-orchestrator.md"],
            },
            {
                "run_id": "run_2",
                "goal": "fix flaky timing test in worker",
                "outcome": "pass",
                "date": "2026-05-03T12:00:00Z",
                "filesTouched": ["src/build_loop/worker.py"],
            },
        ]
    }
    (bl / "state.json").write_text(json.dumps(state), encoding="utf-8")

    project_dir = isolated_root / "projects" / "_unscoped" / "decisions"
    project_dir.mkdir(parents=True)
    (project_dir / "decision-project-unscoped-arch-baseline-20260502-001.md").write_text(
        "---\n"
        "id: '0001'\n"
        "canonical_id: decision-project-unscoped-arch-baseline-20260502-001\n"
        "title: Architecture baseline scan: 142 components\n"
        "date: 2026-05-02T08:00:00Z\n"
        "primary_tag: architecture\n"
        "---\n"
        "Captured 142 components, 191 connections.\n",
        encoding="utf-8",
    )
    (project_dir / "decision-project-unscoped-debug-flakey-20260504-002.md").write_text(
        "---\n"
        "id: '0002'\n"
        "canonical_id: decision-project-unscoped-debug-flakey-20260504-002\n"
        "title: Debug session for flaky test\n"
        "date: 2026-05-04T14:00:00Z\n"
        "primary_tag: debugging\n"
        "---\n"
        "Hypothesis: race condition in scanner.\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Backend isolation tests
# ---------------------------------------------------------------------------

def test_runs_backend_returns_matching_entries(workdir: Path) -> None:
    out, reasons = mf.read_runs(workdir, query="architecture", limit=5)
    assert reasons == []
    assert len(out) == 1
    assert out[0]["run_id"] == "run_1"


def test_runs_backend_empty_query_returns_all(workdir: Path) -> None:
    out, _ = mf.read_runs(workdir, query="", limit=10)
    assert len(out) == 2
    # Sorted by recency desc.
    assert out[0]["run_id"] == "run_2"


def test_runs_backend_handles_missing_file(tmp_path: Path) -> None:
    out, reasons = mf.read_runs(tmp_path, query="anything", limit=5)
    assert out == []
    assert reasons == []  # silent — not an error


def test_decisions_backend_returns_matching(workdir: Path) -> None:
    out, reasons = mf.read_decisions(workdir, query="baseline", limit=10)
    # `reasons` now carries the query-independent index-coverage
    # observability signal (never an error/gate) — the isolated fixture's
    # two decision files have no INDEX.jsonl, so both are reported missing.
    assert any(r.startswith("decision_index_coverage:") for r in reasons)
    assert len(out) == 1
    assert out[0]["primary_tag"] == "architecture"


def _pin_scoped_project(workdir: Path, slug: str) -> None:
    """Give ``workdir`` a `.git` + a durable `memoryProjectSlug` pin so
    `resolve_project(workdir)` returns a real named project instead of the
    `_unscoped` fallback. Required by the global-decision-recall tests
    below, which must exercise a genuinely SCOPED project context."""
    (workdir / ".git").mkdir(exist_ok=True)
    (workdir / ".build-loop" / "config.json").write_text(
        json.dumps({"memoryProjectSlug": slug}), encoding="utf-8"
    )


def test_global_decision_recalled_from_scoped_project(workdir: Path) -> None:
    """A decision routed `project: _unscoped` (the "would this apply to a
    different project? yes -> global" routing rule) must be recallable from
    a DIFFERENT, NAMED project's context — that is the entire point of
    routing it global. Before the fix, `_index_row_to_decision`
    (memory_facade/decisions.py) dropped every `_unscoped` index row the
    moment the caller's own project resolved to a real name, making every
    globally-routed decision invisible everywhere except `_unscoped` itself.
    """
    _pin_scoped_project(workdir, "scoped-project")

    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])
    indexes_dir = memory_root / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    (indexes_dir / "INDEX.jsonl").write_text(
        json.dumps(
            {
                "type": "decision",
                "id": "dec-global-001",
                "canonical_id": "dec-global-001",
                "title": "Global rollback policy: always keep a green main",
                "project": "_unscoped",
                "date": "2026-06-01T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    out, reasons = mf.read_decisions(workdir, query="rollback policy", limit=10)
    ids = [d["canonical_id"] for d in out]
    assert "dec-global-001" in ids, (
        "expected the _unscoped global decision to be recalled from a scoped "
        f"project context; got {ids} (reasons={reasons})"
    )


def test_decision_from_different_named_project_not_recalled(workdir: Path) -> None:
    """A decision belonging to a DIFFERENT, NAMED project must stay
    invisible from `scoped-project`'s recall — only `_unscoped`/global
    decisions become visible everywhere; this guards against an
    over-broad leak while fixing global recall."""
    _pin_scoped_project(workdir, "scoped-project")

    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])
    indexes_dir = memory_root / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    (indexes_dir / "INDEX.jsonl").write_text(
        json.dumps(
            {
                "type": "decision",
                "id": "dec-other-001",
                "canonical_id": "dec-other-001",
                "title": "other-project internal API contract v2",
                "project": "other-project",
                "date": "2026-06-01T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    out, reasons = mf.read_decisions(workdir, query="internal API contract", limit=10)
    ids = [d["canonical_id"] for d in out]
    assert "dec-other-001" not in ids, (
        f"a different named project's decision leaked into scoped-project recall: {ids}"
    )


def test_decisions_backend_handles_missing_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate from the live memory store so this actually exercises the
    # "no decision directories at all" path (previously this test read the
    # developer's real build-loop-memory store by accident, since
    # `project_decisions_dir("_unscoped")` already resolved unconditionally
    # pre-fix; that leak just didn't surface in `reasons` before).
    monkeypatch.delenv("BUILD_LOOP_MEMORY_STORE_ROOT", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_ROOT", raising=False)
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path / "_empty_agent_memory_root"))
    out, reasons = mf.read_decisions(tmp_path, query="anything", limit=5)
    assert out == []
    assert reasons == []


def test_semantic_backend_unavailable_without_env(workdir: Path) -> None:
    out, reasons = mf.read_semantic(workdir, query="x", limit=5, project=None)
    assert out == []
    assert any("BUILD_LOOP_DATABASE_URL" in r for r in reasons)


def test_semantic_backend_reads_local_sqlite_first(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLite semantic facts are returned without a Postgres URL."""
    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])
    db_path = memory_root / "indexes" / "semantic_facts.sqlite"
    upsert_fact(
        subject="fact:sqlite",
        predicate="captures",
        object_text="architecture adapter lesson",
        project="build-loop",
        confidence=0.75,
        db_path=db_path,
    )
    monkeypatch.delenv("BUILD_LOOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    out, reasons = mf.read_semantic(workdir, query="adapter", limit=5, project="build-loop")

    assert reasons == []
    assert len(out) == 1
    assert out[0]["backend"] == "sqlite"
    assert out[0]["subject"] == "fact:sqlite"


def test_semantic_backend_unavailable_when_psycopg_missing(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set the env var but force psycopg ImportError."""
    monkeypatch.setenv("BUILD_LOOP_DATABASE_URL", "postgresql://fake")
    # Force the inner `import psycopg` to fail by removing it from sys.modules
    # and shadowing it with None in the import path.
    monkeypatch.setitem(sys.modules, "psycopg", None)
    out, reasons = mf.read_semantic(workdir, query="x", limit=5, project=None)
    assert out == []
    assert any("psycopg not installed" in r or "ImportError" in r for r in reasons)


def test_debugger_backend_unavailable_when_no_local_incidents(workdir: Path) -> None:
    out, reasons = mf.read_debugger(workdir, query="x", limit=5, project=None)
    assert out == []
    assert any("debugger_unavailable" in r for r in reasons)


def test_debugger_backend_reads_native_structured_incidents(workdir: Path) -> None:
    incidents = workdir / ".claude" / "memory" / "incidents"
    incidents.mkdir(parents=True)
    (incidents / "INC_LOGIC_1.json").write_text(
        json.dumps(
            {
                "incident_id": "INC_LOGIC_1",
                "timestamp": 1787673600000,
                "symptom": "TypeError: vendor was null",
                "root_cause": {"description": "Nullable vendor was not normalized."},
                "fix": {"approach": "Normalize vendor before deduplication."},
                "tags": ["typescript", "null"],
            }
        ),
        encoding="utf-8",
    )

    out, reasons = mf.read_debugger(workdir, query="vendor null", limit=5, project=None)
    assert reasons == []
    assert [item["id"] for item in out] == ["INC_LOGIC_1"]
    assert out[0]["root_cause"] == "Nullable vendor was not normalized."


def test_debugger_backend_uses_injected_runner(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "incidents": [
            {
                "id": "INC_1",
                "symptom": "TypeError: cannot read undefined",
                "root_cause": "missing await",
                "fix": "add await",
                "project": "build-loop",
                "created_at": "2026-05-04T10:00:00Z",
            },
        ],
    }
    monkeypatch.setattr(
        mf, "_DEBUGGER_RUNNER_OVERRIDE",
        lambda query, limit, project: json.dumps(payload),
    )
    out, reasons = mf.read_debugger(workdir, query="anything", limit=5, project=None)
    assert reasons == []
    assert len(out) == 1
    assert out[0]["id"] == "INC_1"
    assert out[0]["symptom"].startswith("TypeError")


# ---------------------------------------------------------------------------
# Top-level recall() tests — all backends mocked
# ---------------------------------------------------------------------------

def test_recall_merges_all_4_backends(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When all 4 backends return results, merged list contains all of them
    sorted by recency."""
    payload = {
        "incidents": [
            {
                "id": "INC_1",
                "symptom": "test failure",
                "root_cause": "race",
                "fix": "lock",
                "project": "build-loop",
                "created_at": "2026-05-04T15:00:00Z",
            },
        ],
    }
    monkeypatch.setattr(
        mf, "_DEBUGGER_RUNNER_OVERRIDE",
        lambda query, limit, project: json.dumps(payload),
    )
    # Semantic backend stays unavailable (no env / no psycopg).
    env = mf.recall(query="", workdir=workdir, limit=10)
    by = env["results_by_kind"]
    assert len(by["runs"]) == 2
    assert len(by["decisions"]) == 2
    assert len(by["semantic"]) == 0  # unavailable
    assert len(by["debugger"]) == 1
    # Merged is ordered by recency desc.
    merged = env["merged"]
    timestamps = [m.get("_recency_ts") for m in merged if m.get("_recency_ts")]
    assert timestamps == sorted(timestamps, reverse=True)
    # Each kind appears.
    kinds = {m["_kind"] for m in merged}
    assert {"runs", "decisions", "debugger"}.issubset(kinds)


def test_recall_kind_filter_isolates_one_backend(workdir: Path) -> None:
    env = mf.recall(query="", kind="runs", workdir=workdir)
    assert env["results_by_kind"]["runs"]
    assert env["results_by_kind"]["decisions"] == []
    assert env["results_by_kind"]["semantic"] == []
    assert env["results_by_kind"]["debugger"] == []


def test_recall_invalid_kind_raises(workdir: Path) -> None:
    with pytest.raises(ValueError):
        mf.recall(query="", kind="bogus", workdir=workdir)


def test_recall_query_filters_each_backend(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A specific query should narrow runs and decisions."""
    monkeypatch.setattr(mf, "_DEBUGGER_RUNNER_OVERRIDE",
                        lambda query, limit, project: json.dumps({"incidents": []}))
    env = mf.recall(query="architecture", workdir=workdir, limit=10)
    by = env["results_by_kind"]
    # Only run_1 mentions "architecture" in goal.
    assert len(by["runs"]) == 1
    assert by["runs"][0]["run_id"] == "run_1"
    # Only the baseline decision matches.
    assert len(by["decisions"]) == 1
    assert by["decisions"][0]["primary_tag"] == "architecture"


def test_recall_envelope_shape(workdir: Path) -> None:
    env = mf.recall(query="x", workdir=workdir)
    for k in ("query", "kind_filter", "project", "results_by_kind", "merged", "reasons"):
        assert k in env
    for k in mf.KINDS:
        assert k in env["results_by_kind"]


def test_recall_records_unavailable_reasons(workdir: Path) -> None:
    """Even when all 4 backends are absent or unavailable, recall() returns
    the envelope shape with `reasons[]` populated and never raises."""
    env = mf.recall(query="anything", workdir=workdir)
    # Semantic is unavailable (no env var).
    assert any("db_unavailable" in r for r in env["reasons"]) or any(
        "BUILD_LOOP_DATABASE_URL" in r for r in env["reasons"]
    )


def test_recall_cli_smoke(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = mf.main(["--query", "architecture", "--workdir", str(workdir), "--limit", "3"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["query"] == "architecture"
    assert "results_by_kind" in data


def test_parse_iso_handles_various_shapes() -> None:
    assert mf._parse_iso("2026-05-01T10:00:00Z") is not None
    assert mf._parse_iso("2026-05-01T10:00:00") is not None
    assert mf._parse_iso(1714559400.0) is not None
    assert mf._parse_iso(1714559400000) is not None  # ms
    assert mf._parse_iso(None) is None
    assert mf._parse_iso("not a date") is None


def test_q_match_case_insensitive_and_empty() -> None:
    assert mf._q_match("Architecture Scan", "architecture") is True
    assert mf._q_match("Architecture Scan", "ARCH") is True
    assert mf._q_match("Architecture Scan", "") is True
    assert mf._q_match("Architecture Scan", "missing") is False
