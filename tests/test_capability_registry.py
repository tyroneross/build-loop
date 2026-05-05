"""Tests for the capability registry + shortlist.

Locks priority 1 of the architecture-awareness follow-up.

Two layers:
  1. Registry generation produces valid JSON with entries for every surface
     kind discovered in this repo (agent, skill, command, hook, mcp_tool,
     script). The repo's own structure is the fixture.
  2. The shortlist returns ≤8 entries and is relevance-aware: a Phase-1
     architecture intent surfaces architecture skills/agents at the top.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

# Make scripts/ importable.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_capability_registry as bcr  # type: ignore  # noqa: E402
import capability_shortlist as cs  # type: ignore  # noqa: E402


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def registry() -> dict:
    """Build the registry once per module — fast (no I/O beyond reads)."""
    return bcr.build_registry(REPO)


def test_registry_has_all_surface_kinds(registry: dict) -> None:
    expected = {"agent", "skill", "command", "hook", "mcp_tool", "script"}
    seen = set(registry["counts_by_kind"].keys())
    missing = expected - seen
    assert not missing, f"registry missing kinds: {missing}"


def test_registry_total_is_meaningful(registry: dict) -> None:
    """Build-loop has > 60 surfaces; registry should reflect that."""
    assert registry["total"] >= 60, (
        f"registry only has {registry['total']} entries — crawler may be broken"
    )


def test_every_entry_has_required_fields(registry: dict) -> None:
    required = {
        "name", "kind", "category", "triggers", "tier",
        "tools_consumed", "owns_files", "description", "source_path",
    }
    for e in registry["entries"]:
        missing = required - set(e.keys())
        assert not missing, (
            f"entry {e.get('name')} missing fields {missing}"
        )


def test_categories_are_known(registry: dict) -> None:
    valid_cats = {
        "architecture", "debugging", "validation", "planning", "execution",
        "observability", "memory", "testing", "deployment", "ux-ui",
        "optimization", "meta", "unknown",
    }
    for e in registry["entries"]:
        assert e["category"] in valid_cats, (
            f"unknown category: {e['category']} on {e['name']}"
        )


def test_tiers_are_valid(registry: dict) -> None:
    for e in registry["entries"]:
        assert e["tier"] in ("opus", "sonnet", "haiku", "n/a"), (
            f"unknown tier on {e['name']}: {e['tier']}"
        )


def test_atomic_write_round_trip(tmp_path: Path) -> None:
    out = tmp_path / "reg.json"
    bcr.atomic_write_json(out, {"foo": "bar", "n": 1})
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["foo"] == "bar"
    assert data["n"] == 1


def test_registry_cli_writes_to_default_path(tmp_path: Path) -> None:
    """Smoke-test the CLI on a synthetic minimal repo."""
    # Synthesize a tiny repo with one of each kind.
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "alpha.md").write_text(
        "---\nname: alpha\ndescription: alpha agent\nmodel: sonnet\n---\nbody",
        encoding="utf-8",
    )
    (tmp_path / "skills" / "beta").mkdir(parents=True)
    (tmp_path / "skills" / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: beta skill validates things\n---\n",
        encoding="utf-8",
    )
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "gamma.md").write_text(
        "---\ndescription: gamma command\n---\n",
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"delta": {"command": "echo", "args": ["hi"]}}}),
        encoding="utf-8",
    )
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "true"}]}]}}),
        encoding="utf-8",
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "epsilon.py").write_text(
        '"""epsilon script does memory recall things."""\n',
        encoding="utf-8",
    )

    rc = bcr.main(["--workdir", str(tmp_path)])
    assert rc == 0
    out = tmp_path / ".build-loop" / "capability-registry.json"
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    kinds = data["counts_by_kind"]
    for k in ("agent", "skill", "command", "hook", "mcp_tool", "script"):
        assert k in kinds, f"kind {k} missing from minimal-repo registry"


# ---------------------------------------------------------------------------
# Shortlist tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_registry() -> dict:
    return bcr.build_registry(REPO)


def test_shortlist_caps_at_8(real_registry: dict) -> None:
    out = cs.shortlist(real_registry, phase=1, intent="anything goes here")
    assert out["shortlist_size"] <= cs.SHORTLIST_CAP


def test_shortlist_phase_1_architecture_relevance(real_registry: dict) -> None:
    out = cs.shortlist(real_registry, phase=1,
                       intent="scan architecture and identify blast radius for downstream changes")
    assert out["shortlist_size"] >= 1
    # The top entry should be architecture-related.
    top = out["results"][0]
    assert top["category"] == "architecture", (
        f"phase-1 architecture intent should top-rank an architecture surface, "
        f"got {top['name']} / {top['category']}"
    )


def test_shortlist_phase_5_debugging_relevance(real_registry: dict) -> None:
    out = cs.shortlist(real_registry, phase=5,
                       intent="debug failing tests and diagnose root cause")
    assert out["shortlist_size"] >= 1
    top = out["results"][0]
    assert top["category"] == "debugging", (
        f"phase-5 debugging intent should top-rank a debugging surface, "
        f"got {top['name']} / {top['category']}"
    )


def test_shortlist_kind_filter(real_registry: dict) -> None:
    out = cs.shortlist(real_registry, phase=4,
                       intent="validate the diff", kinds=["agent"])
    for r in out["results"]:
        assert r["kind"] == "agent"


def test_shortlist_falls_back_when_no_intent_match(real_registry: dict) -> None:
    """Even with garbage intent, return some phase-relevant items, never empty."""
    out = cs.shortlist(real_registry, phase=1,
                       intent="zzzz qqqq xyzzy garbage")
    assert out["shortlist_size"] > 0
    # All entries should at least be in a phase-1 primary or secondary category.
    p1 = set(cs.PHASE_CATEGORIES[1]["primary"]) | set(cs.PHASE_CATEGORIES[1]["secondary"])
    assert all(r["category"] in p1 for r in out["results"])


def test_shortlist_stop_words_dont_pollute(real_registry: dict) -> None:
    """`and`, `for`, `the` etc. should not contribute to score."""
    out = cs.shortlist(real_registry, phase=1, intent="and for the with from")
    # All results should fall back to phase-only matching (no intent tokens
    # survived stop-word filtering).
    for r in out["results"]:
        assert all(not reason.startswith("intent:") for reason in r["reasons"]), (
            f"stop word leaked through to {r['name']}: {r['reasons']}"
        )


def test_shortlist_cli_smoke() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "capability_shortlist.py"),
         "--phase", "1", "--intent", "scan architecture",
         "--workdir", str(REPO), "--no-cache", "--json"],
        capture_output=True, text=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["phase"] == 1
    assert data["shortlist_size"] <= cs.SHORTLIST_CAP


def test_cache_into_state_appends(tmp_path: Path) -> None:
    state_path = tmp_path / ".build-loop" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"phase": "test"}), encoding="utf-8")
    fake = {
        "phase": 1, "intent": "scan",
        "results": [{"name": "foo"}, {"name": "bar"}],
    }
    cs.cache_into_state(tmp_path, fake)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "test"  # preserved
    assert "activeCapabilities" in state
    assert state["activeCapabilities"][-1]["shortlist"] == ["foo", "bar"]
