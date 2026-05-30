# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Import-specifier resolution: in-tree file targets + external-package mapping.

Maps a parsed import specifier either to a repo-relative file (the in-tree
``imports`` edge target) or, failing that, to a declared external package
(``uses-package`` edge). Python relative/absolute resolution is split into two
helpers; ``_external_package_for_import`` returns early per extension family.

Resolution logic is unchanged — same candidate order, same first-match wins.
"""

from __future__ import annotations

import os
import posixpath
import re
import sys
from typing import Dict, Optional, Set, Tuple

PY_EXTS = {".py"}
JS_EXTS = {".js", ".jsx", ".mjs", ".cjs"}
TS_EXTS = {".ts", ".tsx"}


# ---------------------------------------------------------------------------
# External-package classification
# ---------------------------------------------------------------------------

def _toplevel_external_npm(spec: str) -> Optional[str]:
    if not spec or spec.startswith((".", "/", "node:", "data:", "http:", "https:", "file:")):
        return None
    if spec.startswith("@"):
        parts = spec.split("/", 2)
        if len(parts) < 2:
            return None
        return f"{parts[0]}/{parts[1]}"
    return spec.split("/", 1)[0]


def _toplevel_external_py(module: str) -> Optional[str]:
    if not module or module.startswith("."):
        return None
    return module.split(".", 1)[0]


def _is_python_stdlib(name: str) -> bool:
    return name in getattr(sys, "stdlib_module_names", set())


_PIP_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def _normalize_pip_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


_PIP_IMPORT_ALIASES: Dict[str, Set[str]] = {
    "pyyaml": {"yaml"},
    "beautifulsoup4": {"bs4"},
    "pillow": {"pil"},
    "scikit-learn": {"sklearn"},
    "opencv-python": {"cv2"},
    "google-api-python-client": {"googleapiclient", "google"},
}


def _external_package_for_import(
    spec: str,
    ext: str,
    declared_npm: Dict[str, str],
    declared_pip: Dict[str, Tuple[str, str]],
) -> Optional[Tuple[str, str, str]]:
    if ext in TS_EXTS or ext in JS_EXTS:
        pkg = _toplevel_external_npm(spec)
        if pkg and pkg in declared_npm:
            return ("npm", pkg, declared_npm[pkg])
        return None

    if ext in PY_EXTS:
        top = _toplevel_external_py(spec)
        if not top or _is_python_stdlib(top):
            return None
        norm = _normalize_pip_name(top)
        if norm in declared_pip:
            canonical, manifest = declared_pip[norm]
            return ("pip", canonical, manifest)
        for dist, aliases in _PIP_IMPORT_ALIASES.items():
            if norm in aliases and dist in declared_pip:
                canonical, manifest = declared_pip[dist]
                return ("pip", canonical, manifest)
    return None


# ---------------------------------------------------------------------------
# Python module path resolution (best-effort)
# ---------------------------------------------------------------------------

def _resolve_py_relative(module: str, from_rel: str, repo_files: Set[str]) -> Optional[str]:
    """Resolve a dotted RELATIVE import (``.``-prefixed) against ``from_rel``."""
    from_dir = os.path.dirname(from_rel)
    ups = len(module) - len(module.lstrip("."))
    rest = module.lstrip(".")
    base = from_dir
    for _ in range(ups - 1):
        base = os.path.dirname(base) if base else ""
    candidate_stem = os.path.join(base, rest.replace(".", "/")) if rest else base
    for ext in (".py",):
        cand = (candidate_stem + ext).lstrip("/")
        if cand in repo_files:
            return cand
    cand = os.path.join(candidate_stem, "__init__.py").lstrip("/")
    if cand in repo_files:
        return cand
    return None


def _resolve_py_absolute(module: str, repo_files: Set[str]) -> Optional[str]:
    """Resolve an ABSOLUTE dotted import to an in-tree file under any package."""
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        rel = "/".join(parts[:i]) + ".py"
        if rel in repo_files:
            return rel
        rel_init = "/".join(parts[:i]) + "/__init__.py"
        if rel_init in repo_files:
            return rel_init
        # also try src/<module>/...
        for prefix in ("src/", ""):
            rel2 = prefix + "/".join(parts[:i]) + ".py"
            if rel2 in repo_files:
                return rel2
    return None


def _resolve_py_import(module: str, from_rel: str, repo_files: Set[str]) -> Optional[str]:
    """Map a Python import string to a repo-relative file, if it lives in-tree."""
    if not module:
        return None
    if module.startswith("."):
        return _resolve_py_relative(module, from_rel, repo_files)
    return _resolve_py_absolute(module, repo_files)


# ---------------------------------------------------------------------------
# TS/JS module path resolution (best-effort)
# ---------------------------------------------------------------------------

def _resolve_ts_path(base: str, repo_files: Set[str]) -> Optional[str]:
    base = base.replace("\\", "/").lstrip("/")
    candidates = [base]
    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        if base.endswith(ext):
            continue
        candidates.append(base + ext)
    candidates += [
        base + "/index.ts",
        base + "/index.tsx",
        base + "/index.js",
        base + "/index.jsx",
    ]
    # Strip explicit ".js" suffixes commonly seen in TS for ESM (./foo.js -> ./foo.ts).
    if base.endswith(".js"):
        candidates.append(base[:-3] + ".ts")
        candidates.append(base[:-3] + ".tsx")
    for c in candidates:
        c_norm = c.replace("\\", "/").lstrip("/")
        if c_norm in repo_files:
            return c_norm
    return None


def _resolve_ts_import(
    spec: str,
    from_rel: str,
    repo_files: Set[str],
    path_aliases: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Resolve a TS/JS import specifier to an in-tree file (best effort)."""
    if not spec or spec.startswith(("node:", "data:", "http:", "https:", "file:")):
        return None

    if path_aliases:
        for alias, target in sorted(path_aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if not alias or not spec.startswith(alias):
                continue
            rest = spec[len(alias):]
            base = posixpath.normpath(posixpath.join(target, rest)) if target else rest
            resolved = _resolve_ts_path(base, repo_files)
            if resolved:
                return resolved

    if not spec.startswith("."):
        return None
    from_dir = posixpath.dirname(from_rel.replace("\\", "/"))
    base = posixpath.normpath(posixpath.join(from_dir, spec))
    return _resolve_ts_path(base, repo_files)
