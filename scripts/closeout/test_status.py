# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``scripts/closeout/status.py``.

These tests are the structural enforcement layer for the build-loop memory
closeout contract — a skipped or empty closeout with durable signal MUST be
detectable, not silent. The test names are deliberately explicit so future
edits don't accidentally weaken them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/ on path so ``import closeout`` works.

from closeout.status import (  # noqa: E402
    CLOSEOUT_STATUSES,
    detect_durable_signal,
    run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scratch(tmp_path: Path) -> Path:
    """Lay out a minimal build-loop project skeleton inside ``tmp_path``."""
    bl = tmp_path / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "pending-lessons").mkdir(parents=True, exist_ok=True)
    (bl / "pending-lessons" / "pending").mkdir(parents=True, exist_ok=True)
    (bl / "proposals" / "enforce-from-retro").mkdir(parents=True, exist_ok=True)
    (bl / "retrospectives").mkdir(parents=True, exist_ok=True)
    (bl / "closeout").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_flat_candidate(workdir: Path, name: str = "20260609T010203Z-abc.md") -> None:
    body = (
        "---\n"
        "id: abc123\n"
        "kind: lesson\n"
        "signal_type: correction\n"
        "confidence: medium\n"
        "scope: project\n"
        "captured_at: 2026-06-09T01:02:03Z\n"
        "---\n\n"
        "## Quote\n\n"
        "> sample candidate body\n"
    )
    (workdir / ".build-loop" / "pending-lessons" / name).write_text(body, encoding="utf-8")


def _write_queued_candidate(workdir: Path, name: str = "run-1-001-sample.json") -> None:
    payload = {
        "id": "run-1-001-sample",
        "content": "sample queued candidate",
        "hint": None,
        "type": "lesson",
        "name": "sample",
        "project": None,
        "submitted_at": "2026-06-09T01:02:03Z",
        "source_run_id": "run-1",
        "source_host": "claude_code",
        "source_workdir": str(workdir),
        "placement": None,
    }
    target = workdir / ".build-loop" / "pending-lessons" / "pending" / name
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_enforce_candidate(workdir: Path, name: str = "run-1-01.md") -> None:
    body = (
        "# Enforce candidate\n\n"
        "Prompted ≥2× in this run — make this the default next time.\n"
    )
    (workdir / ".build-loop" / "proposals" / "enforce-from-retro" / name).write_text(body, encoding="utf-8")


def _write_retro_summary(
    workdir: Path,
    *,
    date: str = "2026-06-09",
    run_id: str = "run-1",
    with_durable: bool = True,
) -> None:
    date_dir = workdir / ".build-loop" / "retrospectives" / date
    date_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Retrospective summary — {run_id}",
        "",
        f"- active: .build-loop/retrospectives/{date}/{run_id}.md",
    ]
    if with_durable:
        lines.append(
            f"- durable: /tmp/build-loop-memory/projects/build-loop/retrospectives/{date}/{run_id}.md"
        )
    (date_dir / f"{run_id}.summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# detect_durable_signal — pure inspection
# ---------------------------------------------------------------------------


def test_detect_durable_signal_empty(tmp_path: Path) -> None:
    workdir = _scratch(tmp_path)
    sig = detect_durable_signal(workdir)
    assert sig["raw_candidates_flat"] == 0
    assert sig["raw_candidates_queued"] == 0
    assert sig["retro_enforce_candidates"] == 0
    assert sig["retro_durable_path"] is None


def test_detect_durable_signal_counts_each_source(tmp_path: Path) -> None:
    workdir = _scratch(tmp_path)
    _write_flat_candidate(workdir, "a.md")
    _write_flat_candidate(workdir, "b.md")
    _write_queued_candidate(workdir, "q1.json")
    _write_enforce_candidate(workdir, "e1.md")
    _write_retro_summary(workdir)
    sig = detect_durable_signal(workdir)
    assert sig["raw_candidates_flat"] == 2
    assert sig["raw_candidates_queued"] == 1
    assert sig["retro_enforce_candidates"] == 1
    assert sig["retro_durable_path"] and "build-loop-memory" in sig["retro_durable_path"]


# ---------------------------------------------------------------------------
# run() — the three closeout_status outcomes
# ---------------------------------------------------------------------------


def test_run_no_durable_signal_yields_no_durable_lesson(tmp_path: Path) -> None:
    workdir = _scratch(tmp_path)
    env = run(workdir, run_id="r1", source="phase-6-learn")
    assert env["closeout_status"] == "no_durable_lesson"
    assert env["source"] == "phase-6-learn"
    assert env["written_to"] and Path(env["written_to"]).is_file()
    payload = json.loads(Path(env["written_to"]).read_text(encoding="utf-8"))
    assert payload["closeout_status"] == "no_durable_lesson"


def test_run_raw_candidate_only_yields_queued_pending_lesson(tmp_path: Path) -> None:
    workdir = _scratch(tmp_path)
    _write_flat_candidate(workdir)
    env = run(workdir, run_id="r2", source="post-push")
    assert env["closeout_status"] == "queued_pending_lesson"
    assert "candidate" in env["reason"]


def test_run_queued_intake_candidate_yields_queued_pending_lesson(tmp_path: Path) -> None:
    workdir = _scratch(tmp_path)
    _write_queued_candidate(workdir)
    env = run(workdir, run_id="r3", source="post-push-armed")
    assert env["closeout_status"] == "queued_pending_lesson"


def test_run_retro_durable_plus_enforce_yields_wrote_memory(tmp_path: Path) -> None:
    workdir = _scratch(tmp_path)
    _write_enforce_candidate(workdir)
    _write_retro_summary(workdir, with_durable=True)
    env = run(workdir, run_id="r4", source="post-push")
    assert env["closeout_status"] == "wrote_memory"
    assert "durable_path" in env["reason"]


def test_run_retro_enforce_without_durable_falls_back_to_queued(tmp_path: Path) -> None:
    """Enforce-candidates without a durable retro promotion are not wrote_memory."""
    workdir = _scratch(tmp_path)
    _write_enforce_candidate(workdir)
    _write_retro_summary(workdir, with_durable=False)
    env = run(workdir, run_id="r5", source="post-push")
    assert env["closeout_status"] == "queued_pending_lesson"


# ---------------------------------------------------------------------------
# Contract enforcement — durable signal MUST yield a non-empty status
# ---------------------------------------------------------------------------


def test_contract_durable_signal_never_emits_no_durable_lesson(tmp_path: Path) -> None:
    """The spec's detectable-failure mode: durable signal + ``no_durable_lesson`` is a defect.

    This test fails loud if the routing rule is ever weakened to silently drop a
    durable signal. The spec at
    ``build-loop-memory/projects/build-loop/issues/bl-memory-closeout-enforcement.md``
    requires: "A skipped/empty closeout on a session with durable signal is a
    detectable failure, not silent."
    """
    for setup in (_write_flat_candidate, _write_queued_candidate, _write_enforce_candidate):
        workdir = _scratch(tmp_path / setup.__name__)
        setup(workdir)
        env = run(workdir, run_id="contract", source="post-push")
        assert env["closeout_status"] != "no_durable_lesson", (
            f"setup={setup.__name__} emitted no_durable_lesson despite durable signal — "
            "this is the spec's detectable-failure mode and MUST trip CI."
        )
        assert env["closeout_status"] in CLOSEOUT_STATUSES


def test_run_emits_machine_readable_json_artifact(tmp_path: Path) -> None:
    workdir = _scratch(tmp_path)
    env = run(workdir, run_id="json-r", source="phase-6-learn")
    out = Path(env["written_to"])
    payload = json.loads(out.read_text(encoding="utf-8"))
    for key in ("closeout_status", "reason", "source", "run_id", "ts", "signal"):
        assert key in payload
    assert payload["closeout_status"] in CLOSEOUT_STATUSES


def test_run_is_non_raising_on_unreadable_workdir(tmp_path: Path) -> None:
    """Closeout never blocks the caller — internal errors degrade, never raise."""
    workdir = tmp_path / "does-not-exist"
    # ``workdir`` is missing; detect_durable_signal returns zeros; status persists OK.
    env = run(workdir, run_id="nope", source="ad-hoc")
    assert env["closeout_status"] == "no_durable_lesson"
    # The closeout artifact lives inside the (now-created) workdir.
    assert env["written_to"] and Path(env["written_to"]).is_file()


def test_run_source_is_normalized(tmp_path: Path) -> None:
    workdir = _scratch(tmp_path)
    env = run(workdir, run_id="src", source="unknown-source-name")
    assert env["source"] == "ad-hoc"
