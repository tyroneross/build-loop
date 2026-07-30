# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for readme_currency_check.py — Phase-4G README-currency gate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import readme_currency_check as rc  # noqa: E402

DEFAULT = {"enabled": True, "surfaceGlobs": rc._DEFAULT_SURFACE, "readmePaths": rc._DEFAULT_README}


def test_surface_changed_no_readme_warns():
    r = rc.evaluate(["skills/foo/SKILL.md", "scripts/x.py"], DEFAULT)
    assert r["verdict"] == "warn"
    assert r["surface_changes"] == ["skills/foo/SKILL.md"] and r["readme_touched"] is False


def test_surface_changed_with_readme_ok():
    r = rc.evaluate(["agents/bar.md", "README.md"], DEFAULT)
    assert r["verdict"] == "ok" and r["readme_touched"] is True


def test_no_surface_change_skips():
    r = rc.evaluate(["scripts/x.py", "tests/test_x.py"], DEFAULT)
    assert r["verdict"] == "skipped" and r["surface_changes"] == []


def test_disabled_skips():
    r = rc.evaluate(["commands/new.md"], {**DEFAULT, "enabled": False})
    assert r["verdict"] == "skipped"


def test_agents_md_counts_as_readme():
    # cross-tool doc surface: updating AGENTS.md satisfies the gate
    r = rc.evaluate(["commands/new.md", "AGENTS.md"], DEFAULT)
    assert r["verdict"] == "ok"


def test_docs_readme_variant_counts():
    r = rc.evaluate(["agents/bar.md", "docs/README-plugins.md"], DEFAULT)
    assert r["verdict"] == "ok"


def test_skill_glob_is_specific_to_SKILL_md():
    # a non-SKILL.md file under skills/ must NOT trip the surface glob
    r = rc.evaluate(["skills/foo/references/notes.md"], DEFAULT)
    assert r["verdict"] == "skipped"


def test_cli_and_bin_globs():
    r = rc.evaluate(["cli/main.py"], DEFAULT)
    assert r["verdict"] == "warn" and "cli/main.py" in r["surface_changes"]
