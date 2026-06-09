#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Index repo scripts (ring 1) + consumer-project tool surfaces (ring 2) into a freshness-cached capability index for Phase-2 planning.
#   application: meta
#   status: active
"""Capability index builder (WP-B item 1) — scripts as tools + consumer ring-2.

DISTINCT FROM build_capability_registry.py: the *registry* is the orchestrator's
routing decision space across ALL surfaces (agents/skills/commands/hooks/mcp +
scripts) narrowed to <=8 per dispatch. This *index* is narrower and cheaper — a
catalog of CALLABLE TOOLS the plan can reach:

  Ring 1 — repo scripts/: each top-level scripts/*.py parsed via ``ast.parse``
           for {name, path, first docstring line, has_test}. AST (not regex) so a
           syntactically broken script is reported, not silently skipped.
  Ring 2 — consumer-project tool surfaces: package.json "scripts", Makefile /
           justfile targets, pyproject [project.scripts] / [tool.poetry.scripts].
           These are the tools a CONSUMER repo already exposes — the plan should
           prefer an existing target over writing a new script (KISS).
  Ring 3 — plugins / MCP tools / PATH binaries: NOT indexed (verify-on-use). They
           are environmental and change underfoot; indexing them invites staleness.

Generate-don't-maintain: the index is cached at .build-loop/capability-index.json
with mtime+size+hash invalidation (mirrors scripts/architecture_freshness.py and
NavGator's freshness-stamp + dirty-ledger). It is NOT committed (repo-local
runtime artifact, like the registry) and is regenerated at Phase 1 bootstrap, then
injected into Phase 2 planning context.

Stdlib only (ast, json, hashlib, pathlib, argparse, sys, os, tempfile, time, re).
Idempotent. Atomic write (temp + os.replace). Never raises on a single bad file —
a parse error becomes an entry with ``error`` set, so the index stays complete.

CLI::

    python3 scripts/build_capability_index.py --workdir <repo> [--json]
    python3 scripts/build_capability_index.py --workdir <repo> --check   # freshness only
    python3 scripts/build_capability_index.py --workdir <repo> --force   # ignore cache

Exit codes: 0 ok (built or fresh); 2 setup error (workdir missing).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

INDEX_REL = ".build-loop/capability-index.json"
SCHEMA_VERSION = "1.0.0"
# Cache is considered fresh for this long even if nothing changed (cheap re-stat
# still runs; this just bounds a full rebuild). Mirrors the registry cadence.
DEFAULT_MAX_AGE_SECONDS = 3600


# ---------------------------------------------------------------------------
# Ring 1 — repo scripts/ via AST
# ---------------------------------------------------------------------------

def _first_docstring_line(tree: ast.Module) -> str:
    doc = ast.get_docstring(tree, clean=True)
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else ""


def index_scripts(scripts_dir: Path) -> list[dict[str, Any]]:
    """Index every top-level scripts/*.py (skip dunder + _attic/)."""
    out: list[dict[str, Any]] = []
    if not scripts_dir.is_dir():
        return out
    for p in sorted(scripts_dir.glob("*.py")):
        name = p.stem
        if name.startswith("__"):
            continue
        # A test file is itself a tool only incidentally; index non-test scripts,
        # and record whether each has a colocated test_<name>.py (build-loop's
        # every-new-script-gets-a-test contract).
        if name.startswith("test_"):
            continue
        entry: dict[str, Any] = {
            "name": name,
            "path": str(p.relative_to(scripts_dir.parent)),
            "ring": 1,
            "kind": "script",
            "doc": "",
            "has_test": (scripts_dir / f"test_{name}.py").is_file(),
            "error": None,
        }
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            entry["doc"] = _first_docstring_line(tree)
        except (SyntaxError, ValueError, OSError) as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Ring 2 — consumer-project tool surfaces
# ---------------------------------------------------------------------------

_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:(?!=)", re.MULTILINE)
_JUST_TARGET_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:", re.MULTILINE)


def _index_package_json(root: Path) -> list[dict[str, Any]]:
    p = root / "package.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return []
    return [
        {"name": k, "path": "package.json", "ring": 2, "kind": "npm_script",
         "doc": str(v)[:120], "has_test": None, "error": None}
        for k, v in scripts.items() if isinstance(k, str)
    ]


def _index_make_like(root: Path, filename: str, kind: str, regex: re.Pattern) -> list[dict[str, Any]]:
    p = root / filename
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in regex.finditer(text):
        target = m.group(1)
        # Skip the special/phony declarations and pattern rules.
        if target.startswith(".") or target in seen or "%" in target:
            continue
        seen.add(target)
        out.append({"name": target, "path": filename, "ring": 2, "kind": kind,
                    "doc": "", "has_test": None, "error": None})
    return out


def _index_pyproject_scripts(root: Path) -> list[dict[str, Any]]:
    p = root / "pyproject.toml"
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    # tomllib is stdlib 3.11+; fall back to a tiny line scan if unavailable so
    # the index never hard-depends on an optional parser.
    entries: list[dict[str, Any]] = []
    try:
        import tomllib  # type: ignore
        data = tomllib.loads(text)
        for table in (data.get("project", {}).get("scripts", {}),
                      data.get("tool", {}).get("poetry", {}).get("scripts", {})):
            if isinstance(table, dict):
                entries.extend(
                    {"name": k, "path": "pyproject.toml", "ring": 2, "kind": "pyproject_script",
                     "doc": str(v)[:120], "has_test": None, "error": None}
                    for k, v in table.items() if isinstance(k, str)
                )
    except Exception:
        # Minimal fallback: scan a [project.scripts] / [tool.poetry.scripts] block.
        in_block = False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                in_block = s in ("[project.scripts]", "[tool.poetry.scripts]")
                continue
            if in_block and "=" in s and not s.startswith("#"):
                key = s.split("=", 1)[0].strip().strip('"').strip("'")
                if key:
                    entries.append({"name": key, "path": "pyproject.toml", "ring": 2,
                                    "kind": "pyproject_script", "doc": "", "has_test": None, "error": None})
    return entries


def index_consumer_surfaces(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    out.extend(_index_package_json(root))
    out.extend(_index_make_like(root, "Makefile", "make_target", _MAKE_TARGET_RE))
    out.extend(_index_make_like(root, "justfile", "just_target", _JUST_TARGET_RE))
    out.extend(_index_pyproject_scripts(root))
    return out


# ---------------------------------------------------------------------------
# Freshness — mtime + size + content hash of the scripts/ dir listing
# ---------------------------------------------------------------------------

def _fingerprint(root: Path) -> str:
    """Cheap content fingerprint: each indexed source file's (relpath, mtime_ns,
    size). A new/changed/removed script or surface file flips the hash. Reading
    full file bodies is avoided — stat is enough to detect change and is fast."""
    h = hashlib.sha256()
    scripts_dir = root / "scripts"
    parts: list[str] = []
    if scripts_dir.is_dir():
        for p in sorted(scripts_dir.glob("*.py")):
            try:
                st = p.stat()
                parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                parts.append(f"{p.name}:err")
    for surface in ("package.json", "Makefile", "justfile", "pyproject.toml"):
        sp = root / surface
        if sp.is_file():
            try:
                st = sp.stat()
                parts.append(f"{surface}:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                parts.append(f"{surface}:err")
    h.update("\n".join(parts).encode("utf-8"))
    return h.hexdigest()


def _load_cache(index_path: Path) -> dict[str, Any] | None:
    if not index_path.is_file():
        return None
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_fresh(root: Path, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> tuple[bool, str]:
    """Return (fresh, reason). Fresh iff cache exists, fingerprint matches, and
    age < max_age."""
    cache = _load_cache(root / INDEX_REL)
    if cache is None:
        return False, "missing"
    if cache.get("fingerprint") != _fingerprint(root):
        return False, "changed"
    built = cache.get("built_at_epoch", 0)
    if (time.time() - built) > max_age_seconds:
        return False, "aged"
    return True, "fresh"


# ---------------------------------------------------------------------------
# Build + atomic write
# ---------------------------------------------------------------------------

def build_index(root: Path) -> dict[str, Any]:
    ring1 = index_scripts(root / "scripts")
    ring2 = index_consumer_surfaces(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "build-loop-capability-index",
        "built_at_epoch": time.time(),
        "fingerprint": _fingerprint(root),
        "ring1_count": len(ring1),
        "ring2_count": len(ring2),
        "ring3_note": "plugins/MCP/PATH binaries are verify-on-use, never indexed",
        "entries": ring1 + ring2,
    }


def _atomic_write(index_path: Path, payload: dict[str, Any]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(index_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False)
        os.replace(tmp, index_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def ensure_index(root: Path, *, force: bool = False,
                 max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> dict[str, Any]:
    """Build the index unless a fresh cache exists. Returns the index payload."""
    if not force:
        fresh, _reason = is_fresh(root, max_age_seconds)
        if fresh:
            cached = _load_cache(root / INDEX_REL)
            if cached is not None:
                return cached
    payload = build_index(root)
    _atomic_write(root / INDEX_REL, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workdir", type=Path, default=Path.cwd())
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="report freshness only; do not build")
    ap.add_argument("--force", action="store_true", help="rebuild ignoring cache")
    ap.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    args = ap.parse_args(argv)

    root = args.workdir.resolve()
    if not root.is_dir():
        print(f"setup error: workdir not a directory: {root}", file=sys.stderr)
        return 2

    if args.check:
        fresh, reason = is_fresh(root, args.max_age_seconds)
        result = {"fresh": fresh, "reason": reason, "index": str(root / INDEX_REL)}
        print(json.dumps(result, indent=2) if args.json else f"{reason} (fresh={fresh})")
        return 0

    payload = ensure_index(root, force=args.force, max_age_seconds=args.max_age_seconds)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"capability index: ring1={payload['ring1_count']} scripts, "
              f"ring2={payload['ring2_count']} consumer surfaces "
              f"→ {root / INDEX_REL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
