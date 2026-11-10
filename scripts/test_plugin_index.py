#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for plugin_index.py and its capability_shortlist wiring. Stdlib only.

Every test runs against a temporary fake home: two fake Claude plugins, a
Codex config naming one more, and one fake tool on a private PATH. Nothing
reads or writes the real ~/.claude, ~/.codex or ~/.build-loop.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location("plugin_index", HERE / "plugin_index.py")
pi = importlib.util.module_from_spec(_spec)
sys.modules["plugin_index"] = pi
_spec.loader.exec_module(pi)

SECRET_BODY = "BODY-SENTINEL-do-not-index"


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _plugin(root: Path, name: str, desc: str, skills: dict[str, str], agents: dict[str, str] | None = None,
            keywords: list[str] | None = None, mcp: dict | None = None) -> Path:
    _write(root / ".claude-plugin" / "plugin.json", json.dumps(
        {"name": name, "version": "1.0.0", "description": desc, "keywords": keywords or [],
         "author": {"name": "someone"}}))
    for s, d in skills.items():
        _write(root / "skills" / s / "SKILL.md", f"---\nname: {s}\ndescription: {d}\n---\n\n{SECRET_BODY}\n")
    for a, d in (agents or {}).items():
        _write(root / "agents" / f"{a}.md", f"---\nname: {a}\ndescription: {d}\n---\n\n{SECRET_BODY}\n")
    if mcp:
        _write(root / ".mcp.json", json.dumps({"mcpServers": mcp}))
    return root


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cache = home / ".claude" / "plugins" / "cache" / "mkt"
    audit = _plugin(cache / "code-audit" / "1.0.0", "code-audit",
                    "Audit, debug and improve code with root cause investigation.",
                    {"audit": 'Full audit. Triggers on "code audit", "root cause".',
                     "debug": "Debug a live bug."},
                    agents={"root-cause-investigator": "Causal tree analysis."},
                    keywords=["audit", "debugging"],
                    mcp={"audit-mem": {"command": "node", "env": {"TOKEN": "secret-value"}}})
    research = _plugin(cache / "research" / "2.0.0", "research",
                       "Run sourced research and keep the sources.",
                       {"research": 'Use for "research this deeply" and "audit the sources".'})
    _write(home / ".claude" / "plugins" / "installed_plugins.json", json.dumps({"version": 2, "plugins": {
        "code-audit@mkt": [{"installPath": str(audit), "version": "1.0.0"}],
        "research@mkt": [{"installPath": str(research), "version": "2.0.0"}],
    }}))
    # Codex: one enabled plugin with local files, one remote-only, one disabled.
    cx = home / ".codex"
    _plugin(cx / "plugins" / "cache" / "loc" / "research" / "2.0.0", "research",
            "Run sourced research and keep the sources.", {"research": "Research."})
    _write(cx / "config.toml", 'model = "x"\n[plugins."research@loc"]\nenabled = true\n\n'
           '[plugins."remote@cloud"]\nenabled = true\n[plugins."off@loc"]\nenabled = false\n'
           '[mcp_servers.foo]\nenv = { KEY = "secret-value" }\n')
    bindir = tmp_path / "bin"
    tool = _write(bindir / "fakelsp", "#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    tools_file = _write(tmp_path / "tools.json", json.dumps({"tools": [
        {"name": "fakelsp", "purpose": "Symbol lookup tool.", "use_when": ["find references to a symbol"]},
        {"name": "not-installed-tool", "purpose": "Absent.", "use_when": ["never"]},
    ]}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("PATH", str(bindir))
    out = tmp_path / "idx" / "plugin-index.json"
    monkeypatch.setenv("BUILD_LOOP_PLUGIN_INDEX", str(out))
    return {"home": home, "audit": audit, "research": research, "tools": tools_file, "out": out, "bin": bindir}


def _build(fake) -> dict:
    index = pi.build(fake["tools"])
    pi.write_atomic(index, fake["out"])
    return index


def test_build_records_plugins_hosts_and_tools(fake):
    index = _build(fake)
    plugins = [e for e in index["entries"] if e["kind"] == "plugin"]
    assert {(e["host"], e["name"]) for e in plugins} == {
        ("claude", "code-audit"), ("claude", "research"), ("codex", "research")}
    assert index["hosts"]["codex"]["enabled_without_local_files"] == 1
    audit = next(e for e in plugins if e["name"] == "code-audit")
    assert audit["skills"] == ["audit", "debug"]
    assert audit["agents"] == ["root-cause-investigator"]
    assert audit["mcp_servers"] == ["audit-mem"]
    assert audit["use_when"][:2] == ["code audit", "root cause"]
    assert len(audit["summary"]) <= pi.SUMMARY_MAX
    assert set(audit) == {"kind", "host", "name", "marketplace", "version", "install_path", "summary",
                          "use_when", "skills", "agents", "commands", "mcp_servers"}
    tools = {e["name"]: e for e in index["entries"] if e["kind"] == "tool"}
    assert tools["fakelsp"]["present"] is True
    assert tools["not-installed-tool"]["present"] is False


def test_missing_hosts_are_not_found_not_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    index = pi.build(tmp_path / "no-tools.json")
    assert index["hosts"]["claude"]["status"] == "not found"
    assert index["hosts"]["codex"]["status"] == "not found"
    assert index["entries"] == []


def test_no_bodies_or_secrets_copied(fake):
    _build(fake)
    text = fake["out"].read_text(encoding="utf-8")
    assert SECRET_BODY not in text
    assert "secret-value" not in text
    assert "someone" not in text  # manifest author is not a recorded field


def test_query_ranking_and_invoke(fake):
    index = _build(fake)
    res = pi.query(index, "root cause code audit")
    assert res[0]["name"] == "code-audit"
    # The surface whose name matches most query words wins: 2 hits beat 1.
    assert res[0]["invoke"]["agent"] == "code-audit:root-cause-investigator"
    assert pi.query(index, "audit")[0]["invoke"]["skill"] == "code-audit:audit"
    res = pi.query(index, "research sources")
    assert res[0]["name"] == "research"
    assert [r["name"] for r in res].count("research") == 1  # deduped across hosts
    res = pi.query(index, "find references symbol")
    assert res[0]["kind"] == "tool" and res[0]["invoke"]["binary"] == "fakelsp"
    assert all(r["name"] != "not-installed-tool" for r in pi.query(index, "never absent"))
    assert len(pi.query(index, "audit research symbol debug code root", cap=8)) <= 8


def test_size_cap_trims(fake):
    index = pi.build(fake["tools"], max_bytes=2500)
    assert index["trimmed"] is True
    assert len(json.dumps(index).encode()) <= 2500 or all(
        not e.get(k) for e in index["entries"] for k in ("skills", "agents", "commands", "mcp_servers"))


def test_check_goes_stale_after_manifest_change(fake):
    _build(fake)
    assert pi.staleness(fake["out"], fake["tools"]) == []
    manifest = fake["audit"] / ".claude-plugin" / "plugin.json"
    data = json.loads(manifest.read_text())
    data["description"] = "Changed description, longer than before."
    manifest.write_text(json.dumps(data))
    reasons = pi.staleness(fake["out"], fake["tools"])
    assert any("manifest:claude:code-audit@mkt" in r for r in reasons)
    assert pi.main(["check", "--out", str(fake["out"]), "--tools-file", str(fake["tools"])]) == 3


def test_check_goes_stale_when_tool_disappears(fake):
    _build(fake)
    (fake["bin"] / "fakelsp").unlink()
    assert any("tool:fakelsp" in r for r in pi.staleness(fake["out"], fake["tools"]))


def test_planted_defect_stale_index_fails_check(fake, capsys):
    """Planted defect: a cached index whose fingerprint no longer matches must exit 3."""
    index = _build(fake)
    assert pi.main(["check", "--out", str(fake["out"]), "--tools-file", str(fake["tools"])]) == 0
    index["fingerprint"] = "0" * 64
    index["fingerprint_parts"]["host:claude"] = "planted"
    fake["out"].write_text(json.dumps(index))
    assert pi.main(["check", "--out", str(fake["out"]), "--tools-file", str(fake["tools"])]) == 3
    assert "stale" in capsys.readouterr().out
    fake["out"].unlink()
    assert pi.main(["check", "--out", str(fake["out"]), "--tools-file", str(fake["tools"])]) == 3


def test_query_rebuilds_when_stale(fake):
    assert not fake["out"].exists()
    index = pi.ensure_fresh(fake["out"], fake["tools"])
    assert fake["out"].exists() and index["entries"]
    assert pi.staleness(fake["out"], fake["tools"]) == []


def test_verify_on_use_drops_removed_plugin(fake, capsys):
    index = _build(fake)
    shutil.rmtree(fake["audit"])
    res = pi.query(index, "root cause code audit")
    assert all(r["name"] != "code-audit" for r in res)
    assert "dropped plugin code-audit" in capsys.readouterr().err


# ---- capability_shortlist wiring --------------------------------------------

def _registry(entries):
    return {"entries": entries, "total": len(entries)}


def test_shortlist_includes_matching_installed_plugin(fake, tmp_path):
    import capability_shortlist as cs
    _build(fake)
    workdir = tmp_path / "repo"
    workdir.mkdir()
    installed = cs.load_installed("research sources", workdir, fake["out"])
    reg = _registry([{"name": "unrelated", "kind": "script", "category": "meta",
                      "description": "does other things", "triggers": []}])
    out = cs.shortlist(reg, 2, "research sources", workdir=workdir, installed=installed)
    hit = next(r for r in out["results"] if r["name"] == "research")
    assert hit["kind"] == "plugin" and hit["invoke"]["skill"] == "research:research"
    assert sum(1 for r in out["results"] if r["name"] == "research") == 1  # one collapsed entry
    assert out["shortlist_size"] <= cs.SHORTLIST_CAP


def test_shortlist_in_repo_surface_outranks_installed_on_tie(fake, tmp_path):
    import capability_shortlist as cs
    _build(fake)
    workdir = tmp_path / "repo"
    workdir.mkdir()
    installed = cs.load_installed("research", workdir, fake["out"])
    # Category "installed" gets no phase bonus, so give the in-repo entry a
    # non-phase category too: both then score exactly the intent match.
    local = {"name": "zz-local-research", "kind": "skill", "category": "none",
             "description": "research helper", "triggers": [], "source_path": "skills/x/SKILL.md"}
    out = cs.shortlist(_registry([local]), 2, "research", workdir=workdir, installed=installed)
    names = [r["name"] for r in out["results"]]
    scores = {r["name"]: r["score"] for r in out["results"]}
    assert scores["zz-local-research"] == scores["research"]
    assert names.index("zz-local-research") < names.index("research")


def test_shortlist_skips_its_own_plugin(fake, tmp_path):
    import capability_shortlist as cs
    _build(fake)
    workdir = tmp_path / "repo"
    _write(workdir / ".claude-plugin" / "plugin.json", json.dumps({"name": "research"}))
    installed = cs.load_installed("research sources", workdir, fake["out"])
    assert all(e["name"] != "research" for e in installed)
