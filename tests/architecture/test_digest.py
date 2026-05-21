"""T14 — digest writer (NET-NEW; no Stage-1 signature change).

``arch/digest.json`` carries per-type node counts + API/MCP/LLM inventory
hash + dep-manifest hash + stable-ID adjacency matrix. NO frequency/
call-count data (explicit Non-goal — asserted). publish_digest writes the
channel artifact atomically and never touches scripts/rally_point/ signatures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from build_loop.architecture.digest import build_digest, publish_digest

_FREQ_RE = re.compile(r"count_of_calls|freq|frequency|invocation|num_calls|hits", re.I)


def _graph():
    return {
        "nodes": [
            {"id": "COMP_a", "name": "a", "layer": "service", "type": "code-component"},
            {"id": "L1", "name": "anthropic", "layer": "external",
             "type": "llm-callsite"},
            {"id": "M1", "name": "mcp__s__t", "layer": "external",
             "type": "mcp-callsite"},
            {"id": "API1", "name": "https://x/y", "layer": "external",
             "type": "api-callsite"},
            {"id": "D1", "name": "redis", "layer": "external", "type": "dependency",
             "manifest": "package.json"},
        ],
        "edges": [
            {"from": "COMP_a", "to": "L1", "type": "invokes"},
            {"from": "COMP_a", "to": "API1", "type": "invokes"},
        ],
    }


def test_digest_has_per_type_counts():
    d = build_digest(_graph())
    assert d["node_type_counts"]["llm-callsite"] == 1
    assert d["node_type_counts"]["dependency"] == 1
    assert d["node_type_counts"]["code-component"] == 1


def test_digest_has_inventory_and_dep_hashes():
    d = build_digest(_graph())
    assert isinstance(d["inventory_hash"], str) and len(d["inventory_hash"]) > 0
    assert isinstance(d["dep_manifest_hash"], str) and len(d["dep_manifest_hash"]) > 0


def test_digest_inventory_hash_stable_and_sensitive():
    g1 = _graph()
    h1 = build_digest(g1)["inventory_hash"]
    h1b = build_digest(_graph())["inventory_hash"]
    assert h1 == h1b  # stable
    g2 = _graph()
    g2["nodes"].append({"id": "L2", "name": "openai", "layer": "external",
                         "type": "llm-callsite"})
    assert build_digest(g2)["inventory_hash"] != h1  # surface change detected


def test_digest_has_adjacency_matrix():
    d = build_digest(_graph())
    adj = d["adjacency"]
    assert ["COMP_a", "L1"] in adj or ("COMP_a", "L1") in {tuple(x) for x in adj}


def test_digest_has_no_frequency_fields():
    d = build_digest(_graph())
    blob = json.dumps(d)
    assert not _FREQ_RE.search(blob), f"frequency-like data leaked: {blob}"
    for k in d:
        assert not _FREQ_RE.search(k), k


def test_publish_digest_writes_channel_artifact(tmp_path: Path):
    channel = tmp_path / "channel"
    p = publish_digest(_graph(), channel_dir=channel)
    out = channel / "arch" / "digest.json"
    assert out.exists()
    assert str(out) == str(p)
    loaded = json.loads(out.read_text())
    assert loaded["node_type_counts"]["llm-callsite"] == 1
    # Atomic re-write is byte-identical for an identical graph.
    publish_digest(_graph(), channel_dir=channel)
    assert json.loads(out.read_text()) == loaded


def test_publish_digest_no_home_write(tmp_path: Path, monkeypatch):
    # Must accept an explicit channel_dir and never touch $HOME.
    monkeypatch.setenv("HOME", str(tmp_path / "fake_home"))
    publish_digest(_graph(), channel_dir=tmp_path / "ch")
    assert not (tmp_path / "fake_home").exists()
