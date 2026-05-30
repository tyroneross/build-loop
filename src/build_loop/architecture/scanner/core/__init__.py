# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Scan orchestration: ScanResult, file hashing, per-file emit, full + single.

Ties the capability packages together. ``scan_repo`` walks the tree (pass 1 →
Components + hashes; pass 2 → per-file Connections via ``_emit_file_connections``;
pass 3 → backfill links). ``scan_one_file`` is the incremental seam. The emit
step is split into per-edge-type helpers (in-tree imports, external packages,
frontend→api fetches, service calls) to flatten the former 18-branch function.

``ScanResult.to_index()`` output is byte-identical to the pre-split scanner —
the LOCKED six-count + dual-timestamp schema contract (test_schema_parity.py).
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..identity import (
    _append_connection,
    _build_component,
    _ensure_package_component,
    _ensure_service_component,
    _prune_unreferenced_runtime_components,
    _refresh_component_links,
    _seed_runtime_components,
)
from ..imports import _py_imports, _ts_imports
from ..manifests import (
    JS_EXTS,
    PY_EXTS,
    TS_EXTS,
    _iter_files,
    _load_gitignore,
    _load_ts_path_aliases,
    _read_declared_npm_packages,
    _read_declared_pip_packages,
)
from ..patterns import _api_fetches, _resolve_api_route, _service_matches
from ..resolve import (
    _external_package_for_import,
    _resolve_py_import,
    _resolve_ts_import,
)
from ...schemas import Component, Connection, SCHEMA_VERSION


@dataclass
class ScanResult:
    components: List[Component]
    connections: List[Connection]
    file_map: Dict[str, str]            # rel_path -> component_id
    hashes: Dict[str, Dict[str, str]]   # rel_path -> {hash, mtime, size}
    files_scanned: int

    def to_index(self) -> Dict[str, object]:
        # Schema-key parity: NavGator and the orchestrator state.json field
        # convention both use the plural form ("components_count",
        # "connections_count") and "last_scan". Build-loop's native engine
        # historically emitted the singular forms ("component_count",
        # "connection_count") plus "generated_at". Both are written so any
        # consumer (orchestrator state read, NavGator-shape adapter,
        # downstream tools) sees what it expects. Treat all six as a single
        # contract; tests in test_schema_parity.py lock the invariant.
        now_ms = int(time.time() * 1000)
        comp_count = len(self.components)
        conn_count = len(self.connections)
        connection_counts_by_type: Dict[str, int] = {}
        for conn in self.connections:
            connection_counts_by_type[conn.type] = connection_counts_by_type.get(conn.type, 0) + 1
        return {
            "schema_version": SCHEMA_VERSION,
            "component_count": comp_count,
            "components_count": comp_count,
            "connection_count": conn_count,
            "connections_count": conn_count,
            "connection_counts_by_type": connection_counts_by_type,
            "components": [c.to_dict() for c in self.components],
            "connections": [c.to_dict() for c in self.connections],
            "generated_at": now_ms,
            "last_scan": now_ms,
        }


def _hash_file(path: Path) -> Tuple[str, int, int]:
    h = hashlib.blake2b(digest_size=16)
    size = 0
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
    except OSError:
        return ("", 0, 0)
    try:
        mtime = int(path.stat().st_mtime * 1000)
    except OSError:
        mtime = 0
    return (h.hexdigest(), size, mtime)


# ---------------------------------------------------------------------------
# Per-edge-type emit helpers — each appends one kind of Connection.
# ---------------------------------------------------------------------------

def _emit_import_edges(
    *,
    rel: str,
    comp: Component,
    imports: List[Tuple[str, int]],
    resolver,
    rel_files_set: Set[str],
    file_map: Dict[str, str],
    by_id: Dict[str, Component],
    components: List[Component],
    connections: List[Connection],
    seen_connections: Set[Tuple[str, str, str, int, str]],
    runtime_components: Dict[Tuple[str, str], Component],
    declared_npm: Dict[str, str],
    declared_pip: Dict[str, Tuple[str, str]],
    ext: str,
) -> None:
    """In-tree ``imports`` edges + fallback external ``uses-package`` edges."""
    for spec_str, line in imports:
        target_rel = resolver(spec_str, rel, rel_files_set)
        if target_rel:
            target_id = file_map.get(target_rel)
            target_comp = by_id.get(target_id or "")
            if target_comp and target_id != comp.component_id:
                _append_connection(
                    connections,
                    seen_connections,
                    comp,
                    target_comp,
                    "imports",
                    rel,
                    line,
                    spec_str,
                    1.0,
                    "build-loop-native-scanner",
                )
            continue

        package = _external_package_for_import(
            spec_str, ext, declared_npm, declared_pip
        )
        if package:
            manager, package_name, manifest_rel = package
            package_comp = _ensure_package_component(
                components, by_id, runtime_components,
                manager, package_name, manifest_rel,
            )
            _append_connection(
                connections,
                seen_connections,
                comp,
                package_comp,
                "uses-package",
                rel,
                line,
                package_name,
                1.0,
                "build-loop-native-scanner (bare-import)",
                description=f"{rel} uses {package_name}",
            )


def _emit_api_fetch_edges(
    *,
    rel: str,
    comp: Component,
    source: str,
    rel_files_set: Set[str],
    file_map: Dict[str, str],
    by_id: Dict[str, Component],
    connections: List[Connection],
    seen_connections: Set[Tuple[str, str, str, int, str]],
) -> None:
    """``frontend-calls-api`` edges from ``fetch('/api/...')`` to route files."""
    for api_path, line in _api_fetches(source, rel):
        route_rel = _resolve_api_route(api_path, rel_files_set)
        if not route_rel:
            continue
        route_id = file_map.get(route_rel)
        route_comp = by_id.get(route_id or "")
        if not route_comp or route_id == comp.component_id:
            continue
        _append_connection(
            connections,
            seen_connections,
            comp,
            route_comp,
            "frontend-calls-api",
            rel,
            line,
            f"fetch({api_path})",
            0.9,
            "build-loop-native-scanner (fetch)",
            symbol_type="function",
            description=f"{rel} fetches {api_path}",
        )


def _emit_service_edges(
    *,
    rel: str,
    comp: Component,
    source: str,
    by_id: Dict[str, Component],
    components: List[Component],
    connections: List[Connection],
    seen_connections: Set[Tuple[str, str, str, int, str]],
    runtime_components: Dict[Tuple[str, str], Component],
) -> None:
    """``service-call`` edges from matched SDK/service patterns."""
    for pattern, line, snippet, detected in _service_matches(source):
        service_comp = _ensure_service_component(
            components, by_id, runtime_components, pattern
        )
        _append_connection(
            connections,
            seen_connections,
            comp,
            service_comp,
            "service-call",
            rel,
            line,
            pattern.name,
            0.85,
            f"build-loop-native-scanner pattern: {detected}",
            symbol_type="function",
            description=f"Calls {pattern.name}: {snippet}",
        )


def _emit_file_connections(
    *,
    rel: str,
    comp: Component,
    source: str,
    ext: str,
    rel_files_set: Set[str],
    file_map: Dict[str, str],
    by_id: Dict[str, Component],
    components: List[Component],
    connections: List[Connection],
    seen_connections: Set[Tuple[str, str, str, int, str]],
    runtime_components: Dict[Tuple[str, str], Component],
    declared_npm: Dict[str, str],
    declared_pip: Dict[str, Tuple[str, str]],
    path_aliases: Dict[str, str],
) -> None:
    if ext in PY_EXTS:
        imports = _py_imports(source)
        resolver = _resolve_py_import
    elif ext in TS_EXTS or ext in JS_EXTS:
        imports = _ts_imports(source, ext == ".tsx")

        def resolver(spec_str: str, from_rel: str, files: Set[str]) -> Optional[str]:
            return _resolve_ts_import(
                spec_str, from_rel, files, path_aliases=path_aliases
            )
    else:
        return

    _emit_import_edges(
        rel=rel,
        comp=comp,
        imports=imports,
        resolver=resolver,
        rel_files_set=rel_files_set,
        file_map=file_map,
        by_id=by_id,
        components=components,
        connections=connections,
        seen_connections=seen_connections,
        runtime_components=runtime_components,
        declared_npm=declared_npm,
        declared_pip=declared_pip,
        ext=ext,
    )

    if ext in TS_EXTS or ext in JS_EXTS:
        _emit_api_fetch_edges(
            rel=rel,
            comp=comp,
            source=source,
            rel_files_set=rel_files_set,
            file_map=file_map,
            by_id=by_id,
            connections=connections,
            seen_connections=seen_connections,
        )

    _emit_service_edges(
        rel=rel,
        comp=comp,
        source=source,
        by_id=by_id,
        components=components,
        connections=connections,
        seen_connections=seen_connections,
        runtime_components=runtime_components,
    )


def scan_repo(repo_root: Path | str) -> ScanResult:
    """Full scan. Returns a ScanResult; caller persists via storage."""
    repo_root = Path(repo_root).resolve()
    spec = _load_gitignore(repo_root)

    # Pass 1: enumerate files, build component map.
    rel_files: List[str] = []
    for p in _iter_files(repo_root, spec):
        rel_files.append(str(p.relative_to(repo_root)).replace(os.sep, "/"))

    rel_files_set = set(rel_files)
    components: List[Component] = []
    file_map: Dict[str, str] = {}
    hashes: Dict[str, Dict[str, str]] = {}

    for rel in rel_files:
        comp = _build_component(rel)
        components.append(comp)
        file_map[rel] = comp.component_id
        h, size, mtime = _hash_file(repo_root / rel)
        hashes[rel] = {"hash": h, "size": size, "mtime": mtime}

    path_aliases = _load_ts_path_aliases(repo_root)
    declared_npm = _read_declared_npm_packages(repo_root, spec)
    declared_pip = _read_declared_pip_packages(repo_root, spec)
    runtime_components = _seed_runtime_components(components)
    by_id: Dict[str, Component] = {c.component_id: c for c in components}
    seen_connections: Set[Tuple[str, str, str, int, str]] = set()

    # Pass 2: parse imports + runtime calls per file, build connections.
    connections: List[Connection] = []
    for comp in components:
        if comp.metadata.get("kind") != "source-file":
            continue
        rel = comp.metadata.get("file", "")
        full = repo_root / rel
        try:
            source = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ext = os.path.splitext(rel)[1].lower()
        _emit_file_connections(
            rel=rel,
            comp=comp,
            source=source,
            ext=ext,
            rel_files_set=rel_files_set,
            file_map=file_map,
            by_id=by_id,
            components=components,
            connections=connections,
            seen_connections=seen_connections,
            runtime_components=runtime_components,
            declared_npm=declared_npm,
            declared_pip=declared_pip,
            path_aliases=path_aliases,
        )

    # Pass 3: backfill connects_to / connected_from on components.
    _refresh_component_links(components, connections)

    return ScanResult(
        components=components,
        connections=connections,
        file_map=file_map,
        hashes=hashes,
        files_scanned=len(rel_files),
    )


def scan_one_file(
    repo_root: Path | str,
    rel_path: str,
    prior_scan: Optional[ScanResult] = None,
) -> ScanResult:
    """Single-file rescan path for incremental updates.

    Re-parses ``rel_path`` only and merges the new component + outgoing
    connections back into ``prior_scan`` (if provided). This is the seam
    Chunk 4's freshness hooks will pull on.
    """
    repo_root = Path(repo_root).resolve()
    rel_path = rel_path.replace(os.sep, "/")
    if prior_scan is None:
        # No prior context — fall back to a small full scan.
        return scan_repo(repo_root)

    rel_files_set = set(prior_scan.file_map.keys()) | {rel_path}
    full = repo_root / rel_path
    if not full.exists():
        # File was deleted — drop it.
        new_comps = [c for c in prior_scan.components if c.metadata.get("file") != rel_path]
        new_file_map = {k: v for k, v in prior_scan.file_map.items() if k != rel_path}
        new_conns = [c for c in prior_scan.connections if c.file != rel_path]
        new_hashes = {k: v for k, v in prior_scan.hashes.items() if k != rel_path}
        new_comps = _prune_unreferenced_runtime_components(new_comps, new_conns)
        _refresh_component_links(new_comps, new_conns)
        files_scanned = sum(1 for c in new_comps if c.metadata.get("kind") == "source-file")
        return ScanResult(new_comps, new_conns, new_file_map, new_hashes, files_scanned)

    # Re-build component for rel_path.
    comp = _build_component(rel_path)
    h, size, mtime = _hash_file(full)
    new_hashes = dict(prior_scan.hashes)
    new_hashes[rel_path] = {"hash": h, "size": size, "mtime": mtime}

    new_comps = [c for c in prior_scan.components if c.metadata.get("file") != rel_path]
    new_comps.append(comp)
    new_file_map = dict(prior_scan.file_map)
    new_file_map[rel_path] = comp.component_id

    # Drop old outgoing connections from this file, re-emit.
    new_conns = [c for c in prior_scan.connections if c.file != rel_path]
    try:
        source = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""
    ext = os.path.splitext(rel_path)[1].lower()
    spec = _load_gitignore(repo_root)
    path_aliases = _load_ts_path_aliases(repo_root)
    declared_npm = _read_declared_npm_packages(repo_root, spec)
    declared_pip = _read_declared_pip_packages(repo_root, spec)
    runtime_components = _seed_runtime_components(new_comps)
    by_id: Dict[str, Component] = {c.component_id: c for c in new_comps}
    seen_connections: Set[Tuple[str, str, str, int, str]] = {
        (c.type, c.from_id, c.to_id, c.line, c.symbol) for c in new_conns
    }
    _emit_file_connections(
        rel=rel_path,
        comp=comp,
        source=source,
        ext=ext,
        rel_files_set=rel_files_set,
        file_map=new_file_map,
        by_id=by_id,
        components=new_comps,
        connections=new_conns,
        seen_connections=seen_connections,
        runtime_components=runtime_components,
        declared_npm=declared_npm,
        declared_pip=declared_pip,
        path_aliases=path_aliases,
    )

    # Re-derive connects_to / connected_from.
    new_comps = _prune_unreferenced_runtime_components(new_comps, new_conns)
    _refresh_component_links(new_comps, new_conns)
    files_scanned = sum(1 for c in new_comps if c.metadata.get("kind") == "source-file")

    return ScanResult(new_comps, new_conns, new_file_map, new_hashes, files_scanned)
