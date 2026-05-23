#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Slice the ACP to a file set for a single subagent dispatch.

Reads the full ACP (`.build-loop/architecture/acp.json`) and produces a narrowed
copy:

    files_touched_slice  populated with [{file, component_id, layer,
                                          blast_radius_from_root}, ...]
    top_risk             filtered to entries involving any component in the slice
    recent_violations    same filter
    lessons_in_scope     populated only when --lessons-match is set; each
                          lesson's signature regex is tested against the content
                          of `git diff --cached --name-only` files.

Hard cap: 4096 bytes serialized. If exceeded, recent_violations is truncated
first, then top_risk; an integer counter lands in `_truncated`.

Stdlib-only. No network calls.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_REPO_ROOT_GUESS = Path(__file__).resolve().parents[1]
_SRC = (_REPO_ROOT_GUESS / "src").resolve()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from build_loop.architecture.storage import arch_dir, read_json  # noqa: E402

DEFAULT_DEPTH = 1
SLICE_BYTE_CAP = 4096
DEFAULT_ACP_RELPATH = ".build-loop/architecture/acp.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_repo(opt: Optional[str]) -> Path:
    return Path(opt or os.getcwd()).resolve()


def _load_acp(path: Path) -> Dict[str, Any]:
    raw = read_json(path)
    if not raw:
        raise SystemExit(
            f"error: ACP not found at {path} — run `build_acp.py` first."
        )
    return raw


def _load_index(repo_root: Path) -> Dict[str, Any]:
    p = arch_dir(repo_root) / "index.json"
    raw = read_json(p)
    if not raw:
        raise SystemExit(f"error: index.json missing at {p}")
    return raw


def _load_file_map(repo_root: Path) -> Dict[str, str]:
    p = arch_dir(repo_root) / "file_map.json"
    raw = read_json(p) or {}
    return raw.get("files", {}) or {}


def _load_reverse_deps(repo_root: Path) -> Dict[str, List[str]]:
    p = arch_dir(repo_root) / "reverse-deps.json"
    raw = read_json(p) or {}
    return raw.get("reverse_deps", {}) or {}


def _normalize_input_path(repo_root: Path, raw: str) -> str:
    """Return repo-relative POSIX path for matching against file_map."""
    p = Path(raw)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(repo_root)
        except ValueError:
            return raw.replace(os.sep, "/")
    return str(p).replace(os.sep, "/")


def _resolve_files_to_components(
    files: Sequence[str], file_map: Dict[str, str], repo_root: Path
) -> List[Tuple[str, str]]:
    """Return list of (file, component_id). Files with no component are skipped."""
    out: List[Tuple[str, str]] = []
    for f in files:
        norm = _normalize_input_path(repo_root, f)
        cid = file_map.get(norm)
        if cid:
            out.append((norm, cid))
            continue
        # Suffix fallback: scripts/foo.py vs foo.py.
        for k, v in file_map.items():
            if k.endswith("/" + norm) or k == norm:
                out.append((k, v))
                break
    return out


def _collect_neighbors(
    seed_ids: Iterable[str],
    connections: Sequence[Dict[str, Any]],
    reverse_deps: Dict[str, List[str]],
    depth: int,
) -> Set[str]:
    """BFS over both directions up to `depth`."""
    fwd: Dict[str, Set[str]] = {}
    for cn in connections:
        frm = (cn.get("from") or {}).get("component_id", "")
        to = (cn.get("to") or {}).get("component_id", "")
        if frm and to:
            fwd.setdefault(frm, set()).add(to)

    visited: Set[str] = set(seed_ids)
    frontier: Set[str] = set(seed_ids)
    for _ in range(max(0, depth)):
        next_frontier: Set[str] = set()
        for cid in frontier:
            next_frontier.update(fwd.get(cid, set()))
            for src in reverse_deps.get(cid, []):
                next_frontier.add(src)
        next_frontier -= visited
        if not next_frontier:
            break
        visited.update(next_frontier)
        frontier = next_frontier
    return visited


def _layer_for_component(comp: Dict[str, Any]) -> str:
    role = comp.get("role") or {}
    return role.get("layer") or "unknown"


def _blast_radius_from_root(
    cid: str, reverse_deps: Dict[str, List[str]]
) -> int:
    return len(set(reverse_deps.get(cid, [])))


def _git_staged_files(repo_root: Path) -> List[Path]:
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return []
        return [
            (repo_root / line.strip())
            for line in proc.stdout.splitlines()
            if line.strip()
        ]
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []


def _match_lessons(
    repo_root: Path, modified_files: Sequence[Path]
) -> List[Dict[str, Any]]:
    """For each lesson with a signature regex, scan modified files; return matches."""
    lessons_path = arch_dir(repo_root) / "lessons.json"
    raw = read_json(lessons_path)
    if not raw:
        return []
    lessons = raw.get("lessons") or []
    if not lessons:
        return []

    contents: List[Tuple[Path, str]] = []
    for f in modified_files:
        try:
            if f.is_file() and f.stat().st_size < 5_000_000:  # 5MB cap
                contents.append((f, f.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue

    matched: List[Dict[str, Any]] = []
    for lesson in lessons:
        sigs = lesson.get("signature") or []
        if isinstance(sigs, str):
            sigs = [sigs]
        if not sigs:
            continue
        for sig in sigs:
            try:
                rx = re.compile(sig)
            except re.error:
                continue
            for path, text in contents:
                if rx.search(text):
                    matched.append(
                        {
                            "id": lesson.get("id"),
                            "category": lesson.get("category"),
                            "pattern": lesson.get("pattern"),
                            "severity": lesson.get("severity"),
                            "matched_signature": sig,
                            "matched_file": str(path.relative_to(repo_root))
                            if str(path).startswith(str(repo_root))
                            else str(path),
                        }
                    )
                    break  # one match per lesson is enough
            else:
                continue
            break
    return matched


# ---------------------------------------------------------------------------
# Slicing
# ---------------------------------------------------------------------------

def slice_acp(
    *,
    repo_root: Path,
    acp_path: Path,
    files: Sequence[str],
    depth: int = DEFAULT_DEPTH,
    lessons_match: bool = False,
) -> Dict[str, Any]:
    full = _load_acp(acp_path)
    index = _load_index(repo_root)
    file_map = _load_file_map(repo_root)
    reverse_deps = _load_reverse_deps(repo_root)
    components_by_id = {c["component_id"]: c for c in index.get("components", [])}

    resolved = _resolve_files_to_components(files, file_map, repo_root)
    seed_ids = [cid for _, cid in resolved]
    in_scope = _collect_neighbors(
        seed_ids, index.get("connections", []), reverse_deps, depth
    )

    files_touched_slice = []
    for file_rel, cid in resolved:
        comp = components_by_id.get(cid)
        files_touched_slice.append(
            {
                "file": file_rel,
                "component_id": cid,
                "layer": _layer_for_component(comp) if comp else "unknown",
                "blast_radius_from_root": _blast_radius_from_root(cid, reverse_deps),
            }
        )

    # Filter top_risk + recent_violations to entries that touch the in-scope set.
    def _touches(component_ids: Sequence[str]) -> bool:
        return any(cid in in_scope for cid in component_ids)

    top_risk = [
        r for r in (full.get("top_risk") or [])
        if r.get("component_id") in in_scope
    ]
    recent_violations = [
        v for v in (full.get("recent_violations") or [])
        if _touches(v.get("components") or [])
    ]

    lessons_in_scope: List[Dict[str, Any]] = []
    if lessons_match:
        modified = _git_staged_files(repo_root)
        lessons_in_scope = _match_lessons(repo_root, modified)

    sliced: Dict[str, Any] = {
        "schema_version": full.get("schema_version"),
        "scan_ts": full.get("scan_ts"),
        "scan_type": full.get("scan_type"),
        "summary": full.get("summary"),
        "top_risk": top_risk,
        "recent_violations": recent_violations,
        "files_touched_slice": files_touched_slice,
        "lessons_in_scope": lessons_in_scope,
    }

    return _enforce_size_cap(sliced)


def _enforce_size_cap(sliced: Dict[str, Any]) -> Dict[str, Any]:
    """Truncate recent_violations, then top_risk, until under 4KB."""
    encoded = json.dumps(sliced, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= SLICE_BYTE_CAP:
        return sliced

    truncated_total = 0

    # Strategy: drop recent_violations from the tail until under cap.
    rv: List[Dict[str, Any]] = list(sliced.get("recent_violations") or [])
    while rv and len(json.dumps(sliced, separators=(",", ":")).encode("utf-8")) > SLICE_BYTE_CAP:
        rv.pop()
        truncated_total += 1
        sliced["recent_violations"] = rv

    if len(json.dumps(sliced, separators=(",", ":")).encode("utf-8")) <= SLICE_BYTE_CAP:
        if truncated_total:
            sliced["_truncated"] = truncated_total
        return sliced

    tr: List[Dict[str, Any]] = list(sliced.get("top_risk") or [])
    while tr and len(json.dumps(sliced, separators=(",", ":")).encode("utf-8")) > SLICE_BYTE_CAP:
        tr.pop()
        truncated_total += 1
        sliced["top_risk"] = tr

    if truncated_total:
        sliced["_truncated"] = truncated_total
    return sliced


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="slice_acp",
        description="Narrow the ACP to a file set for a single subagent dispatch.",
    )
    p.add_argument("--repo", help="Repo root (defaults to cwd).")
    p.add_argument(
        "--files",
        nargs="+",
        required=True,
        help="One or more repo-relative or absolute file paths.",
    )
    p.add_argument("--depth", type=int, default=DEFAULT_DEPTH, help="Neighbor walk depth (default 1).")
    p.add_argument(
        "--lessons-match",
        action="store_true",
        help="Match lesson signatures against `git diff --cached --name-only` files.",
    )
    p.add_argument(
        "--in",
        dest="in_path",
        help=f"Input ACP path (defaults to <repo>/{DEFAULT_ACP_RELPATH}).",
    )
    p.add_argument(
        "--out",
        help="Output path (defaults to stdout).",
    )
    args = p.parse_args(argv)

    repo = _resolve_repo(args.repo)
    acp_path = (
        Path(args.in_path).resolve()
        if args.in_path
        else (repo / DEFAULT_ACP_RELPATH)
    )

    sliced = slice_acp(
        repo_root=repo,
        acp_path=acp_path,
        files=args.files,
        depth=args.depth,
        lessons_match=args.lessons_match,
    )

    payload = json.dumps(sliced, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"slice ok — {len(payload.encode())} bytes → {args.out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
