#!/usr/bin/env python3
"""Knowledge-graph leg for hybrid recall (Phase B).

In-memory networkx DiGraph built from `markdown_graph_parser.parse_decisions_dir`,
plus PageRank prior used as the third leg of the recall pipeline.

Why in-memory and rebuild-on-demand:
  - Decision corpus is small (~100 entries today, ~1000 ceiling per the
    research entry); rebuild is sub-second.
  - No persisted graph file means no migration, no schema, no stale state.
  - Module-level cache keyed on `.episodic/` mtime gives near-free reuse
    for callers that run several queries in a row (e.g. evaluate.py).

Public API:
    build_graph(edges)              -> nx.DiGraph
    pagerank_prior(graph, alpha)    -> dict[node_id, score]
    get_cached_graph(decisions_dir) -> tuple[nx.DiGraph, dict]
    graph_walk_leg(seed_ids, depth) -> list[Result]

The graph_walk_leg returns results shaped like the other recall legs
(`{"id": ..., "score": ..., ...}`) so it can be passed straight into
`rrf_fuse([vector, sparse, graph])`.

Edge semantics:
  Edges are added in BOTH directions. The forward direction carries the
  parsed edge_type (`wikilink`, `path`, `cite`); the reverse direction
  is added with edge_type `reverse-<original>` so traversal works
  bidirectionally. PageRank treats the graph as directed and uses
  out-degree weighting — the Phase B spec calls for symmetry, so we
  symmetrize at edge-add time rather than calling `graph.to_undirected()`
  in PageRank (the latter would lose edge-type info).
"""
from __future__ import annotations

import sys
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

# Hard-required network dep — networkx is already in pyproject's base deps.
import networkx as nx  # type: ignore

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from markdown_graph_parser import Edge, parse_decisions_dir  # type: ignore  # noqa: E402

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_graph(edges: Iterable[Edge]) -> nx.DiGraph:
    """Construct a DiGraph from the parsed edge list.

    Each edge becomes two graph edges (forward + reverse) so BFS picks
    up both directions. The forward edge keeps its original `edge_type`
    attribute; the reverse edge gets `reverse-<edge_type>`.

    Self-loops are skipped (the parser drops them too, but defensive).
    Duplicate (source, target) pairs collapse — networkx's edge
    semantics keep only the last attr write.
    """
    g = nx.DiGraph()
    for src, dst, et in edges:
        if src == dst:
            continue
        g.add_edge(src, dst, edge_type=et)
        if not g.has_edge(dst, src):
            g.add_edge(dst, src, edge_type=f"reverse-{et}")
    return g


# ---------------------------------------------------------------------------
# PageRank prior
# ---------------------------------------------------------------------------

DEFAULT_ALPHA = 0.85
"""PageRank damping factor. vault_vector.py uses 0.85; the build-loop
spec mirrors that. Lower = more uniform distribution; higher = more
sensitivity to graph structure."""


def pagerank_prior(graph: nx.DiGraph, *, alpha: float = DEFAULT_ALPHA) -> dict[str, float]:
    """Compute personalised-PageRank-style prior over nodes.

    Returns a dict mapping node_id → PageRank score in [0, 1] (sums to
    1.0 across all nodes). Min-max normalisation is the caller's job —
    this module returns the raw distribution so downstream code can
    decide whether to normalise per-result-set or globally.

    Empty graph → empty dict.
    Single-node graph → {node: 1.0}.
    networkx convergence failure → uniform distribution as fallback
      (this is a Phase B additive signal; never gate on it).

    networkx >=3.0 routes `pagerank()` to a scipy implementation by
    default; we fall through to the pure-Python implementation when
    scipy is missing (build-loop's base deps don't include scipy and
    we don't want to add it for a single function call).
    """
    if graph.number_of_nodes() == 0:
        return {}
    try:
        return nx.pagerank(graph, alpha=alpha)
    except nx.PowerIterationFailedConvergence:
        n = graph.number_of_nodes()
        return {node: 1.0 / n for node in graph.nodes()}
    except ImportError:
        # scipy missing — fall through to the pure-Python power iteration
        # baked into networkx as `_pagerank_python`. Slightly slower but
        # has no extra deps.
        try:
            from networkx.algorithms.link_analysis.pagerank_alg import (  # type: ignore
                _pagerank_python,
            )
            return _pagerank_python(graph, alpha=alpha)
        except Exception:  # noqa: BLE001
            n = graph.number_of_nodes()
            return {node: 1.0 / n for node in graph.nodes()}


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_GRAPH_CACHE: dict[str, Any] = {
    "decisions_dir": None,
    "mtime": None,
    "graph": None,
    "ppr": None,
}
_GRAPH_CACHE_LOCK = Lock()


def _dir_mtime(path: Path) -> float:
    """Max mtime across `.episodic/decisions/*.md` (cheap directory scan).

    Used as the cache invalidation signal. Adding a new decision or
    editing an existing one bumps the mtime; the cache rebuilds on the
    next call.
    """
    if not path.is_dir():
        return 0.0
    latest = 0.0
    for p in path.glob("*.md"):
        try:
            m = p.stat().st_mtime
            if m > latest:
                latest = m
        except OSError:
            continue
    # Also include the directory itself so newly added files we somehow
    # missed via glob still bust the cache.
    try:
        d = path.stat().st_mtime
        if d > latest:
            latest = d
    except OSError:
        pass
    return latest


def get_cached_graph(
    decisions_dir: Path | str,
    *,
    extra_paths: Iterable[Path] | None = None,
    force_rebuild: bool = False,
) -> tuple[nx.DiGraph, dict[str, float]]:
    """Return (graph, ppr) for `decisions_dir`, building if stale.

    The cache key is the directory path. Switching the dir (e.g. tests
    using a tmp path) transparently rebuilds.
    """
    decisions_dir = Path(decisions_dir)
    mtime = _dir_mtime(decisions_dir)
    with _GRAPH_CACHE_LOCK:
        cached_dir = _GRAPH_CACHE.get("decisions_dir")
        cached_mtime = _GRAPH_CACHE.get("mtime")
        if (
            not force_rebuild
            and cached_dir == decisions_dir
            and cached_mtime == mtime
            and _GRAPH_CACHE.get("graph") is not None
        ):
            return _GRAPH_CACHE["graph"], _GRAPH_CACHE["ppr"]

        edges = parse_decisions_dir(decisions_dir, extra_paths=extra_paths)
        graph = build_graph(edges)
        ppr = pagerank_prior(graph)
        _GRAPH_CACHE["decisions_dir"] = decisions_dir
        _GRAPH_CACHE["mtime"] = mtime
        _GRAPH_CACHE["graph"] = graph
        _GRAPH_CACHE["ppr"] = ppr
        return graph, ppr


def invalidate_cache() -> None:
    """Force the next get_cached_graph call to rebuild. Tests use this
    to ensure they aren't reading a stale graph from another test's run."""
    with _GRAPH_CACHE_LOCK:
        _GRAPH_CACHE["decisions_dir"] = None
        _GRAPH_CACHE["mtime"] = None
        _GRAPH_CACHE["graph"] = None
        _GRAPH_CACHE["ppr"] = None


# ---------------------------------------------------------------------------
# Graph-walk leg
# ---------------------------------------------------------------------------

DEFAULT_DEPTH = 2
DEFAULT_GRAPH_LEG_LIMIT = 21  # match recall.py's PER_LEG_OVERFETCH


def graph_walk_leg(
    seed_ids: list[str],
    graph: nx.DiGraph,
    *,
    depth: int = DEFAULT_DEPTH,
    limit: int = DEFAULT_GRAPH_LEG_LIMIT,
    ppr: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """BFS depth-`depth` from each seed; return reachable nodes ranked.

    Score model:
      For each seed → reachable node hop_distance d (1..depth):
        leg_score(node) += 1 / (d + 1)
      Then if `ppr` is provided, multiply leg_score by (1 + ppr[node]).
      Final ordering: leg_score descending.

    Why this shape:
      - Closer nodes (1-hop neighbours) score higher than 2-hop.
      - PageRank acts as a *prior boost* on globally-important nodes
        rather than a tie-breaker; this matches the LightRAG
        "vector-first, KG cross-reference" pattern the research entry
        recommends.
      - Output dicts mirror the vector/sparse leg shape so rrf_fuse
        can consume them without translation.

    seed_ids that aren't in the graph are skipped silently (the seed
    might be a decision id that hasn't been linked to anything yet).
    Empty graph or empty seeds → returns [].

    The result rows are minimal: just `{"id": node_id, "score":
    leg_score, "_graph_hop": min_hop}`. Callers that need more data
    will re-fetch from the DB during rerank.
    """
    if not seed_ids or graph.number_of_nodes() == 0:
        return []

    score_by_id: dict[str, float] = {}
    min_hop_by_id: dict[str, int] = {}

    for seed in seed_ids:
        if seed not in graph:
            continue
        # BFS up to `depth` hops.
        # `single_source_shortest_path_length` gives us the shortest-path
        # distance from `seed` to every reachable node, capped at `depth`.
        try:
            dists = nx.single_source_shortest_path_length(graph, seed, cutoff=depth)
        except Exception:  # noqa: BLE001
            continue
        for node, d in dists.items():
            if d == 0:
                continue  # skip the seed itself
            contribution = 1.0 / (d + 1)
            score_by_id[node] = score_by_id.get(node, 0.0) + contribution
            prev = min_hop_by_id.get(node)
            if prev is None or d < prev:
                min_hop_by_id[node] = d

    if not score_by_id:
        return []

    # Apply PageRank prior boost (if available).
    if ppr:
        for node in list(score_by_id.keys()):
            boost = ppr.get(node, 0.0)
            score_by_id[node] *= 1.0 + boost

    ranked = sorted(score_by_id.items(), key=lambda kv: kv[1], reverse=True)
    out: list[dict[str, Any]] = []
    for node, sc in ranked[:limit]:
        out.append({
            "id": node,
            "score": sc,
            "_graph_hop": min_hop_by_id.get(node, depth),
        })
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Dump graph stats. Useful for verifying the parse + build path.

    Usage:
      python3 scripts/recall_graph.py [.episodic/decisions]
    """
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(".episodic/decisions")
    g, ppr = get_cached_graph(root, force_rebuild=True)
    print(f"# nodes: {g.number_of_nodes()}")
    print(f"# edges: {g.number_of_edges()}")
    if g.number_of_nodes():
        top = sorted(ppr.items(), key=lambda kv: kv[1], reverse=True)[:10]
        print("# top-10 PageRank:")
        for node, score in top:
            print(f"  {score:.6f}  {node}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
