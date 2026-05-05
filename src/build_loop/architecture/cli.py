"""Argparse CLI for the architecture engine.

Subcommands (all support ``--json``):
    scan [--full|--incremental] [--repo PATH]
    impact <component-or-file>
    trace <component-or-file> [--depth N] [--direction in|out|both]
    connections <component-or-file>
    rules [--json]
    dead [--json]
    llm-map [--json]                          (NavGator-only)
    schema [model] [--json]                   (NavGator-only)
    diagram [--mode summary|focus|layer] [--focus NAME] [--json]  (NavGator-only)

Global flag ``--mode {auto,native,navgator}`` (default ``auto``) routes through
``adapter.Adapter``. Native engine is canonical for ported capabilities; the
last three subcommands are NavGator-only and require either a ``navgator`` CLI
on PATH or a NavGator MCP server registered in ``.mcp.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import analysis as A
from .adapter import (
    Adapter,
    AdapterError,
    CapabilityNotAvailable,
    NavGatorNotAvailable,
)
from .scanner import scan_repo
from .schemas import Component, Connection, SCHEMA_VERSION
from .storage import (
    arch_dir,
    read_index,
    read_manifest,
    write_file_map,
    write_graph,
    write_hashes,
    write_index,
    write_manifest,
    write_reverse_deps,
    write_timeline,
)


def _resolve_repo(opt: Optional[str]) -> Path:
    return Path(opt or os.getcwd()).resolve()


def _load_graph(repo_root: Path) -> tuple[List[Component], List[Connection]]:
    idx = read_index(repo_root)
    if not idx:
        return [], []
    comps = [Component(**c) for c in idx.get("components", [])]
    conns = [Connection(**c) for c in idx.get("connections", [])]
    return comps, conns


def _resolve_target(
    target: str,
    components: Sequence[Component],
) -> Optional[Component]:
    """Accept either a component_id OR a repo-relative file path."""
    if not components:
        return None
    # Direct id match.
    for c in components:
        if c.component_id == target:
            return c
    # File path match.
    target_norm = target.replace(os.sep, "/").lstrip("./")
    for c in components:
        if c.metadata.get("file") == target_norm:
            return c
    # Suffix file match (so "storage.py" matches "src/build_loop/architecture/storage.py").
    for c in components:
        f = c.metadata.get("file", "")
        if f.endswith("/" + target_norm) or f == target_norm:
            return c
    # Name suffix match.
    for c in components:
        if c.name.endswith(target_norm) or c.name == target_norm:
            return c
    return None


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    t0 = time.time()
    result = scan_repo(repo)
    elapsed_ms = int((time.time() - t0) * 1000)

    write_index(repo, result.to_index())

    # graph.json — adjacency for quick consumption (parity with NavGator).
    graph = {
        "nodes": [
            {"id": c.component_id, "name": c.name, "layer": (
                c.role.layer if hasattr(c.role, "layer") else (c.role or {}).get("layer", "unknown")
            )} for c in result.components
        ],
        "edges": [
            {"from": conn.from_id, "to": conn.to_id, "type": conn.type}
            for conn in result.connections
        ],
    }
    write_graph(repo, graph)

    write_file_map(repo, {"files": result.file_map})
    write_hashes(repo, {"files": result.hashes})

    rev: Dict[str, List[str]] = {}
    for conn in result.connections:
        rev.setdefault(conn.to_id, []).append(conn.from_id)
    write_reverse_deps(repo, {"reverse_deps": rev})

    now_ms = int(time.time() * 1000)
    prior = read_manifest(repo) or {}
    timeline = {
        "events": (prior.get("timeline") or []) + [
            {"event": "scan", "mode": "incremental" if args.incremental else "full",
             "ts": now_ms, "elapsed_ms": elapsed_ms,
             "components": len(result.components), "connections": len(result.connections)}
        ][-50:]
    }
    write_timeline(repo, timeline)

    write_manifest(repo, {
        "schema_version": SCHEMA_VERSION,
        "generator": "build-loop-native",
        "generator_version": "0.1.0",
        "repo_root": str(repo),
        "component_count": len(result.components),
        "connection_count": len(result.connections),
        "files_scanned": result.files_scanned,
        "generated_at": now_ms,
        "last_full_scan_at": now_ms if not args.incremental else (prior.get("last_full_scan_at") or 0),
        "last_incremental_at": now_ms if args.incremental else (prior.get("last_incremental_at") or 0),
        "elapsed_ms": elapsed_ms,
    })

    summary = {
        "ok": True,
        "components": len(result.components),
        "connections": len(result.connections),
        "files_scanned": result.files_scanned,
        "elapsed_ms": elapsed_ms,
        "arch_dir": str(arch_dir(repo)),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"scan ok — {summary['components']} components, "
            f"{summary['connections']} connections, "
            f"{summary['files_scanned']} files, {elapsed_ms}ms"
        )
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    comps, conns = _load_graph(repo)
    if not comps:
        print("No index found. Run `scan` first.", file=sys.stderr)
        return 2
    target = _resolve_target(args.target, comps)
    if not target:
        print(json.dumps({"ok": False, "error": f"target not found: {args.target}"}, indent=2))
        return 1
    report = A.compute_impact(target.component_id, comps, conns)
    print(json.dumps({
        "ok": True,
        "target": {"component_id": target.component_id, "name": target.name, "file": target.metadata.get("file")},
        **report.to_dict(),
    }, indent=2))
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    comps, conns = _load_graph(repo)
    if not comps:
        print("No index found. Run `scan` first.", file=sys.stderr)
        return 2
    target = _resolve_target(args.target, comps)
    if not target:
        print(json.dumps({"ok": False, "error": f"target not found: {args.target}"}, indent=2))
        return 1
    paths = A.trace_dataflow(
        target.component_id, comps, conns,
        depth=args.depth, direction=args.direction,
    )
    print(json.dumps({
        "ok": True,
        "target": target.component_id,
        "direction": args.direction,
        "depth": args.depth,
        "paths": paths,
        "path_count": len(paths),
    }, indent=2))
    return 0


def cmd_connections(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    comps, conns = _load_graph(repo)
    if not comps:
        print("No index found. Run `scan` first.", file=sys.stderr)
        return 2
    target = _resolve_target(args.target, comps)
    if not target:
        print(json.dumps({"ok": False, "error": f"target not found: {args.target}"}, indent=2))
        return 1
    out_edges = [c.to_dict() for c in conns if c.from_id == target.component_id]
    in_edges = [c.to_dict() for c in conns if c.to_id == target.component_id]
    print(json.dumps({
        "ok": True,
        "component_id": target.component_id,
        "name": target.name,
        "outgoing": out_edges,
        "incoming": in_edges,
        "outgoing_count": len(out_edges),
        "incoming_count": len(in_edges),
    }, indent=2))
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    comps, conns = _load_graph(repo)
    if not comps:
        print("No index found. Run `scan` first.", file=sys.stderr)
        return 2
    violations = A.check_rules(comps, conns)
    payload = {
        "ok": True,
        "violation_count": len(violations),
        "violations": [v.to_dict() for v in violations],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if not violations:
            print("rules ok — no violations")
        else:
            for v in violations:
                tag = v.component_id or ",".join(v.component_ids[:3])
                print(f"[{v.severity}] {v.rule}: {tag} — {v.message}")
    return 0


def cmd_dead(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    comps, conns = _load_graph(repo)
    if not comps:
        print("No index found. Run `scan` first.", file=sys.stderr)
        return 2
    report = A.find_dead(comps, conns)
    if args.json:
        print(json.dumps({"ok": True, **report.to_dict()}, indent=2))
    else:
        if not report.orphan_components:
            print("dead ok — no orphans")
        else:
            print(f"orphan components ({len(report.orphan_components)}):")
            for cid in report.orphan_components:
                print(f"  {cid}")
    return 0


# ---------------------------------------------------------------------------
# Adapter-routed subcommands (NavGator-only capabilities)
# ---------------------------------------------------------------------------


def _adapter_for(args: argparse.Namespace) -> Adapter:
    return Adapter(mode=getattr(args, "mode", "auto"))


def _emit_adapter_result(result: Dict[str, Any]) -> int:
    """Print adapter JSON and pick an exit code.

    Exit 0 always, with one exception: a result like
    ``{"available": False, ...}`` (NavGator unavailable in auto mode for an
    escalation-only capability) is still exit 0 — the caller asked for a graceful
    fallback and got one. Real errors raise and are caught by callers below.
    """
    print(json.dumps(result, indent=2))
    return 0


def cmd_llm_map(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    try:
        result = _adapter_for(args).llm_map(repo)
    except CapabilityNotAvailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except NavGatorNotAvailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except AdapterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return _emit_adapter_result(result)


def cmd_schema(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    try:
        result = _adapter_for(args).schema(repo, model=args.model)
    except CapabilityNotAvailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except NavGatorNotAvailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except AdapterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return _emit_adapter_result(result)


def cmd_diagram(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    try:
        result = _adapter_for(args).diagram(
            repo, mode=args.diagram_mode, focus=args.focus
        )
    except CapabilityNotAvailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except NavGatorNotAvailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except AdapterError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return _emit_adapter_result(result)


# ---------------------------------------------------------------------------
# ACP subcommands (Chunk 3 — aliases over scripts/build_acp.py + slice_acp.py)
# ---------------------------------------------------------------------------


def _scripts_dir() -> Path:
    """Locate the repo's scripts/ directory.

    The architecture package lives at ``<repo>/src/build_loop/architecture/``,
    so ``parents[3]`` is the repo root.
    """
    return Path(__file__).resolve().parents[3] / "scripts"


def cmd_acp(args: argparse.Namespace) -> int:
    """Run scripts/build_acp.py inline (import + call, no subprocess)."""
    repo = _resolve_repo(args.repo)
    sys.path.insert(0, str(_scripts_dir()))
    try:
        import build_acp  # type: ignore
    finally:
        # Keep scripts/ on path for symmetry with __main__ invocation.
        pass

    cli_args: List[str] = ["--repo", str(repo)]
    if args.out:
        cli_args += ["--out", args.out]
    if args.json:
        cli_args.append("--json")
    if args.no_state_update:
        cli_args.append("--no-state-update")
    return build_acp.main(cli_args)


def cmd_acp_slice(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    sys.path.insert(0, str(_scripts_dir()))
    try:
        import slice_acp  # type: ignore
    finally:
        pass

    cli_args: List[str] = ["--repo", str(repo), "--files", *args.files,
                           "--depth", str(args.depth)]
    if args.lessons_match:
        cli_args.append("--lessons-match")
    if args.in_path:
        cli_args += ["--in", args.in_path]
    if args.out:
        cli_args += ["--out", args.out]
    return slice_acp.main(cli_args)


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_loop.architecture",
        description="Build-loop native architecture engine + NavGator adapter.",
    )
    p.add_argument("--repo", help="Repo root (defaults to cwd)")
    p.add_argument(
        "--mode",
        choices=["auto", "native", "navgator"],
        default="auto",
        help=(
            "Capability backend. 'auto' (default): native engine for ported "
            "capabilities, NavGator for llm-map/schema/diagram if installed; "
            "'native': force native engine (escalation-only commands fail with "
            "exit 2); 'navgator': force NavGator subprocess."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="Scan the repo and write index/graph/manifest.")
    grp = s.add_mutually_exclusive_group()
    grp.add_argument("--full", action="store_true", help="Force full scan (default).")
    grp.add_argument("--incremental", action="store_true", help="Mark this scan as incremental.")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("impact", help="Show blast-radius for a component or file.")
    s.add_argument("target")
    s.add_argument("--json", action="store_true", help="(default; kept for symmetry)")
    s.set_defaults(func=cmd_impact)

    s = sub.add_parser("trace", help="Trace dataflow paths from a component.")
    s.add_argument("target")
    s.add_argument("--depth", type=int, default=3)
    s.add_argument("--direction", choices=["in", "out", "both"], default="out")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_trace)

    s = sub.add_parser("connections", help="List incoming/outgoing connections.")
    s.add_argument("target")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_connections)

    s = sub.add_parser("rules", help="Run rule checks (cycles, orphans, layer violations, hotspots).")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_rules)

    s = sub.add_parser("dead", help="Find orphan components / unused packages.")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_dead)

    # NavGator-only escalation capabilities (routed through the adapter).
    s = sub.add_parser(
        "llm-map",
        help="Map LLM use cases (NavGator-only; --mode=native exits 2).",
    )
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_llm_map)

    s = sub.add_parser(
        "schema",
        help="Show DB schema reads/writes (NavGator-only; --mode=native exits 2).",
    )
    s.add_argument("model", nargs="?", default=None, help="Optional model name to focus on.")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_schema)

    s = sub.add_parser(
        "diagram",
        help="Render an architecture diagram (NavGator-only; --mode=native exits 2).",
    )
    s.add_argument(
        "--mode",
        dest="diagram_mode",
        choices=["summary", "focus", "layer"],
        default="summary",
        help="Diagram style.",
    )
    s.add_argument("--focus", default=None, help="Component name to focus on (mode=focus).")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_diagram)

    # ACP build + slice (Chunk 3 — thin aliases over ``scripts/build_acp.py``
    # and ``scripts/slice_acp.py`` so callers can use the unified
    # ``python -m build_loop.architecture`` entry point.
    s = sub.add_parser(
        "acp",
        help="Build the Architecture Context Pack (.build-loop/architecture/acp.json).",
    )
    s.add_argument("--out", help="Output path (defaults to .build-loop/architecture/acp.json).")
    s.add_argument("--json", action="store_true", help="Emit summary JSON to stdout.")
    s.add_argument(
        "--no-state-update",
        action="store_true",
        help="Skip writing state.json.architecture.acpPath.",
    )
    s.set_defaults(func=cmd_acp)

    s = sub.add_parser(
        "acp-slice",
        help="Narrow the ACP to a file set for a single subagent dispatch.",
    )
    s.add_argument(
        "--files", nargs="+", required=True,
        help="Repo-relative or absolute file paths.",
    )
    s.add_argument("--depth", type=int, default=1, help="Neighbor walk depth (default 1).")
    s.add_argument(
        "--lessons-match", action="store_true",
        help="Match lesson signatures against staged-file content.",
    )
    s.add_argument(
        "--in", dest="in_path",
        help="Input ACP path (defaults to <repo>/.build-loop/architecture/acp.json).",
    )
    s.add_argument("--out", help="Output path (defaults to stdout).")
    s.set_defaults(func=cmd_acp_slice)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
