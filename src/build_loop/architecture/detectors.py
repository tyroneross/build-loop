"""Scanner-adapter + 3 gap detectors (D5 detection half, Stage 2.5).

After the Stage 2.5 reconciliation the peer native-scanner (``scanner.py``) is
the SINGLE structural detection source for the dependency / LLM / service /
package / internal-API surface. This module no longer re-scans source files for
anything the scanner already represents. It is now a thin, deterministic,
stdlib-only *adapter + gap* layer with two public functions:

  * ``map_scan_result(scan_result)`` — pure mapping of the scanner's runtime
    Components / classified Connections into the existing **unlabelled**
    site-dict shape ``enrich`` already consumes. NEVER sets
    ``purpose``/``model_class``/``model_example`` (semantic half = scout, D5).

  * ``detect_gaps(path, rel)`` — emits ONLY the entity classes the scanner
    structurally cannot represent (justified in ``.build-loop/plan.md``):

      R1  MCP callsites    — scanner ``SERVICE_PATTERNS`` has no MCP pattern;
                             scanner emits nothing for ``mcp__a__b`` /
                             ``session.call_tool(...)``.
      R2  external-URL HTTP — scanner ``frontend-calls-api`` only emits a
                             *resolved internal route id*; an absolute
                             ``http(s)://`` target is dropped. Disjoint from
                             the scanner by construction (absolute URL only).
      R3  infra_kind        — scanner maps ``import redis``/``bullmq``/
                             ``psycopg``/``boto3`` to a ``uses-package``
                             *dependency* edge only, losing the
                             ``infra-component`` + ``infra_kind`` taxonomy that
                             digest / diagram / semantic_todo depend on
                             (test_stage2_acceptance C1 asserts it present).
      R4  Python LLM SDK    — the scanner's ``SERVICE_PATTERNS`` cover the JS
            bare imports         ``@anthropic-ai/sdk`` / ``openai`` import forms but emit
                             NOTHING for Python ``import anthropic`` /
                             ``from openai import OpenAI`` (verified: scan_repo
                             yields zero connections for these). JS LLM SDK
                             stays scanner-sourced (fully covered) — R4 is
                             Python-only, disjoint from the scanner by
                             construction (single representation).

NON-GOAL: nothing here records usage frequency / call counts. Structure and
data-flow only (asserted in tests).

Output records are plain dicts (order-stable, no frequency keys)::

    {
      "node_type": "llm-callsite|mcp-callsite|api-callsite|infra-component|"
                    "external-service|dependency",
      "raw_ref":   "<literal target / package / url / service / symbol>",
      "file": "<rel path>", "line": <int>,
      "context": "<source line / scanner description, stripped>",
      "provider": "<service name>",          # llm only
      "infra_kind": "cache|queue|db|object-store",  # infra only
      "manifest": "<package manager>",        # dependency only
      "purpose": None, "model_class": None, "model_example": None,
    }
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# R3 — infra module → infra_kind classification (deterministic, no inference).
# Retained because the scanner only emits these as `uses-package` dependency
# edges; it loses the infra-component taxonomy + infra_kind.
# ---------------------------------------------------------------------------
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

# R4 — Python LLM SDK modules the scanner's JS-oriented SERVICE_PATTERNS miss
# (scan_repo emits zero connections for bare `import anthropic` /
# `from openai import OpenAI`). Python-only; JS LLM SDK stays scanner-sourced.
_LLM_PY_MODULES = {"anthropic": "anthropic", "openai": "openai"}

_NULL_LABELS = {"purpose": None, "model_class": None, "model_example": None}

# R2: ONLY absolute http(s):// URLs (scanner frontend-calls-api never emits an
# absolute URL — it emits a resolved internal route id). Disjoint by design.
_ABS_URL_RE = re.compile(r"""['"](https?://[^'"\s]+)['"]""")
_FETCH_RE = re.compile(r"\bfetch\s*\(")
# R1: MCP tool-name string literals.
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
# map_scan_result — scanner Components/Connections → taxonomy sites
# ---------------------------------------------------------------------------

def map_scan_result(scan_result: Any) -> List[Dict[str, Any]]:
    """Map a scanner ``ScanResult`` into unlabelled taxonomy sites.

    Single structural source: every dependency / LLM / service / internal-API
    site comes from the scanner here — detectors never re-scan for these.

    Mapping:
      * Connection ``uses-package`` (target Component kind=package)
            → ``dependency`` (raw_ref=package_name, manifest=package_manager)
      * Connection ``service-call`` (target kind=external-service):
            target Component.type == "llm"      → ``llm-callsite``
            target Component.type in {service,database,...} → ``external-service``
      * Connection ``frontend-calls-api`` (resolved internal route)
            → ``api-callsite`` (raw_ref = symbol, e.g. ``fetch(/api/x)``)

    Order-stable, never labels (D5), never raises.
    """
    by_id: Dict[str, Any] = {
        c.component_id: c for c in getattr(scan_result, "components", [])
    }
    out: List[Dict[str, Any]] = []
    for conn in getattr(scan_result, "connections", []):
        ctype = conn.type
        target = by_id.get(conn.to_id)
        if ctype == "uses-package" and target is not None:
            meta = target.metadata or {}
            out.append(_site(
                "dependency",
                meta.get("package_name", target.name),
                conn.file,
                conn.line,
                conn.description or "",
                manifest=meta.get("package_manager", ""),
            ))
        elif ctype == "service-call" and target is not None:
            svc_kind = getattr(target, "type", "")  # llm|service|database|...
            svc_name = (target.metadata or {}).get("service_name", target.name)
            if svc_kind == "llm":
                out.append(_site(
                    "llm-callsite", svc_name, conn.file, conn.line,
                    conn.description or "", provider=svc_name,
                ))
            else:
                out.append(_site(
                    "external-service", svc_name, conn.file, conn.line,
                    conn.description or "",
                ))
        elif ctype == "frontend-calls-api":
            out.append(_site(
                "api-callsite", conn.symbol or "fetch", conn.file, conn.line,
                conn.description or "",
            ))
    out.sort(key=lambda s: (s["file"], s["line"], s["node_type"], s["raw_ref"]))
    return out


# ---------------------------------------------------------------------------
# detect_gaps — ONLY R1 (MCP), R2 (external-URL HTTP), R3 (infra_kind)
# ---------------------------------------------------------------------------

def _gaps_py(text: str, rel: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    lines = text.splitlines()

    # R3 — infra imports → infra-component (with infra_kind).
    # R4 — Python LLM SDK bare imports → llm-callsite (scanner-uncovered).
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in _INFRA_MODULES:
                    out.append(_site("infra-component", top, rel, node.lineno,
                                     _ctx(lines, node.lineno),
                                     infra_kind=_INFRA_MODULES[top]))
                if top in _LLM_PY_MODULES:
                    out.append(_site("llm-callsite", a.name, rel, node.lineno,
                                     _ctx(lines, node.lineno),
                                     provider=_LLM_PY_MODULES[top]))
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in _INFRA_MODULES:
                out.append(_site("infra-component", top, rel, node.lineno,
                                 _ctx(lines, node.lineno),
                                 infra_kind=_INFRA_MODULES[top]))
            if top in _LLM_PY_MODULES:
                out.append(_site("llm-callsite", node.module or top, rel,
                                 node.lineno, _ctx(lines, node.lineno),
                                 provider=_LLM_PY_MODULES[top]))

    # R1 — MCP via session.call_tool(...).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else "")
        if attr == "call_tool":
            ref = ""
            if node.args and isinstance(node.args[0], ast.Constant):
                ref = str(node.args[0].value)
            out.append(_site("mcp-callsite", ref or "call_tool", rel,
                             node.lineno, _ctx(lines, node.lineno)))

    # R1 — MCP tool-name string literals (defensive, not double-counted).
    for m in _MCP_NAME_RE.finditer(text):
        line = text[: m.start()].count("\n") + 1
        if not any(s["node_type"] == "mcp-callsite" and s["line"] == line for s in out):
            out.append(_site("mcp-callsite", m.group(1), rel, line, _ctx(lines, line)))

    # R2 — external (absolute http(s)://) HTTP via requests.*.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                and func.value.id == "requests":
            if node.args and isinstance(node.args[0], ast.Constant):
                url = str(node.args[0].value)
                if url.startswith(("http://", "https://")):
                    out.append(_site("api-callsite", url, rel, node.lineno,
                                     _ctx(lines, node.lineno)))
    return out


def _gaps_js(text: str, rel: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    lines = text.splitlines()

    # R3 — infra imports → infra-component (with infra_kind).
    for m in _JS_IMPORT_RE.finditer(text):
        spec = m.group(1)
        line = text[: m.start()].count("\n") + 1
        if spec in _INFRA_JS:
            out.append(_site("infra-component", spec, rel, line, _ctx(lines, line),
                             infra_kind=_INFRA_JS[spec]))

    # R2 — fetch() to an absolute http(s):// URL ONLY (relative/internal fetch
    # is scanner-sourced via frontend-calls-api — single representation).
    for m in _FETCH_RE.finditer(text):
        line = text[: m.start()].count("\n") + 1
        tail = text[m.start(): m.start() + 200]
        um = _ABS_URL_RE.search(tail)
        if um:
            out.append(_site("api-callsite", um.group(1), rel, line,
                             _ctx(lines, line)))

    # R1 — MCP tool-name string literals.
    for m in _MCP_NAME_RE.finditer(text):
        line = text[: m.start()].count("\n") + 1
        out.append(_site("mcp-callsite", m.group(1), rel, line, _ctx(lines, line)))

    return out


# ---------------------------------------------------------------------------
# R5 — manifest dependency INVENTORY (declared deps, import-independent).
# The scanner is import-graph driven: it emits `uses-package` ONLY for a
# package an actual source import resolves to. It structurally does NOT
# provide the declared-dependency inventory (digest.dep_manifest_hash + the
# `dependency` inventory contract + test_enrich/C1 require declared deps as
# `dependency` nodes regardless of whether they are imported). Retained, and
# deduped in enrich against scanner `uses-package` (single representation).
# ---------------------------------------------------------------------------
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
    """Parse a dependency manifest → ``dependency`` nodes. Order-stable.

    Inventory of declared dependencies (R5) — import-independent, the scanner
    does not provide this. Never raises.
    """
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


def detect_gaps(path: Path | str, rel: str) -> List[Dict[str, Any]]:
    """Emit ONLY the 3 scanner-gap classes for one source file.

    Order-stable, never raises. Returns ``[]`` for unsupported extensions or
    unreadable / unparseable files.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ext = p.suffix.lower()
    if ext == ".py":
        sites = _gaps_py(text, rel)
    elif ext in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        sites = _gaps_js(text, rel)
    else:
        return []
    sites.sort(key=lambda s: (s["line"], s["node_type"], s["raw_ref"]))
    return sites
