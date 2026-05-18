"""Deterministic, stdlib-only call-site / infra / dependency detectors (D5).

This is the *detection* half of the detect/label split. It locates sites and
emits **unlabelled** nodes — type + file:line + a short context slice. It
NEVER sets ``purpose``/``model_class``/``model_example`` or any data-in/out
prose; that is the semantic half (T12 enrich → scout/Claude).

Stdlib only (project memory: minimal deps): ``ast`` for Python, regex for
JS/TS, plain text/JSON parse for manifests. No third-party parser, no LLM.

NON-GOAL: nothing here records usage frequency / call counts. Structure and
data-flow only (explicit design Non-goal — asserted in tests).

Output records are plain dicts (order-stable, no frequency keys)::

    {
      "node_type": "llm-callsite|mcp-callsite|api-callsite|infra-component|dependency",
      "raw_ref":   "<literal target / package / url / symbol>",
      "file": "<rel path>", "line": <int>,
      "context": "<source line, stripped>",
      "provider": "anthropic|openai|...",   # llm only
      "infra_kind": "cache|queue|db|object-store",  # infra only
      "purpose": None, "model_class": None, "model_example": None,
    }
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Module → classification tables (deterministic, no inference).
# ---------------------------------------------------------------------------
_LLM_MODULES = {"anthropic": "anthropic", "openai": "openai"}
_INFRA_MODULES: Dict[str, str] = {
    "redis": "cache",
    "aioredis": "cache",
    "bullmq": "queue",
    "celery": "queue",
    "rq": "queue",
    "psycopg": "db",
    "psycopg2": "db",
    "sqlalchemy": "db",
    "asyncpg": "db",
    "pymongo": "db",
    "prisma": "db",
    "boto3": "object-store",
    "botocore": "object-store",
    "minio": "object-store",
}
_INFRA_JS = {
    "ioredis": "cache",
    "redis": "cache",
    "bullmq": "queue",
    "bull": "queue",
    "pg": "db",
    "@prisma/client": "db",
    "mongodb": "db",
    "@aws-sdk/client-s3": "object-store",
    "aws-sdk": "object-store",
}

_NULL_LABELS = {"purpose": None, "model_class": None, "model_example": None}

_URL_RE = re.compile(r"""['"](https?://[^'"\s]+)['"]""")
_FETCH_RE = re.compile(r"\bfetch\s*\(")
_MCP_NAME_RE = re.compile(r"""['"](mcp__[a-zA-Z0-9_]+__[a-zA-Z0-9_]+)['"]""")
_JS_IMPORT_RE = re.compile(
    r"""(?:import[^'"\n]*from\s*|require\s*\(\s*)['"]([^'"\n]+)['"]"""
)


def _ctx(src_lines: List[str], lineno: int) -> str:
    i = max(0, lineno - 1)
    return src_lines[i].strip() if i < len(src_lines) else ""


def _site(node_type: str, raw_ref: str, rel: str, line: int, ctx: str, **extra) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "node_type": node_type,
        "raw_ref": raw_ref,
        "file": rel,
        "line": int(line),
        "context": ctx,
    }
    rec.update(extra)
    rec.update(_NULL_LABELS)
    return rec


# ---------------------------------------------------------------------------
# Python (ast)
# ---------------------------------------------------------------------------

def _detect_py(text: str, rel: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    lines = text.splitlines()

    # Imports → llm provider / infra.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in _LLM_MODULES:
                    out.append(_site("llm-callsite", a.name, rel, node.lineno,
                                     _ctx(lines, node.lineno), provider=_LLM_MODULES[top]))
                if top in _INFRA_MODULES:
                    out.append(_site("infra-component", top, rel, node.lineno,
                                     _ctx(lines, node.lineno), infra_kind=_INFRA_MODULES[top]))
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in _LLM_MODULES:
                out.append(_site("llm-callsite", node.module or top, rel, node.lineno,
                                 _ctx(lines, node.lineno), provider=_LLM_MODULES[top]))
            if top in _INFRA_MODULES:
                out.append(_site("infra-component", top, rel, node.lineno,
                                 _ctx(lines, node.lineno), infra_kind=_INFRA_MODULES[top]))

    # Calls → MCP tool calls, requests.* HTTP, fetch().
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # session.call_tool("mcp__...") or call_tool(...)
        attr = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else "")
        if attr == "call_tool":
            ref = ""
            if node.args and isinstance(node.args[0], ast.Constant):
                ref = str(node.args[0].value)
            out.append(_site("mcp-callsite", ref or "call_tool", rel, node.lineno,
                             _ctx(lines, node.lineno)))
            continue
        # requests.get/post/... → api-callsite (url literal if present)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                and func.value.id == "requests":
            url = ""
            if node.args and isinstance(node.args[0], ast.Constant):
                url = str(node.args[0].value)
            out.append(_site("api-callsite", url or "requests", rel, node.lineno,
                             _ctx(lines, node.lineno)))

    # MCP tool-name string literals not caught via call_tool (defensive, deterministic).
    for m in _MCP_NAME_RE.finditer(text):
        line = text[: m.start()].count("\n") + 1
        if not any(s["node_type"] == "mcp-callsite" and s["line"] == line for s in out):
            out.append(_site("mcp-callsite", m.group(1), rel, line, _ctx(lines, line)))

    return out


# ---------------------------------------------------------------------------
# JS / TS (regex — deterministic)
# ---------------------------------------------------------------------------

def _detect_js(text: str, rel: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    lines = text.splitlines()

    for m in _JS_IMPORT_RE.finditer(text):
        spec = m.group(1)
        line = text[: m.start()].count("\n") + 1
        if spec in _LLM_MODULES:
            out.append(_site("llm-callsite", spec, rel, line, _ctx(lines, line),
                             provider=_LLM_MODULES[spec]))
        if spec in _INFRA_JS:
            out.append(_site("infra-component", spec, rel, line, _ctx(lines, line),
                             infra_kind=_INFRA_JS[spec]))

    for m in _FETCH_RE.finditer(text):
        line = text[: m.start()].count("\n") + 1
        tail = text[m.start(): m.start() + 200]
        um = _URL_RE.search(tail)
        out.append(_site("api-callsite", um.group(1) if um else "fetch", rel, line,
                         _ctx(lines, line)))

    for m in _MCP_NAME_RE.finditer(text):
        line = text[: m.start()].count("\n") + 1
        out.append(_site("mcp-callsite", m.group(1), rel, line, _ctx(lines, line)))

    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_file(path: Path | str, rel: str) -> List[Dict[str, Any]]:
    """Detect call sites / infra in one source file. Order-stable, never raises."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ext = p.suffix.lower()
    if ext == ".py":
        sites = _detect_py(text, rel)
    elif ext in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        sites = _detect_js(text, rel)
    else:
        return []
    # Deterministic order: (line, node_type, raw_ref).
    sites.sort(key=lambda s: (s["line"], s["node_type"], s["raw_ref"]))
    return sites


_MANIFEST_PARSERS = {
    "package.json": "_npm",
    "package-lock.json": "_npm",
    "pnpm-lock.yaml": "_pnpm",
    "requirements.txt": "_reqs",
    "pyproject.toml": "_pyproject",
    "uv.lock": "_uvlock",
    "Cargo.toml": "_cargo",
    "Cargo.lock": "_cargo",
    "go.mod": "_gomod",
    "Gemfile": "_gemfile",
}


def is_manifest(rel: str) -> bool:
    return Path(rel).name in _MANIFEST_PARSERS


def detect_manifest(path: Path | str, rel: str) -> List[Dict[str, Any]]:
    """Parse a dependency manifest → ``dependency`` nodes. Order-stable."""
    name = Path(rel).name
    if name not in _MANIFEST_PARSERS:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    names: List[str] = []
    try:
        if name in ("package.json", "package-lock.json"):
            data = json.loads(text)
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                names += list((data.get(key) or {}).keys())
        elif name == "pnpm-lock.yaml":
            names += re.findall(r"^\s{2,}([@\w][\w@./-]+):", text, re.M)
        elif name == "requirements.txt":
            for ln in text.splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    names.append(re.split(r"[=<>!~ \[]", ln, maxsplit=1)[0])
        elif name == "pyproject.toml":
            block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
            if block:
                for q in re.findall(r'["\']([A-Za-z0-9_.\-]+)', block.group(1)):
                    names.append(q)
        elif name == "uv.lock":
            names += re.findall(r'name\s*=\s*"([^"]+)"', text)
        elif name in ("Cargo.toml", "Cargo.lock"):
            names += re.findall(r'name\s*=\s*"([^"]+)"', text)
        elif name == "go.mod":
            names += re.findall(r"^\s*([\w./-]+)\s+v[\d.]", text, re.M)
        elif name == "Gemfile":
            names += re.findall(r"^\s*gem\s+['\"]([^'\"]+)", text, re.M)
    except (ValueError, json.JSONDecodeError):
        return []

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for n in names:
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(_site("dependency", n, rel, 0, "", manifest=name))
    out.sort(key=lambda s: s["raw_ref"])
    return out
