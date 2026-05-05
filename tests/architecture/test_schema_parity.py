"""Schema parity test against a real NavGator component file.

Loads the first ``COMP_*.json`` in NavGator's architecture dir (read-only) and
verifies build-loop's ``Component`` accepts it via ``Component(**json_data)``
without raising. This is the canary that protects Chunk 2's adapter interop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from build_loop.architecture.schemas import Component, Connection

NAVGATOR_COMPONENTS = Path(
    "/Users/tyroneross/dev/git-folder/NavGator/.navgator/architecture/components"
)
NAVGATOR_CONNECTIONS = Path(
    "/Users/tyroneross/dev/git-folder/NavGator/.navgator/architecture/connections"
)


@pytest.mark.skipif(
    not NAVGATOR_COMPONENTS.exists(),
    reason="NavGator architecture dir not present on this machine",
)
def test_component_accepts_real_navgator_component() -> None:
    files = sorted(NAVGATOR_COMPONENTS.glob("COMP_*.json"))
    assert files, "expected at least one COMP_*.json fixture"
    raw = json.loads(files[0].read_text())
    comp = Component(**raw)
    # Round-trip must preserve the canonical fields.
    out = comp.to_dict()
    for key in ("component_id", "name", "type", "role", "source",
                "connects_to", "connected_from", "status", "tags",
                "metadata", "timestamp", "last_updated", "stable_id"):
        assert key in out, f"missing key {key} after round-trip"
    assert out["component_id"] == raw["component_id"]
    assert out["stable_id"] == raw["stable_id"]


@pytest.mark.skipif(
    not NAVGATOR_CONNECTIONS.exists(),
    reason="NavGator architecture dir not present on this machine",
)
def test_connection_accepts_real_navgator_connection() -> None:
    files = sorted(NAVGATOR_CONNECTIONS.glob("CONN_*.json"))
    assert files, "expected at least one CONN_*.json fixture"
    raw = json.loads(files[0].read_text())
    conn = Connection(**raw)
    assert conn.connection_id == raw["connection_id"]
    assert conn.from_id == raw["from"]["component_id"]
    assert conn.to_id == raw["to"]["component_id"]
    assert conn.type == raw["connection_type"]
    # Round-trip preserves the original on-disk shape.
    out = conn.to_dict()
    assert out["connection_id"] == raw["connection_id"]
    assert "from" in out and "to" in out
