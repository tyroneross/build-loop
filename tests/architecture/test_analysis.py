"""Analysis pure-function tests on a synthetic graph.

Graph fixture: 5 components A..E, 8 edges, a 3-node cycle A→B→C→A and a
diamond around D, E to exercise both compute_impact and check_rules.
"""

from __future__ import annotations

from typing import List

import pytest

from build_loop.architecture.analysis import (
    check_rules,
    compute_impact,
    find_dead,
    trace_dataflow,
)
from build_loop.architecture.schemas import Component, Connection


def _comp(cid: str, layer: str = "backend") -> Component:
    return Component(
        component_id=cid,
        name=cid,
        type="component",
        role={"purpose": "", "layer": layer, "critical": False},
        source={"detection_method": "auto", "config_files": [], "confidence": 1.0},
        metadata={"file": f"{cid}.py", "kind": "source-file"},
        stable_id=f"STABLE_{cid}",
    )


def _conn(frm: str, to: str, line: int = 1) -> Connection:
    return Connection(
        connection_id=f"CONN_{frm}_{to}",
        from_id=frm,
        to_id=to,
        from_stable=f"STABLE_{frm}",
        to_stable=f"STABLE_{to}",
        type="imports",
        file=f"{frm}.py",
        line=line,
        symbol=to,
        confidence=1.0,
        classification="production",
    )


@pytest.fixture
def synthetic_graph() -> tuple[List[Component], List[Connection]]:
    components = [_comp(c) for c in "ABCDE"]
    connections = [
        _conn("A", "B"),
        _conn("B", "C"),
        _conn("C", "A"),  # cycle A->B->C->A
        _conn("A", "D"),
        _conn("D", "E"),
        _conn("B", "E"),
        _conn("D", "C"),
        _conn("E", "C"),  # 8 edges total
    ]
    return components, connections


def test_compute_impact_blast_radius(synthetic_graph) -> None:
    components, connections = synthetic_graph
    # Who depends on A? In a cycle A<-C<-B<-A so impact of A reaches A,B,C,
    # and via the cycle, anyone upstream of A. Blast must be >= 4 (A,B,C, and
    # at least one outside via reachability). With cycle, ancestors(A) = {A,B,C}
    # plus anything pointing in. Affected set includes A itself per spec, so
    # we expect blast_radius >= 4 only when A is reached by D or E. In our
    # graph, B->E is a dead-end for A's incoming. Check the cycle members.
    report = compute_impact("A", components, connections)
    affected = set(report.affected)
    # A,B,C are mutually reachable via the cycle -> all impact each other.
    assert {"A", "B", "C"}.issubset(affected)
    # Per spec the test says "blast radius >= 4". D imports C and B->E->C, so
    # D and E are upstream of C (and thus, via the cycle, of A). Verify.
    assert report.blast_radius >= 4, report.to_dict()


def test_check_rules_detects_cycle(synthetic_graph) -> None:
    components, connections = synthetic_graph
    violations = check_rules(components, connections, hotspot_threshold=999)
    cycles = [v for v in violations if v.rule == "circular_dependency"]
    assert cycles, "expected at least one circular_dependency violation"
    # The A->B->C->A cycle members must appear in some cycle.
    members = set()
    for v in cycles:
        members.update(v.component_ids)
    assert {"A", "B", "C"}.issubset(members)


def test_check_rules_detects_layer_violation() -> None:
    backend = _comp("X", layer="backend")
    frontend = _comp("Y", layer="frontend")
    components = [backend, frontend]
    connections = [_conn("X", "Y")]
    violations = check_rules(components, connections)
    layer_v = [v for v in violations if v.rule == "layer_violation"]
    assert layer_v, "expected backend->frontend to flag layer_violation"


def test_trace_dataflow_out(synthetic_graph) -> None:
    components, connections = synthetic_graph
    paths = trace_dataflow("A", components, connections, depth=3, direction="out")
    # A->B, A->D should appear at depth 1.
    one_hop = {tuple(p) for p in paths if len(p) == 2}
    assert ("A", "B") in one_hop
    assert ("A", "D") in one_hop


def test_find_dead_orphan_only() -> None:
    # Component F has no edges anywhere.
    a, b, f = _comp("A"), _comp("B"), _comp("F")
    components = [a, b, f]
    connections = [_conn("A", "B")]
    report = find_dead(components, connections)
    assert "F" in report.orphan_components
    # A and B are connected, must NOT be orphans.
    assert "A" not in report.orphan_components
    assert "B" not in report.orphan_components
