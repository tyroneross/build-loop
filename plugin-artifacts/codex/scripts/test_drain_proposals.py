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


# --- content-keyed decisions -------------------------------------------------
# Producers re-emit one finding under a fresh datestamped filename every run and
# the daily dedup pass archives the superseded copy. A path-derived key voided the
# decision on every cycle; these pin the repaired behaviour.

SELF_REVIEW = "**Kind**: `self_missing_test`\n\n## Finding: No test file for widget.py\n"
AUTO_FINDING = "---\nid: X\nfinding_hash: abc123\n---\n\n## Finding (no severity)\nbody\n"


def _proposals(fake_world):
    return fake_world["repo_a"] / ".build-loop" / "proposals"


def _item_by_id(state, item_id):
    return next(i for i in dp.build_digest(state)["items"] if i["id"] == item_id)


def test_decision_survives_reemission_under_new_filename(fake_world):
    """The defect this repair closes: same finding, new filename, decision holds."""
    state, pdir = fake_world["state"], _proposals(fake_world)
    (pdir / "self-review-2026-07-19-95-missing-test.md").write_text(SELF_REVIEW)
    first = _item_by_id(state, "self-review-2026-07-19-95-missing-test")
    assert dp.cmd_set(dp.argparse.Namespace(
        state_dir=str(state), key=first["key"], status="apply", note="fixed")) == 0

    # The producer re-emits; the dedup pass archives the old copy.
    (pdir / "self-review-2026-07-19-95-missing-test.md").unlink()
    (pdir / "self-review-2026-08-30-145-missing-test.md").write_text(SELF_REVIEW)

    reemitted = _item_by_id(state, "self-review-2026-08-30-145-missing-test")
    assert reemitted["key"] == first["key"]
    assert reemitted["status"] == "applied"  # would be "new" under path keys


def test_finding_hash_frontmatter_is_the_identity(fake_world):
    """auto-finding-sweep already stamps finding_hash; reuse it, don't mint another."""
    state, pdir = fake_world["state"], _proposals(fake_world)
    (pdir / "auto-finding-20260824T205114Z-abc123.md").write_text(AUTO_FINDING)
    (pdir / "auto-finding-20260830T101010Z-abc123.md").write_text(AUTO_FINDING)
    items = [i for i in dp.build_digest(state)["items"] if "auto-finding" in i["path"]]
    assert len(items) == 1, "same finding_hash must collapse to one decidable row"
    assert items[0]["duplicate_count"] == 2


def test_identical_finding_in_two_repos_stays_separately_decidable(fake_world):
    """146 clusters span repos and each needs its own fix — never collapse across."""
    state = fake_world["state"]
    repo_a = _proposals(fake_world)
    repo_b = repo_a.parent.parent.parent / "repo-b" / ".build-loop" / "proposals"
    (repo_a / "shared.md").write_text(SELF_REVIEW)
    (repo_b / "shared.md").write_text(SELF_REVIEW)
    keys = {i["key"] for i in dp.build_digest(state)["items"] if i["path"].endswith("shared.md")}
    assert len(keys) == 2


def test_file_without_producer_identity_keeps_its_path_key(fake_world):
    """No content key derivable -> unchanged behaviour, so old decisions survive."""
    state, pdir = fake_world["state"], _proposals(fake_world)
    p = pdir / "plain.md"
    p.write_text("# Just a heading\nno producer identity here\n")
    item = _item_by_id(state, "plain")
    assert item["key"] == dp._stable_key("repo-a", p) == item["legacy_key"]


def test_migrate_rekeys_path_decision_onto_content_key(fake_world):
    state, pdir = fake_world["state"], _proposals(fake_world)
    p = pdir / "self-review-2026-07-19-95-missing-test.md"
    p.write_text(SELF_REVIEW)
    item = _item_by_id(state, "self-review-2026-07-19-95-missing-test")
    legacy = {item["legacy_key"]: {"status": "applied", "decided_at": "2026-07-17", "note": "n"}}
    report = dp.migrate_state(legacy, dp._collect_items())
    assert item["key"] in report["state"]
    assert report["state"][item["key"]]["note"] == "n"
    assert report["remapped"] == [item["legacy_key"]]


def test_migrate_keeps_orphaned_decisions(fake_world):
    """The file is gone, so identity is unrecoverable — keep it, never discard."""
    legacy = {"deadbeefdeadbeef": {"status": "applied", "decided_at": "2026-07-17", "note": "n"}}
    report = dp.migrate_state(legacy, dp._collect_items())
    assert report["orphaned"] == ["deadbeefdeadbeef"]
    assert "deadbeefdeadbeef" in report["state"]


def test_migrate_dry_run_does_not_write(fake_world):
    state = fake_world["state"]
    state.mkdir(parents=True, exist_ok=True)
    (state / "drain-state.json").write_text(json.dumps({"deadbeefdeadbeef": {"status": "applied"}}))
    assert dp.cmd_migrate(dp.argparse.Namespace(state_dir=str(state), apply=False)) == 0
    assert not (state / "drain-state.backup.json").exists()


def test_migrate_apply_backs_up_before_writing(fake_world):
    state = fake_world["state"]
    state.mkdir(parents=True, exist_ok=True)
    before = {"deadbeefdeadbeef": {"status": "applied", "decided_at": "2026-07-17", "note": "n"}}
    (state / "drain-state.json").write_text(json.dumps(before))
    assert dp.cmd_migrate(dp.argparse.Namespace(state_dir=str(state), apply=True)) == 0
    assert json.loads((state / "drain-state.backup.json").read_text()) == before
