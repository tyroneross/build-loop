# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for drain_proposals.py — cross-repo proposal drain gate."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import drain_proposals as dp  # noqa: E402


@pytest.fixture()
def fake_world(tmp_path, monkeypatch):
    """A fake memory-root with a registry pointing at two repos + an assistant queue."""
    mem = tmp_path / "memory"
    (mem / "registry").mkdir(parents=True)
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for r in (repo_a, repo_b):
        (r / ".build-loop" / "proposals" / "enforce-from-retro").mkdir(parents=True)
    (repo_a / ".build-loop" / "proposals" / "p1.md").write_text(
        "<!-- proposal_id: A1 -->\n# First finding\nbody\n"
    )
    (repo_a / ".build-loop" / "proposals" / "enforce-from-retro" / "e1.md").write_text(
        "---\nproposal_id: E1\nstatus: proposed\n---\n# Enforce candidate\n"
    )
    (repo_b / ".build-loop" / "proposals" / "done.md").write_text(
        "---\nid: B1\nstatus: applied\n---\n# Already applied upstream\n"
    )
    (mem / "registry" / "registry.json").write_text(json.dumps({
        "repos": [
            {"name": "repo-a", "path": str(repo_a)},
            {"name": "repo-b", "path": str(repo_b)},
        ]
    }))
    assistant = tmp_path / "home" / ".assistant" / "proposals"
    assistant.mkdir(parents=True)
    (assistant / "routing.md").write_text("# Routing refinement\n")

    monkeypatch.setenv("BUILD_LOOP_MEMORY_ROOT", str(mem))
    monkeypatch.setattr(dp.Path, "home", staticmethod(lambda: tmp_path / "home"))
    return {"state": tmp_path / "state", "repo_a": repo_a}


def test_scan_collects_all_sources(fake_world):
    digest = dp.build_digest(fake_world["state"])
    ids = {i["id"] for i in digest["items"]}
    assert {"A1", "E1", "B1"} <= ids
    assert any(i["repo"] == "ai-assistant" for i in digest["items"])


def test_enforce_from_retro_included(fake_world):
    digest = dp.build_digest(fake_world["state"])
    assert any("enforce-from-retro" in i["path"] for i in digest["items"])


def test_upstream_applied_status_respected(fake_world):
    digest = dp.build_digest(fake_world["state"])
    b1 = next(i for i in digest["items"] if i["id"] == "B1")
    assert b1["status"] == "applied"  # body says applied -> not surfaced as new


def test_decision_persists_no_resurface(fake_world, monkeypatch):
    state = fake_world["state"]
    digest = dp.build_digest(state)
    a1 = next(i for i in digest["items"] if i["id"] == "A1")
    args = dp.argparse.Namespace(state_dir=str(state), key=a1["key"],
                                 status="apply", note="x")
    assert dp.cmd_set(args) == 0
    digest2 = dp.build_digest(state)
    a1b = next(i for i in digest2["items"] if i["id"] == "A1")
    assert a1b["status"] == "applied"


def test_never_auto_applies(fake_world):
    """scan/list must never mutate a proposal's on-disk state."""
    digest = dp.build_digest(fake_world["state"])
    # Fresh items with no decision + no upstream marker stay "new".
    a1 = next(i for i in digest["items"] if i["id"] == "A1")
    assert a1["status"] == "new"


def test_set_unknown_key_returns_1(fake_world):
    args = dp.argparse.Namespace(state_dir=str(fake_world["state"]), key="nope",
                                 status="apply", note="")
    assert dp.cmd_set(args) == 1
