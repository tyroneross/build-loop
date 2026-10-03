#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Focused integration tests for repo_search's live and indexed evidence paths."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_search as searcher
from _paths import set_memory_workdir


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "sample-project"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True)
    (repo / "worker.py").write_text("def retry_queue():\n    return 'worker-marker'\n")
    subprocess.run(["git", "-C", str(repo), "add", "worker.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "Add retry queue worker"], check=True)
    return repo


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_content_query_is_live_and_does_not_require_an_index(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = searcher.search(repo, "worker-marker", kind="content")
    assert result["index"]["used"] is False
    assert not (repo / searcher.INDEX_REL).exists()
    assert [(hit["path"], hit["line"]) for hit in result["hits"]] == [("worker.py", 2)]
    assert result["content"]["complete"] is True


def test_stopword_query_does_not_claim_decision_coverage(tmp_path: Path) -> None:
    result = searcher.search(_repo(tmp_path), "the and for", kind="decision")
    assert result["terms_used"] == []
    assert result["complete"] is False
    assert result["decisions"]["complete"] is False
    assert "query_has_no_search_terms" in result["decisions"]["reasons"]


def test_decision_search_does_not_create_build_loop_state_in_new_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    set_memory_workdir(repo)
    try:
        result = searcher.search(repo, "retry queue worker", kind="decision", persist_index=False)
    finally:
        set_memory_workdir(None)
    assert result["index"]["used"] is True
    assert result["index"]["rebuilt"] is False
    assert not (repo / ".build-loop").exists()


def test_index_unifies_changes_runs_decisions_and_structure_without_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    memory = tmp_path / "memory"
    monkeypatch.setenv("BUILD_LOOP_MEMORY_STORE_ROOT", str(memory))
    decision = memory / "projects/sample-project/decisions/0001-retry.md"
    decision.parent.mkdir(parents=True)
    decision.write_text("---\ntitle: Retry queue decision\n---\nWorkers use a bounded retry queue.\n")
    _write_json(repo / ".build-loop/state.json", {
        "runs": [{"run_id": "RUN-1", "goal": "Ship retry queue worker", "outcome": "pass"}],
    })
    _write_json(repo / ".build-loop/architecture/index.json", {
        "components": [{"component_id": "COMP-worker", "name": "Retry worker",
                        "metadata": {"file": "worker.py"},
                        "role": {"purpose": "Processes retry queue"}}],
    })

    index = searcher.build_index(repo)

    ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q",
                              str(searcher.INDEX_REL)])
    assert ignored.returncode == 0

    assert index["coverage"]["changes"] == 1
    assert index["coverage"]["runs"] == 1
    assert index["coverage"]["decisions"] == 1
    assert index["coverage"]["structure"] == 1
    assert index["coverage"]["annotations"] == 0
    assert any(hit["kind"] == "decision" and hit["path"] == str(decision)
               for hit in searcher.search(repo, "bounded retry queue", kind="decision")["hits"])
    assert any(hit["kind"] == "structure" and hit["path"] == "worker.py"
               for hit in searcher.search(repo, "Retry worker", kind="structure")["hits"])
    assert any(hit["kind"] == "change" for hit in searcher.search(repo, "retry queue worker", kind="change")["hits"])
    assert any(hit["kind"] == "run" for hit in searcher.search(repo, "Ship retry queue", kind="run")["hits"])
    combined = searcher.search(repo, "retry queue", kind="all", limit=4)
    assert {hit["kind"] for hit in combined["hits"]} >= {"content", "decision", "structure"}


def test_ignored_running_decision_log_is_searched_by_section_and_refreshed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text(".build-loop/\n")
    log = repo / searcher.LOCAL_DECISION_LOG
    log.parent.mkdir(parents=True)
    log.write_text(
        "# Private project choice hidden from the index\n\n---\n\n"
        "## 2026-07-25 · Search intent stays deterministic\n"
        "**Decision.** Escalate only when rules cannot resolve intent.\n\n"
        "## 2026-07-27 · Release history belongs in the expanded view\n"
        "**Decision.** Keep the brief compact; history remains available.\n",
        encoding="utf-8",
    )
    assert subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", str(log)]).returncode == 0

    first = searcher.search(repo, "release history compact", kind="decision")
    assert first["index"]["coverage"]["local_docs"] == 1
    index = json.loads((repo / searcher.INDEX_REL).read_text())
    assert "Private project choice hidden" not in json.dumps(index)
    assert first["decisions"]["local_log_present"] is True
    assert first["decisions"]["local_sections"] == 2
    assert [(hit["line"], hit["title"]) for hit in first["hits"]
            if hit["source"] == "repo-local-decision-log"] == [
                (8, "2026-07-27 · Release history belongs in the expanded view")]
    assert first["hits"][0]["summary"] == ""

    log.write_text(log.read_text() + "\n## 2026-10-03 · Search has one results page\n"
                   "**Decision.** Search uses one entry point.\n", encoding="utf-8")
    second = searcher.search(repo, "search results page", kind="decision")
    assert second["index"]["rebuilt"] is True
    assert any(hit["title"].endswith("Search has one results page")
               for hit in second["hits"])


def test_decision_search_ranks_partial_body_matches_without_single_term_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    memory = tmp_path / "memory"
    monkeypatch.setenv("BUILD_LOOP_MEMORY_STORE_ROOT", str(memory))
    folder = memory / "projects" / repo.name / "decisions"
    folder.mkdir(parents=True)
    (folder / "strong.md").write_text("# Adaptive search\nUse shared precompute for search.")
    (folder / "weak.md").write_text("# Search\nOnly search is discussed here.")

    result = searcher.search(repo, "adaptive search precompute migration", kind="decision")

    assert result["hits"]
    assert result["hits"][0]["path"].endswith("strong.md")
    assert all(hit["matched_terms"] >= 2 for hit in result["hits"])


def test_source_change_rebuilds_metadata_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("BUILD_LOOP_MEMORY_STORE_ROOT", str(tmp_path / "memory"))
    first = searcher.search(repo, "worker", kind="structure")
    second = searcher.search(repo, "worker", kind="structure")
    assert first["index"]["rebuilt"] is True
    assert second["index"]["rebuilt"] is False

    _write_json(repo / ".build-loop/state.json", {"runs": [
        {"run_id": "RUN-2", "goal": "Change worker process", "outcome": "pass"},
    ]})
    third = searcher.search(repo, "worker", kind="run")
    assert third["index"]["rebuilt"] is True
    assert any(hit["kind"] == "run" for hit in third["hits"])


def test_python_fallback_reports_incomplete_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("no match")
    (repo / "z.txt").write_text("needle-marker")
    monkeypatch.setattr(searcher.shutil, "which", lambda _name: None)
    monkeypatch.setattr(searcher, "MAX_FALLBACK_FILES", 2)

    result = searcher.search(repo, "needle-marker", kind="content")

    assert result["content"]["engine"] == "python"
    assert result["content"]["complete"] is False
    assert result["content"]["scanned_files"] == 2
    assert result["complete"] is False


def test_python_fallback_respects_gitignore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text("credentials.json\n")
    (repo / "credentials.json").write_text("private-marker")
    monkeypatch.setattr(searcher.shutil, "which", lambda _name: None)

    result = searcher.search(repo, "private-marker", kind="content")

    assert result["hits"] == []
    assert result["content"]["engine"] == "python"
    assert result["content"]["complete"] is True


def test_python_fallback_continues_after_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    (repo / "a-large.txt").write_bytes(b"x" * (searcher.MAX_READ_BYTES + 1))
    worker = repo / "src/worker.py"
    worker.parent.mkdir()
    worker.write_text("late-marker\n")
    monkeypatch.setattr(searcher.shutil, "which", lambda _name: None)

    result = searcher.search(repo, "late-marker", kind="content")

    assert any(hit["path"] == "src/worker.py" for hit in result["hits"])
    assert "oversized_files_skipped" in result["content"]["reasons"]
    assert result["content"]["complete"] is False


def test_stopword_only_query_has_complete_cli_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repo(tmp_path)

    assert searcher.main(["query", "--workdir", str(repo), "--query", "a",
                          "--kind", "content"]) == 0
    assert "0 hits" in capsys.readouterr().out


def test_generated_and_secret_files_are_not_returned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    for relative in (".env.local", ".rally/worktrees/old/app.py", ".claude/worktrees/old/app.py"):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private-marker")

    assert searcher.search(repo, "private-marker", kind="content")["hits"] == []
    monkeypatch.setattr(searcher.shutil, "which", lambda _name: None)
    assert searcher.search(repo, "private-marker", kind="content")["hits"] == []


def test_candidate_limit_is_explicit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for number in range(4):
        (repo / f"f{number}.py").write_text("same-marker\n")

    result = searcher.search(repo, "same-marker", kind="content", max_files=2)

    assert result["content"]["candidate_files"] == 4
    assert result["content"]["inspected_files"] == 2
    assert result["content"]["truncated"] is True
    assert result["complete"] is False
