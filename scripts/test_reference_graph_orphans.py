#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for reference_graph_orphans.py (WP-E item 2). Stdlib only."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("reference_graph_orphans", HERE / "reference_graph_orphans.py")
rgo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rgo)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "skills").mkdir()
    return tmp_path


def test_referenced_by_skill_is_not_orphan(tmp_path):
    repo = _repo(tmp_path)
    (repo / "scripts" / "used.py").write_text('"""Used."""\n', encoding="utf-8")
    (repo / "skills" / "s.md").write_text("Run `scripts/used.py` at Phase 1.\n", encoding="utf-8")
    result = rgo.find_orphans(repo)
    assert "used" not in result["orphans"]


def test_unreferenced_script_is_orphan_candidate(tmp_path):
    repo = _repo(tmp_path)
    (repo / "scripts" / "lonely.py").write_text('"""Lonely."""\n', encoding="utf-8")
    result = rgo.find_orphans(repo)
    assert "lonely" in result["orphans"]


def test_referenced_by_python_import_is_not_orphan(tmp_path):
    repo = _repo(tmp_path)
    (repo / "scripts" / "lib.py").write_text('"""Lib."""\nVALUE = 1\n', encoding="utf-8")
    (repo / "scripts" / "consumer.py").write_text("from lib import VALUE\n", encoding="utf-8")
    result = rgo.find_orphans(repo)
    assert "lib" not in result["orphans"]
    # consumer is itself unreferenced → orphan candidate (correct).
    assert "consumer" in result["orphans"]


def test_own_file_and_test_do_not_count_as_reference(tmp_path):
    repo = _repo(tmp_path)
    (repo / "scripts" / "solo.py").write_text('"""Solo references solo.py in its own docstring."""\n', encoding="utf-8")
    (repo / "scripts" / "test_solo.py").write_text("import solo  # noqa\n", encoding="utf-8")
    result = rgo.find_orphans(repo)
    # The test_solo.py import is the colocated test — must NOT rescue solo from orphan.
    assert "solo" in result["orphans"]


def test_test_files_and_dunder_not_checked(tmp_path):
    repo = _repo(tmp_path)
    (repo / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "scripts" / "test_x.py").write_text("x=1\n", encoding="utf-8")
    (repo / "scripts" / "real.py").write_text('"""Real."""\n', encoding="utf-8")
    result = rgo.find_orphans(repo)
    assert result["checked"] == 1  # only real.py
    assert result["orphans"] == ["real"]


def test_note_marks_propose_not_delete(tmp_path):
    repo = _repo(tmp_path)
    (repo / "scripts" / "a.py").write_text('"""A."""\n', encoding="utf-8")
    result = rgo.find_orphans(repo)
    assert "never auto-delete" in result["note"] or "PROPOSE" in result["note"]


def test_main_json_and_output(tmp_path):
    repo = _repo(tmp_path)
    (repo / "scripts" / "a.py").write_text('"""A."""\n', encoding="utf-8")
    out = tmp_path / "report.json"
    assert rgo.main(["--workdir", str(repo), "--json", "--output", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["orphan_count"] == 1


def test_main_missing_workdir_exits_2(tmp_path):
    assert rgo.main(["--workdir", str(tmp_path / "nope")]) == 2


def test_real_repo_runs_clean():
    # The actual repo must scan without error and return a list (possibly empty).
    repo = HERE.parent
    result = rgo.find_orphans(repo)
    assert result["error"] is None
    assert result["checked"] > 50
    assert isinstance(result["orphans"], list)
