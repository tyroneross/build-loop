"""Tests for the rename-detection WARNING (actuates the dormant `dir_missing`
signal in `backend_health.probe_decisions`).

2026-07-09 control-plane RCA (P0-4): `derive_slug_from_cwd` had no durable
pin, so renaming `RossLabs-AI-Assistant` silently orphaned 7 lessons under
the old `ai-assistant` slug — `backend_health.py` already computed
`dir_missing` for the canonical store, but nothing routed that into a
surfaced warning. This file proves the routing:

  1. `dir_missing` + a sibling `projects/<other-slug>/` with content ->
     a `renameWarning` on `decisions.canonical` + a WARNING line in the
     one-liner summary naming the likely old slug + remediation.
  2. `dir_missing` with NO sibling content -> no `renameWarning` (quiet;
     matches the pre-existing "canonical decision store missing" reason).
  3. Canonical store PRESENT -> no `renameWarning`, even if unrelated
     sibling project dirs exist.
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


def _base_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("BUILD_LOOP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_STORE_ROOT", raising=False)
    monkeypatch.delenv("BUILD_LOOP_MEMORY_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "_no_home"))
    monkeypatch.setenv("AGENT_MEMORY_ROOT", str(tmp_path / "_global"))
    bh.set_debugger_runner(lambda: (False, "stubbed"))
    bh.set_semantic_runner(lambda: (False, "stubbed"))

    workdir = tmp_path / "RossLabs-AI-Assistant"
    workdir.mkdir()
    (workdir / ".build-loop").mkdir()
    (workdir / ".build-loop" / "state.json").write_text(
        json.dumps({"runs": []}), encoding="utf-8",
    )
    (workdir / ".git").mkdir()
    (workdir / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return workdir


@pytest.fixture
def env_renamed_with_sibling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Canonical store missing for the CURRENT slug; a similarly-named
    sibling project dir with content exists — the rename signature."""
    workdir = _base_env(tmp_path, monkeypatch)
    # Old-slug sibling with content — current slug derives to
    # "rosslabs-ai-assistant"; old slug "ai-assistant" is a substring, so
    # the similarity heuristic should pick it as the likely match.
    old = tmp_path / "_global" / "projects" / "ai-assistant" / "lessons"
    old.mkdir(parents=True)
    (old / "feedback_one.md").write_text("# one", encoding="utf-8")
    (old / "feedback_two.md").write_text("# two", encoding="utf-8")
    yield workdir
    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)


@pytest.fixture
def env_missing_no_siblings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Canonical store missing; no sibling project dirs exist at all."""
    workdir = _base_env(tmp_path, monkeypatch)
    yield workdir
    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)


@pytest.fixture
def env_missing_only_empty_siblings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Canonical store missing; sibling project dirs exist but are empty
    (no decisions/ or lessons/ markdown) — must not trigger the warning."""
    workdir = _base_env(tmp_path, monkeypatch)
    empty_sibling = tmp_path / "_global" / "projects" / "some-other-project"
    empty_sibling.mkdir(parents=True)
    yield workdir
    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)


@pytest.fixture
def env_canonical_present_with_unrelated_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Canonical store PRESENT for the current slug; an unrelated sibling
    project dir with content also exists. No warning should fire."""
    workdir = _base_env(tmp_path, monkeypatch)
    from project_resolver import resolve_project  # type: ignore  # noqa: PLC0415
    proj = resolve_project(workdir) or "_unscoped"
    canonical_dir = tmp_path / "_global" / "projects" / proj / "decisions"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "001-current.md").write_text("# current", encoding="utf-8")

    other = tmp_path / "_global" / "projects" / "totally-unrelated" / "lessons"
    other.mkdir(parents=True)
    (other / "feedback.md").write_text("# unrelated", encoding="utf-8")

    yield workdir
    bh.set_debugger_runner(None)
    bh.set_semantic_runner(None)


def test_rename_warning_fires_with_content_sibling(env_renamed_with_sibling: Path) -> None:
    env = bh.run_health_check(env_renamed_with_sibling)
    canonical = env["decisions"]["canonical"]
    assert canonical["reason"] == "dir_missing"
    warning = canonical.get("renameWarning")
    assert warning is not None, "expected renameWarning to be populated"
    assert warning["likely_old_slug"] == "ai-assistant"
    assert warning["likely_old_slug_file_count"] == 2
    assert warning["current_slug"] == "rosslabs-ai-assistant"

    summary = env["summary"]
    assert "WARNING" in summary
    assert "ai-assistant" in summary
    assert "memoryProjectSlug" in summary


def test_rename_warning_quiet_when_no_siblings(env_missing_no_siblings: Path) -> None:
    env = bh.run_health_check(env_missing_no_siblings)
    canonical = env["decisions"]["canonical"]
    assert canonical["reason"] == "dir_missing"
    assert canonical.get("renameWarning") is None
    assert "WARNING" not in env["summary"]


def test_rename_warning_quiet_when_siblings_empty(
    env_missing_only_empty_siblings: Path,
) -> None:
    env = bh.run_health_check(env_missing_only_empty_siblings)
    canonical = env["decisions"]["canonical"]
    assert canonical["reason"] == "dir_missing"
    assert canonical.get("renameWarning") is None
    assert "WARNING" not in env["summary"]


def test_rename_warning_quiet_when_canonical_present(
    env_canonical_present_with_unrelated_sibling: Path,
) -> None:
    """Even with an unrelated sibling project holding content, a present
    canonical store must never trigger the warning — it's scoped strictly
    to the `dir_missing` signal."""
    env = bh.run_health_check(env_canonical_present_with_unrelated_sibling)
    canonical = env["decisions"]["canonical"]
    assert canonical["ok"] is True
    assert "reason" not in canonical
    assert canonical.get("renameWarning") is None
    assert "WARNING" not in env["summary"]


def test_detect_possible_rename_never_raises_on_missing_inputs() -> None:
    """Direct unit check on the advisory helper's degrade-gracefully contract."""
    assert bh.detect_possible_rename(None, Path("/nonexistent")) is None
    assert bh.detect_possible_rename("slug", None) is None
    assert bh.detect_possible_rename("slug", Path("/definitely/not/a/real/path")) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
