# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
# SPDX-License-Identifier: Apache-2.0
"""Open, growable controlled-vocabulary registry for node/edge types (D7).

Single source of truth for:
  * the *known* node/edge type vocabulary,
  * each type's default layer (used for diagram vertical rank, T13),
  * the layer-rank ordering itself.

D7 contract:
  - The vocabulary is OPEN. ``register_type`` adds a type additively and
    persists it; it is idempotent and NEVER bumps ``SCHEMA_VERSION``.
  - ``describe`` on an unknown type returns a *generic* descriptor and never
    raises — the unknown threads the full chain (schema → enrich → digest →
    diagram → checkpoint) without being dropped.

NavGator's taxonomy is reference inspiration only; this is a minimal,
self-contained reimplementation (D8 — no NavGator import).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Seed file ships alongside this module.
_DEFAULT_STORE = Path(__file__).resolve().parent / "arch" / "_taxonomy.json"

# ---------------------------------------------------------------------------
# Layer ordering — the single source of vertical rank for the diagram (T13).
# UI/edge → service → queue/cache → store → external → unknown(last).
# ---------------------------------------------------------------------------
LAYER_ORDER: List[str] = [
    "ui",
    "edge",
    "service",
    "queue",
    "cache",
    "store",
    "external",
    "unknown",
]
_LAYER_RANK: Dict[str, int] = {name: i for i, name in enumerate(LAYER_ORDER)}

# Built-in seeded vocabulary. ``arch/_taxonomy.json`` may extend (never shrink)
# this at runtime via register_type.
_SEED_NODE_TYPES: Dict[str, str] = {
    "code-component": "service",
    "infra-component": "store",
    "llm-callsite": "external",
    "mcp-callsite": "external",
    "api-callsite": "external",
    "external-service": "external",
    "dependency": "external",
}
_SEED_EDGE_TYPES: List[str] = [
    "imports",
    "data-in",
    "data-out",
    "transforms",
    "invokes",
    "runs-on",
]


def layer_rank(layer: str) -> int:
    """Total, deterministic rank. Unknown layers sort last (== unknown's rank)."""
    return _LAYER_RANK.get(layer, _LAYER_RANK["unknown"])


def _load_store(store_path: Optional[Path]) -> Dict[str, Any]:
    p = Path(store_path) if store_path else _DEFAULT_STORE
    base: Dict[str, Any] = {
        "node_types": dict(_SEED_NODE_TYPES),
        "edge_types": list(_SEED_EDGE_TYPES),
    }
    try:
        disk = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    # Disk extends the seed; seed always present (open vocab grows, never shrinks).
    for name, layer in (disk.get("node_types") or {}).items():
        base["node_types"][name] = layer
    for e in disk.get("edge_types") or []:
        if e not in base["edge_types"]:
            base["edge_types"].append(e)
    return base


def known_node_types(*, store_path: Optional[Path] = None) -> List[str]:
    return list(_load_store(store_path)["node_types"].keys())


def known_edge_types(*, store_path: Optional[Path] = None) -> List[str]:
    return list(_load_store(store_path)["edge_types"])


def describe(name: str, *, store_path: Optional[Path] = None) -> Dict[str, Any]:
    """Descriptor for a node type. Unknown → generic descriptor, never raises."""
    store = _load_store(store_path)
    if name in store["node_types"]:
        return {
            "name": name,
            "known": True,
            "layer": store["node_types"][name],
            "kind": "node",
        }
    return {"name": name, "known": False, "layer": "unknown", "kind": "node"}


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register_type(
    kind: str,
    name: str,
    *,
    layer: Optional[str] = None,
    store_path: Optional[Path] = None,
) -> None:
    """Additively register a node/edge type. Idempotent. No SCHEMA_VERSION bump (D7).

    ``kind`` is ``"node"`` or ``"edge"``. For node types ``layer`` defaults to
    ``"unknown"``. Persists the *delta* over the seed to ``store_path``.
    """
    p = Path(store_path) if store_path else _DEFAULT_STORE
    try:
        disk = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        disk = {}
    disk.setdefault("node_types", {})
    disk.setdefault("edge_types", [])

    if kind == "node":
        disk["node_types"][name] = layer or "unknown"
    elif kind == "edge":
        if name not in disk["edge_types"]:
            disk["edge_types"].append(name)
    else:  # pragma: no cover — defensive
        raise ValueError(f"kind must be 'node' or 'edge', got {kind!r}")

    # Sort for byte-stable persistence.
    disk["edge_types"] = sorted(set(disk["edge_types"]))
    _atomic_write(p, disk)
