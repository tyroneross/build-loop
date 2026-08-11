"""Tests for the self-review proposal drain.

The drain's job is to collapse a producer-only queue into an actionable set.
Its failure modes are asymmetric: dropping a real finding is much worse than
keeping a stale one, so the tests below concentrate on what gets DROPPED and
on the ordering guarantee that decides which duplicate survives.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drain_self_review_proposals as drain_mod  # noqa: E402


def _write(dirpath: Path, name: str, *, finding: str, kind: str,
           severity: str = "LOW", classify: str = "SAFE",
           created: str = "2026-07-01T00:00:00+00:00", evidence: str = "") -> Path:
    body = f"""---
source: self-review
mode: deep
severity: {severity}
classify_hint: {classify}
created_ts: {created}
target: self
---
## Finding: {finding}

**Kind**: `{kind}`
**Severity**: {severity}

### Evidence

```
{evidence}
```
"""
    p = dirpath / name
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture()
def proposals(tmp_path: Path) -> Path:
    d = tmp_path / ".build-loop" / "proposals"
    d.mkdir(parents=True)
    return d


def test_collapses_repeated_emissions_of_one_finding(proposals: Path):
    for i in range(5):
        _write(proposals, f"p{i}.md", finding="No test file for foo.py",
               kind="self_missing_test", created=f"2026-07-0{i+1}T00:00:00+00:00",
               evidence="script='/nope/foo.py' expected_test='/nope/test_foo.py'")
    r = drain_mod.drain(proposals)
    assert r["total_files"] == 5
    assert r["unique_findings"] == 1
    assert len(r["superseded"]) == 4


def test_newest_emission_is_the_survivor(proposals: Path):
    _write(proposals, "old.md", finding="dup", kind="self_oversized_file",
           created="2026-01-01T00:00:00+00:00")
    _write(proposals, "new.md", finding="dup", kind="self_oversized_file",
           created="2026-07-01T00:00:00+00:00")
    r = drain_mod.drain(proposals)
    survivors = [p["path"].name for p in r["actionable"]]
    assert survivors == ["new.md"], "the most recent emission must be the one kept"


def test_distinct_findings_are_never_merged(proposals: Path, tmp_path: Path):
    # Source scripts must actually exist, else the drain correctly marks both
    # findings stale ("source script deleted") and there is nothing actionable
    # left to assert the non-merging behavior against.
    for name in ("a", "b"):
        (tmp_path / f"{name}.py").write_text("x = 1")
        _write(proposals, f"{name}.md", finding=f"No test file for {name}.py",
               kind="self_missing_test",
               evidence=f"script='{tmp_path / f'{name}.py'}' "
                        f"expected_test='{tmp_path / f'test_{name}.py'}'")
    r = drain_mod.drain(proposals)
    assert r["unique_findings"] == 2
    assert len(r["actionable"]) == 2


def test_same_title_under_different_kinds_stays_separate(proposals: Path):
    _write(proposals, "a.md", finding="'x.py' is a problem", kind="self_oversized_file")
    _write(proposals, "b.md", finding="'x.py' is a problem", kind="self_complexity_high_complexity")
    r = drain_mod.drain(proposals)
    assert r["unique_findings"] == 2, "kind is part of finding identity"


def test_missing_test_finding_drops_once_the_test_exists(proposals: Path, tmp_path: Path):
    script = tmp_path / "real.py"
    script.write_text("x = 1")
    test = tmp_path / "test_real.py"
    test.write_text("def test_x(): pass")
    _write(proposals, "p.md", finding="No test file for real.py", kind="self_missing_test",
           evidence=f"script='{script}' expected_test='{test}'")
    r = drain_mod.drain(proposals)
    assert len(r["stale"]) == 1
    assert not r["actionable"]
    assert "test now exists" in r["stale"][0]["reason"]


def test_missing_test_finding_survives_while_the_test_is_absent(proposals: Path, tmp_path: Path):
    script = tmp_path / "real.py"
    script.write_text("x = 1")
    _write(proposals, "p.md", finding="No test file for real.py", kind="self_missing_test",
           evidence=f"script='{script}' expected_test='{tmp_path / 'test_real.py'}'")
    r = drain_mod.drain(proposals)
    assert len(r["actionable"]) == 1, "a still-true finding must never be dropped"
    assert not r["stale"]


def test_deleted_source_script_makes_the_finding_stale(proposals: Path, tmp_path: Path):
    _write(proposals, "p.md", finding="No test file for gone.py", kind="self_missing_test",
           evidence=f"script='{tmp_path / 'gone.py'}' expected_test='{tmp_path / 'test_gone.py'}'")
    r = drain_mod.drain(proposals)
    assert len(r["stale"]) == 1
    assert "source script deleted" in r["stale"][0]["reason"]


def test_churn_findings_are_dropped_as_advisory(proposals: Path):
    _write(proposals, "p.md", finding="'scripts/backlog.py' changed 7 times in 7d",
           kind="high_churn_file", classify="RISKY")
    r = drain_mod.drain(proposals)
    assert len(r["non_actionable"]) == 1
    assert not r["actionable"]


def test_generated_artifacts_are_dropped_regardless_of_kind(proposals: Path):
    _write(proposals, "p.md", finding="'architecture/model.json' is oversized",
           kind="self_oversized_file")
    r = drain_mod.drain(proposals)
    assert len(r["non_actionable"]) == 1, "build products are not authored source"


def test_authored_source_inside_build_loop_worktree_is_not_generated(proposals: Path, tmp_path: Path):
    script = tmp_path / ".build-loop" / "worktrees" / "run-1" / "scripts" / "worker.py"
    script.parent.mkdir(parents=True)
    script.write_text("x = 1", encoding="utf-8")
    _write(
        proposals,
        "p.md",
        finding="No test file for worker.py",
        kind="self_missing_test",
        evidence=f"script='{script}' expected_test='{script.parent / 'test_worker.py'}'",
    )
    result = drain_mod.drain(proposals)
    assert len(result["buckets"]["auto-fixable"]) == 1


def test_routing_sends_each_kind_to_its_lane(proposals: Path, tmp_path: Path):
    _write(proposals, "t.md", finding="No test file for z.py", kind="self_missing_test",
           evidence=f"script='{__file__}' expected_test='{tmp_path / 'test_z.py'}'")
    _write(proposals, "c.md", finding="Recurring user correction (x3)",
           kind="user_correction_cluster")
    _write(proposals, "b.md", finding="repeated bash ritual", kind="bash_ritual_candidate")
    _write(proposals, "j.md", finding="'q.py' is complex", kind="self_complexity_high_complexity",
           classify="RISKY")
    r = drain_mod.drain(proposals)
    got = {k: len(v) for k, v in r["buckets"].items()}
    assert got.get("auto-fixable") == 1
    assert got.get("memory") == 1
    assert got.get("skill-candidate") == 1
    assert got.get("needs-judgment") == 1


def test_archive_moves_and_never_deletes(proposals: Path):
    for i in range(3):
        _write(proposals, f"p{i}.md", finding="dup", kind="self_oversized_file",
               created=f"2026-07-0{i+1}T00:00:00+00:00")
    r = drain_mod.drain(proposals)
    moved = drain_mod.archive(r, proposals, "teststamp")
    assert moved == 2
    archived = list((proposals / "archive" / "teststamp").rglob("*.md"))
    assert len(archived) == 2, "superseded files must still exist after archiving"
    assert len(list(proposals.glob("*.md"))) == 1


def test_empty_queue_is_not_an_error(proposals: Path):
    r = drain_mod.drain(proposals)
    assert r["total_files"] == 0
    assert r["actionable"] == []


def test_unparseable_file_does_not_crash_the_drain(proposals: Path):
    (proposals / "junk.md").write_text("not a proposal at all", encoding="utf-8")
    r = drain_mod.drain(proposals)
    assert r["total_files"] == 1  # degrades to filename-derived identity
