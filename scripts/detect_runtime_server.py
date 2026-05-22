#!/usr/bin/env python3
"""Detect whether a project ships a live HTTP/SSE runtime server.

Phase 1 ASSESS calls this with ``--workdir <repo> --json`` and writes the
returned envelope to ``.build-loop/state.json.runtimeServerInfo`` plus a
boolean trigger flag at ``state.json.triggers.runtimeServer``. Phase 4
sub-step B Validate consults the trigger and, when true and the diff
touched the detected server module, runs a live curl-against-SSE smoke
gate before falling through to the LLM-as-judge graders.

This is the lightweight detection arm of the live-smoke gate documented
in:
  - decision _unscoped/0003 (live HTTP/SSE smoke required when
    build-loop touches a runtime server)
  - feedback_buildloop_pytest_insufficient_for_runtime (rule semantics)
  - gotcha_serve_sse_live_smoke_required (example-app reference impl)

Stdlib only. Heuristic; intentionally conservative — single-pattern
matches don't trigger so test files that import e.g. ``flask`` for unit
tests don't false-positive. ``runtimeServer: false`` is the silent
default for CLIs, libraries, plugins, and static-render web apps.

Output schema:
    {
      "runtimeServer": true|false,
      "server_module": "<rel-path>" | null,
      "sse_route": "/api/research" | null,
      "default_port": 11435 | null,
      "embedded_ui_module": "<rel-path>" | null,
      "event_handler_locations": [
        {"file": "<rel-path>", "line": N, "function": "<name>"}
      ],
      "evidence": ["matched <pattern> in <file>", ...]
    }
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Heuristic patterns
# ---------------------------------------------------------------------------

# HTTP / SSE substrate — anything that wires a runtime listener or emits
# Server-Sent Events. Match-once-per-file is enough; the goal is "is
# there a server in this file?".
SUBSTRATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("BaseHTTPRequestHandler", re.compile(r"\bBaseHTTPRequestHandler\b")),
    ("aiohttp.web.Application", re.compile(r"\baiohttp\.web\.Application\b")),
    ("from fastapi", re.compile(r"\bfrom\s+fastapi\b")),
    ("from flask", re.compile(r"\bfrom\s+flask\b")),
    ("import bottle", re.compile(r"\bimport\s+bottle\b")),
    ("wsgiref.simple_server", re.compile(r"\bwsgiref\.simple_server\b")),
    ("EventSourceResponse", re.compile(r"\bEventSourceResponse\b")),
    ("text/event-stream", re.compile(r"text/event-stream")),
]

# Event-emit pattern — the file actually streams events back to a client.
# Pairing substrate + emit in the same file is the signal we trust.
EMIT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("_send_event", re.compile(r"\b_send_event\b")),
    ('yield {"type":', re.compile(r"yield\s+\{\s*['\"]type['\"]\s*:")),
    ("Event(", re.compile(r"\bEvent\s*\(")),
    ("EventSourceResponse", re.compile(r"\bEventSourceResponse\b")),
    (
        "self.wfile.write(b\"data:",
        re.compile(r"self\.wfile\.write\s*\(\s*b?['\"]data:"),
    ),
]

# SSE route detection — the URL path the client posts to.
# Pair 1: BaseHTTPRequestHandler-style — `if self.path == "/route":` near
#         a `text/event-stream` Content-Type.
# Pair 2: framework-style — `@app.get("/route")` or `@app.post(...)` near
#         an `EventSourceResponse`.
SELF_PATH_RE = re.compile(r"self\.path\s*==\s*['\"]([^'\"]+)['\"]")
APP_ROUTE_RE = re.compile(
    r"@app\.(?:get|post|route)\s*\(\s*['\"]([^'\"]+)['\"]"
)
EVENT_STREAM_NEARBY_RE = re.compile(r"text/event-stream")
EVENT_SOURCE_RESPONSE_RE = re.compile(r"\bEventSourceResponse\b")

# Default port — first match wins.
PORT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"--port[\s\"',=]+(\d+)"),
    re.compile(r"\bport\s*=\s*(\d+)"),
    re.compile(r"PORT\s*=\s*(\d+)"),
]

# Embedded UI signals — the same file ships HTML + JS that consumes SSE.
HTML_DOCTYPE_RE = re.compile(r"<!DOCTYPE\s+html", re.IGNORECASE)
SCRIPT_TAG_RE = re.compile(r"<script\b", re.IGNORECASE)
EVENT_SOURCE_CTOR_RE = re.compile(r"\bEventSource\s*\(")
HANDLE_EVENT_FN_RE = re.compile(r"\bfunction\s+handleEvent\b")
ONMESSAGE_RE = re.compile(r"\.onmessage\s*=\s*function\b")

# Event-handler function locations. Returns line + function name.
HANDLER_LOCATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfunction\s+(handleEvent)\b"), "handleEvent"),
    (re.compile(r"\b(\w+)\.onmessage\s*=\s*function\b"), "onmessage"),
]

# File walk filters.
SKIP_DIRS = frozenset({
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".episodic",
    ".build-loop",
    ".navgator",
    ".ibr",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
})
ALLOWED_SUFFIXES = frozenset({".py", ".js", ".ts", ".mjs", ".cjs"})
MAX_FILE_BYTES = 200 * 1024  # 200 KB cap; servers larger than this still
                              # match because we read line-by-line from
                              # the start; the cap just bounds memory.


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _walk_candidate_files(root: Path) -> Iterable[Path]:
    """Yield candidate source files under ``root`` honoring SKIP_DIRS.

    Non-tests/ paths yield BEFORE tests/ paths so that when multiple files
    match the substrate+emit signature, a real server module wins over a
    test fixture that imports ``BaseHTTPRequestHandler`` for mock-server
    purposes. example-app 2026-05-11 evidence: without this priority,
    ``tests/test_sse_cancellation.py`` was picked over
    ``src/localsmartz/serve.py`` and the inline-UI was invisible.
    """
    test_paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # In-place prune; os.walk respects this.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            suffix = Path(fn).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                continue
            candidate = Path(dirpath) / fn
            if _is_test_path(candidate, root):
                test_paths.append(candidate)
            else:
                yield candidate
    # Tests last — they're the fallback when no real server module exists.
    for p in test_paths:
        yield p


def _is_test_path(path: Path, root: Path) -> bool:
    """Return True if ``path`` lives inside a tests/ directory or is named
    ``test_*.py`` / ``*_test.py`` / ``*.test.{js,ts,mjs,cjs}``.

    Walks the rel-path's parts so ``tests/`` at any depth qualifies (not
    just root-level). Filename heuristic mirrors pytest + jest defaults.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    parts = {p.lower() for p in rel.parts[:-1]}
    if "tests" in parts or "test" in parts or "__tests__" in parts:
        return True
    name = path.name.lower()
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    for suf in (".test.js", ".test.ts", ".test.mjs", ".test.cjs"):
        if name.endswith(suf):
            return True
    return False


def _read_capped(path: Path) -> str | None:
    """Read up to MAX_FILE_BYTES of text. Returns ``None`` on error."""
    try:
        with path.open("rb") as fh:
            data = fh.read(MAX_FILE_BYTES)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


def _first_substrate(text: str) -> tuple[str, str] | None:
    """Return (label, snippet) of the first substrate pattern hit."""
    for label, pat in SUBSTRATE_PATTERNS:
        m = pat.search(text)
        if m:
            return label, m.group(0)
    return None


def _first_emit(text: str) -> tuple[str, str] | None:
    """Return (label, snippet) of the first emit pattern hit."""
    for label, pat in EMIT_PATTERNS:
        m = pat.search(text)
        if m:
            return label, m.group(0)
    return None


def _detect_sse_route(text: str) -> str | None:
    """Best-effort SSE route extraction.

    Pair 1 (BaseHTTPRequestHandler-style): a ``self.path == "<route>"``
    comparison whose surrounding ±20 lines mention ``text/event-stream``.
    Pair 2 (framework-style): an ``@app.get/post/route("<route>")``
    decorator whose surrounding ±20 lines mention ``EventSourceResponse``.
    First match wins.
    """
    lines = text.splitlines()
    n = len(lines)

    def _nearby(idx: int, pat: re.Pattern[str]) -> bool:
        lo = max(0, idx - 20)
        hi = min(n, idx + 21)
        return any(pat.search(lines[j]) for j in range(lo, hi))

    for idx, line in enumerate(lines):
        m = SELF_PATH_RE.search(line)
        if m and _nearby(idx, EVENT_STREAM_NEARBY_RE):
            return m.group(1)
    for idx, line in enumerate(lines):
        m = APP_ROUTE_RE.search(line)
        if m and _nearby(idx, EVENT_SOURCE_RESPONSE_RE):
            return m.group(1)
    return None


def _detect_default_port(text: str) -> int | None:
    """First numeric port match across PORT_PATTERNS."""
    for pat in PORT_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _detect_embedded_ui(text: str) -> bool:
    """Embedded UI = HTML doctype + <script> tag + (EventSource OR
    handleEvent OR onmessage) all present in the same file."""
    has_doctype = bool(HTML_DOCTYPE_RE.search(text))
    has_script = bool(SCRIPT_TAG_RE.search(text))
    consumer = (
        EVENT_SOURCE_CTOR_RE.search(text)
        or HANDLE_EVENT_FN_RE.search(text)
        or ONMESSAGE_RE.search(text)
    )
    return has_doctype and has_script and bool(consumer)


def _find_handler_locations(text: str, rel_path: str) -> list[dict]:
    """Return event-handler function locations as {file, line, function}."""
    out: list[dict] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        for pat, fn_label in HANDLER_LOCATION_PATTERNS:
            m = pat.search(line)
            if m:
                # If the regex captured the function name (group 1), use it;
                # else fall back to the static label.
                try:
                    name = m.group(1)
                except IndexError:
                    name = fn_label
                if not name:
                    name = fn_label
                out.append({"file": rel_path, "line": idx, "function": name})
    return out


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def detect(workdir: Path) -> dict:
    """Run the runtime-server detection heuristic over ``workdir``.

    Returns the envelope schema documented in this module's docstring.
    Never raises on ordinary file-walk errors; bad files are skipped.
    Helper failures elsewhere should treat ``runtimeServer: false`` as
    the silent default.
    """
    workdir = workdir.resolve()
    if not workdir.is_dir():
        return _negative_envelope()

    server_module: Path | None = None
    server_text: str | None = None
    evidence: list[str] = []

    for candidate in _walk_candidate_files(workdir):
        text = _read_capped(candidate)
        if text is None:
            continue
        substrate = _first_substrate(text)
        if substrate is None:
            continue
        emit = _first_emit(text)
        if emit is None:
            continue
        # First file that hits BOTH wins. Server modules are usually
        # singular per project; if more than one matches, the first by
        # walk order is the canonical entry point.
        try:
            rel = str(candidate.relative_to(workdir))
        except ValueError:
            rel = str(candidate)
        evidence.append(f"matched '{substrate[0]}' in {rel}")
        evidence.append(f"matched '{emit[0]}' in {rel}")
        server_module = candidate
        server_text = text
        break

    if server_module is None or server_text is None:
        return _negative_envelope()

    rel_server = str(server_module.relative_to(workdir))
    sse_route = _detect_sse_route(server_text)
    if sse_route:
        evidence.append(f"matched SSE route '{sse_route}' in {rel_server}")
    default_port = _detect_default_port(server_text)
    if default_port:
        evidence.append(f"matched port {default_port} in {rel_server}")

    has_ui = _detect_embedded_ui(server_text)
    embedded_ui_module: str | None = rel_server if has_ui else None
    handler_locations: list[dict] = (
        _find_handler_locations(server_text, rel_server) if has_ui else []
    )
    if has_ui:
        evidence.append(f"matched embedded HTML/JS in {rel_server}")

    return {
        "runtimeServer": True,
        "server_module": rel_server,
        "sse_route": sse_route,
        "default_port": default_port,
        "embedded_ui_module": embedded_ui_module,
        "event_handler_locations": handler_locations,
        "evidence": evidence,
    }


def _negative_envelope() -> dict:
    return {
        "runtimeServer": False,
        "server_module": None,
        "sse_route": None,
        "default_port": None,
        "embedded_ui_module": None,
        "event_handler_locations": [],
        "evidence": [],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Detect whether a project ships a live HTTP/SSE server."
    )
    p.add_argument(
        "--workdir",
        default=".",
        help="Project root to scan (default: cwd).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON envelope (default behavior; flag preserved for "
        "explicit caller intent).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workdir = Path(args.workdir)
    try:
        envelope = detect(workdir)
    except Exception as exc:  # noqa: BLE001
        # Per the gate contract: detector outage is non-fatal. Emit the
        # negative envelope and surface a one-line warning.
        print(
            f"[detect_runtime_server] WARN: detection failed ({exc!r}); "
            "treating as runtimeServer:false.",
            file=sys.stderr,
        )
        envelope = _negative_envelope()
    print(json.dumps(envelope, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
