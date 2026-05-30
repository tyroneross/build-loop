# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic identity + Component/Connection construction.

Houses the ID/slug/hash/layer helpers whose output feeds downstream dedup keys
and the architecture graph — every byte here is contract. Also the Component
builders (source-file, package, external-service) and the seed/ensure/link
maintenance helpers that keep ``connects_to`` / ``connected_from`` consistent.

LOCKED: ``_stable_id`` / ``_component_id`` / ``_runtime_component_id`` /
``_connection_id`` / ``_slug`` / ``_short_hash`` must produce identical output —
a one-char change breaks connection dedup across the whole graph.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Dict, List, Optional, Set, Tuple

from ..patterns import ServicePattern
from ...schemas import Component, Connection

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slug(s: str) -> str:
    return _SLUG_RE.sub("_", s).strip("_").lower()


def _short_hash(s: str, n: int = 4) -> str:
    return hashlib.blake2b(s.encode("utf-8"), digest_size=8).hexdigest()[:n]


def _component_id(rel_path: str) -> str:
    base = _slug(rel_path)[:32]
    return f"COMP_component_{base}_{_short_hash(rel_path)}"


def _runtime_component_id(kind: str, name: str) -> str:
    base = _slug(name)[:32] or "unnamed"
    return f"COMP_{_slug(kind)}_{base}_{_short_hash(f'{kind}:{name}')}"


def _stable_id(rel_path: str) -> str:
    return f"STABLE_component_{rel_path.replace('/', '-')}"


def _runtime_stable_id(kind: str, name: str) -> str:
    return f"STABLE_{_slug(kind)}_{_slug(name) or 'unnamed'}"


def _connection_id(
    from_id: str,
    to_id: str,
    line: int,
    connection_type: str = "imports",
    symbol: str = "",
) -> str:
    if connection_type == "imports" and not symbol:
        seed = f"{from_id}->{to_id}@{line}"
    else:
        seed = f"{connection_type}:{from_id}->{to_id}@{line}:{symbol}"
    digest = _short_hash(seed, 6)
    return f"CONN_{_slug(connection_type)[:24]}_{digest}"


def _layer_for_path(rel_path: str) -> str:
    parts = rel_path.split("/")
    p0 = parts[0] if parts else ""
    if p0 in {"src", "lib", "core", "engine", "build_loop"}:
        return "backend"
    if p0 in {"web", "frontend", "ui", "app", "pages", "components"}:
        return "frontend"
    if p0 in {"scripts", "cli", "bin"}:
        return "tooling"
    if p0 in {"tests", "test", "__tests__"}:
        return "test"
    if p0 in {"docs", "doc"}:
        return "docs"
    return "unknown"


def _classification_for_file(rel_path: str) -> str:
    lower = rel_path.lower()
    return "test" if "test" in lower or "__tests__" in lower else "production"


# ---------------------------------------------------------------------------
# Component builders
# ---------------------------------------------------------------------------

def _build_component(rel_path: str) -> Component:
    cid = _component_id(rel_path)
    name = rel_path.rsplit(".", 1)[0].replace("\\", "/")
    return Component(
        component_id=cid,
        name=name,
        type="component",
        role={
            "purpose": f"Internal module at {rel_path}",
            "layer": _layer_for_path(rel_path),
            "critical": False,
        },
        source={
            "detection_method": "auto",
            "config_files": [rel_path],
            "confidence": 0.95,
        },
        connects_to=[],
        connected_from=[],
        status="active",
        tags=["internal", "module"],
        metadata={"file": rel_path, "kind": "source-file"},
        timestamp=int(time.time() * 1000),
        last_updated=int(time.time() * 1000),
        stable_id=_stable_id(rel_path),
    )


def _build_package_component(
    manager: str,
    package_name: str,
    manifest_rel: str,
) -> Component:
    now = int(time.time() * 1000)
    return Component(
        component_id=_runtime_component_id(f"{manager}-package", package_name),
        name=package_name,
        type="package",
        role={
            "purpose": f"{manager} package {package_name}",
            "layer": "external",
            "critical": False,
        },
        source={
            "detection_method": "manifest",
            "config_files": [manifest_rel],
            "confidence": 0.9,
        },
        connects_to=[],
        connected_from=[],
        status="active",
        tags=["external", "package", manager],
        metadata={
            "kind": "package",
            "package_manager": manager,
            "package_name": package_name,
        },
        timestamp=now,
        last_updated=now,
        stable_id=_runtime_stable_id(f"{manager}-package", package_name),
    )


def _build_service_component(pattern: ServicePattern, confidence: float = 0.85) -> Component:
    now = int(time.time() * 1000)
    return Component(
        component_id=_runtime_component_id(pattern.component_type, pattern.name),
        name=pattern.name,
        type=pattern.component_type,
        role={
            "purpose": pattern.purpose,
            "layer": pattern.layer,
            "critical": pattern.component_type in {"llm", "database"},
        },
        source={
            "detection_method": "pattern",
            "config_files": [],
            "confidence": confidence,
        },
        connects_to=[],
        connected_from=[],
        status="active",
        tags=[pattern.component_type, pattern.layer, "external"],
        metadata={"kind": "external-service", "service_name": pattern.name},
        timestamp=now,
        last_updated=now,
        stable_id=_runtime_stable_id(pattern.component_type, pattern.name),
    )


# ---------------------------------------------------------------------------
# Component-link + runtime-component maintenance
# ---------------------------------------------------------------------------

def _refresh_component_links(components: List[Component], connections: List[Connection]) -> None:
    by_id: Dict[str, Component] = {c.component_id: c for c in components}
    for comp in components:
        comp.connects_to = []
        comp.connected_from = []
    for conn in connections:
        f = by_id.get(conn.from_id)
        t = by_id.get(conn.to_id)
        if f and conn.to_id not in f.connects_to:
            f.connects_to.append(conn.to_id)
        if t and conn.from_id not in t.connected_from:
            t.connected_from.append(conn.from_id)


def _prune_unreferenced_runtime_components(
    components: List[Component],
    connections: List[Connection],
) -> List[Component]:
    referenced = {c.from_id for c in connections} | {c.to_id for c in connections}
    return [
        comp for comp in components
        if comp.metadata.get("kind") == "source-file" or comp.component_id in referenced
    ]


def _seed_runtime_components(
    components: List[Component],
) -> Dict[Tuple[str, str], Component]:
    runtime_components: Dict[Tuple[str, str], Component] = {}
    for existing in components:
        kind = existing.metadata.get("kind")
        if kind == "package":
            manager = existing.metadata.get("package_manager", "")
            package_name = existing.metadata.get("package_name", existing.name)
            runtime_components[(f"{manager}-package", package_name)] = existing
        elif kind == "external-service":
            service_name = existing.metadata.get("service_name", existing.name)
            runtime_components[(existing.type, service_name)] = existing
    return runtime_components


def _ensure_package_component(
    components: List[Component],
    by_id: Dict[str, Component],
    runtime_components: Dict[Tuple[str, str], Component],
    manager: str,
    package_name: str,
    manifest_rel: str,
) -> Component:
    key = (f"{manager}-package", package_name)
    existing = runtime_components.get(key)
    if existing:
        return existing
    package_comp = _build_package_component(manager, package_name, manifest_rel)
    runtime_components[key] = package_comp
    components.append(package_comp)
    by_id[package_comp.component_id] = package_comp
    return package_comp


def _ensure_service_component(
    components: List[Component],
    by_id: Dict[str, Component],
    runtime_components: Dict[Tuple[str, str], Component],
    pattern: ServicePattern,
) -> Component:
    key = (pattern.component_type, pattern.name)
    existing = runtime_components.get(key)
    if existing:
        return existing
    service_comp = _build_service_component(pattern)
    runtime_components[key] = service_comp
    components.append(service_comp)
    by_id[service_comp.component_id] = service_comp
    return service_comp


def _append_connection(
    connections: List[Connection],
    seen_connections: Set[Tuple[str, str, str, int, str]],
    from_comp: Component,
    to_comp: Component,
    connection_type: str,
    rel: str,
    line: int,
    symbol: str,
    confidence: float,
    detected_from: str,
    symbol_type: str = "import",
    description: str = "",
) -> None:
    key = (connection_type, from_comp.component_id, to_comp.component_id, line, symbol)
    if key in seen_connections:
        return
    seen_connections.add(key)
    connections.append(Connection(
        connection_id=_connection_id(
            from_comp.component_id,
            to_comp.component_id,
            line,
            connection_type=connection_type,
            symbol=symbol,
        ),
        from_id=from_comp.component_id,
        to_id=to_comp.component_id,
        from_stable=from_comp.stable_id,
        to_stable=to_comp.stable_id,
        type=connection_type,
        file=rel,
        line=line,
        symbol=symbol,
        symbol_type=symbol_type,
        confidence=confidence,
        classification=_classification_for_file(rel),
        detected_from=detected_from,
        description=description,
    ))
