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


def test_check_rules_cycle_search_is_bounded_and_fast() -> None:
    """Regression: a large, densely-connected graph must NOT hang.

    Before the fix, ``check_rules`` materialized EVERY simple cycle via an
    unbounded ``nx.simple_cycles``, which is exponential in graph size and hung
    indefinitely on the real build-loop repo (≈11k nodes / 29k edges). The fix
    caps both cycle length (CYCLE_LENGTH_BOUND) and the number of cycles
    reported (MAX_CYCLES_REPORTED). This builds a fully-connected directed
    graph — a worst case for cycle enumeration — and asserts the call returns
    quickly with a bounded number of circular-dependency violations.
    """
    import time

    from build_loop.architecture.analysis import MAX_CYCLES_REPORTED

    n = 40  # K_40 has 40*39 = 1560 edges and astronomically many simple cycles
    ids = [f"N{i}" for i in range(n)]
    components = [_comp(cid) for cid in ids]
    connections = [
        _conn(a, b)
        for a in ids
        for b in ids
        if a != b
    ]

    start = time.monotonic()
    violations = check_rules(components, connections, hotspot_threshold=10**9)
    elapsed = time.monotonic() - start

    # Must terminate fast — an unbounded search on K_40 never returns.
    assert elapsed < 10.0, f"cycle search took {elapsed:.1f}s — bound regressed"
    cycles = [v for v in violations if v.rule == "circular_dependency"]
    assert len(cycles) <= MAX_CYCLES_REPORTED, (
        f"reported {len(cycles)} cycles, cap is {MAX_CYCLES_REPORTED}"
    )
    assert cycles, "K_40 has cycles — at least one must be reported"

    # F5: a truncated report must be distinguishable from a complete one. K_40
    # has far more than MAX_CYCLES_REPORTED short cycles, so the cap is hit and
    # an info-level cycle_search_truncated marker must be emitted.
    truncated = [v for v in violations if v.rule == "cycle_search_truncated"]
    assert len(truncated) == 1, "expected exactly one cycle_search_truncated marker"
    assert truncated[0].severity == "info"
    assert truncated[0].details.get("max_cycles_reported") == MAX_CYCLES_REPORTED


def test_check_rules_detects_layer_violation() -> None:
    backend = _comp("X", layer="backend")
    frontend = _comp("Y", layer="frontend")
    components = [backend, frontend]
    connections = [_conn("X", "Y")]
    violations = check_rules(components, connections)
    layer_v = [v for v in violations if v.rule == "layer_violation"]
    assert layer_v, "expected backend->frontend to flag layer_violation"


def test_check_rules_detects_shallow_module() -> None:
    # HUB imports 4 leaves (fan-out 4) but nobody imports HUB (fan-in 0):
    # shallownessScore = 4 / (0 + 1) = 4 >= 2 and fan-out 4 >= 4 → shallow.
    hub = _comp("HUB")
    leaves = [_comp(n) for n in ("L1", "L2", "L3", "L4")]
    components = [hub, *leaves]
    connections = [_conn("HUB", leaf.component_id) for leaf in leaves]
    violations = check_rules(components, connections, hotspot_threshold=999)
    shallow = [v for v in violations if v.rule == "shallow_module"]
    assert shallow, "expected HUB (imports 4, used by 0) to flag shallow_module"
    hub_v = next(v for v in shallow if v.component_id == "HUB")
    assert hub_v.severity == "warn"
    assert hub_v.details["fan_out"] == 4
    assert hub_v.details["fan_in"] == 0
    # A leaf (imported once, imports nothing) is not a shallow module.
    assert not any(v.component_id == "L1" for v in shallow)


def test_check_rules_deep_module_not_flagged_shallow() -> None:
    # DEEP is imported by 3 modules but imports only 1: fan-out 1 < 4 → not shallow.
    deep = _comp("DEEP")
    users = [_comp(n) for n in ("U1", "U2", "U3")]
    dep = _comp("DEP")
    components = [deep, dep, *users]
    connections = [_conn(u.component_id, "DEEP") for u in users] + [_conn("DEEP", "DEP")]
    violations = check_rules(components, connections, hotspot_threshold=999)
    assert not any(v.rule == "shallow_module" and v.component_id == "DEEP" for v in violations)


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
    # Without repo_root, package-level detection is skipped (notes explain why).
    assert report.unused_packages == []
    assert any("repo_root" in n for n in report.notes)


# ---------------------------------------------------------------------------
# Package-level dead detection
# ---------------------------------------------------------------------------

def _make_file_component(rel: str) -> Component:
    return Component(
        component_id=f"COMP_{rel.replace('/', '_')}",
        name=rel.rsplit(".", 1)[0],
        type="component",
        role={"purpose": "", "layer": "backend", "critical": False},
        source={"detection_method": "auto", "config_files": [rel], "confidence": 1.0},
        metadata={"file": rel, "kind": "source-file"},
        stable_id=f"STABLE_{rel}",
    )


def test_find_unused_packages_npm(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"left-pad": "^1.0.0", "react": "^18.0.0", '
        '"@scope/used": "^1.0.0", "@scope/dead": "^1.0.0"}, '
        '"devDependencies": {"typescript": "^5.0.0", "vite": "^5.0.0"}}',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.ts").write_text(
        "import React from 'react';\nimport {x} from '@scope/used/sub';\n",
        encoding="utf-8",
    )
    components = [_make_file_component("src/app.ts")]
    from build_loop.architecture.analysis import find_unused_packages
    unused = find_unused_packages(tmp_path, components)
    # left-pad declared, never imported
    assert "left-pad" in unused
    # @scope/dead declared, never imported (scoped)
    assert "@scope/dead" in unused
    # Imported packages must NOT appear
    assert "react" not in unused
    assert "@scope/used" not in unused
    # Build-only tools must be filtered out even though they're declared
    assert "typescript" not in unused
    assert "vite" not in unused


def test_find_unused_packages_pip(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n'
        'dependencies = ["requests>=2", "pyyaml", "left-pad-py"]\n'
        '[project.optional-dependencies]\ntest = ["pytest>=8"]\n',
        encoding="utf-8",
    )
    src = tmp_path / "demo"
    src.mkdir()
    (src / "main.py").write_text(
        "import requests\nimport yaml\nimport os\n",
        encoding="utf-8",
    )
    components = [_make_file_component("demo/main.py")]
    from build_loop.architecture.analysis import find_unused_packages
    unused = find_unused_packages(tmp_path, components)
    # Declared but never imported
    assert "left-pad-py" in unused
    # requests imported directly
    assert "requests" not in unused
    # pyyaml imports as `yaml` — alias map must save it
    assert "pyyaml" not in unused
    # pytest is in the runtime-only allowlist
    assert "pytest" not in unused


def test_find_dead_with_repo_root_populates_unused(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"unused-dep": "^1.0.0"}}', encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.ts").write_text("export const x = 1;\n", encoding="utf-8")
    components = [_make_file_component("src/x.ts")]
    report = find_dead(components, [], repo_root=tmp_path)
    assert "unused-dep" in report.unused_packages
