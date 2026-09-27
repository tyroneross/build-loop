#!/usr/bin/env python3
"""Run contrastive retrieval probes against a shipped JSON search command.

Spec example::

    {
      "cases": [
        {"id": "portfolio", "argv": ["./search", "my portfolio", "--json"],
         "expected_route": "family_anchor", "required_ids": ["portfolio-summary"]},
        {"id": "named-deal", "argv": ["./search", "my Pila investment", "--json"],
         "top_id": "deal-pila", "no_write_paths": [".vector/graph.db"]}
      ],
      "distinct_top_pairs": [["portfolio", "named-deal"]]
    }

Commands run without a shell. Each must emit one JSON object with `hits` and
`actual_route` (or `route`). A no-write path watches its parent directory,
itself, WAL/SHM sidecars, and any symlink target before and after the command.
This is an acceptance probe: failures show behavior or coverage gaps; it does
not infer whether a changed file was written by this command or a concurrent
writer.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _stat(path: Path) -> tuple[Any, ...] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return (info.st_mode, info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _path_snapshot(path: Path) -> dict[str, Any]:
    canonical = path.resolve(strict=False)
    watched = {path, canonical}
    for base in (path, canonical):
        watched.add(Path(f"{base}-wal"))
        watched.add(Path(f"{base}-shm"))
    parent = canonical.parent
    try:
        entries = sorted(os.listdir(parent))
    except FileNotFoundError:
        entries = None
    return {
        "target": str(canonical),
        "files": {str(item): _stat(item) for item in sorted(watched)},
        "parent": (str(parent), _stat(parent), entries),
    }


def _ids(payload: dict[str, Any]) -> list[str]:
    hits = payload.get("hits")
    if not isinstance(hits, list):
        raise ValueError("result has no hits array")
    values = []
    for hit in hits:
        if not isinstance(hit, dict):
            raise ValueError("result hit is not an object")
        identity = hit.get("id") or hit.get("page_id")
        if identity is not None:
            values.append(str(identity))
    return values


def run_case(case: dict[str, Any], workdir: Path, timeout: float) -> dict[str, Any]:
    case_id = str(case["id"])
    argv = case["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise ValueError(f"{case_id}: argv must be a nonempty string array")
    watch = case.get("no_write_paths", [])
    if not isinstance(watch, list) or not all(isinstance(v, str) for v in watch):
        raise ValueError(f"{case_id}: no_write_paths must be a string array")
    watched_paths = [(workdir / item) for item in watch]
    before = [_path_snapshot(item) for item in watched_paths]
    errors: list[str] = []
    try:
        completed = subprocess.run(argv, cwd=workdir, capture_output=True,
                                   text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"id": case_id, "pass": False, "errors": [f"command failed: {exc}"],
                "route": None, "top_id": None}
    after = [_path_snapshot(item) for item in watched_paths]
    for name, old, new in zip(watch, before, after):
        if old != new:
            errors.append(f"no-write path changed: {name}")
    if completed.returncode != 0:
        errors.append(f"command exited {completed.returncode}: {completed.stderr.strip()[:300]}")
    try:
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("result is not an object")
        ids = _ids(payload)
        route = payload.get("actual_route") or payload.get("route")
        if "expected_route" in case and route != case["expected_route"]:
            errors.append(f"route {route!r} != {case['expected_route']!r}")
        if "top_id" in case and (not ids or ids[0] != case["top_id"]):
            errors.append(f"top id {ids[0] if ids else None!r} != {case['top_id']!r}")
        for required in case.get("required_ids", []):
            if required not in ids:
                errors.append(f"required id missing: {required}")
        for requirement in case.get("required_evidence", []):
            if not isinstance(requirement, dict) or not isinstance(requirement.get("id"), str):
                raise ValueError("required_evidence entries need an id")
            terms = requirement.get("contains", [])
            if not isinstance(terms, list) or not terms or not all(isinstance(term, str) and term for term in terms):
                raise ValueError("required_evidence contains must be a nonempty string array")
            matching = [hit for hit in payload["hits"] if hit.get("id", hit.get("page_id")) == requirement["id"]]
            if not matching:
                errors.append(f"required evidence id missing: {requirement['id']}")
                continue
            evidence = " ".join(str(matching[0].get(field, "")) for field in ("title", "heading", "snippet")).casefold()
            for term in terms:
                if term.casefold() not in evidence:
                    errors.append(f"required evidence term missing for {requirement['id']}: {term}")
        for forbidden in case.get("forbidden_ids", []):
            if forbidden in ids:
                errors.append(f"forbidden id returned: {forbidden}")
    except (json.JSONDecodeError, ValueError) as exc:
        route, ids = None, []
        errors.append(f"invalid JSON result: {exc}")
    return {"id": case_id, "pass": not errors, "errors": errors,
            "route": route, "top_id": ids[0] if ids else None}


def run_spec(spec: dict[str, Any], workdir: Path, timeout: float) -> dict[str, Any]:
    cases = spec.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("spec must contain nonempty cases array")
    if not all(isinstance(case, dict) for case in cases):
        raise ValueError("every case must be an object")
    ids = [case.get("id") for case in cases]
    if not all(isinstance(value, str) and value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("case ids must be unique nonempty strings")
    results = [run_case(case, workdir, timeout) for case in cases]
    by_id = {result["id"]: result for result in results}
    pair_failures = []
    for pair in spec.get("distinct_top_pairs", []):
        if not isinstance(pair, list) or len(pair) != 2 or any(item not in by_id for item in pair):
            raise ValueError(f"invalid distinct_top_pairs entry: {pair!r}")
        left, right = (by_id[item] for item in pair)
        if left["top_id"] is None or right["top_id"] is None or left["top_id"] == right["top_id"]:
            pair_failures.append(f"{pair[0]} and {pair[1]} did not produce distinct top ids")
    return {"pass": all(result["pass"] for result in results) and not pair_failures,
            "cases": results, "pair_failures": pair_failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        spec = json.loads(args.spec.read_text())
        if not isinstance(spec, dict):
            raise ValueError("spec must be a JSON object")
        result = run_spec(spec, args.workdir.resolve(), args.timeout)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(json.dumps({"pass": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2 if args.json else None))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
