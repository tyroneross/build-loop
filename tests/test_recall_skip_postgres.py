"""Tests for Priority 21 — recall(skip_postgres=True) bypass.

Phase 5 Iterate may run dozens of `recall()` calls during the
memory-first gate. When P17's backend_health probe writes
`state.json.architecture.backendHealth.semantic.ok = false`, every call
otherwise wastes a 3-second psycopg connect_timeout per attempt.

P21 fix: `recall()` and `read_semantic()` accept `skip_postgres: bool=False`.
When True, the semantic backend is bypassed entirely (no env-var check, no
psycopg import attempt, no connection attempt). The `reasons[]` envelope
records `skipped_postgres` — a distinct token from `db_unavailable: ...`
so consumers can tell intentional skip from genuine backend-down.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import memory_facade as mf  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: clean workdir, debugger stubbed, BUILD_LOOP_DATABASE_URL set so
# the non-skip path WOULD attempt the connection (proving the skip flag
# short-circuits that attempt).
# ---------------------------------------------------------------------------

@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workdir with no semantic data and the debugger stub returning empty."""
    monkeypatch.setenv("BUILD_LOOP_DATABASE_URL", "postgres://stub-host/db")
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path / "_no_canonical"))
    # Stub debugger so the npx CLI is never spawned.
    mf.set_debugger_runner(lambda query, limit, project: '{"incidents": []}')
    yield tmp_path
    mf.set_debugger_runner(None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_skip_postgres_flag_excludes_semantic_backend(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When skip_postgres=True, psycopg is never imported and no connection
    attempt happens. We prove this by failing-loud on import attempts."""
    # Sentinel: replace `import psycopg` resolution with a guard that raises
    # if `read_semantic` ever reaches the import line.
    sentinel_called = {"hit": False}

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "psycopg":
            sentinel_called["hit"] = True
            raise AssertionError(
                "psycopg import attempted while skip_postgres=True"
            )
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)

    env = mf.recall("foo", skip_postgres=True, workdir=workdir)
    assert sentinel_called["hit"] is False
    # Semantic results must be empty.
    assert env["results_by_kind"]["semantic"] == []


def test_skip_postgres_envelope_marks_skipped(workdir: Path) -> None:
    """`reasons[]` carries `skipped_postgres`, not `postgres_unavailable` or
    `db_unavailable: ...`. This token is the contract Phase 5 reads to
    confirm the skip was intentional."""
    env = mf.recall("foo", skip_postgres=True, workdir=workdir)
    reasons = env.get("reasons") or []
    assert "skipped_postgres" in reasons, f"reasons missing skipped_postgres: {reasons}"
    # The genuine-down tokens must NOT be present (proves the bypass took
    # over before the env-var / import probes ran).
    for r in reasons:
        assert not r.startswith("db_unavailable"), (
            f"skip_postgres path emitted db_unavailable token: {r}"
        )
        assert not r.startswith("postgres_unavailable"), (
            f"skip_postgres path emitted postgres_unavailable token: {r}"
        )


def test_skip_postgres_default_off_preserves_legacy_behavior(workdir: Path) -> None:
    """Sanity guard: omitting the flag keeps the pre-P21 behavior. Since the
    BUILD_LOOP_DATABASE_URL points at a stub host that won't connect, we
    expect a `db_unavailable:` token (NOT `skipped_postgres`)."""
    env = mf.recall("foo", workdir=workdir)
    reasons = env.get("reasons") or []
    assert "skipped_postgres" not in reasons, (
        f"default path leaked skipped_postgres: {reasons}"
    )
    # At least one db_unavailable reason must surface (psycopg missing or
    # connection failure depending on host); both are valid pre-P21 tokens.
    assert any(r.startswith("db_unavailable") for r in reasons), (
        f"default path didn't emit any db_unavailable token: {reasons}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
