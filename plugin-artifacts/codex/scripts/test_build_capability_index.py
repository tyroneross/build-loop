#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for build_capability_index.py (WP-B). Stdlib only."""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("build_capability_index", HERE / "build_capability_index.py")
bci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bci)


def _mk_repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    return tmp_path


# ---- Ring 1: scripts via AST -----------------------------------------------

def test_ring1_indexes_script_with_docstring_and_has_test(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "scripts" / "foo.py").write_text('"""Foo helper — does a thing.\n\nMore."""\nx = 1\n', encoding="utf-8")
    (repo / "scripts" / "test_foo.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    entries = bci.index_scripts(repo / "scripts")
    foo = next(e for e in entries if e["name"] == "foo")
    assert foo["doc"] == "Foo helper — does a thing."
    assert foo["has_test"] is True
    assert foo["ring"] == 1
    assert foo["error"] is None


def test_ring1_skips_test_and_dunder_files(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "scripts" / "real.py").write_text('"""Real."""\n', encoding="utf-8")
    (repo / "scripts" / "test_real.py").write_text("x=1\n", encoding="utf-8")
    (repo / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    names = {e["name"] for e in bci.index_scripts(repo / "scripts")}
    assert names == {"real"}


def test_ring1_records_parse_error_without_dropping_entry(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "scripts" / "broken.py").write_text("def (:\n", encoding="utf-8")  # syntax error
    entries = bci.index_scripts(repo / "scripts")
    broken = next(e for e in entries if e["name"] == "broken")
    assert broken["error"] is not None
    assert "SyntaxError" in broken["error"]


def test_ring1_no_test_flagged_false(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "scripts" / "lonely.py").write_text('"""Lonely."""\n', encoding="utf-8")
    entries = bci.index_scripts(repo / "scripts")
    assert next(e for e in entries if e["name"] == "lonely")["has_test"] is False


# ---- Ring 2: consumer surfaces ---------------------------------------------

def test_ring2_package_json_scripts(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "package.json").write_text(json.dumps({"scripts": {"build": "tsc", "test": "vitest"}}), encoding="utf-8")
    entries = bci.index_consumer_surfaces(repo)
    npm = {e["name"]: e for e in entries if e["kind"] == "npm_script"}
    assert set(npm) == {"build", "test"}
    assert npm["build"]["ring"] == 2


def test_ring2_makefile_targets(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "Makefile").write_text("build:\n\tgo build\n\n.PHONY: build\ntest:\n\tgo test\n", encoding="utf-8")
    targets = {e["name"] for e in bci.index_consumer_surfaces(repo) if e["kind"] == "make_target"}
    assert "build" in targets and "test" in targets
    assert not any(t.startswith(".") for t in targets)  # .PHONY skipped


def test_ring2_pyproject_scripts(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        "[project.scripts]\nmycli = \"pkg.module:main\"\n", encoding="utf-8"
    )
    entries = [e for e in bci.index_consumer_surfaces(repo) if e["kind"] == "pyproject_script"]
    assert any(e["name"] == "mycli" for e in entries)


def test_ring2_absent_surfaces_yield_nothing(tmp_path):
    repo = _mk_repo(tmp_path)
    assert bci.index_consumer_surfaces(repo) == []


# ---- Freshness + atomic build ----------------------------------------------

def test_build_and_cache_then_fresh(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "scripts" / "a.py").write_text('"""A."""\n', encoding="utf-8")
    payload = bci.ensure_index(repo, force=True)
    assert payload["ring1_count"] == 1
    assert (repo / bci.INDEX_REL).is_file()
    fresh, reason = bci.is_fresh(repo)
    assert fresh and reason == "fresh"


def test_changed_script_invalidates_cache(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "scripts" / "a.py").write_text('"""A."""\n', encoding="utf-8")
    bci.ensure_index(repo, force=True)
    time.sleep(0.01)
    # Add a new script → fingerprint changes → not fresh.
    (repo / "scripts" / "b.py").write_text('"""B."""\n', encoding="utf-8")
    fresh, reason = bci.is_fresh(repo)
    assert not fresh and reason == "changed"


def test_missing_cache_is_not_fresh(tmp_path):
    repo = _mk_repo(tmp_path)
    fresh, reason = bci.is_fresh(repo)
    assert not fresh and reason == "missing"


def test_aged_cache_is_not_fresh(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "scripts" / "a.py").write_text('"""A."""\n', encoding="utf-8")
    bci.ensure_index(repo, force=True)
    # max_age=0 forces the age check to fail even though content is unchanged.
    fresh, reason = bci.is_fresh(repo, max_age_seconds=0)
    assert not fresh and reason == "aged"


def test_index_not_committed_marker_present(tmp_path):
    # The index carries the ring-3 note so a reader knows it's intentionally
    # scoped to rings 1-2 (plugins/MCP/PATH = verify-on-use).
    repo = _mk_repo(tmp_path)
    payload = bci.build_index(repo)
    assert "verify-on-use" in payload["ring3_note"]


def test_main_check_and_build(tmp_path):
    repo = _mk_repo(tmp_path)
    (repo / "scripts" / "a.py").write_text('"""A."""\n', encoding="utf-8")
    assert bci.main(["--workdir", str(repo), "--force", "--json"]) == 0
    assert bci.main(["--workdir", str(repo), "--check", "--json"]) == 0


def test_main_missing_workdir_exits_2(tmp_path):
    assert bci.main(["--workdir", str(tmp_path / "nope")]) == 2


def test_real_repo_builds_clean():
    # The actual build-loop repo must index without raising; ring1 should find
    # many scripts and the index file is gitignored (repo-local runtime).
    repo = HERE.parent
    payload = bci.build_index(repo)
    assert payload["ring1_count"] > 50  # build-loop has ~280 scripts
    # No indexed ring-1 entry should be a test_ file.
    assert not any(e["name"].startswith("test_") for e in payload["entries"] if e["ring"] == 1)
