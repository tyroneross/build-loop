"""T10 — schema accepts enriched node/edge types; open-vocab warn-not-drop (D7).

The enriched taxonomy must not require a Component/Connection constructor
change: both already stash unknown keys (``extra``/``raw``) and accept an
arbitrary string ``type``. This locks that contract and adds the
``validate_node_type`` / ``validate_edge_type`` gate used by enrich/digest/
diagram. Unknown types WARN and are RETAINED — never dropped (D7).
"""

from __future__ import annotations

from build_loop.architecture.schemas import (
    SCHEMA_VERSION,
    Component,
    Connection,
    validate_edge_type,
    validate_node_type,
)


def test_new_node_types_round_trip_via_existing_constructor():
    for t in (
        "llm-callsite",
        "mcp-callsite",
        "api-callsite",
        "infra-component",
        "external-service",
        "dependency",
    ):
        c = Component(
            component_id=f"NODE_{t}_x",
            name=f"{t}-node",
            type=t,
            metadata={"file": "app/svc.py", "line": 10},
        )
        d = c.to_dict()
        assert d["type"] == t  # not coerced, not dropped


def test_new_edge_types_round_trip():
    for t in ("data-in", "data-out", "transforms", "invokes", "runs-on"):
        conn = Connection(
            connection_id=f"E_{t}", from_id="a", to_id="b", type=t,
        )
        assert conn.to_dict()["connection_type"] == t


def test_validate_known_node_type_ok_no_warning():
    ok, normalized, warning = validate_node_type("llm-callsite")
    assert ok is True
    assert normalized == "llm-callsite"
    assert warning is None


def test_validate_unknown_node_type_warns_but_retained():
    ok, normalized, warning = validate_node_type("quantum-link")
    # D7: unknown is RETAINED (ok True) but flagged with a warning string.
    assert ok is True
    assert normalized == "quantum-link"
    assert warning is not None
    assert "quantum-link" in warning


def test_validate_known_edge_type_ok():
    ok, normalized, warning = validate_edge_type("invokes")
    assert ok is True and normalized == "invokes" and warning is None


def test_validate_unknown_edge_type_warns_retained():
    ok, normalized, warning = validate_edge_type("teleports")
    assert ok is True and normalized == "teleports" and warning is not None


def test_existing_import_component_path_unchanged():
    # Regression: the import-graph component shape must be byte-identical.
    c = Component(
        component_id="COMP_component_x",
        name="pkg/a",
        type="component",
        role={"purpose": "Internal module", "layer": "backend", "critical": False},
        metadata={"file": "pkg/a.py", "kind": "source-file"},
    )
    d = c.to_dict()
    assert d["type"] == "component"
    assert d["role"]["layer"] == "backend"
    # imports connection unchanged.
    conn = Connection(
        connection_id="CONN_imports_x", from_id="a", to_id="b",
        type="imports", file="pkg/a.py", line=1, symbol=".b",
    )
    assert conn.to_dict()["connection_type"] == "imports"


def test_schema_version_unchanged_by_enrichment():
    # Enriched types are open-vocab — adding them does NOT bump the version.
    assert SCHEMA_VERSION == "1.0.0"
