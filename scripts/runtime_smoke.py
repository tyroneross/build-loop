#!/usr/bin/env python3
"""Runtime smoke gate entry point for the build-loop plugin.

Detects whether any changed file matches a runtime-smoke trigger pattern, selects the
appropriate adapter, and runs it against the project's dev server.

CLI:
    python3 scripts/runtime_smoke.py \\
        --changed-files <file1> [<file2> ...] \\
        [--workdir <path>] \\
        [--json] \\
        [--dry-run]

Exit codes:
    0 — pass | skipped | dry_run
    1 — fail (adapter ran and reported a failing render)
    2 — runner error (malformed input, missing adapter module, import failure)

Stdlib only (argparse, json, pathlib, subprocess, socket, re, sys, os, time).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Trigger patterns — kept in sync with references/runtime-smoke-triggers.md
# ---------------------------------------------------------------------------

_TRIGGER_PATTERNS: list[re.Pattern] = [
    # App Router pages
    re.compile(r"^app/.+/page\.[tj]sx?$"),
    re.compile(r"^app/page\.[tj]sx?$"),
    # App Router API handlers
    re.compile(r"^app/.+/route\.[tj]s$"),
    # App Router layouts
    re.compile(r"^app/.+/layout\.[tj]sx?$"),
    re.compile(r"^app/layout\.[tj]sx?$"),
    # App Router middleware
    re.compile(r"^app/.+/middleware\.[tj]s$"),
    re.compile(r"^middleware\.[tj]s$"),
    # Pages Router
    re.compile(r"^pages/.+\.[tj]sx?$"),
    # Custom server entries
    re.compile(r".*[\\/]server\.[tj]s$"),
    # SSE producers/consumers
    re.compile(r".*[\\/]sse-[^/]+\.[tj]sx?$"),
]


def _matches_trigger(file_path: str) -> bool:
    """Return True if the given (repo-relative) file path matches any trigger pattern."""
    # Normalize to forward slashes for consistent matching
    normalized = file_path.replace("\\", "/")
    for pattern in _TRIGGER_PATTERNS:
        if pattern.search(normalized):
            return True
    # Additional content-based triggers would require reading the file; skip here —
    # those are handled at the adapter level when needed.
    return False


def _filter_trigger_files(changed_files: list[str]) -> list[str]:
    """Return the subset of changed_files that match a smoke trigger."""
    return [f for f in changed_files if _matches_trigger(f)]


# ---------------------------------------------------------------------------
# Adapter detection
# ---------------------------------------------------------------------------

def _detect_adapter(workdir: Path) -> str | None:
    """Return the adapter name for the project, or None if no adapter matched."""
    pkg_path = workdir / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "next" in deps:
            return "nextjs"
        # Future: express, vite, etc.
    return None


def _load_adapter(name: str):
    """Dynamically import scripts/runtime_smoke_adapters/<name>.py."""
    # Ensure the adapters package directory is importable
    scripts_dir = Path(__file__).parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        module = importlib.import_module(f"runtime_smoke_adapters.{name}")
    except ImportError as exc:
        print(f"error: could not import adapter '{name}': {exc}", file=sys.stderr)
        sys.exit(2)
    return module


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit(envelope: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(envelope, indent=2))
    else:
        status = envelope.get("status", "unknown")
        adapter = envelope.get("adapter") or "n/a"
        reason = envelope.get("reason", "")
        print(f"runtime-smoke: status={status} adapter={adapter}" +
              (f" reason={reason}" if reason else ""))
        findings = envelope.get("findings", [])
        if findings:
            for f in findings:
                route = f.get("route", "?")
                rstat = f.get("render_status", "?")
                note = f.get("finding") or ""
                print(f"  {rstat:6s}  {route}  {note}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Runtime smoke gate — detect and fire adapter for changed routes.",
    )
    parser.add_argument(
        "--changed-files",
        nargs="+",
        metavar="FILE",
        required=True,
        help="Repo-relative paths of files changed by the build.",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="Project root (defaults to cwd).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit JSON envelope to stdout.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Skip adapter execution; return dry_run envelope for wiring self-tests.",
    )

    args = parser.parse_args(argv)
    workdir = Path(args.workdir).resolve() if args.workdir else Path.cwd()
    changed_files: list[str] = args.changed_files

    # Step 1: check for trigger files
    trigger_files = _filter_trigger_files(changed_files)
    if not trigger_files:
        envelope = {
            "status": "skipped",
            "reason": "no_trigger_files",
            "adapter": None,
            "trigger_files": [],
        }
        _emit(envelope, args.as_json)
        return 0

    # Step 2: detect adapter
    adapter_name = _detect_adapter(workdir)
    if adapter_name is None:
        envelope = {
            "status": "skipped",
            "reason": "no_adapter_matched",
            "adapter": None,
            "trigger_files": trigger_files,
        }
        _emit(envelope, args.as_json)
        return 0

    # Step 3: dry-run short-circuit
    if args.dry_run:
        envelope = {
            "status": "dry_run",
            "would_invoke": adapter_name,
            "trigger_files": trigger_files,
        }
        _emit(envelope, args.as_json)
        return 0

    # Step 4: load and run the adapter
    adapter_module = _load_adapter(adapter_name)
    try:
        result = adapter_module.run(changed_files, workdir)
    except Exception as exc:  # noqa: BLE001
        # Adapter raised during run() — treat as a failed render, not a runner
        # error. Exit 1 routes to Iterate (see build-orchestrator.md Review-B).
        # Reserve exit 2 for runner-level failures (import failure, malformed
        # input) handled in _load_adapter.
        error_envelope = {
            "status": "fail",
            "adapter": adapter_name,
            "reason": f"adapter_exception: {exc}",
            "trigger_files": trigger_files,
            "findings": [],
        }
        _emit(error_envelope, args.as_json)
        return 1

    _emit(result, args.as_json)

    status = result.get("status", "fail")
    if status == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
