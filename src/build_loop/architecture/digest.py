"""Compact App Pulse channel digest (D2 — published, full graph stays local).

NET-NEW module. Does NOT touch any ``scripts/app_pulse/`` signature/test;
the Stage-1 checkpoint reader already *consumes* ``arch/digest.json`` but
nothing wrote it until now (it noted "Stage 2 publishes it").

The digest is structure-only (explicit Non-goal: NO usage-frequency /
call-count data — asserted in tests):

  * ``node_type_counts``  — per node-type count (sorted).
  * ``inventory_hash``    — blake2b over the sorted API/MCP/LLM/dependency
    (type, ref) tuples; changes iff the LLM/API surface changed.
  * ``dep_manifest_hash`` — blake2b over the sorted dependency node refs.
  * ``adjacency``         — sorted [from, to] stable-id pairs.

``publish_digest`` writes ``<channel_dir>/arch/digest.json`` atomically.
``channel_dir`` is explicit (tests pass a tmp dir — never touches $HOME); if
omitted the caller may resolve it via ``scripts/app_pulse/_paths`` itself.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import atomic_write_json

_INVENTORY_TYPES = {"llm-callsite", "mcp-callsite", "api-callsite", "dependency"}


def _blake(parts: List[str]) -> str:
    h = hashlib.blake2b(digest_size=16)
    for p in sorted(parts):
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def build_digest(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Pure, deterministic digest of a graph.json dict. No frequency data."""
    nodes: List[Dict[str, Any]] = list(graph.get("nodes") or [])
    edges: List[Dict[str, Any]] = list(graph.get("edges") or [])

    counts = Counter(
        n.get("type", "code-component") for n in nodes
    )
    node_type_counts = dict(sorted(counts.items()))

    inventory_parts: List[str] = []
    dep_parts: List[str] = []
    for n in nodes:
        t = n.get("type", "")
        if t in _INVENTORY_TYPES:
            ref = str(n.get("name") or n.get("id") or "")
            inventory_parts.append(f"{t}|{ref}")
            if t == "dependency":
                dep_parts.append(f"{n.get('manifest', '')}|{ref}")

    adjacency = sorted(
        [str(e.get("from", "")), str(e.get("to", ""))]
        for e in edges
    )

    return {
        "node_type_counts": node_type_counts,
        "inventory_hash": _blake(inventory_parts),
        "dep_manifest_hash": _blake(dep_parts),
        "adjacency": adjacency,
        "node_total": len(nodes),
        "edge_total": len(edges),
    }


def publish_digest(
    graph: Dict[str, Any],
    *,
    channel_dir: Path | str,
) -> Path:
    """Write ``<channel_dir>/arch/digest.json`` atomically. Returns its path.

    ``channel_dir`` is required and explicit — this function never resolves a
    $HOME path itself (tests pass a tmp dir; the orchestrator/hook passes the
    app-slug channel resolved via ``scripts/app_pulse/_paths``).
    """
    out = Path(channel_dir) / "arch" / "digest.json"
    atomic_write_json(out, build_digest(graph))
    return out
