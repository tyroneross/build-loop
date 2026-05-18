"""T11 — deterministic stdlib detectors (D5 detection half).

Detectors locate call sites + infra + dependency manifest entries using ONLY
``ast`` (.py) and regex (.ts/.js) + manifest text parse. They emit UNLABELLED
nodes (type + file:line + context slice) and NEVER set purpose/model_class —
that is the semantic half (T12, Claude/scout).

NON-GOAL GUARD: assert NO usage-frequency/call-count key exists anywhere in
detector output (the explicit design Non-goal).
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from build_loop.architecture.detectors import detect_file, detect_manifest

_FREQ_RE = re.compile(r"count|freq|frequency|invocation|num_calls|hits|times_called", re.I)


def _w(p: Path, s: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(s), encoding="utf-8")
    return p


def test_anthropic_sdk_detected(tmp_path):
    f = _w(tmp_path / "llm.py", """
        import anthropic
        client = anthropic.Anthropic()
        def ask(q):
            return client.messages.create(model="claude-x", messages=[])
    """)
    sites = detect_file(f, "llm.py")
    llm = [s for s in sites if s["node_type"] == "llm-callsite"]
    assert llm, sites
    s = llm[0]
    assert s["provider"] == "anthropic"
    assert s["file"] == "llm.py" and s["line"] > 0
    assert "context" in s and s["context"]
    # NEVER labelled by the detector.
    assert s.get("purpose") is None
    assert s.get("model_class") is None


def test_openai_sdk_detected(tmp_path):
    f = _w(tmp_path / "o.py", """
        from openai import OpenAI
        c = OpenAI()
        c.chat.completions.create(model="gpt-x", messages=[])
    """)
    sites = detect_file(f, "o.py")
    assert any(s["node_type"] == "llm-callsite" and s["provider"] == "openai" for s in sites)


def test_generic_fetch_and_requests_are_api_callsites(tmp_path):
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
    s1 = detect_file(f1, "a.ts")
    s2 = detect_file(f2, "b.py")
    assert any(s["node_type"] == "api-callsite" for s in s1)
    assert any(s["node_type"] == "api-callsite" for s in s2)


def test_mcp_tool_call_detected(tmp_path):
    f = _w(tmp_path / "m.py", """
        async def run(session):
            return await session.call_tool("mcp__search__query", {"q": "x"})
    """)
    sites = detect_file(f, "m.py")
    assert any(s["node_type"] == "mcp-callsite" for s in sites)


def test_infra_imports_detected(tmp_path):
    f = _w(tmp_path / "i.py", """
        import redis
        from bullmq import Queue
        import psycopg
        import boto3
    """)
    sites = detect_file(f, "i.py")
    kinds = {s.get("infra_kind") for s in sites if s["node_type"] == "infra-component"}
    assert {"cache", "queue", "db", "object-store"}.issubset(kinds), sites


def test_manifest_parsed_npm_and_pip(tmp_path):
    _w(tmp_path / "package.json", '{"dependencies": {"react": "^19", "redis": "^4"}}')
    _w(tmp_path / "requirements.txt", "anthropic==0.40\nrequests>=2.31\n")
    npm = detect_manifest(tmp_path / "package.json", "package.json")
    pip = detect_manifest(tmp_path / "requirements.txt", "requirements.txt")
    npm_names = {s["raw_ref"] for s in npm}
    pip_names = {s["raw_ref"] for s in pip}
    assert {"react", "redis"}.issubset(npm_names)
    assert {"anthropic", "requests"}.issubset(pip_names)
    assert all(s["node_type"] == "dependency" for s in npm + pip)


def test_no_frequency_fields_anywhere(tmp_path):
    # NON-GOAL GUARD — structure/data-flow only, never observability.
    f = _w(tmp_path / "all.py", """
        import anthropic, redis, requests
        anthropic.Anthropic().messages.create(model="m", messages=[])
        requests.get("https://x.y/z")
    """)
    _w(tmp_path / "pyproject.toml", '[project]\ndependencies = ["openai"]\n')
    payload = (
        detect_file(f, "all.py")
        + detect_manifest(tmp_path / "pyproject.toml", "pyproject.toml")
    )
    for site in payload:
        for k in site.keys():
            assert not _FREQ_RE.search(k), f"frequency-like key leaked: {k}"


def test_detect_file_is_deterministic(tmp_path):
    f = _w(tmp_path / "d.py", """
        import anthropic
        anthropic.Anthropic().messages.create(model="m", messages=[])
        import requests
        requests.post("https://a.b/c")
    """)
    a = detect_file(f, "d.py")
    b = detect_file(f, "d.py")
    assert a == b  # byte-identical, order-stable


def test_syntax_error_file_does_not_crash(tmp_path):
    f = _w(tmp_path / "bad.py", "def (((  :\n")
    assert detect_file(f, "bad.py") == []
