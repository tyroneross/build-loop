"""T12 — enrich orchestration + semantic-todo handoff (D5/D6).

``enrich`` runs the deterministic detectors over a repo and builds enriched
nodes + dataflow edges, plus a ``semantic_todo[]`` of sites that still need
Claude/scout labelling. It NEVER fabricates ``purpose``/``model_class`` —
those stay ``None`` until the scout fills them (D5). LLM nodes are
model-class-agnostic (D6): no behavior is keyed on a literal model id.

``merge_into_graph`` is additive over the frozen graph.json shape
``{nodes:[{id,name,layer,...}], edges:[{from,to,type,...}]}`` (D2).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from build_loop.architecture.enrich import enrich, merge_into_graph


def _w(p: Path, s: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(s), encoding="utf-8")
    return p


@pytest.fixture
def app(tmp_path: Path) -> Path:
    _w(tmp_path / "svc" / "llm.py", """
        import anthropic
        def ask(q):
            return anthropic.Anthropic().messages.create(model="claude-x", messages=[])
    """)
    _w(tmp_path / "svc" / "io.py", """
        import redis
        import requests
        def health():
            requests.get("https://svc.internal/health")
    """)
    _w(tmp_path / "package.json", '{"dependencies": {"redis": "^4"}}')
    return tmp_path


def test_enrich_builds_typed_nodes(app):
    r = enrich(app)
    types = {n["type"] for n in r.nodes}
    assert "llm-callsite" in types
    assert "api-callsite" in types
    assert "infra-component" in types
    assert "dependency" in types


def test_enrich_never_fabricates_semantics(app):
    r = enrich(app)
    for n in r.nodes:
        if n["type"] == "llm-callsite":
            assert n["purpose"] is None
            assert n["model_class"] is None
            assert n["model_example"] is None


def test_llm_node_is_model_class_agnostic(app):
    r = enrich(app)
    llm = [n for n in r.nodes if n["type"] == "llm-callsite"]
    assert llm
    n = llm[0]
    # Durable field is model_class (open vocab), absent until scout (D6).
    assert "model_class" in n
    # No node key carries a literal model id as a behavioural key.
    assert "model" not in n
    assert "model_id" not in n


def test_semantic_todo_lists_unlabelled_sites(app):
    r = enrich(app)
    assert r.semantic_todo, "every callsite needs a semantic_todo entry"
    todo = r.semantic_todo[0]
    assert "node_id" in todo and "file" in todo and "line" in todo
    assert "needs" in todo and isinstance(todo["needs"], list)
    # LLM site asks for purpose + model_class.
    llm_todo = [t for t in r.semantic_todo if t["node_id"].startswith("NODE_llm-callsite")]
    assert llm_todo
    assert "model_class" in llm_todo[0]["needs"]


def test_enrich_emits_dataflow_edges(app):
    r = enrich(app)
    etypes = {e["type"] for e in r.edges}
    assert "invokes" in etypes  # callsite --invokes--> external target


def test_node_ids_are_stable_across_runs(app):
    a = enrich(app)
    b = enrich(app)
    assert [n["id"] for n in a.nodes] == [n["id"] for n in b.nodes]
    assert [e for e in a.edges] == [e for e in b.edges]


def test_merge_into_graph_is_additive(app):
    base = {
        "nodes": [{"id": "COMP_x", "name": "pkg/x", "layer": "backend"}],
        "edges": [{"from": "COMP_x", "to": "COMP_y", "type": "imports"}],
    }
    r = enrich(app)
    merged = merge_into_graph(base, r)
    # Original import node/edge untouched (frozen D2 shape).
    assert {"id": "COMP_x", "name": "pkg/x", "layer": "backend"} in merged["nodes"]
    assert {"from": "COMP_x", "to": "COMP_y", "type": "imports"} in merged["edges"]
    # Enriched nodes appended (frozen import nodes have no 'type' key — D2).
    assert any(n.get("type") == "llm-callsite" for n in merged["nodes"])
    assert len(merged["nodes"]) > 1


def test_no_frequency_fields_in_enrich_output(app):
    import re
    freq = re.compile(r"count|freq|frequency|invocation|hits|num_calls", re.I)
    r = enrich(app)
    for n in r.nodes:
        for k in n:
            assert not freq.search(k), k
    for e in r.edges:
        for k in e:
            assert not freq.search(k), k
