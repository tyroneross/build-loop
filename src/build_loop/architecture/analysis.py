# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Pure analysis functions over the component/connection graph.

Most functions are pure — they take ``components`` + ``connections`` and return
reports, which makes them trivially unit-testable on synthetic graphs.

The package-level dead-code helpers ``find_unused_packages`` and the
``repo_root``-aware path through ``find_dead`` need disk access to read manifest
files and re-walk source for external import names. Those helpers tolerate
missing manifests and unreadable files, returning empty results rather than
raising.

Uses ``networkx`` for graph operations (cycles, descendants, reachability).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import networkx as nx

from .schemas import Component, Connection

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — build-loop pins >=3.11 in pyproject.toml
    import tomli as tomllib  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_digraph(
    components: Sequence[Component],
    connections: Sequence[Connection],
) -> nx.DiGraph:
    g = nx.DiGraph()
    for c in components:
        g.add_node(c.component_id, component=c)
    for conn in connections:
        if conn.from_id and conn.to_id:
            g.add_edge(conn.from_id, conn.to_id, connection=conn)
    return g


# ---------------------------------------------------------------------------
# Impact analysis
# ---------------------------------------------------------------------------

@dataclass
class ImpactReport:
    component_id: str
    affected: List[str] = field(default_factory=list)
    direct_dependents: List[str] = field(default_factory=list)
    transitive_dependents: List[str] = field(default_factory=list)
    blast_radius: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component_id": self.component_id,
            "blast_radius": self.blast_radius,
            "direct_dependents": list(self.direct_dependents),
            "transitive_dependents": list(self.transitive_dependents),
            "affected": list(self.affected),
            "notes": list(self.notes),
        }


def compute_impact(
    component_id: str,
    components: Sequence[Component],
    connections: Sequence[Connection],
) -> ImpactReport:
    """Blast-radius for a component: who depends on it (transitively)?

    The ancestor set in the import digraph IS the impact surface — every
    file that imports (directly or indirectly) the changed component.
    """
    g = build_digraph(components, connections)
    if component_id not in g:
        return ImpactReport(
            component_id=component_id,
            notes=[f"component_id {component_id} not in graph"],
        )
    direct = sorted(g.predecessors(component_id))
    transitive = sorted(nx.ancestors(g, component_id))
    affected = sorted(set(direct) | set(transitive) | {component_id})
    return ImpactReport(
        component_id=component_id,
        affected=affected,
        direct_dependents=direct,
        transitive_dependents=transitive,
        blast_radius=len(affected),
    )


# ---------------------------------------------------------------------------
# Dataflow tracing
# ---------------------------------------------------------------------------

def trace_dataflow(
    component_id: str,
    components: Sequence[Component],
    connections: Sequence[Connection],
    depth: int = 3,
    direction: str = "out",
) -> List[List[str]]:
    """Walk the graph from ``component_id`` returning paths.

    direction: "out" (downstream — what this calls), "in" (upstream — who
    calls this), or "both".
    """
    g = build_digraph(components, connections)
    if component_id not in g:
        return []

    paths: List[List[str]] = []

    def dfs(node: str, path: List[str], remaining: int, fwd: bool) -> None:
        if remaining <= 0:
            return
        nbrs = g.successors(node) if fwd else g.predecessors(node)
        for nb in nbrs:
            if nb in path:  # cycle guard
                continue
            new_path = path + [nb]
            paths.append(list(new_path))
            dfs(nb, new_path, remaining - 1, fwd)

    if direction in ("out", "both"):
        dfs(component_id, [component_id], depth, fwd=True)
    if direction in ("in", "both"):
        dfs(component_id, [component_id], depth, fwd=False)

    return paths


# ---------------------------------------------------------------------------
# Rule checks (orphans, cycles, layer violations, hotspots)
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    rule: str
    severity: str  # "info" | "warn" | "error"
    component_id: Optional[str] = None
    component_ids: List[str] = field(default_factory=list)
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "component_id": self.component_id,
            "component_ids": list(self.component_ids),
            "message": self.message,
            "details": dict(self.details),
        }


# Layers that must not import from layers later in the list.
LAYER_ORDER = ["frontend", "backend", "tooling", "test", "docs", "unknown"]


def _layer_index(layer: str) -> int:
    try:
        return LAYER_ORDER.index(layer)
    except ValueError:
        return len(LAYER_ORDER)


def check_rules(
    components: Sequence[Component],
    connections: Sequence[Connection],
    hotspot_threshold: int = 15,
) -> List[Violation]:
    """Run rule checks. Currently:

    * orphan: component with no edges (in or out) — might be dead.
    * circular_dependency: any directed cycle.
    * layer_violation: backend importing frontend.
    * hotspot: component with > ``hotspot_threshold`` total edges.
    """
    violations: List[Violation] = []
    g = build_digraph(components, connections)

    # Orphans (excluding test/docs which often legitimately stand alone).
    for c in components:
        if c.component_id not in g:
            continue
        layer = c.role.layer if hasattr(c.role, "layer") else (c.role or {}).get("layer", "unknown")
        if layer in ("test", "docs"):
            continue
        if g.degree(c.component_id) == 0:
            violations.append(Violation(
                rule="orphan",
                severity="warn",
                component_id=c.component_id,
                message=f"{c.name} has no incoming or outgoing imports",
            ))

    # Cycles.
    try:
        for cycle in nx.simple_cycles(g):
            violations.append(Violation(
                rule="circular_dependency",
                severity="error",
                component_ids=list(cycle),
                message=f"cycle of length {len(cycle)}: {' -> '.join(cycle)}",
            ))
    except nx.NetworkXNoCycle:
        pass

    # Layer violations.
    by_id = {c.component_id: c for c in components}
    for u, v in g.edges():
        cu = by_id.get(u)
        cv = by_id.get(v)
        if not cu or not cv:
            continue
        u_layer = cu.role.layer if hasattr(cu.role, "layer") else (cu.role or {}).get("layer", "unknown")
        v_layer = cv.role.layer if hasattr(cv.role, "layer") else (cv.role or {}).get("layer", "unknown")
        # Backend should not import frontend.
        if u_layer == "backend" and v_layer == "frontend":
            violations.append(Violation(
                rule="layer_violation",
                severity="error",
                component_ids=[u, v],
                message=f"{cu.name} ({u_layer}) imports {cv.name} ({v_layer})",
                details={"from_layer": u_layer, "to_layer": v_layer},
            ))

    # Hotspots.
    for c in components:
        if c.component_id not in g:
            continue
        deg = g.in_degree(c.component_id) + g.out_degree(c.component_id)
        if deg > hotspot_threshold:
            violations.append(Violation(
                rule="hotspot",
                severity="info",
                component_id=c.component_id,
                message=f"{c.name} has {deg} total edges (threshold {hotspot_threshold})",
                details={"total_edges": deg},
            ))

    return violations


# ---------------------------------------------------------------------------
# Dead-code detection
# ---------------------------------------------------------------------------

@dataclass
class DeadReport:
    orphan_components: List[str] = field(default_factory=list)
    unused_packages: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "orphan_components": list(self.orphan_components),
            "unused_packages": list(self.unused_packages),
            "notes": list(self.notes),
        }


def find_dead(
    components: Sequence[Component],
    connections: Sequence[Connection],
    repo_root: Path | str | None = None,
) -> DeadReport:
    """Find orphan components and (optionally) declared-but-unused packages.

    The component-level orphan walk is pure — it only needs the in-memory graph.
    Package-level detection requires ``repo_root`` so we can read manifests and
    re-walk source for external import names. When ``repo_root`` is omitted the
    report carries an empty ``unused_packages`` list and a note explaining why.
    """
    g = build_digraph(components, connections)
    orphans: List[str] = []
    for c in components:
        if c.component_id not in g:
            orphans.append(c.component_id)
            continue
        layer = c.role.layer if hasattr(c.role, "layer") else (c.role or {}).get("layer", "unknown")
        if layer in ("test", "docs"):
            continue
        if g.degree(c.component_id) == 0:
            orphans.append(c.component_id)

    unused: List[str] = []
    notes: List[str] = []
    if repo_root is not None:
        try:
            unused = find_unused_packages(repo_root, components)
        except Exception as exc:  # never break a scan over manifest parsing
            notes.append(f"package-level dead detection failed: {exc!r}")
    else:
        notes.append("package-level dead detection skipped (no repo_root passed)")

    return DeadReport(
        orphan_components=sorted(orphans),
        unused_packages=sorted(unused),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Package-level dead detection helpers
# ---------------------------------------------------------------------------

# Build/runtime tools that legitimately appear in manifests without being
# imported from any source file. Conservative — false positives here are worse
# than false negatives, since the goal is to surface real cleanup candidates.
_RUNTIME_ONLY_NPM = {
    "typescript", "tsx", "ts-node", "tsup", "tsc-watch",
    "eslint", "prettier", "jest", "vitest", "mocha", "chai", "ava",
    "vite", "webpack", "rollup", "esbuild", "parcel", "swc", "@swc/core",
    "next", "nuxt", "remix", "astro", "expo",
    "tailwindcss", "postcss", "autoprefixer", "sass", "less",
    "husky", "lint-staged", "concurrently", "rimraf", "cross-env", "dotenv-cli",
    "nodemon", "pm2", "supervisor",
    "@types/node", "@types/jest", "@types/react", "@types/react-dom",
}
_RUNTIME_ONLY_NPM_PREFIXES = ("@types/", "eslint-", "@eslint/", "prettier-",
                              "babel-", "@babel/", "vite-plugin-", "rollup-plugin-",
                              "@rollup/", "webpack-")

_RUNTIME_ONLY_PIP = {
    "pip", "setuptools", "wheel", "build", "uv",
    "pytest", "pytest-cov", "pytest-xdist", "pytest-mock",
    "ruff", "black", "isort", "mypy", "pyright", "flake8",
    "twine", "tox", "nox", "pre-commit", "coverage",
}


def _read_npm_packages(repo_root: Path) -> Set[str]:
    pkg_json = repo_root / "package.json"
    if not pkg_json.exists():
        return set()
    try:
        import json
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    declared: Set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies",
                "optionalDependencies"):
        block = data.get(key) or {}
        if isinstance(block, dict):
            declared.update(block.keys())
    return declared


_PIP_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def _normalize_pip_name(name: str) -> str:
    """PEP 503 normalization — case fold + collapse runs of ``-_.``."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _read_pip_packages(repo_root: Path) -> Set[str]:
    declared: Set[str] = set()

    pyproj = repo_root / "pyproject.toml"
    if pyproj.exists():
        try:
            data = tomllib.loads(pyproj.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        proj = data.get("project") or {}
        for spec in proj.get("dependencies") or []:
            m = _PIP_REQ_NAME_RE.match(spec or "")
            if m:
                declared.add(_normalize_pip_name(m.group(1)))
        for group in (proj.get("optional-dependencies") or {}).values():
            for spec in group or []:
                m = _PIP_REQ_NAME_RE.match(spec or "")
                if m:
                    declared.add(_normalize_pip_name(m.group(1)))
        for group in (data.get("dependency-groups") or {}).values():  # PEP 735
            for spec in group or []:
                if isinstance(spec, str):
                    m = _PIP_REQ_NAME_RE.match(spec)
                    if m:
                        declared.add(_normalize_pip_name(m.group(1)))
        uv_block = (data.get("tool") or {}).get("uv") or {}
        for spec in uv_block.get("dev-dependencies") or []:
            m = _PIP_REQ_NAME_RE.match(spec or "")
            if m:
                declared.add(_normalize_pip_name(m.group(1)))

    for req_name in ("requirements.txt", "requirements-dev.txt"):
        req = repo_root / req_name
        if not req.exists():
            continue
        try:
            for line in req.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-")):
                    continue
                m = _PIP_REQ_NAME_RE.match(line)
                if m:
                    declared.add(_normalize_pip_name(m.group(1)))
        except OSError:
            continue

    return declared


def _toplevel_external_npm(spec: str) -> Optional[str]:
    if not spec or spec.startswith((".", "/", "node:")):
        return None
    if spec.startswith("@"):
        # Scoped package: @scope/name[/sub] -> @scope/name
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
    # sys.stdlib_module_names is available on 3.10+ — covers us at >=3.11.
    return name in getattr(sys, "stdlib_module_names", set())


def _used_external_packages(
    repo_root: Path,
    components: Sequence[Component],
) -> Tuple[Set[str], Set[str]]:
    """Return (used_npm_packages, used_pip_packages_normalized)."""
    # Local import to avoid a top-of-module cycle: scanner imports schemas
    # eagerly; analysis is otherwise stdlib + networkx only.
    from .scanner import (
        JS_EXTS, PY_EXTS, TS_EXTS,
        _py_imports, _resolve_py_import,
        _ts_imports, _resolve_ts_import,
    )

    rel_files: Set[str] = set()
    file_paths: List[Tuple[str, str]] = []  # (rel, ext)
    for c in components:
        rel = c.metadata.get("file") if c.metadata else None
        if not rel:
            continue
        rel_files.add(rel)
        file_paths.append((rel, os.path.splitext(rel)[1].lower()))

    used_npm: Set[str] = set()
    used_pip: Set[str] = set()

    for rel, ext in file_paths:
        full = repo_root / rel
        try:
            source = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if ext in PY_EXTS:
            for spec_str, _line in _py_imports(source):
                if _resolve_py_import(spec_str, rel, rel_files) is not None:
                    continue  # in-tree
                top = _toplevel_external_py(spec_str)
                if top and not _is_python_stdlib(top):
                    used_pip.add(_normalize_pip_name(top))
        elif ext in TS_EXTS or ext in JS_EXTS:
            is_tsx = ext == ".tsx"
            for spec_str, _line in _ts_imports(source, is_tsx):
                if _resolve_ts_import(spec_str, rel, rel_files) is not None:
                    continue  # in-tree
                top = _toplevel_external_npm(spec_str)
                if top:
                    used_npm.add(top)
    return used_npm, used_pip


# Map normalized pip names back to the canonical distribution name where the
# import name diverges. Conservative — only entries that bite in practice. Each
# row says "if this distribution is declared but not imported, look for the
# import name on the right before flagging".
_PIP_IMPORT_ALIASES: Dict[str, Set[str]] = {
    "pyyaml":               {"yaml"},
    "psycopg":              {"psycopg"},
    "psycopg-binary":       {"psycopg"},
    "beautifulsoup4":       {"bs4"},
    "pillow":               {"pil"},
    "scikit-learn":         {"sklearn"},
    "opencv-python":        {"cv2"},
    "google-cloud-storage": {"google"},
    "google-api-python-client": {"googleapiclient", "google"},
}


def find_unused_packages(
    repo_root: Path | str,
    components: Sequence[Component],
) -> List[str]:
    """Return packages declared in manifests but never imported from source.

    Filters out well-known build/runtime tools that legitimately appear in
    manifests without an explicit import (linters, bundlers, type stubs, etc.).
    """
    repo_root = Path(repo_root)
    declared_npm = _read_npm_packages(repo_root)
    declared_pip = {_normalize_pip_name(p) for p in _read_pip_packages(repo_root)}
    used_npm, used_pip = _used_external_packages(repo_root, components)

    unused: List[str] = []

    for pkg in declared_npm:
        if pkg in _RUNTIME_ONLY_NPM:
            continue
        if any(pkg.startswith(prefix) for prefix in _RUNTIME_ONLY_NPM_PREFIXES):
            continue
        if pkg in used_npm:
            continue
        unused.append(pkg)

    for pkg in declared_pip:
        if pkg in _RUNTIME_ONLY_PIP:
            continue
        if pkg in used_pip:
            continue
        aliases = _PIP_IMPORT_ALIASES.get(pkg, set())
        if aliases and any(_normalize_pip_name(a) in used_pip for a in aliases):
            continue
        unused.append(pkg)

    return unused


# ---------------------------------------------------------------------------
# Convenience: lookups by file path
# ---------------------------------------------------------------------------

def find_component_by_file(
    file_path: str,
    components: Sequence[Component],
) -> Optional[Component]:
    for c in components:
        if c.metadata.get("file") == file_path:
            return c
    return None
