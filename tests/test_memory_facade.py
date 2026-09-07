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

import content_index  # type: ignore  # noqa: E402
import memory_facade as mf  # type: ignore  # noqa: E402
from memory_facade import decisions as decisions_backend  # type: ignore  # noqa: E402
from semantic_index import upsert_fact  # type: ignore  # noqa: E402


def _rebuild_content_index(memory_root: Path) -> None:
    """Build the content-FTS index over *memory_root* for a test fixture.

    `content_index.build(..., incremental=False)` on a tiny fixture store is
    fast (full walk of a handful of files, not the 10,545-doc live store) --
    the brief for this chunk explicitly names this as the sanctioned way to
    populate the FTS leg in tests without depending on the sibling chunk's
    `index_paths` helper landing first.
    """
    content_index.build(memory_root, incremental=False)


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
    # The `decision_index_coverage:` signal was observability for the
    # INDEX.jsonl leg this backend no longer reads (see decisions.py module
    # docstring) -- it must never appear in `reasons` again.
    assert not any(r.startswith("decision_index_coverage:") for r in reasons)
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
    routing it global. Before the original fix, `_index_row_to_decision`
    (memory_facade/decisions.py) dropped every `_unscoped` index row the
    moment the caller's own project resolved to a real name, making every
    globally-routed decision invisible everywhere except `_unscoped` itself.

    Re-expressed for the content-FTS leg (INDEX.jsonl is no longer read):
    the same routing rule now lives in `_content_row_to_decision`, keyed off
    the FTS row's `_scope` instead of a JSONL row's `project` field. A file
    under `projects/_unscoped/decisions/` gets `_scope == "_unscoped"` from
    `content_index._scope`, which is the FTS-side equivalent of the old
    JSONL sentinel.
    """
    _pin_scoped_project(workdir, "scoped-project")

    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])
    unscoped_dir = memory_root / "projects" / "_unscoped" / "decisions"
    unscoped_dir.mkdir(parents=True, exist_ok=True)
    (unscoped_dir / "dec-global-001.md").write_text(
        "---\n"
        "title: Global rollback policy: always keep a green main\n"
        "type: decision\n"
        "date: 2026-06-01\n"
        "---\n"
        "Always keep a green main branch; roll back rather than forward-fix.\n",
        encoding="utf-8",
    )
    _rebuild_content_index(memory_root)

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
    over-broad leak while fixing global recall.

    Re-expressed for the content-FTS leg: the file lives under
    `projects/other-project/decisions/`, so `content_index._scope` assigns
    it `_scope == "other-project"` -- a NAMED scope distinct from
    "scoped-project", which `_content_row_to_decision` must still exclude.
    """
    _pin_scoped_project(workdir, "scoped-project")

    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])
    other_dir = memory_root / "projects" / "other-project" / "decisions"
    other_dir.mkdir(parents=True, exist_ok=True)
    (other_dir / "dec-other-001.md").write_text(
        "---\n"
        "title: other-project internal API contract v2\n"
        "type: decision\n"
        "date: 2026-06-01\n"
        "---\n"
        "Internal API contract v2 for other-project only.\n",
        encoding="utf-8",
    )
    _rebuild_content_index(memory_root)

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


def test_decisions_backend_never_raises_when_fts_db_absent(workdir: Path) -> None:
    """`read_decisions` must still return the file-scan leg's results, and
    must not raise, when the content-FTS index has never been built --
    the `workdir` fixture never calls `_rebuild_content_index`, so this
    exercises the ordinary "not built yet" path for every other test in
    this module too."""
    out, reasons = mf.read_decisions(workdir, query="baseline", limit=10)
    assert len(out) == 1
    assert out[0]["canonical_id"] == "decision-project-unscoped-arch-baseline-20260502-001"
    assert not any(r.startswith("content_index_error:") for r in reasons)


def test_decision_typed_lesson_doc_recalled_via_read_decisions(workdir: Path) -> None:
    """A decision-typed doc that lives in the `lessons/*.md` lane (not a
    `decisions/` directory) is reachable via `read_decisions` ONLY through
    the content-FTS leg -- `_resolve_decision_dirs`/`_scan_decision_files`
    never walk `lessons/`. This is the coverage claim (plan.md: "37 of the
    53 JSONL rows point at lessons/*.md decision-typed docs") that justifies
    reading the FTS index here instead of just deleting the leg (Path A)."""
    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])
    lessons_dir = memory_root / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    (lessons_dir / "2026-06-26-decision-navgator-test.md").write_text(
        "---\n"
        "title: NavGator cross-project knowledge base direction\n"
        "type: decision\n"
        "date: 2026-06-26\n"
        "---\n"
        "Decision captured in the lessons lane about NavGator's knowledge base.\n",
        encoding="utf-8",
    )
    _rebuild_content_index(memory_root)

    out, reasons = mf.read_decisions(workdir, query="navgator knowledge base", limit=10)
    ids = [d["canonical_id"] for d in out]
    assert "2026-06-26-decision-navgator-test" in ids, (
        f"expected the lessons-lane decision doc to be recalled; got {ids} "
        f"(reasons={reasons})"
    )
    hit = next(d for d in out if d["canonical_id"] == "2026-06-26-decision-navgator-test")
    assert hit["_source"] == "content"


def test_content_leg_ranks_by_frontmatter_date_not_file_mtime(workdir: Path) -> None:
    """The content-FTS leg must rank by the decision's own `created`/`date`
    frontmatter, not by `files.mtime_ns` (checkout time). A fresh clone or
    git worktree gives every file the SAME mtime, flattening recall's
    recency signal to a constant -- exactly the case this test forces by
    touching an OLD decision's mtime forward, so the fix would fail here for
    the right reason if it silently regressed to mtime."""
    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])
    decisions_dir = memory_root / "projects" / "_unscoped" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    old_decision = decisions_dir / "dec-old-but-touched.md"
    old_decision.write_text(
        "---\n"
        "title: Recency regression probe decision\n"
        "type: decision\n"
        "date: 2020-01-01\n"
        "---\n"
        "Old decision whose file gets touched recently by this test.\n",
        encoding="utf-8",
    )
    _rebuild_content_index(memory_root)
    # Touch the file forward AFTER indexing, so `files.mtime_ns` (recorded at
    # build time) and the on-disk mtime now disagree with the frontmatter
    # date -- and re-touch it, since some filesystems round mtime writes.
    recent_ts = 2_000_000_000  # 2033-05-18, far newer than 2020-01-01
    os.utime(old_decision, (recent_ts, recent_ts))
    assert old_decision.stat().st_mtime == recent_ts

    out, reasons = mf.read_decisions(workdir, query="recency regression probe", limit=10)
    hits = [d for d in out if d["canonical_id"] == "dec-old-but-touched"]
    assert hits, f"expected the probe decision to be recalled; reasons={reasons}"
    hit = hits[0]
    assert hit["_source"] == "content"

    old_date_ts = mf._parse_iso("2020-01-01")
    assert old_date_ts is not None
    # Ranked by the frontmatter date: within a second of the parsed 2020
    # timestamp, and nowhere near the 2033 mtime the file was touched to.
    assert abs(hit["_recency_ts"] - old_date_ts) < 1.0, (
        f"expected _recency_ts near frontmatter date {old_date_ts}, "
        f"got {hit['_recency_ts']} (file mtime is {recent_ts})"
    )
    assert abs(hit["_recency_ts"] - recent_ts) > 1.0


def test_content_leg_row_survives_merged_truncation_for_a_title_query(workdir: Path) -> None:
    """The crowding-out reservation removed from `_indexed_decisions` was
    dead code -- `content_index.query(..., limit=limit)` already applies
    `ORDER BY relevance LIMIT limit` in SQL before this function ever sees a
    row, so the reservation's truncation step was a no-op. But nothing
    previously guarded that a content-sourced row actually survives
    `read_decisions`'s own final `merged[:limit]` truncation for a
    realistic, title-targeted query. (The auditor measured 0/10 content
    rows surviving for a generic `query='decision'` at `limit=10` -- not a
    regression this fix addresses, but this locks down the title-targeted
    case that IS expected to work.)"""
    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])
    lessons_dir = memory_root / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    (lessons_dir / "2026-07-01-decision-truncation-guard.md").write_text(
        "---\n"
        "title: Truncation guard regression probe\n"
        "type: decision\n"
        "date: 2026-07-01\n"
        "---\n"
        "Content-only decision used to guard merged[:limit] truncation.\n",
        encoding="utf-8",
    )
    _rebuild_content_index(memory_root)

    out, reasons = mf.read_decisions(workdir, query="Truncation guard regression probe", limit=10)
    content_rows = [d for d in out if d["_source"] == "content"]
    assert content_rows, (
        f"expected a content-sourced decision row to survive merged[:limit]; got {out} (reasons={reasons})"
    )


def test_read_content_finds_docs_from_non_store_root_workdir(
    workdir: Path, tmp_path: Path
) -> None:
    """Regression for the `default_db_path(workdir)` bug: `read_content`
    must find store documents even when called from a workdir that is NOT
    the memory store root -- e.g. from a project repo, which is every real
    caller except a session running directly inside build-loop-memory."""
    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])
    _rebuild_content_index(memory_root)

    caller_workdir = tmp_path / "some-other-repo"
    caller_workdir.mkdir()
    assert caller_workdir != memory_root

    out, reasons = mf.read_content(caller_workdir, query="baseline", limit=5, project=None)
    assert out, f"expected content results from a non-store-root workdir; reasons={reasons}"
    assert any("baseline" in (r.get("title") or "").lower() for r in out)


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


# --- The FTS decision leg must not lose local decisions to the SQL LIMIT -----
# Three regressions the content-FTS swap introduced over the retired
# INDEX.jsonl leg, which read the whole file and filtered in Python.

def _write_decision(directory: Path, stem: str, title: str, body: str, date: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.md").write_text(
        f"---\ntitle: {title}\ntype: decision\ndate: {date}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_local_decisions_survive_foreign_high_ranking_rows(workdir: Path) -> None:
    """A content-index-only local decision must survive a flood of foreign
    decisions that all outrank it on the same query.

    `content_index.query` applies LIMIT inside SQL. Filtering foreign rows out
    afterwards in Python spends the whole limit on rows this project cannot
    see, so with enough higher-ranking foreign matches the leg returns ZERO
    local decisions while the store holds them. The foreign rows below repeat
    the query terms so they genuinely outrank the single local mention.
    """
    _pin_scoped_project(workdir, "scoped-project")
    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])

    for index in range(12):
        _write_decision(
            memory_root / "projects" / f"foreign-{index}" / "decisions",
            f"dec-foreign-{index:03d}",
            f"foreign retry backoff retry backoff policy {index}",
            "retry backoff retry backoff retry backoff retry backoff policy.",
            "2026-07-01",
        )
    _write_decision(
        memory_root / "projects" / "scoped-project" / "decisions",
        "dec-local-001",
        "scoped-project retry policy",
        "Our own retry policy for this project.",
        "2026-06-01",
    )
    _rebuild_content_index(memory_root)

    out, reasons = decisions_backend._indexed_decisions(workdir, "retry backoff policy", 5)
    ids = [d["canonical_id"] for d in out]

    assert "dec-local-001" in ids, (
        "the project's own decision was crowded out of the SQL LIMIT by foreign "
        f"higher-ranking rows; got {ids} (reasons={reasons})"
    )
    assert not any(i.startswith("dec-foreign-") for i in ids), (
        f"a different named project's decision leaked into recall: {ids}"
    )


def test_newer_global_decisions_cannot_zero_out_a_project_s_own(workdir: Path) -> None:
    """The crowding-out reservation: a burst of newer global decisions may
    narrow, but never erase, a project's view of its own recent decisions."""
    _pin_scoped_project(workdir, "scoped-project")
    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])

    for index in range(12):
        _write_decision(
            memory_root / "projects" / "_unscoped" / "decisions",
            f"dec-global-{index:03d}",
            f"global cache eviction policy {index}",
            "cache eviction policy cache eviction policy.",
            "2026-08-01",
        )
    _write_decision(
        memory_root / "projects" / "scoped-project" / "decisions",
        "dec-local-cache-001",
        "scoped-project cache eviction policy",
        "Our own cache eviction policy.",
        "2026-05-01",  # older than every global row above
    )
    _rebuild_content_index(memory_root)

    out, reasons = decisions_backend._indexed_decisions(workdir, "cache eviction policy", 10)
    ids = [d["canonical_id"] for d in out]

    assert "dec-local-cache-001" in ids, (
        "12 newer global decisions zeroed out the project's own decision; "
        f"got {ids} (reasons={reasons})"
    )
    assert any(i.startswith("dec-global-") for i in ids), (
        f"global decisions must stay visible, not be replaced wholesale: {ids}"
    )


def test_empty_query_browses_recent_decisions(workdir: Path) -> None:
    """`_q_match` treats an empty query as "matches everything", so the leg
    that replaced the whole-file reader has to browse rather than return
    nothing — otherwise a bare "show me recent decisions" recalls zero."""
    _pin_scoped_project(workdir, "scoped-project")
    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])

    _write_decision(
        memory_root / "projects" / "scoped-project" / "decisions",
        "dec-browse-local", "scoped-project browse target", "Local body.", "2026-06-01",
    )
    _write_decision(
        memory_root / "projects" / "_unscoped" / "decisions",
        "dec-browse-global", "global browse target", "Global body.", "2026-06-02",
    )
    _rebuild_content_index(memory_root)

    out, reasons = decisions_backend._indexed_decisions(workdir, "", 10)
    ids = [d["canonical_id"] for d in out]

    assert "dec-browse-local" in ids and "dec-browse-global" in ids, (
        f"empty query must browse the visible decision set; got {ids} (reasons={reasons})"
    )


def test_empty_query_browse_still_excludes_other_named_projects(workdir: Path) -> None:
    """The guard for the test above: browsing widens the query, never the scope."""
    _pin_scoped_project(workdir, "scoped-project")
    memory_root = Path(os.environ["AGENT_MEMORY_ROOT"])

    _write_decision(
        memory_root / "projects" / "other-project" / "decisions",
        "dec-browse-foreign", "other-project browse target", "Foreign body.", "2026-06-03",
    )
    _rebuild_content_index(memory_root)

    out, _ = decisions_backend._indexed_decisions(workdir, "", 10)

    assert "dec-browse-foreign" not in [d["canonical_id"] for d in out]
