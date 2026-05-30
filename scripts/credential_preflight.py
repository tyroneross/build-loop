#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Credential preflight for build-loop Phase 1.

Scans source files for referenced environment-variable credentials, then
cross-checks against declared .env files and the live process environment.
Reports which keys are referenced but not set so build-loop can surface
[CREDENTIAL REQUIRED] before dispatching implementers.

CLI
---
    credential_preflight.py --workdir <repo> [--changed-files f1 f2 ...] --json

Exit codes
----------
    0  always (bad args → exit 1)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILES = 500

SOURCE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".py", ".env.example"}

SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__"}

# Well-known credential key names (exact).
WELL_KNOWN_KEYS: frozenset[str] = frozenset(
    [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "COHERE_API_KEY",
        "MISTRAL_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENERATIVE_AI_API_KEY",
        "HUGGINGFACE_API_KEY",
        "REPLICATE_API_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_API_KEY",
        "STRIPE_SECRET_KEY",
        "STRIPE_PUBLISHABLE_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "SENDGRID_API_KEY",
        "RESEND_API_KEY",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_ACCOUNT_SID",
        "GITHUB_TOKEN",
        "GITHUB_APP_PRIVATE_KEY",
        "DATABASE_URL",
        "DATABASE_PASSWORD",
        "POSTGRES_URL",
        "POSTGRES_PASSWORD",
        "MONGODB_URI",
        "REDIS_URL",
        "REDIS_PASSWORD",
        "NEXTAUTH_SECRET",
        "JWT_SECRET",
        "SESSION_SECRET",
        "CLERK_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ANON_KEY",
        "SENTRY_DSN",
        "LANGCHAIN_API_KEY",
        "LANGSMITH_API_KEY",
        "PINECONE_API_KEY",
        "WEAVIATE_API_KEY",
        "ELEVENLABS_API_KEY",
        "DEEPGRAM_API_KEY",
        "ASSEMBLYAI_API_KEY",
        "TOGETHER_API_KEY",
        "FIREWORKS_API_KEY",
        "PERPLEXITY_API_KEY",
    ]
)

# Pattern: anything that looks like a credential by name suffix.
# Matches: FOO_KEY, FOO_TOKEN, FOO_SECRET, FOO_API_KEY, FOO_PASSWORD, FOO_DSN, FOO_URL
# Must start with an uppercase letter, then 2+ uppercase-or-digit-or-underscore chars.
_SUFFIX_RE = re.compile(
    r'\b([A-Z][A-Z0-9_]{2,}(?:_KEY|_TOKEN|_SECRET|_API_KEY|_PASSWORD|_DSN|_URL))\b'
)

# JS/TS patterns: process.env.X, process.env["X"], import.meta.env.X
_JS_DOTENV_RE = re.compile(r'process\.env\.([A-Z][A-Z0-9_]+)')
_JS_BRACKET_RE = re.compile(r'process\.env\[[\'"]([\w]+)[\'"]\]')
_META_ENV_RE = re.compile(r'import\.meta\.env\.([A-Z][A-Z0-9_]+)')

# Python patterns: os.environ["X"], os.environ.get("X"), os.getenv("X")
_PY_ENVIRON_RE = re.compile(r'os\.environ\[[\'"]([\w]+)[\'"]\]')
_PY_ENVIRON_GET_RE = re.compile(r'os\.environ\.get\([\'"]([\w]+)[\'"]')
_PY_GETENV_RE = re.compile(r'os\.getenv\([\'"]([\w]+)[\'"]')


# ---------------------------------------------------------------------------
# Dotenv parsing — keys only, never values
# ---------------------------------------------------------------------------

_DOTENV_KEY_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)(?:\s*=|:)', re.MULTILINE)


def _read_dotenv_keys(path: Path) -> set[str]:
    """Return set of declared key names from a .env-style file. Never returns values."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return set(_DOTENV_KEY_RE.findall(text))


def _collect_dotenv_keys(workdir: Path) -> set[str]:
    """Collect all keys declared in any .env* file under workdir (top-level only)."""
    keys: set[str] = set()
    for p in workdir.iterdir():
        if p.is_file() and (p.name.startswith(".env") or p.suffix == ".example"):
            keys |= _read_dotenv_keys(p)
    return keys


# ---------------------------------------------------------------------------
# Source scanning
# ---------------------------------------------------------------------------

def _extract_keys_from_text(text: str, path: Path) -> list[tuple[str, int]]:
    """Return [(key_name, line_number), ...] for all credential references in text."""
    found: list[tuple[str, int]] = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        candidates: set[str] = set()

        # JS/TS explicit patterns
        for m in _JS_DOTENV_RE.finditer(line):
            candidates.add(m.group(1))
        for m in _JS_BRACKET_RE.finditer(line):
            candidates.add(m.group(1))
        for m in _META_ENV_RE.finditer(line):
            candidates.add(m.group(1))

        # Python explicit patterns
        for m in _PY_ENVIRON_RE.finditer(line):
            candidates.add(m.group(1))
        for m in _PY_ENVIRON_GET_RE.finditer(line):
            candidates.add(m.group(1))
        for m in _PY_GETENV_RE.finditer(line):
            candidates.add(m.group(1))

        # Well-known names appearing anywhere on the line
        for m in _SUFFIX_RE.finditer(line):
            name = m.group(1)
            if name in WELL_KNOWN_KEYS:
                candidates.add(name)

        # Generic suffix pattern anywhere on the line (catches non-well-known)
        for m in _SUFFIX_RE.finditer(line):
            candidates.add(m.group(1))

        for key in sorted(candidates):
            found.append((key, lineno))

    return found


def _should_scan(path: Path) -> bool:
    suffix = path.suffix.lower()
    name = path.name.lower()
    return suffix in SOURCE_EXTS or name.endswith(".env.example")


def _walk_source_files(workdir: Path) -> list[Path]:
    """Bounded walk of source files; skip SKIP_DIRS; cap at MAX_FILES."""
    results: list[Path] = []
    stack = [workdir]
    while stack and len(results) < MAX_FILES:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except PermissionError:
            continue
        for entry in entries:
            if len(results) >= MAX_FILES:
                break
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.is_file() and _should_scan(entry):
                results.append(entry)
    return results


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def run_preflight(
    workdir: Path,
    changed_files: list[Path] | None,
) -> dict[str, Any]:
    errors: list[str] = []

    # Determine files to scan
    if changed_files:
        files_to_scan = [f for f in changed_files if f.is_file() and _should_scan(f)]
    else:
        try:
            files_to_scan = _walk_source_files(workdir)
        except Exception as exc:
            errors.append(f"walk error: {exc}")
            files_to_scan = []

    # Collect satisfied keys
    dotenv_keys = _collect_dotenv_keys(workdir)
    process_env_keys = set(os.environ.keys())

    # Scan files → accumulate references
    # key -> list of "file:line" strings
    refs: dict[str, list[str]] = {}

    for path in files_to_scan:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"read error {path}: {exc}")
            continue

        for key, lineno in _extract_keys_from_text(text, path):
            loc = f"{path}:{lineno}"
            refs.setdefault(key, []).append(loc)

    # Build result list
    required: list[dict[str, Any]] = []
    for key in sorted(refs.keys()):
        in_dotenv = key in dotenv_keys
        in_env = key in process_env_keys
        present = in_dotenv or in_env
        source: str | None = None
        if in_env:
            source = "env"
        elif in_dotenv:
            source = "dotenv"
        required.append(
            {
                "key": key,
                "present": present,
                "source": source,
                "referenced_in": refs[key],
            }
        )

    missing = [r["key"] for r in required if not r["present"]]

    return {
        "required": required,
        "missing": missing,
        "scanned_files": len(files_to_scan),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Credential preflight: scan for env-var references and report missing keys."
    )
    p.add_argument("--workdir", required=True, help="Root of the repository to scan.")
    p.add_argument(
        "--changed-files",
        nargs="*",
        metavar="FILE",
        help="Limit scan to these files (absolute or relative to workdir).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit JSON to stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 1

    workdir = Path(args.workdir).resolve()
    if not workdir.is_dir():
        print(f"error: --workdir {workdir} is not a directory", file=sys.stderr)
        return 1

    changed_files: list[Path] | None = None
    if args.changed_files:
        changed_files = []
        for f in args.changed_files:
            p = Path(f)
            if not p.is_absolute():
                p = workdir / p
            changed_files.append(p.resolve())

    result = run_preflight(workdir, changed_files)

    # Human summary → stderr
    missing = result["missing"]
    n_scanned = result["scanned_files"]
    if missing:
        refs_summary = "; ".join(
            f"{r['key']} (in {r['referenced_in'][0]}" + (
                f" +{len(r['referenced_in'])-1} more)" if len(r['referenced_in']) > 1 else ")"
            )
            for r in result["required"]
            if not r["present"]
        )
        print(
            f"[CREDENTIAL REQUIRED] {len(missing)} key(s) referenced but not set: "
            f"{', '.join(missing)}\n  {refs_summary}\n  ({n_scanned} files scanned)",
            file=sys.stderr,
        )
    else:
        print(
            f"[CREDENTIAL PREFLIGHT] All {len(result['required'])} referenced key(s) satisfied. "
            f"({n_scanned} files scanned)",
            file=sys.stderr,
        )

    if result["errors"]:
        for err in result["errors"]:
            print(f"[CREDENTIAL PREFLIGHT WARNING] {err}", file=sys.stderr)

    if args.output_json:
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
