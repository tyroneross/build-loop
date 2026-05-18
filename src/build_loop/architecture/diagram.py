"""Deterministic graph.json → diagram.{mmd,dot} generator (D8 — native only).

No LLM, no NavGator import. Pure function of the graph dict:

  * Vertical = taxonomy layer rank (single source: ``_taxonomy.LAYER_ORDER``;
    layer comes from each node's ``layer`` key, falling back to the
    taxonomy's descriptor for the node ``type``; an unknown type lands in the
    generic ``unknown`` layer — never crashes, never dropped — D7).
  * Horizontal = dataflow peers: nodes sharing a layer rank are grouped into
    the same rank subgraph.
  * BYTE-STABLE: every collection is sorted by a total key before emission,
    so identical graphs (in any input order) produce identical bytes.

Frozen graph.json shape (D2): ``{nodes:[{id,name,layer,...}],
edges:[{from,to,type,...}]}`` — read-only here; never renames a key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import _taxonomy as _tx
from .storage import arch_dir, atomic_write_text


def _node_layer(node: Dict[str, Any]) -> str:
    """Resolve a node's layer. Prefer explicit ``layer``; else taxonomy of
    ``type``; else generic ``unknown`` (never raises)."""
    layer = node.get("layer")
    if layer and layer in _tx.LAYER_ORDER:
        return layer
    ntype = node.get("type")
    if ntype:
        return _tx.describe(ntype)["layer"]
    return "unknown"


def _sanitize(s: str) -> str:
    # Mermaid/DOT-safe id (deterministic).
    return "".join(c if c.isalnum() or c == "_" else "_" for c in str(s))


def render(graph: Dict[str, Any]) -> Dict[str, str]:
    """Return ``{"mmd": <mermaid>, "dot": <graphviz>}``. Pure, byte-stable."""
    nodes: List[Dict[str, Any]] = list(graph.get("nodes") or [])
    edges: List[Dict[str, Any]] = list(graph.get("edges") or [])

    # Group nodes by (layer_rank, layer_name); sort everything by total keys.
    by_rank: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
    for n in nodes:
        layer = _node_layer(n)
        key = (_tx.layer_rank(layer), layer)
        by_rank.setdefault(key, []).append(n)

    sorted_rank_keys = sorted(by_rank.keys())
    sorted_edges = sorted(
        edges, key=lambda e: (str(e.get("from")), str(e.get("to")), str(e.get("type")))
    )

    # ---- Mermaid ----
    mmd: List[str] = ["flowchart TB"]
    for rk in sorted_rank_keys:
        _rank, layer = rk
        mmd.append(f"  subgraph {_sanitize(layer)}[{layer}]")
        for n in sorted(by_rank[rk], key=lambda x: str(x.get("id"))):
            nid = _sanitize(n.get("id", ""))
            label = str(n.get("name") or n.get("id") or "")
            ntype = n.get("type", "")
            cap = f"{label}<br/>{ntype}" if ntype else label
            mmd.append(f'    {nid}["{cap}"]')
        mmd.append("  end")
    for e in sorted_edges:
        f = _sanitize(e.get("from", ""))
        t = _sanitize(e.get("to", ""))
        etype = str(e.get("type", ""))
        mmd.append(f"  {f} -->|{etype}| {t}")
    mmd_text = "\n".join(mmd) + "\n"

    # ---- DOT ----
    dot: List[str] = ["digraph architecture {", "  rankdir=TB;"]
    for rk in sorted_rank_keys:
        _rank, layer = rk
        dot.append(f'  subgraph "cluster_{_sanitize(layer)}" {{')
        dot.append(f'    label="{layer}";')
        for n in sorted(by_rank[rk], key=lambda x: str(x.get("id"))):
            nid = _sanitize(n.get("id", ""))
            label = str(n.get("name") or n.get("id") or "")
            ntype = n.get("type", "")
            cap = f"{label}\\n{ntype}" if ntype else label
            dot.append(f'    {nid} [label="{cap}"];')
        dot.append("  }")
    for e in sorted_edges:
        f = _sanitize(e.get("from", ""))
        t = _sanitize(e.get("to", ""))
        etype = str(e.get("type", ""))
        dot.append(f'  {f} -> {t} [label="{etype}"];')
    dot.append("}")
    dot_text = "\n".join(dot) + "\n"

    return {"mmd": mmd_text, "dot": dot_text}


def write_diagrams(repo_root: Path | str, graph: Dict[str, Any]) -> List[str]:
    """Render + atomically write ``diagram.mmd`` and ``diagram.dot``.

    Returns the two written paths (sorted). Deterministic: re-running with an
    identical graph rewrites byte-identical files.
    """
    out = render(graph)
    d = arch_dir(repo_root)
    mmd_path = d / "diagram.mmd"
    dot_path = d / "diagram.dot"
    atomic_write_text(mmd_path, out["mmd"])
    atomic_write_text(dot_path, out["dot"])
    return sorted([str(mmd_path), str(dot_path)])
