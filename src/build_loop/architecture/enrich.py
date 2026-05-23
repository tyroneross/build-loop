# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Enrich orchestration — scanner output → enriched taxonomy graph + handoff.

Stage 2.5 reconciliation: the peer native-scanner (``scanner.py``) is the
SINGLE structural detection source. ``enrich`` no longer re-scans source files;
it CONSUMES the scanner's runtime Components / classified Connections and adds
ONLY its additive value:

  * the open-taxonomy mapping (→ ``llm-callsite`` / ``api-callsite`` /
    ``mcp-callsite`` / ``infra-component`` / ``external-service`` /
    ``dependency``) via ``detectors.map_scan_result`` + the 3 retained gap
    detectors ``detectors.detect_gaps`` (MCP / external-URL HTTP / infra_kind),
  * stable enriched nodes (same id scheme as before so node ids are stable),
    dataflow ``invokes`` edges (callsite → external target),
  * the ``semantic_todo[]`` — sites still needing Claude/scout labelling.
    ``enrich`` NEVER fabricates ``purpose``/``model_class``/``model_example``
    (D5); LLM nodes are model-class-agnostic (D6).

**Single-representation invariant**: every entity is detected once. The scanner
owns dependency / known-LLM/service / internal-API; ``detect_gaps`` owns ONLY
the 3 disjoint gap classes. Dedup ordering contract (plan W1/W2): gap detectors
run FIRST; package names that were infra-classified (R3) suppress the scanner's
duplicate ``dependency`` node for that same package (the entity is represented
once, as ``infra-component``).

``merge_into_graph`` is additive over the frozen graph.json shape
``{nodes:[{id,name,layer,...}], edges:[{from,to,type,...}]}`` (D2 — never
renames ``id``/``name``/``layer``/``from``/``to``/``type``).

Self-contained — no NavGator import (D8). Stdlib only (the scanner's own
pathspec/tree_sitter deps are pre-existing, not introduced here).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from . import _taxonomy as _tx
from .detectors import detect_gaps, detect_manifest, is_manifest, map_scan_result
from .scanner import scan_repo

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
    "external-service": ["purpose"],
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
            if ext in _SUPPORTED_CODE:
                yield Path(dirpath) / fn, rel


def _iter_manifest_files(repo_root: Path):
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, fn), repo_root).replace(os.sep, "/")
            if is_manifest(rel):
                yield Path(dirpath) / fn, rel


def _collect_sites(repo_root: Path) -> List[Dict[str, Any]]:
    """Single detection: scanner-mapped sites + 3 gap-detector sites.

    Dedup ordering contract: gap detectors run FIRST so infra-classified
    package names suppress the scanner's duplicate ``dependency`` site for
    that same package (single representation — infra import → infra-component,
    not also dependency).
    """
    gap_sites: List[Dict[str, Any]] = []
    for abs_path, rel in sorted(_iter_source_files(repo_root), key=lambda t: t[1]):
        gap_sites.extend(detect_gaps(abs_path, rel))

    infra_pkg_names = {
        s["raw_ref"] for s in gap_sites if s["node_type"] == "infra-component"
    }

    scan_result = scan_repo(repo_root)
    scanner_sites = []
    scanner_dep_names: set[str] = set()
    for s in map_scan_result(scan_result):
        # Suppress the scanner's dependency node for an infra-classified
        # package — it is represented once, as infra-component (W1).
        if s["node_type"] == "dependency" and s["raw_ref"] in infra_pkg_names:
            continue
        if s["node_type"] == "dependency":
            scanner_dep_names.add(s["raw_ref"])
        scanner_sites.append(s)

    # R5 — declared-dependency INVENTORY the import-driven scanner does not
    # provide. Dedup target is ONLY the true peer-duplication case: a package
    # the scanner already emitted as a `uses-package` dependency Connection.
    # An infra-classified package legitimately appears BOTH as runtime
    # `infra-component` (from the import) AND as a declared `dependency`
    # (inventory / digest.dep_manifest_hash) — these are DISTINCT layers with
    # distinct node ids, not a double representation of the same role (matches
    # pre-reconciliation Stage-2 enrich behavior + test_enrich/C1 contract).
    manifest_sites = []
    for abs_path, rel in sorted(_iter_manifest_files(repo_root), key=lambda t: t[1]):
        for s in detect_manifest(abs_path, rel):
            if s["raw_ref"] in scanner_dep_names:
                continue
            manifest_sites.append(s)

    return scanner_sites + gap_sites + manifest_sites


def enrich(repo_root: Path | str) -> EnrichResult:
    """Consume the scanner over ``repo_root`` and build the enriched delta.

    Deterministic + order-stable. Never raises on a bad file (the scanner and
    the gap detectors swallow SyntaxError / OSError). Never fabricates
    semantics (D5/D6).
    """
    repo_root = Path(repo_root).resolve()
    res = EnrichResult()
    seen_nodes: set[str] = set()

    sites = _collect_sites(repo_root)

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
                          "dependency", "infra-component", "external-service"):
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
