"""Tests for Priority 20 — backend_health canonical decisions probe.

P17's `backend_health.py` only probed legacy `.episodic/decisions/`, missing
the canonical store at
`~/dev/git-folder/build-loop-memory/projects/<project>/decisions/`.

P20 contract:
  1. probe_decisions returns a structured envelope with `legacy` and
     `canonical` sub-keys, each `{ok, count, path}`.
  2. The one-liner shows `decisions: OK <canonical_n> canonical + <legacy_n>
     legacy-diagnostic`.
  3. When the canonical store is missing, `decisions: DOWN canonical decision store missing`.
  4. The top-level `ok` / `count` keys remain (backward-compat with any
     pre-P20 consumer reading them flat).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backend_health as bh  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: synthetic workdir with both stores under tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture
def env_with_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workdir with both legacy AND canonical decision stores populated.

    Pins `AGENT_MEMORY_ROOT` to tmp_path / "_global" so the canonical resolver
    lands inside our control.
    """
    monkeypatch.delenv("BUILD_LOOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_STORE_ROOT", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "_no_home"))
    bh.set_debugger_runner(lambda: (False, "stubbed"))
    bh.set_semantic_runner(lambda: (False, "stubbed"))

    workdir = tmp_path / "project"
    workdir.mkdir()
    (workdir / ".build-loop").mkdir()
    (workdir / ".build-loop" / "state.json").write_text(
        json.dumps({"runs": []}), encoding="utf-8",
    )

    # Legacy store: 2 entries
    legacy = workdir / ".episodic" / "decisions"
    legacy.mkdir(parents=True)
    (legacy / "001-legacy-a.md").write_text("# x", encoding="utf-8")
    (legacy / "002-legacy-b.md").write_text("# y", encoding="utf-8")

    # Canonical store: 3 entries under the project's resolved tag.
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path / "_global"))

    # The project_resolver fall-back uses the workdir's basename when no
    # `.git` or `pyproject.toml` carries an explicit project name. Use the
    # resolver directly to find the project tag, then create that subdir.
    from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
    proj = resolve_project(workdir) or "_unscoped"
    canonical_dir = tmp_path / "_global" / "projects" / proj / "decisions"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "100-canonical-a.md").write_text("# a", encoding="utf-8")
    (canonical_dir / "101-canonical-b.md").write_text("# b", encoding="utf-8")
    (canonical_dir / "102-canonical-c.md").write_text("# c", encoding="utf-8")

    yield workdir

    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)


@pytest.fixture
def env_canonical_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workdir with NO legacy store; canonical present with N entries."""
    monkeypatch.delenv("BUILD_LOOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_STORE_ROOT", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "_no_home"))
    bh.set_debugger_runner(lambda: (False, "stubbed"))
    bh.set_semantic_runner(lambda: (False, "stubbed"))

    workdir = tmp_path / "project"
    workdir.mkdir()
    (workdir / ".build-loop").mkdir()
    (workdir / ".build-loop" / "state.json").write_text(
        json.dumps({"runs": []}), encoding="utf-8",
    )
    # No legacy `.episodic/decisions/`.

    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path / "_global"))

    from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
    proj = resolve_project(workdir) or "_unscoped"
    canonical_dir = tmp_path / "_global" / "projects" / proj / "decisions"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "100-canonical.md").write_text("# c", encoding="utf-8")

    yield workdir

    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)


@pytest.fixture
def env_neither(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workdir with NEITHER legacy nor canonical decisions present."""
    monkeypatch.delenv("BUILD_LOOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_STORE_ROOT", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "_no_home"))
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path / "_no_canonical"))
    bh.set_debugger_runner(lambda: (False, "stubbed"))
    bh.set_semantic_runner(lambda: (False, "stubbed"))

    workdir = tmp_path / "project"
    workdir.mkdir()
    (workdir / ".build-loop").mkdir()
    (workdir / ".build-loop" / "state.json").write_text(
        json.dumps({"runs": []}), encoding="utf-8",
    )
    # No legacy, no canonical.

    yield workdir

    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_envelope_has_legacy_and_canonical_subkeys(env_with_both: Path) -> None:
    """probe_decisions exposes both `legacy` and `canonical` sub-keys with
    `{ok, count, path}` shape."""
    env = bh.run_health_check(env_with_both)
    decisions = env["decisions"]

    assert "legacy" in decisions, "decisions envelope missing 'legacy' sub-key"
    assert "canonical" in decisions, "decisions envelope missing 'canonical' sub-key"

    for sub in (decisions["legacy"], decisions["canonical"]):
        assert "ok" in sub
        assert "count" in sub
        assert "path" in sub

    assert decisions["legacy"]["ok"] is True
    assert decisions["legacy"]["count"] == 2
    assert decisions["canonical"]["ok"] is True
    assert decisions["canonical"]["count"] == 3


def test_summary_shows_legacy_plus_canonical_split(env_with_both: Path) -> None:
    """One-liner format: canonical count first, legacy diagnostic second."""
    env = bh.run_health_check(env_with_both)
    summary = env["summary"]
    assert "3 canonical + 2 legacy-diagnostic" in summary, (
        f"summary missing split format: {summary!r}"
    )


def test_top_level_ok_and_count_preserved_for_backward_compat(env_with_both: Path) -> None:
    """Pre-P20 consumers reading `env['decisions']['ok']` / `['count']` flat
    must keep working. Top-level count is the sum of both stores."""
    env = bh.run_health_check(env_with_both)
    decisions = env["decisions"]
    assert decisions["ok"] is True
    assert decisions["count"] == 3
    assert "duration_ms" in decisions


def test_canonical_only_works_legacy_zero(env_canonical_only: Path) -> None:
    """Mock only canonical → still ok, legacy.ok=False, top-level ok=True."""
    env = bh.run_health_check(env_canonical_only)
    decisions = env["decisions"]
    assert decisions["ok"] is True
    assert decisions["legacy"]["ok"] is False
    assert decisions["legacy"]["count"] == 0
    assert decisions["canonical"]["ok"] is True
    assert decisions["canonical"]["count"] == 1
    assert "1 canonical + 0 legacy-diagnostic" in env["summary"]


def test_both_missing_emits_no_decision_stores(env_neither: Path) -> None:
    """Mock both stores missing → canonical store down."""
    env = bh.run_health_check(env_neither)
    decisions = env["decisions"]
    assert decisions["ok"] is False
    assert "canonical decision store missing" in decisions.get("reason", "")
    assert "canonical decision store missing" in env["summary"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
