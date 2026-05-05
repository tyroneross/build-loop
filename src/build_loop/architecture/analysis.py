"""Pure analysis functions over the component/connection graph.

No I/O — functions take ``components`` + ``connections`` and return reports.
This makes them trivially unit-testable on synthetic graphs.

Uses ``networkx`` for graph operations (cycles, descendants, reachability).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import networkx as nx

from .schemas import Component, Connection


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
) -> DeadReport:
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
    return DeadReport(
        orphan_components=sorted(orphans),
        unused_packages=[],  # populated in Chunk 8 alongside native lessons store.
        notes=["Chunk 1 detects orphan components only; package-level dead detection arrives in Chunk 8."],
    )


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
