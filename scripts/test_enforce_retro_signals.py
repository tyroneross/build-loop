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
    assert out == {"scannedFiles": 0, "patterns": []}


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
