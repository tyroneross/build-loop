#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""import_manifest_lint.py — catch undeclared third-party imports before CI.

Named, observed failure this control earns its place against: the 2026-06 CI
outage was an undeclared ``pyyaml`` import. ``import yaml`` sat at module top
level in ``scripts/architecture_diagram/generate.py`` but PyYAML was declared in
NO manifest — it lived only in an inline ``pip install pyyaml`` step. A fresh
``uv sync --extra test`` therefore lacked it and the gate broke on main.

What it does
------------
Walks every ``.py`` under the scanned roots (default: ``scripts/`` + ``tests/``),
collects each *hard* third-party import, and fails if any is not declared in
``pyproject.toml`` (core ``dependencies`` + every ``optional-dependencies`` extra
+ every ``dependency-groups`` group).

Definitions that keep this false-positive-free against THIS repo's patterns:

- **hard import** — an ``import``/``from`` statement that is an *unconditional,
  module-top-level* statement: none of its AST ancestors is a ``try``, ``if``,
  ``for``/``while``/``with``, function, lambda, or class. The pyyaml outage was
  exactly this shape. The repo's graceful-degradation pattern (``try: import
  numpy except ImportError`` in ``optimize_doe.py``; function-local ``from
  mlx_embeddings import …`` in ``embed_backend.py``) is GUARDED and therefore
  exempt — those optional deps are undeclared *by design*.
- **first-party** — any module/package name that exists ANYWHERE in this repo
  (every ``.py`` stem + every directory that CONTAINS python). This covers
  ``sys.path``-insertion sibling imports (e.g. ``from layout_fill import …`` in
  ``tests/test_layout_fill.py``, resolved from ``skills/native-ax-driver/
  scripts/``) and intra-package bare imports (``from post import post`` inside
  ``scripts/rally_point/``).
- **stdlib** — ``sys.stdlib_module_names`` of the running interpreter (the CI
  gate of record runs 3.11) plus a small curated supplement.

Anything left after subtracting stdlib + first-party is a third-party root; it
must resolve (via the import→distribution alias map, then PEP 503
normalization) to a declared distribution, else it is a finding.

Usage
-----
  python3 scripts/import_manifest_lint.py [--repo PATH] [--roots scripts tests]
                                          [--json] [--quiet]

Exit codes
----------
- 0  no undeclared hard third-party imports.
- 1  one or more findings.
- 2  the lint itself could not run (pyproject missing/unparseable).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:  # 3.11+ stdlib; the requires-python floor guarantees it on CI.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - pre-3.11 only
    tomllib = None  # type: ignore[assignment]

# Directories never scanned for sources NOR mined for first-party names.
_IGNORE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", "build-loop.worktrees", "plugin-artifacts",
    "dist", "build", ".ci-rally-apps",
}

# AST node types that make a contained import CONDITIONAL/LOCAL (i.e. not a
# hard top-level import). An import under any of these is treated as guarded.
_GUARD_NODES = (
    ast.Try,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.If,
    ast.While,
    ast.For,
    ast.AsyncFor,
    ast.With,
    ast.AsyncWith,
    ast.ExceptHandler,
)

# import-root -> PyPI distribution, for the cases where they differ. Kept small
# and only what THIS repo actually needs; extend when a real divergence appears.
_IMPORT_TO_DIST = {
    "yaml": "pyyaml",
    "tree_sitter": "tree-sitter",
    "tree_sitter_typescript": "tree-sitter-typescript",
    "sentence_transformers": "sentence-transformers",
    "dateutil": "python-dateutil",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml_include": "pyyaml-include",
}

# stdlib names that may be absent from sys.stdlib_module_names on some builds,
# plus the bootstrap/meta roots. Belt-and-suspenders; harmless if redundant.
_STDLIB_EXTRA = {
    "__future__", "__main__", "tomllib", "zoneinfo", "graphlib",
}


def _norm(name: str) -> str:
    """PEP 503 distribution-name normalization."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


# ---------------------------------------------------------------------------
# pyproject — declared distributions
# ---------------------------------------------------------------------------


def _req_dist_name(requirement: str) -> str | None:
    """Extract the distribution name from a PEP 508 requirement string."""
    req = requirement.strip()
    if not req or req.startswith("#"):
        return None
    # Cut at the first extras bracket / version / marker / url boundary.
    name = re.split(r"[\s<>=!~;,\[\(@]", req, maxsplit=1)[0]
    return name or None


def declared_distributions(pyproject: Path) -> set[str]:
    """All distribution names declared across core + extras + dependency-groups."""
    if tomllib is None:  # pragma: no cover
        raise RuntimeError("tomllib unavailable (need Python >= 3.11)")
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dists: set[str] = set()

    project = data.get("project", {})
    for dep in project.get("dependencies", []) or []:
        n = _req_dist_name(dep)
        if n:
            dists.add(_norm(n))
    for _extra, deps in (project.get("optional-dependencies", {}) or {}).items():
        for dep in deps or []:
            n = _req_dist_name(dep)
            if n:
                dists.add(_norm(n))
    # PEP 735 dependency-groups: list of requirement strings or
    # {"include-group": "name"} dicts (the include is a group ref, not a dist).
    for _grp, entries in (data.get("dependency-groups", {}) or {}).items():
        for entry in entries or []:
            if isinstance(entry, str):
                n = _req_dist_name(entry)
                if n:
                    dists.add(_norm(n))
    return dists


# ---------------------------------------------------------------------------
# first-party + stdlib name universes
# ---------------------------------------------------------------------------


def first_party_modules(repo: Path) -> set[str]:
    """Every module/package name that exists anywhere in the repo.

    Comprehensive on purpose: bare/sibling imports resolve via runtime
    ``sys.path`` insertion, so a module is first-party if a file with that
    name exists *somewhere* in this tree, not only on the import path.
    """
    names: set[str] = set()
    for py in repo.rglob("*.py"):
        rel = py.relative_to(repo)
        if any(p in _IGNORE_DIRS for p in rel.parts):
            continue
        names.add(py.stem)
        # Only directories that actually CONTAIN python are importable package
        # names. Admitting every dir repo-wide (incl. markdown-only docs/,
        # references/) would let a third-party dist whose import root collides
        # with such a dir be silently reclassified first-party and missed — a
        # false negative for the exact class this lint exists to catch.
        for part in rel.parts[:-1]:
            names.add(part)
    names.discard("")
    return names


def stdlib_modules() -> set[str]:
    base = set(getattr(sys, "stdlib_module_names", set()))
    return base | _STDLIB_EXTRA


# ---------------------------------------------------------------------------
# AST scan
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    file: str
    lineno: int
    import_root: str
    dist_guess: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "lineno": self.lineno,
            "import_root": self.import_root,
            "dist_guess": self.dist_guess,
        }


def _hard_imports(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    """Return import statements that are unconditional + module-top-level."""
    hard: list[ast.Import | ast.ImportFrom] = []

    def rec(node: ast.AST, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                if not guarded:
                    hard.append(child)
                continue
            rec(child, guarded or isinstance(child, _GUARD_NODES))

    rec(tree, False)
    return hard


def _roots_of(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    # ImportFrom: relative (level>0) is always first-party intra-package.
    if node.level and node.level > 0:
        return []
    if node.module:
        return [node.module.split(".")[0]]
    return []


def scan(
    repo: Path,
    roots: Iterable[str],
    *,
    declared: set[str],
    firstparty: set[str],
    stdlib: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for root_name in roots:
        root = repo / root_name
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            parts = py.relative_to(repo).parts
            if any(p in _IGNORE_DIRS for p in parts):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in _hard_imports(tree):
                for r in _roots_of(node):
                    if not r:
                        continue
                    if r in stdlib or r in firstparty:
                        continue
                    dist = _IMPORT_TO_DIST.get(r, r)
                    if _norm(dist) in declared:
                        continue
                    rel = str(py.relative_to(repo))
                    key = (rel, r)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(Finding(rel, node.lineno, r, _norm(dist)))
    findings.sort(key=lambda f: (f.file, f.lineno, f.import_root))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _detect_repo() -> Path:
    here = Path(__file__).resolve().parent
    for parent in [here] + list(here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd().resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=None, help="Repo root (default: detect).")
    parser.add_argument("--roots", nargs="+", default=["scripts", "tests"],
                        help="Source roots to scan (default: scripts tests).")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true",
                        help="Print nothing on success; findings only on failure.")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve() if args.repo else _detect_repo()
    pyproject = repo / "pyproject.toml"
    if not pyproject.exists():
        msg = f"pyproject.toml not found at {pyproject}"
        sys.stderr.write(msg + "\n")
        if args.json:
            sys.stdout.write(json.dumps({"error": msg}) + "\n")
        return 2
    try:
        declared = declared_distributions(pyproject)
    except Exception as exc:  # noqa: BLE001 - report and fail closed
        msg = f"could not parse {pyproject}: {exc}"
        sys.stderr.write(msg + "\n")
        if args.json:
            sys.stdout.write(json.dumps({"error": msg}) + "\n")
        return 2

    firstparty = first_party_modules(repo)
    stdlib = stdlib_modules()
    findings = scan(repo, args.roots, declared=declared,
                    firstparty=firstparty, stdlib=stdlib)

    if args.json:
        sys.stdout.write(json.dumps(
            {"findings": [f.as_dict() for f in findings],
             "scanned_roots": list(args.roots),
             "declared_count": len(declared)},
            indent=2, sort_keys=True) + "\n")
    else:
        if findings:
            sys.stderr.write(
                "✖ undeclared third-party imports (declare in pyproject.toml, "
                "then `uv sync --extra test`):\n")
            for f in findings:
                sys.stderr.write(
                    f"  {f.file}:{f.lineno}  import '{f.import_root}'  "
                    f"→ add distribution '{f.dist_guess}'\n")
            sys.stderr.write(
                "  (guarded `try/except ImportError` or function-local imports "
                "are exempt — only unconditional top-level imports are checked)\n")
        elif not args.quiet:
            sys.stdout.write("✓ no undeclared third-party imports.\n")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
