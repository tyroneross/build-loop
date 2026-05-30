# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Filesystem walk + declared-dependency + TS path-alias readers.

Owns ``.gitignore`` loading, the supported-file and named-file iterators, the
declared-npm/declared-pip manifest readers, and the tsconfig/jsconfig/next.js
path-alias loader. The worst pre-split hotspot (``_read_declared_pip_packages``)
is split here into ``_parse_requirements_txt`` + ``_parse_pyproject_deps`` so
each manifest format is handled in its own bounded helper.

Parsing semantics are unchanged: same packages, same ``setdefault`` first-wins
ordering, same manifest-relative paths.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

import tomllib

import pathspec

from ..resolve import _PIP_REQ_NAME_RE, _normalize_pip_name

# Extensions we currently understand.
PY_EXTS = {".py"}
JS_EXTS = {".js", ".jsx", ".mjs", ".cjs"}
TS_EXTS = {".ts", ".tsx"}
SUPPORTED_EXTS = PY_EXTS | JS_EXTS | TS_EXTS

# Always-skip directories (in addition to .gitignore matches).
ALWAYS_SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "dist", "build", "out",
    ".venv", "venv", "env", ".env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".navgator", ".build-loop", ".bookmark", ".ibr",
    ".next", ".turbo", ".cache", "coverage",
}


# ---------------------------------------------------------------------------
# .gitignore support + file iteration
# ---------------------------------------------------------------------------

def _load_gitignore(repo_root: Path) -> pathspec.PathSpec:
    patterns: List[str] = []
    for fname in (".gitignore", ".git/info/exclude"):
        p = repo_root / fname
        if p.exists():
            try:
                patterns.extend(p.read_text(encoding="utf-8").splitlines())
            except OSError:
                pass
    # Also seed with our always-skip directory globs so the spec is the only check.
    patterns.extend([f"{d}/" for d in ALWAYS_SKIP_DIRS])
    # "gitignore" pattern style is the modern equivalent of the deprecated
    # "gitwildmatch"; semantics are equivalent for our use.
    try:
        return pathspec.PathSpec.from_lines("gitignore", patterns)
    except (ValueError, KeyError):
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def _iter_named_files(
    repo_root: Path,
    spec: pathspec.PathSpec,
    names: Set[str],
) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(repo_root):
        rel_dir = os.path.relpath(dirpath, repo_root)
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_SKIP_DIRS]
        keep = []
        for d in dirnames:
            rel = os.path.join(rel_dir, d) if rel_dir else d
            if spec.match_file(rel + "/"):
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if fn not in names:
                continue
            rel = os.path.join(rel_dir, fn) if rel_dir else fn
            if spec.match_file(rel):
                continue
            yield Path(repo_root) / rel


def _iter_files(repo_root: Path, spec: pathspec.PathSpec) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(repo_root):
        rel_dir = os.path.relpath(dirpath, repo_root)
        if rel_dir == ".":
            rel_dir = ""
        # Prune always-skip dirs for speed (still defer to .gitignore for the rest).
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_SKIP_DIRS]
        # Apply pathspec to remaining dirs.
        keep = []
        for d in dirnames:
            rel = os.path.join(rel_dir, d) if rel_dir else d
            if spec.match_file(rel + "/"):
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            rel = os.path.join(rel_dir, fn) if rel_dir else fn
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SUPPORTED_EXTS:
                continue
            if spec.match_file(rel):
                continue
            yield Path(repo_root) / rel


# ---------------------------------------------------------------------------
# TS path aliases
# ---------------------------------------------------------------------------

def _json_loads_lenient(raw: str) -> Dict[str, object]:
    stripped = re.sub(r"/\*[\s\S]*?\*/", "", raw)
    stripped = re.sub(r"(?m)^\s*//.*$", "", stripped)
    stripped = re.sub(r",\s*([}\]])", r"\1", stripped)
    data = json.loads(stripped)
    return data if isinstance(data, dict) else {}


def _load_ts_path_aliases(repo_root: Path) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for fname in ("tsconfig.json", "jsconfig.json"):
        p = repo_root / fname
        if not p.exists():
            continue
        try:
            data = _json_loads_lenient(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        compiler = data.get("compilerOptions") if isinstance(data, dict) else {}
        if not isinstance(compiler, dict):
            continue
        base_url = str(compiler.get("baseUrl") or ".").replace("\\", "/")
        paths = compiler.get("paths") or {}
        if not isinstance(paths, dict):
            continue
        for alias, targets in paths.items():
            if not isinstance(alias, str) or not isinstance(targets, list) or not targets:
                continue
            target = targets[0]
            if not isinstance(target, str):
                continue
            alias_prefix = alias.replace("*", "")
            target_prefix = target.replace("*", "").replace("\\", "/")
            resolved = posixpath.normpath(posixpath.join(base_url, target_prefix))
            aliases[alias_prefix] = "" if resolved == "." else resolved.strip("/")
        break

    if not aliases:
        next_configs = (
            "next.config.js", "next.config.mjs", "next.config.ts",
            "next.config.cjs",
        )
        if any((repo_root / name).exists() for name in next_configs):
            aliases["@/"] = ""
    return aliases


# ---------------------------------------------------------------------------
# Declared dependencies
# ---------------------------------------------------------------------------

def _read_declared_npm_packages(
    repo_root: Path,
    spec: pathspec.PathSpec,
) -> Dict[str, str]:
    declared: Dict[str, str] = {}
    for manifest in _iter_named_files(repo_root, spec, {"package.json"}):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        rel = str(manifest.relative_to(repo_root)).replace(os.sep, "/")
        for key in (
            "dependencies", "devDependencies", "peerDependencies",
            "optionalDependencies",
        ):
            block = data.get(key) or {}
            if not isinstance(block, dict):
                continue
            for pkg in block:
                if isinstance(pkg, str):
                    declared.setdefault(pkg, rel)
    return declared


def _add_pip_spec(
    declared: Dict[str, Tuple[str, str]],
    spec_text: str,
    manifest_rel: str,
) -> None:
    m = _PIP_REQ_NAME_RE.match(spec_text or "")
    if not m:
        return
    canonical = m.group(1)
    declared.setdefault(_normalize_pip_name(canonical), (canonical, manifest_rel))


def _parse_requirements_txt(
    declared: Dict[str, Tuple[str, str]],
    manifest: Path,
    rel: str,
) -> None:
    """Add ``requirements*.txt`` package specs to ``declared`` (in place)."""
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        _add_pip_spec(declared, line, rel)


def _parse_pyproject_deps(
    declared: Dict[str, Tuple[str, str]],
    manifest: Path,
    rel: str,
) -> None:
    """Add ``pyproject.toml`` deps (PEP 621 + optional + PEP 735 groups + uv
    dev-deps) to ``declared`` (in place)."""
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return
    project = data.get("project") or {}
    for spec_text in project.get("dependencies") or []:
        if isinstance(spec_text, str):
            _add_pip_spec(declared, spec_text, rel)
    for group in (project.get("optional-dependencies") or {}).values():
        for spec_text in group or []:
            if isinstance(spec_text, str):
                _add_pip_spec(declared, spec_text, rel)
    for group in (data.get("dependency-groups") or {}).values():
        for spec_text in group or []:
            if isinstance(spec_text, str):
                _add_pip_spec(declared, spec_text, rel)
    uv_block = (data.get("tool") or {}).get("uv") or {}
    for spec_text in uv_block.get("dev-dependencies") or []:
        if isinstance(spec_text, str):
            _add_pip_spec(declared, spec_text, rel)


def _read_declared_pip_packages(
    repo_root: Path,
    spec: pathspec.PathSpec,
) -> Dict[str, Tuple[str, str]]:
    declared: Dict[str, Tuple[str, str]] = {}
    for manifest in _iter_named_files(
        repo_root,
        spec,
        {"pyproject.toml", "requirements.txt", "requirements-dev.txt"},
    ):
        rel = str(manifest.relative_to(repo_root)).replace(os.sep, "/")
        if manifest.name.endswith(".txt"):
            _parse_requirements_txt(declared, manifest, rel)
        else:
            _parse_pyproject_deps(declared, manifest, rel)
    return declared
