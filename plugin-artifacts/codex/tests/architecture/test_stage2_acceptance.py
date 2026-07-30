"""Stage 2 acceptance gate + full-chain new-node-type survival test.

C1 fixture app → correct llm/mcp/api/infra/dependency nodes + dataflow edges.
C2 LLM nodes model_class-agnostic; no literal-model behavioural key.
C3 diagram.{mmd,dot} byte-stable across reruns.
C4 manifest pre-edit marks enrich-needed, no inline enrich (covered in
   test_pre_edit_manifest.py — re-asserted here at the integration layer).
C5 FULL-CHAIN: a brand-new node type survives
   schema-validate → enrich-merge → digest → diagram → checkpoint_read
   without being dropped.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from build_loop.architecture.diagram import render, write_diagrams
from build_loop.architecture.digest import build_digest, publish_digest
from build_loop.architecture.enrich import enrich, merge_into_graph
from build_loop.architecture.schemas import validate_node_type

_REPO = Path(__file__).resolve().parents[2]


def _checkpoint_read():
    """Import the FROZEN Stage-1 checkpoint reader (consumed, not modified)."""
    ap = _REPO / "scripts" / "rally_point"
    if str(ap) not in sys.path:
        sys.path.insert(0, str(ap))
    import checkpoint  # type: ignore

    return checkpoint.checkpoint_read


def _w(p: Path, s: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(s), encoding="utf-8")
    return p


@pytest.fixture
def fixture_app(tmp_path: Path) -> Path:
    _w(tmp_path / "svc" / "ai.py", """
        import anthropic
        from openai import OpenAI
        def ask(q):
            anthropic.Anthropic().messages.create(model="claude-x", messages=[])
            OpenAI().chat.completions.create(model="gpt-x", messages=[])
    """)
    _w(tmp_path / "svc" / "io.py", """
        import redis
        from bullmq import Queue
        import psycopg
        import boto3
        import requests
        async def run(session):
            requests.get("https://svc.internal/health")
            await session.call_tool("mcp__search__query", {"q": "x"})
    """)
    _w(tmp_path / "package.json", '{"dependencies": {"redis": "^4", "react": "^19"}}')
    return tmp_path


# --- C1 ---------------------------------------------------------------------

def test_C1_fixture_yields_all_node_types_and_dataflow_edges(fixture_app):
    r = enrich(fixture_app)
    types = {n["type"] for n in r.nodes}
    for expected in (
        "llm-callsite", "mcp-callsite", "api-callsite",
        "infra-component", "dependency",
    ):
        assert expected in types, f"missing {expected}: {sorted(types)}"
    edge_types = {e["type"] for e in r.edges}
    assert "invokes" in edge_types


# --- C2 ---------------------------------------------------------------------

def test_C2_llm_nodes_model_class_agnostic(fixture_app):
    r = enrich(fixture_app)
    llm = [n for n in r.nodes if n["type"] == "llm-callsite"]
    assert llm
    for n in llm:
        assert n["model_class"] is None       # durable field, scout fills it
        assert n["model_example"] is None
        assert "model" not in n               # no literal-model behavioural key
        assert "model_id" not in n
        assert n["purpose"] is None           # never fabricated (D5)


# --- C3 ---------------------------------------------------------------------

def test_C3_diagram_byte_stable(fixture_app, tmp_path):
    r = enrich(fixture_app)
    graph = merge_into_graph({"nodes": [], "edges": []}, r)
    a = render(graph)
    b = render(graph)
    assert a["mmd"] == b["mmd"] and a["dot"] == b["dot"]
    p1 = write_diagrams(tmp_path, graph)
    mmd = Path([x for x in p1 if x.endswith(".mmd")][0]).read_text()
    write_diagrams(tmp_path, graph)
    mmd2 = Path([x for x in p1 if x.endswith(".mmd")][0]).read_text()
    assert mmd == mmd2 == a["mmd"]


# --- C5 full-chain new-node-type survival -----------------------------------

def test_C5_novel_node_type_survives_full_chain(fixture_app, tmp_path):
    novel = "quantum-link"

    # 1. schema validate — unknown WARNS but is RETAINED (D7).
    ok, normalized, warning = validate_node_type(novel)
    assert ok is True and normalized == novel and warning is not None

    # 2. enrich merge — inject a novel-type node into the enriched graph.
    r = enrich(fixture_app)
    graph = merge_into_graph({"nodes": [], "edges": []}, r)
    graph["nodes"].append({
        "id": "NODE_quantum-link_zzz",
        "name": "qlink",
        "layer": "unknown",
        "type": novel,
        "purpose": None, "model_class": None, "model_example": None,
    })
    graph["edges"].append({
        "from": "NODE_quantum-link_zzz", "to": "NODE_quantum-link_zzz",
        "type": "teleports",  # novel edge type too
    })

    # 3. digest — novel type counted, not dropped.
    dg = build_digest(graph)
    assert dg["node_type_counts"].get(novel) == 1

    # 4. diagram — novel node rendered under the generic layer, no crash.
    out = render(graph)
    assert "NODE_quantum_link_zzz" in out["mmd"]  # id sanitized but present
    assert "NODE_quantum_link_zzz" in out["dot"]

    # 5. publish digest → channel, then FROZEN checkpoint_read consumes it.
    channel = tmp_path / "channel"
    publish_digest(graph, channel_dir=channel)

    # Drive a revision bump + cursor so checkpoint_read takes the changed path.
    sys.path.insert(0, str(_REPO / "scripts" / "rally_point"))
    import revision as _rev  # type: ignore
    import changes as _ch     # type: ignore

    channel.mkdir(parents=True, exist_ok=True)
    _ch.append_change(channel, {
        "ts": 0, "kind": "arch-scan-complete", "tool": "test",
        "model": "n/a", "run_id": "r1", "app_slug": "x", "payload": {},
    })
    _rev.bump_revision(channel)

    checkpoint_read = _checkpoint_read()
    env = checkpoint_read(channel, session_id="s1", my_files=[])
    assert env["arch_digest"] is not None, "digest dropped before checkpoint"
    # The novel type threaded the FULL chain into the consumer envelope.
    assert env["arch_digest"]["node_type_counts"].get(novel) == 1
    assert env["changed"] is True


# --- non-goal guard at the integration layer --------------------------------

def test_no_observability_anywhere_in_pipeline(fixture_app):
    import re
    freq = re.compile(r"count_of_calls|freq|frequency|invocation|num_calls|hits|times_called", re.I)
    r = enrich(fixture_app)
    graph = merge_into_graph({"nodes": [], "edges": []}, r)
    dg = build_digest(graph)
    dia = render(graph)
    blob = json.dumps(r.to_dict()) + json.dumps(dg) + dia["mmd"] + dia["dot"]
    assert not freq.search(blob), "usage-frequency data leaked into the pipeline"
