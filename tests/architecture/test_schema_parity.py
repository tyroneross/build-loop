"""Schema parity test against a real NavGator component file.

Loads the first ``COMP_*.json`` in NavGator's architecture dir (read-only) and
verifies build-loop's ``Component`` accepts it via ``Component(**json_data)``
without raising. This is the canary that protects Chunk 2's adapter interop.

Also locks the index.json / manifest.json key-alias contract introduced in
priority 7 of the architecture-awareness follow-up: both singular
("component_count", "connection_count", "generated_at") and plural / state-shape
("components_count", "connections_count", "last_scan") keys must coexist so
NavGator-shape consumers and orchestrator state readers each see what they
expect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from build_loop.architecture.scanner import ScanResult, scan_repo
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


# ---------------------------------------------------------------------------
# Priority 7: index.json / manifest.json key-alias contract
# ---------------------------------------------------------------------------

REQUIRED_INDEX_KEYS = {
    "schema_version",
    "component_count",
    "components_count",
    "connection_count",
    "connections_count",
    "components",
    "connections",
    "generated_at",
    "last_scan",
}


def test_scanresult_to_index_emits_all_alias_keys() -> None:
    """ScanResult.to_index() must emit both singular and plural count keys
    plus both `generated_at` and `last_scan` so any consumer (orchestrator
    state read, NavGator-shape adapter, downstream tools) sees the keys it
    expects without a separate adapter."""
    result = ScanResult(components=[], connections=[], file_map={}, hashes={}, files_scanned=0)
    idx = result.to_index()
    missing = REQUIRED_INDEX_KEYS - set(idx.keys())
    assert not missing, f"index.json missing alias keys: {missing}"
    # Counts are mirrored.
    assert idx["component_count"] == idx["components_count"] == 0
    assert idx["connection_count"] == idx["connections_count"] == 0
    # Timestamps are mirrored to the same value.
    assert idx["generated_at"] == idx["last_scan"]


def test_real_scan_index_carries_alias_keys(tmp_path: Path) -> None:
    """End-to-end: scan a tiny repo, then read the persisted index.json and
    confirm the alias-key contract holds on the real artifact."""
    # Minimal synthetic repo with one Python module so the scanner has work.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "src" / "beta.py").write_text("from .alpha import os as _\n", encoding="utf-8")

    result = scan_repo(tmp_path)
    idx = result.to_index()
    missing = REQUIRED_INDEX_KEYS - set(idx.keys())
    assert not missing, f"real index missing alias keys: {missing}"
    # Counts mirror.
    assert idx["component_count"] == idx["components_count"] == len(result.components)
    assert idx["connection_count"] == idx["connections_count"] == len(result.connections)
    # Timestamps mirror.
    assert idx["generated_at"] == idx["last_scan"]
