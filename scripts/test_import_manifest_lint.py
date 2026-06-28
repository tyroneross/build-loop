# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/import_manifest_lint.py — the import-vs-manifest lint.

Covers the named failure it earns its place against (the 2026-06 undeclared
pyyaml outage) and the false-positive guards that keep it green against THIS
repo's graceful-degradation pattern:

  - hard (unconditional, module-top-level) undeclared third-party import -> FINDING
  - try/except-guarded, function-local, if-guarded, class-body imports -> exempt
  - declared-via-alias (yaml -> pyyaml) -> exempt
  - stdlib + first-party (any module that exists in the repo) -> exempt
  - REGRESSION: the real shipped tree scans clean (exit 0).
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "import_manifest_lint.py"
_REPO = _SCRIPT.parent.parent  # build-loop repo root


def _load():
    spec = importlib.util.spec_from_file_location("import_manifest_lint", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


iml = _load()


def _mk_project(tmp_path: Path, *, deps=("pathspec",), test_extra=("pytest", "pyyaml")):
    pyproject = tmp_path / "pyproject.toml"
    deps_s = ", ".join(f'"{d}"' for d in deps)
    test_s = ", ".join(f'"{d}"' for d in test_extra)
    pyproject.write_text(textwrap.dedent(f"""
        [project]
        name = "t"
        dependencies = [{deps_s}]
        [project.optional-dependencies]
        test = [{test_s}]
    """))
    (tmp_path / "scripts").mkdir()
    return tmp_path


def _scan(repo: Path, body: str, *, roots=("scripts",), filename="scripts/m.py"):
    f = repo / filename
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(textwrap.dedent(body))
    declared = iml.declared_distributions(repo / "pyproject.toml")
    firstparty = iml.first_party_modules(repo)
    stdlib = iml.stdlib_modules()
    return iml.scan(repo, roots, declared=declared,
                    firstparty=firstparty, stdlib=stdlib)


# --- distribution parsing -------------------------------------------------


def test_declared_distributions_covers_core_extras_and_groups(tmp_path):
    (tmp_path / "pyproject.toml").write_text(textwrap.dedent("""
        [project]
        name = "t"
        dependencies = ["networkx>=3.1", "pathspec>=0.12"]
        [project.optional-dependencies]
        test = ["pytest>=8", "pyyaml"]
        db = ["psycopg[binary]>=3.1"]
        [dependency-groups]
        dev = ["pytest-timeout>=2.4.0"]
    """))
    got = iml.declared_distributions(tmp_path / "pyproject.toml")
    assert {"networkx", "pathspec", "pytest", "pyyaml", "psycopg",
            "pytest-timeout"} <= got


def test_norm_pep503():
    assert iml._norm("PyYAML") == "pyyaml"
    assert iml._norm("tree_sitter.typescript") == "tree-sitter-typescript"


# --- the core rule --------------------------------------------------------


def test_hard_undeclared_import_is_a_finding(tmp_path):
    repo = _mk_project(tmp_path)
    findings = _scan(repo, "import requests\n")
    assert [f.import_root for f in findings] == ["requests"]
    assert findings[0].dist_guess == "requests"


def test_declared_via_alias_is_exempt(tmp_path):
    repo = _mk_project(tmp_path)  # pyyaml in test extra
    findings = _scan(repo, "import yaml\n")
    assert findings == []


@pytest.mark.parametrize("body", [
    "try:\n    import numpy\nexcept ImportError:\n    numpy = None\n",
    "def f():\n    import boto3\n    return boto3\n",
    "import sys\nif sys.version_info >= (3, 12):\n    import tomllib\n",
    "class C:\n    import requests\n",
])
def test_guarded_or_local_imports_are_exempt(tmp_path, body):
    repo = _mk_project(tmp_path)
    assert _scan(repo, body) == []


def test_stdlib_is_exempt(tmp_path):
    repo = _mk_project(tmp_path)
    assert _scan(repo, "import os, sys, json, ast, subprocess\n") == []


def test_markdown_only_dir_does_not_mask_third_party(tmp_path):
    """A dir named like an import root but containing NO python must NOT
    reclassify a genuine undeclared third-party import as first-party
    (the false-negative class the tightened first_party rule closes)."""
    repo = _mk_project(tmp_path)
    (repo / "docs" / "requests").mkdir(parents=True)
    (repo / "docs" / "requests" / "x.md").write_text("not python")
    findings = _scan(repo, "import requests\n")
    assert [f.import_root for f in findings] == ["requests"]


def test_py_containing_dir_is_first_party(tmp_path):
    repo = _mk_project(tmp_path)
    (repo / "mypkg").mkdir()
    (repo / "mypkg" / "__init__.py").write_text("")
    assert _scan(repo, "import mypkg\n") == []


def test_first_party_sibling_is_exempt(tmp_path):
    repo = _mk_project(tmp_path)
    # A bare import that resolves to a repo file anywhere (sys.path trick).
    (repo / "scripts" / "helper_mod.py").write_text("x = 1\n")
    assert _scan(repo, "from helper_mod import x\n") == []


def test_relative_import_is_exempt(tmp_path):
    repo = _mk_project(tmp_path)
    assert _scan(repo, "from . import sibling\n") == []


# --- CLI exit codes -------------------------------------------------------


def test_main_exit_codes(tmp_path, capsys):
    repo = _mk_project(tmp_path)
    (repo / "scripts" / "ok.py").write_text("import os\n")
    assert iml.main(["--repo", str(repo), "--quiet"]) == 0
    (repo / "scripts" / "bad.py").write_text("import requests\n")
    assert iml.main(["--repo", str(repo), "--quiet"]) == 1


def test_main_missing_pyproject_exits_2(tmp_path):
    assert iml.main(["--repo", str(tmp_path)]) == 2


# --- regression against the real shipped tree -----------------------------


def test_real_repo_tree_scans_clean():
    """The shipped tree must pass — guards against re-introducing the
    false-positive class (sibling/optional imports misread as undeclared)."""
    assert iml.main(["--repo", str(_REPO), "--quiet"]) == 0
