"""Stage 2.5 reconciliation acceptance — SINGLE representation per entity.

The defect this stage exists to fix: two overlapping detectors (peer scanner
runtime Connections + Stage-2 enrich re-detection) representing the same
anthropic/fetch/redis entity twice with divergent schemas.

Acceptance gate (from the build brief): a fixture app with an anthropic call +
a fetch() + a redis import yields EXACTLY ONE representation per entity — no
entity appearing as BOTH a peer-scanner-derived node AND a separate Stage-2
detector node. Also re-asserts: byte-stable diagram, structural-only digest,
no usage-frequency anywhere.
"""

from __future__ import annotations

import json
import re
import textwrap
from collections import Counter
from pathlib import Path

from build_loop.architecture.diagram import render
from build_loop.architecture.digest import build_digest
from build_loop.architecture.enrich import enrich, merge_into_graph

_FREQ_RE = re.compile(
    r"count_of_calls|freq|frequency|invocation|num_calls|hits|times_called", re.I
)


def _w(p: Path, s: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(s), encoding="utf-8")
    return p


def _fixture(tmp_path: Path) -> Path:
    # anthropic call (Python SDK) + a fetch() + a redis import.
    _w(tmp_path / "svc" / "ai.py", """
        import anthropic
        def ask(q):
            return anthropic.Anthropic().messages.create(
                model="claude-x", messages=[])
    """)
    _w(tmp_path / "web" / "page.ts", """
        export async function load() {
          const r = await fetch("https://api.example.com/v1/data");
          return r.json();
        }
    """)
    _w(tmp_path / "svc" / "cache.py", """
        import redis
        r = redis.Redis()
    """)
    _w(tmp_path / "package.json", '{"dependencies": {"redis": "^4"}}')
    return tmp_path


def test_exactly_one_representation_per_entity(tmp_path):
    app = _fixture(tmp_path)
    r = enrich(app)

    # The anthropic entity: exactly ONE llm-callsite node, not duplicated.
    anth = [
        n for n in r.nodes
        if n["type"] == "llm-callsite" and n.get("provider") == "anthropic"
    ]
    assert len(anth) == 1, f"anthropic represented {len(anth)}x: {anth}"

    # The fetch entity: exactly ONE api-callsite for the external URL.
    fetch_nodes = [
        n for n in r.nodes
        if n["type"] == "api-callsite"
        and "api.example.com" in str(n.get("name", ""))
    ]
    assert len(fetch_nodes) == 1, f"fetch represented {len(fetch_nodes)}x"

    # The redis entity: ONE infra-component (runtime, from import) and AT MOST
    # one declared dependency (inventory layer) — never two infra-components
    # nor an infra-component duplicated as a runtime `uses-package` dependency.
    redis_infra = [
        n for n in r.nodes
        if n["type"] == "infra-component" and n.get("name") == "redis"
    ]
    assert len(redis_infra) == 1, f"redis infra represented {len(redis_infra)}x"
    redis_dep = [
        n for n in r.nodes
        if n["type"] == "dependency" and n.get("name") == "redis"
    ]
    assert len(redis_dep) <= 1, f"redis dependency duplicated: {redis_dep}"

    # Global invariant: no (type, name) pair appears more than once among the
    # detected (non-external-target) nodes — single representation per entity.
    detected = [
        (n["type"], n.get("name"))
        for n in r.nodes
        if not n["id"].startswith("EXT_")
    ]
    dupes = [k for k, c in Counter(detected).items() if c > 1]
    assert not dupes, f"entities double-represented: {dupes}"


def test_diagram_byte_stable_and_digest_structural(tmp_path):
    app = _fixture(tmp_path)
    r = enrich(app)
    graph = merge_into_graph({"nodes": [], "edges": []}, r)

    a, b = render(graph), render(graph)
    assert a["mmd"] == b["mmd"] and a["dot"] == b["dot"]

    dg = build_digest(graph)
    assert "inventory_hash" in dg and "dep_manifest_hash" in dg
    blob = json.dumps(r.to_dict()) + json.dumps(dg) + a["mmd"] + a["dot"]
    assert not _FREQ_RE.search(blob), "usage-frequency leaked into pipeline"


def test_no_double_scan_scanner_owned_vs_gap(tmp_path):
    # A JS @anthropic-ai/sdk import is FULLY scanner-covered; it must appear
    # once (scanner-sourced) and NOT also via a gap detector.
    _w(tmp_path / "package.json", '{"dependencies": {"@anthropic-ai/sdk": "^0.30"}}')
    _w(tmp_path / "a.ts", """
        import Anthropic from "@anthropic-ai/sdk";
        const c = new Anthropic();
        export const go = () => c.messages.create({ model: "x" });
    """)
    r = enrich(tmp_path)
    llm = [n for n in r.nodes if n["type"] == "llm-callsite"]
    # Single representation: exactly one llm-callsite for the scanner-covered
    # JS SDK (service-call), not also a duplicate from a detector path.
    assert len(llm) == 1, f"JS LLM SDK represented {len(llm)}x: {llm}"
