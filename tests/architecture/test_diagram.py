"""T13 — deterministic graph.json → diagram.{mmd,dot} (D8 native, no NavGator).

Vertical = taxonomy layer rank (single source = _taxonomy.LAYER_ORDER).
Horizontal = dataflow peers (same rank grouped). Unknown node type → generic
layer (no crash). Output is BYTE-STABLE across reruns for an identical graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from build_loop.architecture.diagram import render, write_diagrams


def _graph():
    return {
        "nodes": [
            {"id": "COMP_a", "name": "svc/a", "layer": "service", "type": "code-component"},
            {"id": "NODE_llm_1", "name": "anthropic", "layer": "external",
             "type": "llm-callsite"},
            {"id": "NODE_q_1", "name": "queue", "layer": "queue",
             "type": "infra-component"},
            {"id": "NODE_weird_1", "name": "weird", "layer": "unknown",
             "type": "quantum-link"},  # unknown type
        ],
        "edges": [
            {"from": "COMP_a", "to": "NODE_llm_1", "type": "invokes"},
            {"from": "COMP_a", "to": "NODE_q_1", "type": "data-out"},
        ],
    }


def test_render_returns_mmd_and_dot():
    out = render(_graph())
    assert "mmd" in out and "dot" in out
    assert out["mmd"].lstrip().startswith("flowchart")
    assert out["dot"].lstrip().startswith("digraph")


def test_render_is_byte_stable_across_runs():
    g = _graph()
    a = render(g)
    b = render(g)
    assert a["mmd"] == b["mmd"]
    assert a["dot"] == b["dot"]


def test_node_order_independence():
    # Same graph, nodes shuffled → identical output (deterministic sort).
    g1 = _graph()
    g2 = _graph()
    g2["nodes"] = list(reversed(g2["nodes"]))
    g2["edges"] = list(reversed(g2["edges"]))
    assert render(g1)["mmd"] == render(g2)["mmd"]


def test_unknown_type_does_not_crash_and_lands_generic_layer():
    out = render(_graph())
    # The quantum-link node must appear (not dropped) under a generic layer.
    assert "NODE_weird_1" in out["mmd"]
    assert "NODE_weird_1" in out["dot"]


def test_vertical_layer_rank_ordering():
    out = render(_graph())["mmd"]
    # service rank < queue rank < external rank → service subgraph appears
    # before queue, queue before external (taxonomy LAYER_ORDER).
    i_service = out.index("service")
    i_queue = out.index("queue")
    i_external = out.index("external")
    assert i_service < i_queue < i_external


def test_write_diagrams_emits_two_files(tmp_path: Path):
    paths = write_diagrams(tmp_path, _graph())
    mmd = tmp_path / ".build-loop" / "architecture" / "diagram.mmd"
    dot = tmp_path / ".build-loop" / "architecture" / "diagram.dot"
    assert mmd.exists() and dot.exists()
    assert mmd.read_text() == render(_graph())["mmd"]
    # Re-write → byte-identical (atomic, deterministic).
    write_diagrams(tmp_path, _graph())
    assert mmd.read_text() == render(_graph())["mmd"]
    assert set(paths) == {str(mmd), str(dot)}


def test_empty_graph_does_not_crash():
    out = render({"nodes": [], "edges": []})
    assert out["mmd"].lstrip().startswith("flowchart")
    assert out["dot"].lstrip().startswith("digraph")
