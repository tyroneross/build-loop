#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Credential preflight for build-loop Phase 1.

Ripgrep first selects files with explicit environment accesses; Python then
extracts credential-shaped keys from that small set. A Python walk is the
fallback when ripgrep is unavailable. This is an availability hint, not proof
that every referenced key is required by the current task or deployment.

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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".py"}

SKIP_DIRS = {
    "node_modules", ".venv", "venv", ".git", ".build-loop", ".next",
    "dist", "build", ".rally", "__pycache__", "tests", "test", "__tests__",
    "fixtures", "__fixtures__", "mocks", "__mocks__",
}
TEST_FILE_RE = re.compile(r"(?:^test_|[._-](?:test|spec)\.)", re.IGNORECASE)
CANDIDATE_LITERALS = ("process.env", "import.meta.env", "Deno.env", "os.getenv", "os.environ")

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
        "DB_URL",
        "DATABASE_PASSWORD",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
        "POSTGRES_PASSWORD",
        "MYSQL_URL",
        "MONGO_URL",
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

# Pattern: credential-shaped names. Generic API_URL / BASE_URL are configuration,
# not credentials; only database or Redis URLs are included by suffix.
# Matches: FOO_KEY, FOO_TOKEN, FOO_SECRET, FOO_PASSWORD, FOO_DSN,
# ADMIN_DATABASE_URL, BULLMQ_REDIS_URL, MYSQL_URL, and MONGO_URI.
# Must start with an uppercase letter, then 1+ uppercase-or-digit-or-underscore chars.
_SUFFIX_RE = re.compile(
    r'\b([A-Z][A-Z0-9_]{1,}(?:_KEY|_TOKEN|_SECRET|_PASSWORD|_DSN|_(?:DATABASE|DB|POSTGRES|POSTGRESQL|MYSQL|MONGO|MONGODB|REDIS)_URL|_(?:DATABASE|DB|POSTGRES|POSTGRESQL|MYSQL|MONGO|MONGODB|REDIS)_URI))\b'
)

# JS/TS patterns: dot, optional-chain, bracket, and destructuring accesses.
_JS_DOTENV_RE = re.compile(r'process\.env(?:\?\.|\.)([A-Z][A-Z0-9_]+)')
_JS_BRACKET_RE = re.compile(r'process\.env(?:\?\.)?\[[\'"]([\w]+)[\'"]\]')
_META_ENV_RE = re.compile(r'import\.meta\.env(?:\?\.|\.)([A-Z][A-Z0-9_]+)')
_META_BRACKET_RE = re.compile(r'import\.meta\.env(?:\?\.)?\[[\'"]([\w]+)[\'"]\]')
_DENO_GET_RE = re.compile(r'Deno\.env\.get\([\'"]([\w]+)[\'"]')
_JS_DESTRUCTURE_RE = re.compile(r'\{([^{}]{0,1000})\}\s*=\s*process\.env\b', re.DOTALL)
_JS_DESTRUCTURED_KEY_RE = re.compile(r'(?:^|,)\s*([A-Z][A-Z0-9_]+)\s*(?=[:,=]|,|$)')

# Python patterns: os.environ["X"], os.environ.get("X"), os.getenv("X")
_PY_ENVIRON_RE = re.compile(r'os\.environ\[[\'"]([\w]+)[\'"]\]')
_PY_ENVIRON_GET_RE = re.compile(r'os\.environ\.get\([\'"]([\w]+)[\'"]')
_PY_GETENV_RE = re.compile(r'os\.getenv\([\'"]([\w]+)[\'"]')


# ---------------------------------------------------------------------------
# Dotenv parsing — keys only, never values
# ---------------------------------------------------------------------------

_DOTENV_KEY_RE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|:)\s*(.*)$')


def _read_dotenv_keys(path: Path) -> set[str]:
    """Return keys with nonempty local values. Never return the values."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    keys: set[str] = set()
    for line in text.splitlines():
        match = _DOTENV_KEY_RE.match(line)
        if match and match.group(2).strip() not in {"", "''", '""'} and not match.group(2).lstrip().startswith("#"):
            keys.add(match.group(1))
    return keys


def _collect_dotenv_keys(workdir: Path) -> set[str]:
    """Collect available keys from local .env files, never examples/templates."""
    keys: set[str] = set()
    for p in workdir.iterdir():
        if (
            p.is_file() and p.name.startswith(".env")
            and not p.name.endswith((".example", ".sample", ".template"))
        ):
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
        for m in _META_BRACKET_RE.finditer(line):
            candidates.add(m.group(1))
        for m in _DENO_GET_RE.finditer(line):
            candidates.add(m.group(1))

        # Python explicit patterns
        for m in _PY_ENVIRON_RE.finditer(line):
            candidates.add(m.group(1))
        for m in _PY_ENVIRON_GET_RE.finditer(line):
            candidates.add(m.group(1))
        for m in _PY_GETENV_RE.finditer(line):
            candidates.add(m.group(1))

        for key in sorted(candidates):
            if key in WELL_KNOWN_KEYS or _SUFFIX_RE.fullmatch(key):
                found.append((key, lineno))

    for match in _JS_DESTRUCTURE_RE.finditer(text):
        for key_match in _JS_DESTRUCTURED_KEY_RE.finditer(match.group(1)):
            key = key_match.group(1)
            if key in WELL_KNOWN_KEYS or _SUFFIX_RE.fullmatch(key):
                lineno = text.count("\n", 0, match.start(1) + key_match.start(1)) + 1
                found.append((key, lineno))

    return sorted(set(found), key=lambda item: (item[1], item[0]))


def _should_scan(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in SOURCE_EXTS


def _walk_source_files(workdir: Path, errors: list[str]) -> list[Path]:
    """Portable fallback: read eligible files once to select env-access candidates."""
    results: list[Path] = []
    literals = tuple(literal.encode("ascii") for literal in CANDIDATE_LITERALS)
    for directory, dirs, files in os.walk(workdir):
        dirs[:] = sorted(
            name for name in dirs
            if name not in SKIP_DIRS
            and not (name == "worktrees" and Path(directory).name in {".claude", ".codex"})
        )
        for name in sorted(files):
            path = Path(directory) / name
            if _should_scan(path) and not TEST_FILE_RE.search(name):
                try:
                    with path.open("rb") as source:
                        if any(any(literal in line for literal in literals) for line in source):
                            results.append(path)
                except OSError as exc:
                    errors.append(f"read error {path}: {exc}")
    return results


def _candidate_source_files(workdir: Path, errors: list[str]) -> tuple[list[Path], str]:
    """Use a fast content prefilter, then let Python inspect matching files."""
    if shutil.which("rg"):
        command = ["rg", "-l", "-0", "--hidden", "--fixed-strings", "--no-messages"]
        for literal in CANDIDATE_LITERALS:
            command.extend(("-e", literal))
        for suffix in sorted(SOURCE_EXTS):
            command.extend(("--glob", f"*{suffix}"))
        for directory in sorted(SKIP_DIRS):
            command.extend(("--glob", f"!**/{directory}/**"))
        for tree in (".claude", ".codex"):
            command.extend(("--glob", f"!**/{tree}/worktrees/**"))
        for filename_glob in ("!**/*.test.*", "!**/*.spec.*", "!**/test_*"):
            command.extend(("--glob", filename_glob))
        command.append(".")
        try:
            result = subprocess.run(command, cwd=workdir, capture_output=True, timeout=10)
            if result.returncode in (0, 1):
                files = []
                for raw in result.stdout.split(b"\0"):
                    if not raw:
                        continue
                    path = workdir / os.fsdecode(raw)
                    relative = path.relative_to(workdir)
                    if (
                        path.is_file() and _should_scan(path)
                        and not any(part in SKIP_DIRS for part in relative.parts[:-1])
                        and not TEST_FILE_RE.search(path.name)
                    ):
                        files.append(path)
                return sorted(set(files)), "ripgrep"
            errors.append(f"ripgrep exited {result.returncode}; used Python fallback")
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"ripgrep unavailable ({type(exc).__name__}); used Python fallback")
    return _walk_source_files(workdir, errors), "python"


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
            files_to_scan, scan_method = _candidate_source_files(workdir, errors)
        except Exception as exc:
            errors.append(f"walk error: {exc}")
            files_to_scan = []
            scan_method = "python"

    if changed_files:
        scan_method = "explicit"

    # Collect satisfied keys
    dotenv_keys = _collect_dotenv_keys(workdir)
    process_env_keys = {key for key, value in os.environ.items() if value}

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
        "scan_method": scan_method,
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
        help="Emit a compact JSON summary to stdout.",
    )
    p.add_argument(
        "--details",
        action="store_true",
        help="Include every matched key and source location in JSON output.",
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

    # Human summary → stderr. Keep the first pass bounded; full evidence is opt-in.
    missing = result["missing"]
    n_scanned = result["scanned_files"]
    if missing:
        sample = ", ".join(missing[:5])
        more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        print(
            f"[CREDENTIAL UNAVAILABLE] {len(missing)} referenced key(s): "
            f"{sample}{more}; {n_scanned} candidate files scanned by "
            f"{result['scan_method']}. A reference does not prove this task requires the key.",
            file=sys.stderr,
        )
    else:
        print(
            f"[CREDENTIAL PREFLIGHT] {len(result['required'])} referenced key(s) "
            f"available locally; {n_scanned} candidate files scanned by {result['scan_method']}.",
            file=sys.stderr,
        )

    if result["errors"]:
        for err in result["errors"]:
            print(f"[CREDENTIAL PREFLIGHT WARNING] {err}", file=sys.stderr)

    if args.output_json:
        compact = {
            "missing": missing,
            "missing_count": len(missing),
            "referenced_count": len(result["required"]),
            "scanned_files": n_scanned,
            "scan_method": result["scan_method"],
            "reference_samples": [
                {"key": item["key"], "first_reference": item["referenced_in"][0]}
                for item in result["required"] if not item["present"]
            ][:5],
            "errors": result["errors"],
        }
        print(json.dumps(result if args.details else compact, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
