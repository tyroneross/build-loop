# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Colocated tests for ``scripts/enforce_retro_signals.py``.

Run under ``env -u PYTHONPATH python3 -m pytest scripts/test_enforce_retro_signals.py``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Import as a module under scripts/ — conftest.py already puts scripts/ on
# sys.path for the broader suite. Use the package-style import to be safe.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import enforce_retro_signals as ers  # noqa: E402


def _write_candidate(dirp: Path, run_id: str, seq: int, candidate_text: str) -> Path:
    """Write a retro-shaped enforce-candidate file."""
    p = dirp / f"{run_id}-{seq:02d}.md"
    body = (
        f"# Enforce candidate — {run_id} #{seq}\n\n"
        f"_Source: post-push retrospective (2026-06-06)_\n\n"
        f"## Candidate\n\n{candidate_text}\n\n"
        f"## Disposition\n\n"
        f"- [ ] Adopt as default in build-loop\n"
        f"- [ ] Route to Phase 6 Learn as A/B experiment\n"
        f"- [ ] Reject — note reason below\n"
    )
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    """Empty workdir with the proposals dir not yet created."""
    return tmp_path


def test_empty_dir_returns_zero_patterns(workdir: Path) -> None:
    """No proposals dir at all → envelope with scannedFiles=0, patterns=[]."""
    out = ers.scan(workdir)
    # The skip counters are additive: the detector splices patterns[], so a
    # superset envelope is safe, and a skipped-count that is never reported
    # would be the same silent drop these keys exist to prevent.
    assert out == {
        "scannedFiles": 0,
        "dispositionedSkipped": 0,
        "placeholderSkipped": 0,
        "patterns": [],
    }


def test_one_run_only_does_not_cross_threshold(workdir: Path) -> None:
    """A single run with one or more candidates does NOT emit a pattern.

    Threshold is recurrence across DISTINCT run-ids (>= 2).
    """
    d = workdir / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    _write_candidate(d, "runA", 1, "Always commit at end of chunk")
    _write_candidate(d, "runA", 2, "Always commit at end of chunk")  # same run, dup

    out = ers.scan(workdir)
    assert out["scannedFiles"] == 2
    assert out["patterns"] == []


def test_recurrence_across_two_runs_emits_one_pattern(workdir: Path) -> None:
    """Three files across two run-ids with the same normalized signature
    yields one pattern with count=2 (distinct run-ids)."""
    d = workdir / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    _write_candidate(d, "runA", 1, "Always commit at end of chunk")
    _write_candidate(d, "runA", 2, "ALWAYS  commit at end   of chunk")  # whitespace + case
    _write_candidate(d, "runB", 1, "Always commit at end of chunk")
    # A second, distinct signature in only one run (should not surface)
    _write_candidate(d, "runA", 3, "Verify peer merge status before warning")

    out = ers.scan(workdir)
    assert out["scannedFiles"] == 4
    assert len(out["patterns"]) == 1
    pat = out["patterns"][0]
    assert pat["type"] == "enforce_recurrence"
    assert pat["count"] == 2  # distinct run-ids
    assert pat["confidence"] == "medium"  # 2-3 runs => medium
    assert pat["signature"].startswith("always commit at end of chunk")
    assert pat["proposal"]["skillSkeleton"]["name"].startswith("enforce-")
    # Evidence carries up to 5 entries each with date + detail + run_id + file
    assert 1 <= len(pat["evidence"]) <= 5
    for ev in pat["evidence"]:
        assert "date" in ev and "detail" in ev and "run_id" in ev


def test_high_confidence_at_four_distinct_runs(workdir: Path) -> None:
    """At 4 distinct run-ids the confidence ratchets to ``high``."""
    d = workdir / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for run in ("runA", "runB", "runC", "runD"):
        _write_candidate(d, run, 1, "Verify peer merge status before warning")

    out = ers.scan(workdir)
    assert len(out["patterns"]) == 1
    assert out["patterns"][0]["count"] == 4
    assert out["patterns"][0]["confidence"] == "high"


def test_malformed_files_are_silently_skipped(workdir: Path) -> None:
    """A file with no ``## Candidate`` section, a non-md file, and a file
    that does not match the ``<run-id>-<NN>.md`` naming all skip silently."""
    d = workdir / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    # No `## Candidate` heading
    (d / "runA-01.md").write_text("# Some title\n\nNo candidate here.\n", encoding="utf-8")
    # Wrong extension
    (d / "runB-01.txt").write_text("ignored", encoding="utf-8")
    # Doesn't match naming
    (d / "garbage.md").write_text("## Candidate\n\nignored\n", encoding="utf-8")
    # An empty `## Candidate` body
    (d / "runC-01.md").write_text("## Candidate\n\n\n## Disposition\n\n", encoding="utf-8")

    out = ers.scan(workdir)
    # Only runA-01.md and runC-01.md scan (both match the naming pattern); both
    # contribute zero usable candidates → no patterns emitted.
    assert out["scannedFiles"] == 2
    assert out["patterns"] == []


def test_cli_emits_json_envelope(tmp_path: Path) -> None:
    """The CLI form prints a JSON envelope to stdout, exit 0."""
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    _write_candidate(d, "runA", 1, "Adopt this")
    _write_candidate(d, "runB", 1, "Adopt this")

    script = Path(__file__).resolve().parent / "enforce_retro_signals.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--workdir", str(tmp_path), "--json"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    envelope = json.loads(proc.stdout)
    assert envelope["scannedFiles"] == 2
    assert len(envelope["patterns"]) == 1
    assert envelope["patterns"][0]["count"] == 2


def test_run_id_with_hyphens_is_preserved(workdir: Path) -> None:
    """Run-ids contain hyphens (e.g. `learn-mandatory-20260606-0106`).
    The parser splits on the trailing `-<digits>.md` only."""
    d = workdir / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    _write_candidate(d, "learn-mandatory-20260606-0106", 1, "A signal text")
    _write_candidate(d, "another-run-99", 1, "A signal text")

    out = ers.scan(workdir)
    assert out["scannedFiles"] == 2
    assert len(out["patterns"]) == 1
    # Distinct run-ids, not file-count
    assert out["patterns"][0]["count"] == 2


def _candidate(text: str, *, disposed_box: bool = False, status: str | None = None) -> str:
    fm = f"---\nstatus: {status}\n---\n\n" if status else ""
    box = "- [x] Adopt as default in build-loop" if disposed_box else "- [ ] Adopt as default in build-loop"
    return f"{fm}# Enforce candidate\n\n## Candidate\n\n{text}\n\n## Disposition\n\n{box}\n"


def test_dispositioned_candidates_do_not_count_toward_recurrence(tmp_path):
    """The defect this closes: a 2026-07-08 triage marked 6 of 13 candidates DONE
    in a separate doc and left the files in place, so the closed
    inline-self-verification candidate stayed the STRONGEST signal in the queue
    (4 distinct run-ids) six weeks later — closed work outranking open work."""
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for run in ("run-aaa", "run-bbb"):
        (d / f"{run}-01.md").write_text(_candidate("enforce gate: already-closed", disposed_box=True))
    out = ers.scan(tmp_path)
    assert out["patterns"] == [], "a dispositioned candidate still counted"
    assert out["dispositionedSkipped"] == 2
    assert out["scannedFiles"] == 2, "skipped files must still be counted as scanned"


def test_undispositioned_candidates_still_recur(tmp_path):
    """Mutation check the other way: if this fails, the skip has silenced the
    whole scanner rather than only closed items."""
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for run in ("run-aaa", "run-bbb"):
        (d / f"{run}-01.md").write_text(_candidate("enforce gate: genuinely open"))
    out = ers.scan(tmp_path)
    assert len(out["patterns"]) == 1, out
    assert out["patterns"][0]["count"] == 2
    assert out["dispositionedSkipped"] == 0


def test_terminal_frontmatter_status_also_disposes(tmp_path):
    """Hand-written proposals carry `status:` instead of a checklist."""
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for run in ("run-aaa", "run-bbb"):
        (d / f"{run}-01.md").write_text(_candidate("enforce gate: closed via frontmatter", status="done"))
    assert ers.scan(tmp_path)["patterns"] == []


def test_proposed_status_is_not_terminal(tmp_path):
    """`status: proposed` is the OPEN state — it must keep counting."""
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for run in ("run-aaa", "run-bbb"):
        (d / f"{run}-01.md").write_text(_candidate("enforce gate: still open", status="proposed"))
    assert len(ers.scan(tmp_path)["patterns"]) == 1


PLACEHOLDER_TEXT = "Enforce gate: rule (failed this run)"


def test_placeholder_candidates_do_not_count_toward_recurrence(tmp_path):
    """The defect this closes (ross-labs-astro, 2026-09-12).

    Two run-ids — `session-20b35f7a-...` and `session-transcript-normalized` —
    each wrote a candidate whose entire body was the unresolved template
    `Enforce gate: rule (failed this run)`. Identical text across 2 distinct
    run-ids is exactly this scanner's threshold, so Phase 6 Learn raised work
    order learn-4290d6ae4098 against a candidate that names no gate.
    """
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for run in ("session-20b35f7a-2916-4b0e-972e-f716ce85b273",
                "session-transcript-normalized"):
        _write_candidate(d, run, 1, PLACEHOLDER_TEXT)

    out = ers.scan(tmp_path)
    assert out["patterns"] == [], "an unresolved template still raised a pattern"
    assert out["placeholderSkipped"] == 2
    assert out["scannedFiles"] == 2, "skipped files must still be counted as scanned"


def test_placeholder_filter_is_case_and_whitespace_insensitive(tmp_path):
    """The filter matches the NORMALIZED signature, so spelling drift in the
    template (casing, wrapped lines) cannot slip past it."""
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    _write_candidate(d, "run-aaa", 1, "ENFORCE GATE: RULE (FAILED THIS RUN)")
    _write_candidate(d, "run-bbb", 1, "Enforce gate:   rule\n(failed this run)")

    out = ers.scan(tmp_path)
    assert out["patterns"] == []
    assert out["placeholderSkipped"] == 2


def test_named_gate_candidates_still_recur(tmp_path):
    """Mutation check the other way: if this fails, the placeholder filter has
    silenced real enforce-gate candidates, not only the empty template."""
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for run in ("run-aaa", "run-bbb"):
        _write_candidate(d, run, 1, "Enforce gate: Review-A (failed this run)")

    out = ers.scan(tmp_path)
    assert len(out["patterns"]) == 1, out
    assert out["patterns"][0]["count"] == 2
    assert out["placeholderSkipped"] == 0


def test_empty_gate_name_is_a_placeholder(tmp_path):
    """The pre-fix producer's SECOND empty shape.

    A whitespace-only `checkpoint_id` was truthy under the old
    `checkpoint_id or judge_id or "rule"` chain, so it wrote
    `Enforce gate:     (failed this run)` — a name-less candidate that the
    `rule`-only filter missed.
    """
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for run in ("run-aaa", "run-bbb"):
        _write_candidate(d, run, 1, "Enforce gate:    (failed this run)")

    out = ers.scan(tmp_path)
    assert out["patterns"] == []
    assert out["placeholderSkipped"] == 2


def test_judge_named_candidates_do_not_count_toward_recurrence(tmp_path):
    """The defect the first fix missed (build-loop's own queue, 2026-09-12).

    19 of 52 candidates in build-loop's queue said `Enforce gate: <judge>`, not
    one said `rule`, and the strongest signal in the whole queue was
    `inline-self-verification` at count=6 / confidence=high. That name is what a
    nested orchestrator calls itself when GAP-1 leaves it unable to dispatch the
    real auditor — an actor, not a practice a run failed. A filter that caught
    only the `rule` spelling was inert against every real file on disk.
    """
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for i, run in enumerate(("run-aaa", "run-bbb", "run-ccc"), start=1):
        _write_candidate(d, run, i, "Enforce gate: inline-self-verification (failed this run)")
    _write_candidate(d, "run-ddd", 1, "Enforce gate: independent-auditor (failed this run)")
    _write_candidate(d, "run-eee", 1, "Enforce gate: independent-auditor (failed this run)")

    out = ers.scan(tmp_path)
    assert out["patterns"] == [], "a judge identity still counted as a gate"
    assert out["placeholderSkipped"] == 5


def test_real_checkpoint_ids_are_never_treated_as_placeholders(tmp_path):
    """The negative control that bounds the widened filter.

    These are the checkpoint ids build-loop's own judges actually record. If any
    is skipped, the filter has started eating the signal it exists to protect.
    """
    for gate in ("review-g", "build", "integration-final", "final-integration", "Review-A"):
        d = tmp_path / gate / ".build-loop" / "proposals" / "enforce-from-retro"
        d.mkdir(parents=True)
        for run in ("run-aaa", "run-bbb"):
            _write_candidate(d, run, 1, f"Enforce gate: {gate} (failed this run)")
        out = ers.scan(tmp_path / gate)
        assert len(out["patterns"]) == 1, f"{gate} was wrongly filtered: {out}"
        assert out["placeholderSkipped"] == 0, gate


def test_non_enforce_gate_candidates_are_untouched(tmp_path):
    """The filter keys on the `Enforce gate: ... (failed this run)` shape only.

    Automation-ritual candidates share the queue and must keep recurring.
    """
    d = tmp_path / ".build-loop" / "proposals" / "enforce-from-retro"
    d.mkdir(parents=True)
    for run in ("run-aaa", "run-bbb"):
        _write_candidate(d, run, 1, "Automate recurring ritual (×16): Bash → SendMessage → Bash — draft a script/hook")

    out = ers.scan(tmp_path)
    assert len(out["patterns"]) == 1, out
    assert out["placeholderSkipped"] == 0
