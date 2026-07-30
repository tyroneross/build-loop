"""Static-analysis tests for the architecture-scout agent definition (Chunk 5).

Parses ``agents/architecture-scout.md`` and asserts:

* Frontmatter has ``model: sonnet`` and the exact tool set ``{Read, Grep, Glob, Bash}``.
* Body length (excluding frontmatter) is <= 200 lines.
* All 5 task playbooks exist (``baseline``, ``chunk-impact``, ``review-rules``,
  ``iterate-subgraph``, ``learn-sync``).
* Output envelope template documents required keys.
* Orchestrator references the scout in 5+ wiring lines (Phase 1, 2, 4-D, 5, 6).
* Implementer-style agents (``implementer``, ``mock-scanner``, ``database-assessor``,
  ``fact-checker``) carry an ``architecture_context`` directive block.

These are pure static-analysis assertions — no agent execution. Per build-loop's
``plugin-tests`` static tier, this is the deterministic floor; runtime
validation (envelope size, latency) lives in the manual smoke test in the
chunk's verification checklist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - PyYAML is a build-loop test-time dep
    yaml = None  # noqa: N816


_REPO = Path(__file__).resolve().parents[2]
_AGENT = _REPO / "agents" / "architecture-scout.md"
_ORCH = _REPO / "agents" / "build-orchestrator.md"


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_yaml, body) given a markdown file text."""
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise AssertionError("agent file missing YAML frontmatter")
    return parts[1], parts[2]


def test_scout_file_exists():
    assert _AGENT.exists(), f"missing: {_AGENT}"


def test_scout_frontmatter_model_and_tools():
    if yaml is None:
        pytest.skip("PyYAML not available")
    fm, _body = _split_frontmatter(_AGENT.read_text())
    data = yaml.safe_load(fm)
    assert data["model"] == "sonnet", f"expected sonnet, got {data['model']!r}"
    assert set(data["tools"]) == {"Read", "Grep", "Glob", "Bash"}, (
        f"unexpected tools: {data['tools']!r}"
    )
    # Name should be the qualified plugin form (per plan & build-loop conventions).
    assert data["name"] == "build-loop:architecture-scout"


def test_scout_body_under_200_lines():
    _fm, body = _split_frontmatter(_AGENT.read_text())
    body_lines = body.strip().splitlines()
    assert len(body_lines) <= 200, (
        f"body is {len(body_lines)} lines (>200); concision rule violated"
    )


def test_scout_has_all_five_task_playbooks():
    body = _AGENT.read_text()
    expected = ["baseline", "chunk-impact", "review-rules", "iterate-subgraph", "learn-sync"]
    for task in expected:
        # Headings or table rows reference each task type.
        assert task in body, f"missing task playbook for {task!r}"


def test_scout_documents_output_envelope():
    body = _AGENT.read_text()
    for key in ['"task"', '"summary"', '"findings"', '"side_effects"', '"schema_version"']:
        assert key in body, f"output envelope missing key {key}"


def test_scout_declares_native_vs_navgator_decision_rule():
    body = _AGENT.read_text()
    assert "Native vs NavGator" in body or "native vs NavGator" in body
    assert "navgator" in body.lower()
    assert "native" in body.lower()


def test_scout_explicitly_read_only():
    """Scout must not list Edit or Write in its tools."""
    if yaml is None:
        pytest.skip("PyYAML not available")
    fm, _body = _split_frontmatter(_AGENT.read_text())
    data = yaml.safe_load(fm)
    forbidden = {"Edit", "Write"}
    assert not (set(data["tools"]) & forbidden), (
        f"scout tools include forbidden write tools: {data['tools']!r}"
    )


def test_orchestrator_wires_scout_at_five_or_more_points():
    text = _ORCH.read_text()
    hits = text.count("architecture-scout")
    assert hits >= 5, f"expected >=5 wiring references, found {hits}"


def test_implementer_style_agents_accept_architecture_context():
    targets = [
        _REPO / "agents" / "implementer.md",
        _REPO / "agents" / "mock-scanner.md",
        _REPO / "agents" / "database-assessor.md",
        _REPO / "agents" / "fact-checker.md",
    ]
    for t in targets:
        assert t.exists(), f"missing agent file: {t}"
        text = t.read_text()
        assert "architecture_context" in text, (
            f"{t.name} does not document architecture_context block"
        )
