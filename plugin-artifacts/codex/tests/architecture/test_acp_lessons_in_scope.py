"""Tests for build_acp's Phase 1 back-pressure (Chunk 8).

When .build-loop/architecture/lessons.json exists and a recent change set is
discoverable, the resulting ACP must populate lessons_in_scope[] with one
entry per lesson whose signature regex matches a changed file path or its
content sample.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_acp  # type: ignore  # noqa: E402
from build_loop.architecture.storage import arch_dir, atomic_write_json  # noqa: E402


# ---------- helpers ----------


def _seed_minimal_arch(repo: Path) -> None:
    """Write a one-component, no-connection scan to satisfy build_acp's reads."""
    arch = arch_dir(repo)
    arch.mkdir(parents=True, exist_ok=True)
    component = {
        "component_id": "COMP_storage",
        "name": "storage",
        "type": "component",
        "role": {"purpose": "storage", "layer": "infra", "critical": False},
        "source": {
            "detection_method": "auto",
            "config_files": ["src/build_loop/architecture/storage.py"],
            "confidence": 1.0,
        },
        "connects_to": [],
        "connected_from": [],
        "status": "active",
        "tags": [],
        "metadata": {"file": "src/build_loop/architecture/storage.py"},
        "timestamp": 0,
        "last_updated": 0,
        "stable_id": "stable-storage",
    }
    atomic_write_json(arch / "index.json", {
        "schema_version": "1.0.0",
        "components": [component],
        "connections": [],
    })
    atomic_write_json(arch / "manifest.json", {
        "schema_version": "1.0.0",
        "last_full_scan_at": 1700000000000,
        "last_incremental_at": 0,
        "generated_at": 1700000000000,
    })
    atomic_write_json(arch / "file_map.json", {
        "files": {"src/build_loop/architecture/storage.py": "COMP_storage"},
    })
    atomic_write_json(arch / "reverse-deps.json", {"reverse_deps": {}})


def _write_lessons(repo: Path, lessons: List[Dict[str, Any]]) -> None:
    arch = arch_dir(repo)
    arch.mkdir(parents=True, exist_ok=True)
    atomic_write_json(arch / "lessons.json", {
        "schema_version": "1.0.0",
        "lessons": lessons,
    })


# ---------- tests ----------


def test_lessons_in_scope_populated_when_signature_matches(monkeypatch, tmp_path):
    """A lesson whose regex matches a file in the recent change set lands in lessons_in_scope."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _seed_minimal_arch(repo)

    # Write the file we'll claim was recently changed; its content carries
    # the keyword that the regex would target if path-match falls through.
    target = repo / "src" / "build_loop" / "architecture" / "storage.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# circular import involving storage\n"
        "from .scanner import Scanner  # noqa\n",
        encoding="utf-8",
    )

    # Lesson with a signature that matches the file path.
    _write_lessons(repo, [
        {
            "id": "lesson-build-loop-test1",
            "category": "data-flow",
            "pattern": "circular dep involving storage",
            "signature": r"build_loop/architecture/storage\.py",
            "severity": "error",
            "promoted": True,
        },
    ])

    # Stub the git invocation: pretend HEAD~10 diff returned the storage file.
    def fake_recent_changed(repo_root, depth=10):  # noqa: ARG001
        return [target]

    monkeypatch.setattr(build_acp, "_git_recent_changed_files", fake_recent_changed)

    acp = build_acp.build_acp(repo)
    assert "lessons_in_scope" in acp
    assert len(acp["lessons_in_scope"]) == 1
    entry = acp["lessons_in_scope"][0]
    assert entry["id"] == "lesson-build-loop-test1"
    assert entry["category"] == "data-flow"
    assert entry["matched_signature"] == r"build_loop/architecture/storage\.py"
    assert entry["matched_file"].endswith("storage.py")


def test_lessons_in_scope_empty_when_no_match(monkeypatch, tmp_path):
    """Same lessons.json but unrelated change set → lessons_in_scope == []."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _seed_minimal_arch(repo)

    unrelated = repo / "docs" / "README.md"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("# unrelated\nNothing about storage here.\n", encoding="utf-8")

    _write_lessons(repo, [
        {
            "id": "lesson-build-loop-test2",
            "category": "data-flow",
            "pattern": "circular dep involving storage",
            "signature": r"build_loop/architecture/storage\.py",
            "severity": "error",
            "promoted": True,
        },
    ])

    monkeypatch.setattr(
        build_acp,
        "_git_recent_changed_files",
        lambda repo_root, depth=10: [unrelated],  # noqa: ARG005
    )

    acp = build_acp.build_acp(repo)
    assert acp["lessons_in_scope"] == []


def test_lessons_in_scope_handles_missing_lessons_file(monkeypatch, tmp_path):
    """No lessons.json → lessons_in_scope is an empty array, not an error."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _seed_minimal_arch(repo)

    monkeypatch.setattr(
        build_acp,
        "_git_recent_changed_files",
        lambda repo_root, depth=10: [],  # noqa: ARG005
    )

    acp = build_acp.build_acp(repo)
    assert acp["lessons_in_scope"] == []


def test_lessons_in_scope_handles_invalid_regex(monkeypatch, tmp_path):
    """A lesson with a malformed signature is silently skipped (not raised)."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _seed_minimal_arch(repo)

    target = repo / "src" / "build_loop" / "architecture" / "storage.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("placeholder\n", encoding="utf-8")

    _write_lessons(repo, [
        {
            "id": "bad-regex",
            "category": "data-flow",
            "pattern": "broken",
            "signature": r"[unclosed",  # invalid regex
            "severity": "warn",
            "promoted": True,
        },
        {
            "id": "good-regex",
            "category": "data-flow",
            "pattern": "good",
            "signature": r"storage\.py",
            "severity": "warn",
            "promoted": True,
        },
    ])

    monkeypatch.setattr(
        build_acp,
        "_git_recent_changed_files",
        lambda repo_root, depth=10: [target],  # noqa: ARG005
    )

    acp = build_acp.build_acp(repo)
    ids = {e["id"] for e in acp["lessons_in_scope"]}
    assert "good-regex" in ids
    assert "bad-regex" not in ids
