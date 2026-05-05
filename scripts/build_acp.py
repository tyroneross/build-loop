#!/usr/bin/env python3
"""Build the Architecture Context Pack (ACP) from `.build-loop/architecture/`.

The ACP is a compact JSON object summarizing current architecture state. Phase 1
Assess of future builds (Chunk 5) embeds it in subagent briefs; the slicer
(`slice_acp.py`) narrows it to a file set per dispatch.

Inputs:
    .build-loop/architecture/index.json      — components + connections
    .build-loop/architecture/manifest.json   — scan timestamp + type
    .build-loop/architecture/reverse-deps.json — fan-in for top_risk
    .build-loop/architecture/lessons.json    — optional, may be missing

Output:
    .build-loop/architecture/acp.json        — atomic write

State (Chunk 5 will read this):
    state.json.architecture.acpPath = ".build-loop/architecture/acp.json"

Stdlib-only. Target build cost: ≤200ms on a 124-component repo. ≤1s hard ceiling.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Ensure ``src/`` is importable so we can reuse the architecture package's
# storage primitives + analysis helpers without an editable install.
_REPO_ROOT_GUESS = Path(__file__).resolve().parents[1]
_SRC = (_REPO_ROOT_GUESS / "src").resolve()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from build_loop.architecture import analysis as A  # noqa: E402
from build_loop.architecture.schemas import Component, Connection  # noqa: E402
from build_loop.architecture.storage import (  # noqa: E402
    arch_dir,
    atomic_write_json,
    read_json,
)

ACP_SCHEMA_VERSION = "1.0.0"
ACP_FILENAME = "acp.json"
TOP_RISK_LIMIT = 10
RECENT_VIOLATION_LIMIT = 50
HUB_FAN_IN_THRESHOLD = 10  # ≥ this = "hub"; top-3 of those = "hotspot"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_repo(opt: Optional[str]) -> Path:
    return Path(opt or os.getcwd()).resolve()


def _ms_to_iso(ts_ms: int) -> str:
    if not ts_ms:
        return ""
    return (
        _dt.datetime.fromtimestamp(ts_ms / 1000, tz=_dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _scan_ts_and_type(manifest: Dict[str, Any]) -> Tuple[str, str]:
    last_full = int(manifest.get("last_full_scan_at") or 0)
    last_inc = int(manifest.get("last_incremental_at") or 0)
    generated = int(manifest.get("generated_at") or 0)
    ts_ms = max(last_full, last_inc, generated)
    scan_type = "incremental" if last_inc > last_full else "full"
    return _ms_to_iso(ts_ms), scan_type


def _cycle_member_set(violations: Sequence[A.Violation]) -> set:
    members: set = set()
    for v in violations:
        if v.rule == "cycle":
            members.update(v.component_ids)
    return members


def _component_index(components: Sequence[Component]) -> Dict[str, Component]:
    return {c.component_id: c for c in components}


def _layer_for(c: Component) -> str:
    role = c.role
    if hasattr(role, "layer"):
        return getattr(role, "layer", "unknown") or "unknown"
    if isinstance(role, dict):
        return role.get("layer") or "unknown"
    return "unknown"


# ---------------------------------------------------------------------------
# ACP construction
# ---------------------------------------------------------------------------

def _summary(
    components: Sequence[Component], connections: Sequence[Connection]
) -> Dict[str, Any]:
    layers = sorted({_layer_for(c) for c in components})
    by_type: Dict[str, int] = {}
    for c in components:
        by_type[c.type] = by_type.get(c.type, 0) + 1
    by_conn_type: Dict[str, int] = {}
    for cn in connections:
        by_conn_type[cn.type] = by_conn_type.get(cn.type, 0) + 1
    return {
        "components": len(components),
        "connections": len(connections),
        "layers": layers,
        "components_by_type": by_type,
        "connections_by_type": by_conn_type,
    }


def _compute_top_risk(
    components: Sequence[Component],
    reverse_deps: Dict[str, List[str]],
    cycle_members: set,
) -> List[Dict[str, Any]]:
    by_id = _component_index(components)

    # fan-in score = count of distinct sources (de-dup, since reverse-deps
    # may carry per-import duplicates).
    scored: List[Tuple[int, Component]] = []
    for cid, sources in reverse_deps.items():
        comp = by_id.get(cid)
        if not comp:
            continue
        fan_in = len(set(sources))
        scored.append((fan_in, comp))

    scored.sort(key=lambda t: (-t[0], t[1].component_id))
    top = scored[:TOP_RISK_LIMIT]
    if not top:
        return []

    # Top-3 of those reaching threshold = hotspots; threshold-only = hub;
    # cycle members override (regardless of fan-in rank).
    hotspot_cutoff_idx = 3

    out: List[Dict[str, Any]] = []
    for idx, (fan_in, comp) in enumerate(top):
        if comp.component_id in cycle_members:
            kind = "cycle-member"
        elif idx < hotspot_cutoff_idx and fan_in >= HUB_FAN_IN_THRESHOLD:
            kind = "hotspot"
        elif fan_in >= HUB_FAN_IN_THRESHOLD:
            kind = "hub"
        else:
            kind = "hub" if fan_in > 0 else "hotspot"
        out.append(
            {
                "component_id": comp.component_id,
                "name": comp.name,
                "blast_radius": fan_in,
                "layer": _layer_for(comp),
                "kind": kind,
            }
        )
    return out


_SEVERITY_ORDER = {"error": 0, "warn": 1, "warning": 1, "info": 2}


def _compute_recent_violations(
    components: Sequence[Component], connections: Sequence[Connection]
) -> Tuple[List[A.Violation], List[Dict[str, Any]]]:
    """Run rule checks and return (raw_violations, dict_payload_for_acp)."""
    violations = list(A.check_rules(components, connections))
    violations.sort(
        key=lambda v: (_SEVERITY_ORDER.get(v.severity, 9), v.rule, v.component_id or "")
    )
    capped = violations[:RECENT_VIOLATION_LIMIT]

    now_iso = _ms_to_iso(int(time.time() * 1000))
    payload: List[Dict[str, Any]] = []
    for v in capped:
        ids = list(v.component_ids) if v.component_ids else (
            [v.component_id] if v.component_id else []
        )
        payload.append(
            {
                "rule_id": v.rule,
                "severity": v.severity,
                "components": ids,
                "first_seen": now_iso,
                "message": v.message,
            }
        )
    return violations, payload


def build_acp(repo_root: Path) -> Dict[str, Any]:
    arch = arch_dir(repo_root)

    index = read_json(arch / "index.json")
    if not index:
        raise SystemExit(
            f"error: {arch / 'index.json'} not found — run "
            "`python -m build_loop.architecture scan` first."
        )
    manifest = read_json(arch / "manifest.json") or {}
    rev = (read_json(arch / "reverse-deps.json") or {}).get("reverse_deps", {})

    components = [Component(**c) for c in index.get("components", [])]
    connections = [Connection(**c) for c in index.get("connections", [])]

    scan_ts, scan_type = _scan_ts_and_type(manifest)
    summary = _summary(components, connections)
    raw_violations, recent = _compute_recent_violations(components, connections)
    cycle_members = _cycle_member_set(raw_violations)
    top_risk = _compute_top_risk(components, rev, cycle_members)

    return {
        "schema_version": ACP_SCHEMA_VERSION,
        "scan_ts": scan_ts,
        "scan_type": scan_type,
        "summary": summary,
        "top_risk": top_risk,
        "recent_violations": recent,
        "files_touched_slice": None,
        "lessons_in_scope": [],
    }


def write_acp(repo_root: Path, acp: Dict[str, Any], out: Optional[Path] = None) -> Path:
    target = out or (arch_dir(repo_root) / ACP_FILENAME)
    atomic_write_json(target, acp)
    return target


def _update_state(repo_root: Path, acp_relpath: str) -> None:
    """Set ``state.json.architecture.acpPath`` non-destructively."""
    state_path = repo_root / ".build-loop" / "state.json"
    try:
        if state_path.exists():
            data = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            data = {}
        arch_block = data.get("architecture") or {}
        arch_block["acpPath"] = acp_relpath
        data["architecture"] = arch_block
        # Atomic write — same pattern as storage.atomic_write_json.
        atomic_write_json(state_path, data)
    except Exception as exc:  # pragma: no cover — non-fatal
        print(f"warn: failed to update state.json.architecture.acpPath: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="build_acp",
        description="Build the Architecture Context Pack (ACP) from .build-loop/architecture/.",
    )
    p.add_argument("--repo", help="Repo root (defaults to cwd).")
    p.add_argument("--out", help="Output path (defaults to .build-loop/architecture/acp.json).")
    p.add_argument("--json", action="store_true", help="Emit summary JSON to stdout.")
    p.add_argument(
        "--no-state-update",
        action="store_true",
        help="Skip writing state.json.architecture.acpPath.",
    )
    args = p.parse_args(argv)

    repo = _resolve_repo(args.repo)
    out = Path(args.out).resolve() if args.out else None

    t0 = time.time()
    acp = build_acp(repo)
    target = write_acp(repo, acp, out)
    elapsed_ms = int((time.time() - t0) * 1000)

    if not args.no_state_update and out is None:
        rel = str(target.relative_to(repo)) if str(target).startswith(str(repo)) else str(target)
        _update_state(repo, rel)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "path": str(target),
                    "elapsed_ms": elapsed_ms,
                    "components": acp["summary"]["components"],
                    "connections": acp["summary"]["connections"],
                    "top_risk_count": len(acp["top_risk"]),
                    "violation_count": len(acp["recent_violations"]),
                },
                indent=2,
            )
        )
    else:
        print(
            f"acp ok — {acp['summary']['components']} components, "
            f"{acp['summary']['connections']} connections, "
            f"{len(acp['top_risk'])} top_risk, "
            f"{len(acp['recent_violations'])} violations, {elapsed_ms}ms → {target}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
