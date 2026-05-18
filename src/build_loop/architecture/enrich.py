"""Enrich orchestration — deterministic detectors → enriched graph + handoff.

This is the *orchestration* of the detect/label split (D5):

  * Run the deterministic detectors over a repo (T11).
  * Build enriched, **unlabelled** nodes (stable ids, taxonomy layer) and
    dataflow edges (``invokes``: callsite → external target).
  * Emit a ``semantic_todo[]`` — the sites that still need Claude/scout
    labelling. ``enrich`` NEVER fabricates ``purpose``/``model_class``/
    ``model_example``; they stay ``None`` (D5).

LLM nodes are model-class-agnostic (D6): the durable field is ``model_class``
(open vocab, filled by scout); no node key carries a literal model id as a
behavioural key.

``merge_into_graph`` is additive over the frozen graph.json shape
``{nodes:[{id,name,layer,...}], edges:[{from,to,type,...}]}`` (D2 — never
renames ``id``/``name``/``layer``/``from``/``to``/``type``).

Self-contained — no NavGator import (D8). Stdlib only.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from . import _taxonomy as _tx
from .detectors import detect_file, detect_manifest, is_manifest

_SUPPORTED_CODE = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".build-loop",
    ".navgator", ".ibr", ".bookmark", "dist", "build", ".next", ".pytest_cache",
}

# Which semantic fields each node type needs the scout to fill.
_NEEDS = {
    "llm-callsite": ["purpose", "model_class", "data_in", "data_out"],
    "mcp-callsite": ["purpose", "data_in", "data_out"],
    "api-callsite": ["purpose", "data_in", "data_out"],
    "infra-component": ["purpose"],
    "dependency": [],
}


@dataclass
class EnrichResult:
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    semantic_todo: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "semantic_todo": self.semantic_todo,
        }


def _node_id(node_type: str, rel: str, line: int, raw_ref: str) -> str:
    h = hashlib.blake2b(
        f"{rel}:{line}:{raw_ref}".encode("utf-8"), digest_size=6
    ).hexdigest()
    return f"NODE_{node_type}_{h}"


def _external_id(node_type: str, raw_ref: str) -> str:
    h = hashlib.blake2b(raw_ref.encode("utf-8"), digest_size=6).hexdigest()
    return f"EXT_{node_type}_{h}"


def _iter_source_files(repo_root: Path):
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, fn), repo_root).replace(os.sep, "/")
            ext = os.path.splitext(fn)[1].lower()
            if ext in _SUPPORTED_CODE or is_manifest(rel):
                yield Path(dirpath) / fn, rel


def enrich(repo_root: Path | str) -> EnrichResult:
    """Run detectors over ``repo_root`` and build the enriched graph delta.

    Deterministic + order-stable. Never raises on a bad file (detectors
    swallow SyntaxError / OSError). Never fabricates semantics.
    """
    repo_root = Path(repo_root).resolve()
    res = EnrichResult()
    seen_nodes: set[str] = set()

    files = sorted(_iter_source_files(repo_root), key=lambda t: t[1])
    for abs, rel in files:
        if is_manifest(rel):
            sites = detect_manifest(abs, rel)
        else:
            sites = detect_file(abs, rel)

        for s in sites:
            ntype = s["node_type"]
            ok, ntype_norm, _warn = _tx_validate(ntype)  # warn-not-drop (D7)
            nid = _node_id(ntype_norm, s["file"], s["line"], s["raw_ref"])
            if nid in seen_nodes:
                continue
            seen_nodes.add(nid)

            node: Dict[str, Any] = {
                "id": nid,
                "name": s["raw_ref"] or ntype_norm,
                "layer": _tx.describe(ntype_norm)["layer"],
                "type": ntype_norm,
                "file": s["file"],
                "line": s["line"],
                "context": s.get("context", ""),
                # Semantic fields — NEVER fabricated here (D5/D6). Scout fills.
                "purpose": None,
                "model_class": None,
                "model_example": None,
            }
            if "provider" in s:
                node["provider"] = s["provider"]
            if "infra_kind" in s:
                node["infra_kind"] = s["infra_kind"]
            if "manifest" in s:
                node["manifest"] = s["manifest"]
            res.nodes.append(node)

            # Dataflow edge: callsite/dependency --invokes--> external target.
            if ntype_norm in ("llm-callsite", "mcp-callsite", "api-callsite",
                              "dependency", "infra-component"):
                tgt = _external_id(ntype_norm, s["raw_ref"] or ntype_norm)
                if tgt not in seen_nodes:
                    seen_nodes.add(tgt)
                    res.nodes.append({
                        "id": tgt,
                        "name": s["raw_ref"] or ntype_norm,
                        "layer": "external",
                        "type": "external-service",
                        "purpose": None,
                        "model_class": None,
                        "model_example": None,
                    })
                res.edges.append({"from": nid, "to": tgt, "type": "invokes"})

            needs = _NEEDS.get(ntype_norm, [])
            if needs:
                res.semantic_todo.append({
                    "node_id": nid,
                    "file": s["file"],
                    "line": s["line"],
                    "context": s.get("context", ""),
                    "needs": list(needs),
                })

    # Final deterministic ordering.
    res.nodes.sort(key=lambda n: (n["id"]))
    res.edges.sort(key=lambda e: (e["from"], e["to"], e["type"]))
    res.semantic_todo.sort(key=lambda t: t["node_id"])
    return res


def _tx_validate(t: str):
    # Local indirection so the schema gate (T10) stays the single validator.
    from .schemas import validate_node_type
    return validate_node_type(t)


def merge_into_graph(graph: Dict[str, Any], result: EnrichResult) -> Dict[str, Any]:
    """Additively merge enriched nodes/edges into a frozen graph.json dict.

    Existing import nodes/edges are preserved verbatim (D2). De-dupes enriched
    nodes by ``id`` so a re-run is idempotent.
    """
    out_nodes = list(graph.get("nodes") or [])
    out_edges = list(graph.get("edges") or [])
    existing_ids = {n.get("id") for n in out_nodes}

    for n in result.nodes:
        if n["id"] not in existing_ids:
            out_nodes.append(n)
            existing_ids.add(n["id"])

    existing_edges = {(e.get("from"), e.get("to"), e.get("type")) for e in out_edges}
    for e in result.edges:
        key = (e["from"], e["to"], e["type"])
        if key not in existing_edges:
            out_edges.append(e)
            existing_edges.add(key)

    merged = dict(graph)
    merged["nodes"] = out_nodes
    merged["edges"] = out_edges
    return merged
