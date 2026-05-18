"""T9 — open growable controlled-vocabulary taxonomy registry (D7).

The registry is the single source of truth for known node/edge types and the
layer-rank ordering consumed by the diagram generator (T13). Adding a type is
additive and must NOT bump SCHEMA_VERSION (D7). Unknown lookups return a
generic descriptor and never raise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from build_loop.architecture import _taxonomy as T
from build_loop.architecture.schemas import SCHEMA_VERSION


def test_known_node_types_seeded():
    nt = set(T.known_node_types())
    assert {
        "code-component",
        "infra-component",
        "llm-callsite",
        "mcp-callsite",
        "api-callsite",
        "external-service",
        "dependency",
    }.issubset(nt)


def test_known_edge_types_seeded():
    et = set(T.known_edge_types())
    assert {
        "imports",
        "data-in",
        "data-out",
        "transforms",
        "invokes",
        "runs-on",
    }.issubset(et)


def test_describe_known_returns_descriptor():
    d = T.describe("llm-callsite")
    assert d["name"] == "llm-callsite"
    assert d["known"] is True
    assert "layer" in d


def test_describe_unknown_returns_generic_never_raises():
    d = T.describe("quantum-link")
    assert d["name"] == "quantum-link"
    assert d["known"] is False
    assert d["layer"] == "unknown"


def test_register_type_is_additive_and_persisted(tmp_path: Path):
    store = tmp_path / "_taxonomy.json"
    T.register_type("node", "quantum-link", layer="external", store_path=store)
    # Visible immediately in this process.
    assert "quantum-link" in T.known_node_types(store_path=store)
    # Persisted to disk.
    raw = json.loads(store.read_text())
    assert "quantum-link" in raw["node_types"]
    # Idempotent — re-register does not duplicate or raise.
    T.register_type("node", "quantum-link", layer="external", store_path=store)
    assert T.known_node_types(store_path=store).count("quantum-link") == 1


def test_register_type_does_not_bump_schema_version():
    before = SCHEMA_VERSION
    T.register_type("node", "ephemeral-test-type", layer="service")
    # D7: adding a type is additive — NO schema-version bump.
    from build_loop.architecture.schemas import SCHEMA_VERSION as after
    assert before == after == "1.0.0"


def test_layer_rank_is_total_and_monotonic():
    # Single source of vertical ordering for the diagram generator (T13).
    ranks = [T.layer_rank(l) for l in T.LAYER_ORDER]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(T.LAYER_ORDER)
    # Unknown layer sorts last, deterministically.
    assert T.layer_rank("nonsense-layer") >= max(ranks)
