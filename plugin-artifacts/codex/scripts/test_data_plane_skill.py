# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Activation and packaging tests for the data-plane-worktrees skill."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import build_capability_registry as registry  # noqa: E402


def _read(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def test_skill_is_internal_concise_and_trigger_complete() -> None:
    text = _read("skills/data-plane-worktrees/SKILL.md")

    assert "name: build-loop:data-plane-worktrees" in text
    assert "user-invocable: false" in text
    assert len(text.splitlines()) <= 200
    for trigger in (
        "SQLite",
        "PostgreSQL",
        "generated search/vector indexes",
        "Docker volumes",
        "mutable file trees",
        "external cloud/account namespaces",
    ):
        assert trigger in text


def test_skill_encodes_the_four_isolation_modes_and_nonforce_cleanup_rule() -> None:
    text = _read("skills/data-plane-worktrees/SKILL.md")

    for mode in (
        "per_worktree",
        "shared_readonly",
        "shared_serialized",
        "external_namespaced",
    ):
        assert mode in text
    assert "normal non-force `git worktree remove` fail" in text
    for command in (" add ", " validate ", " close ", " terminal "):
        assert command in text


def test_build_loop_routes_to_skill_from_assess_and_public_codex_entrypoint() -> None:
    paths = (
        "agents/build-orchestrator.md",
        "skills/build-loop/SKILL.md",
        "skills/build-loop/references/capability-routing.md",
        "skills/build-loop/references/phase-1-assess.md",
        "codex-skills/build-loop/SKILL.md",
    )
    for path in paths:
        text = _read(path)
        assert "data-plane-worktrees" in text, path
    phase_one = _read("skills/build-loop/references/phase-1-assess.md")
    assert "state.json.triggers.dataPlaneWorktree: true" in phase_one


def test_capability_registry_discovers_the_skill() -> None:
    built = registry.build_registry(_ROOT)
    matches = [
        entry
        for entry in built["entries"]
        if entry.get("source_path") == "skills/data-plane-worktrees/SKILL.md"
    ]

    assert len(matches) == 1
    assert matches[0]["name"] == "build-loop:data-plane-worktrees"


def test_openai_metadata_keeps_implicit_routing_prompt() -> None:
    text = _read("skills/data-plane-worktrees/agents/openai.yaml")

    assert 'display_name: "Data-Plane Worktrees"' in text
    assert "$data-plane-worktrees" in text
