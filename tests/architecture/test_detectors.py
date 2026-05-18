"""Stage 2.5 — detectors reduced to scanner-adapter + 3 gap detectors (D5).

After reconciliation the scanner (peer native-scanner) is the SINGLE structural
detection source. ``detectors.py`` no longer re-scans source for scanner-covered
entities. It exposes two pure functions:

  * ``map_scan_result(scan_result)`` — maps the scanner's runtime Components /
    classified Connections into the existing unlabelled site-dict shape that
    ``enrich`` consumes (dependency / llm-callsite / external-service /
    api-callsite). Deterministic, never labels (D5).
  * ``detect_gaps(path, rel)`` — emits ONLY the 3 entity classes the scanner
    structurally cannot represent: R1 MCP callsites, R2 external-URL HTTP,
    R3 infra-component (infra_kind classification of an infra import).

NON-GOAL GUARD: no usage-frequency / call-count key anywhere.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from build_loop.architecture.detectors import (
    detect_gaps,
    detect_manifest,
    is_manifest,
    map_scan_result,
)
from build_loop.architecture.scanner import scan_repo

_FREQ_RE = re.compile(r"count|freq|frequency|invocation|num_calls|hits|times_called", re.I)


def _w(p: Path, s: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(s), encoding="utf-8")
    return p


# --- map_scan_result: scanner output → taxonomy sites -----------------------

def test_map_scan_result_maps_package_to_dependency(tmp_path):
    _w(tmp_path / "package.json", '{"dependencies": {"react": "^19"}}')
    _w(tmp_path / "app" / "page.js", """
        import React from "react";
        export default function P() { return React.version; }
    """)
    sr = scan_repo(tmp_path)
    sites = map_scan_result(sr)
    deps = [s for s in sites if s["node_type"] == "dependency"]
    assert any(s["raw_ref"] == "react" for s in deps), sites
    s = next(s for s in deps if s["raw_ref"] == "react")
    assert s.get("purpose") is None and s.get("model_class") is None


def test_map_scan_result_maps_llm_service_to_llm_callsite(tmp_path):
    _w(tmp_path / "lib" / "ai.js", """
        export async function run() {
          const url = process.env.OLLAMA_BASE_URL || "http://localhost:11434";
          return fetch(url);
        }
    """)
    sr = scan_repo(tmp_path)
    sites = map_scan_result(sr)
    llm = [s for s in sites if s["node_type"] == "llm-callsite"]
    assert llm, sites
    assert llm[0]["provider"]  # service name carried as provider
    assert llm[0].get("model_class") is None  # never fabricated (D5/D6)
    assert "model" not in llm[0] and "model_id" not in llm[0]


def test_map_scan_result_maps_frontend_fetch_to_api_callsite(tmp_path):
    _w(tmp_path / "app" / "page.js", """
        export default async function P() {
          await fetch("/api/ping");
          return 1;
        }
    """)
    _w(tmp_path / "app" / "api" / "ping" / "route.js",
       "export function GET() { return Response.json({ok:true}); }\n")
    sr = scan_repo(tmp_path)
    sites = map_scan_result(sr)
    assert any(s["node_type"] == "api-callsite" for s in sites), sites


def test_map_scan_result_is_deterministic(tmp_path):
    _w(tmp_path / "package.json", '{"dependencies": {"react": "^19"}}')
    _w(tmp_path / "app" / "p.js", 'import React from "react";\nexport const x = React;\n')
    a = map_scan_result(scan_repo(tmp_path))
    b = map_scan_result(scan_repo(tmp_path))
    assert a == b


# --- detect_gaps: ONLY the 3 retained detectors -----------------------------

def test_gap_R1_mcp_callsite(tmp_path):
    f = _w(tmp_path / "m.py", """
        async def run(session):
            return await session.call_tool("mcp__search__query", {"q": "x"})
    """)
    sites = detect_gaps(f, "m.py")
    assert any(s["node_type"] == "mcp-callsite" for s in sites), sites


def test_gap_R1_mcp_string_literal(tmp_path):
    f = _w(tmp_path / "m.ts", 'const t = "mcp__plugin__do_thing";\n')
    sites = detect_gaps(f, "m.ts")
    assert any(s["node_type"] == "mcp-callsite" for s in sites), sites


def test_gap_R2_external_url_http_only(tmp_path):
    f1 = _w(tmp_path / "a.ts", """
        export async function go() {
          const r = await fetch("https://api.example.com/v1/data");
          return r.json();
        }
    """)
    f2 = _w(tmp_path / "b.py", """
        import requests
        requests.get("https://svc.internal/health")
    """)
    s1 = detect_gaps(f1, "a.ts")
    s2 = detect_gaps(f2, "b.py")
    assert any(s["node_type"] == "api-callsite" for s in s1), s1
    assert any(s["node_type"] == "api-callsite" for s in s2), s2


def test_gap_R2_relative_fetch_not_emitted(tmp_path):
    # Internal/relative fetch is scanner-sourced — detect_gaps must NOT emit it
    # (single-representation: avoids double-counting with frontend-calls-api).
    f = _w(tmp_path / "p.js", 'export const go = () => fetch("/api/ping");\n')
    sites = detect_gaps(f, "p.js")
    assert not any(s["node_type"] == "api-callsite" for s in sites), sites


def test_gap_R3_infra_imports_classified(tmp_path):
    f = _w(tmp_path / "i.py", """
        import redis
        from bullmq import Queue
        import psycopg
        import boto3
    """)
    sites = detect_gaps(f, "i.py")
    kinds = {s.get("infra_kind") for s in sites if s["node_type"] == "infra-component"}
    assert {"cache", "queue", "db", "object-store"}.issubset(kinds), sites


def test_gap_R4_python_llm_sdk_bare_import(tmp_path):
    # Scanner emits zero connections for Python `import anthropic` /
    # `from openai import OpenAI` (JS forms ARE scanner-covered). R4 fills it.
    f = _w(tmp_path / "ai.py", """
        import anthropic
        from openai import OpenAI
        def ask():
            anthropic.Anthropic().messages.create(model="x", messages=[])
            OpenAI().chat.completions.create(model="y", messages=[])
    """)
    sites = detect_gaps(f, "ai.py")
    providers = {s.get("provider") for s in sites if s["node_type"] == "llm-callsite"}
    assert {"anthropic", "openai"}.issubset(providers), sites
    for s in sites:
        if s["node_type"] == "llm-callsite":
            assert s.get("model_class") is None and s.get("purpose") is None


def test_gap_R4_js_llm_sdk_not_emitted(tmp_path):
    # JS @anthropic-ai/sdk is fully scanner-covered — detect_gaps must NOT
    # also emit it (single representation).
    f = _w(tmp_path / "a.ts",
           'import Anthropic from "@anthropic-ai/sdk";\nconst c = new Anthropic();\n')
    sites = detect_gaps(f, "a.ts")
    assert not any(s["node_type"] == "llm-callsite" for s in sites), sites


def test_gap_detectors_never_emit_scanner_owned_types(tmp_path):
    # detect_gaps must NOT emit dependency / generic LLM-SDK-import nodes —
    # those are scanner-owned (single representation).
    f = _w(tmp_path / "x.py", """
        import anthropic
        anthropic.Anthropic().messages.create(model="m", messages=[])
    """)
    sites = detect_gaps(f, "x.py")
    assert not any(s["node_type"] == "dependency" for s in sites), sites


def test_gap_R5_manifest_dependency_inventory(tmp_path):
    # Scanner is import-driven and does NOT provide the declared-dependency
    # inventory. R5 (detect_manifest) parses all declared deps regardless of
    # whether they are imported.
    _w(tmp_path / "package.json", '{"dependencies": {"react": "^19", "redis": "^4"}}')
    _w(tmp_path / "requirements.txt", "anthropic==0.40\nrequests>=2.31\n")
    assert is_manifest("package.json") and is_manifest("requirements.txt")
    npm = detect_manifest(tmp_path / "package.json", "package.json")
    pip = detect_manifest(tmp_path / "requirements.txt", "requirements.txt")
    assert {"react", "redis"}.issubset({s["raw_ref"] for s in npm})
    assert {"anthropic", "requests"}.issubset({s["raw_ref"] for s in pip})
    assert all(s["node_type"] == "dependency" for s in npm + pip)


def test_no_frequency_fields_anywhere(tmp_path):
    _w(tmp_path / "package.json", '{"dependencies": {"react": "^19"}}')
    _w(tmp_path / "app" / "p.js", """
        import React from "react";
        export default async function P() {
          await fetch("https://x.y/z");
          return React.version;
        }
    """)
    f = _w(tmp_path / "m.py",
           'async def r(s):\n    return await s.call_tool("mcp__a__b", {})\n')
    payload = map_scan_result(scan_repo(tmp_path)) + detect_gaps(f, "m.py")
    for site in payload:
        for k in site.keys():
            assert not _FREQ_RE.search(k), f"frequency-like key leaked: {k}"


def test_detect_gaps_syntax_error_does_not_crash(tmp_path):
    f = _w(tmp_path / "bad.py", "def (((  :\n")
    assert detect_gaps(f, "bad.py") == []
