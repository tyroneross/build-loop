"""Tests for scripts/promote_violation_to_lesson.py (Chunk 8).

Coverage:
  - promotes when distinct_runs >= threshold (default 3)
  - --dry-run reports correctly without mutating files
  - subsequent run skips already-promoted entries
  - below-threshold entries surface under below_threshold
  - idempotent: re-running with a new run added still doesn't re-promote
  - sync invocation uses --source-prefix lesson:bl: and the local lessons file
  - signature regexes are derived (escaped) from registry components

All subprocess calls into sync_navgator_lessons.py are mocked. No live
Postgres or filesystem outside tmp_path.
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import promote_violation_to_lesson as promote_mod  # type: ignore  # noqa: E402


# ---------- helpers ----------


def _make_registry(violations_map: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "created_at": "2026-05-05T00:00:00Z",
        "violations": violations_map,
    }


def _make_run(run_id: str, violation_ids: list[str], *, severity: str = "warn") -> dict:
    """Synthesize a minimal state.json runs[] entry with architecture.violations[]."""
    return {
        "run_id": run_id,
        "timestamp": f"2026-05-05T20:{int(run_id[-2:]):02d}:00Z",
        "architecture": {
            "violations": [
                {
                    "id": vid,
                    "severity": severity,
                    "components": [f"src/foo_{vid}.py"],
                    "message": f"Violation {vid}",
                }
                for vid in violation_ids
            ],
        },
    }


def _seed_workdir(
    workdir: Path,
    *,
    violations_map: dict,
    runs: list[dict],
) -> None:
    """Write .build-loop/state.json + .episodic/architecture/known_violations.json."""
    bl = workdir / ".build-loop"
    bl.mkdir(parents=True, exist_ok=True)
    (bl / "architecture").mkdir(parents=True, exist_ok=True)

    state = {"schema_version": "1.0.0", "runs": runs}
    (bl / "state.json").write_text(json.dumps(state), encoding="utf-8")

    epi = workdir / ".episodic" / "architecture"
    epi.mkdir(parents=True, exist_ok=True)
    (epi / "known_violations.json").write_text(
        json.dumps(_make_registry(violations_map)), encoding="utf-8"
    )


def _capture_stdout(monkeypatch) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    return buf


@pytest.fixture
def stub_sync(monkeypatch):
    """Replace subprocess.run within promote_mod with a recorder."""
    calls: list[list[str]] = []

    class FakeCompleted:
        def __init__(self):
            self.returncode = 0
            self.stdout = '{"synced": 1, "global_synced": 0, "skipped_templates": 0, "errors": [], "schema_version": "1.0.0"}\n'
            self.stderr = ""

    def fake_run(cmd, capture_output=False, text=False, timeout=None, check=False):  # noqa: ARG001
        calls.append(list(cmd))
        return FakeCompleted()

    monkeypatch.setattr(promote_mod.subprocess, "run", fake_run)
    return calls


# ---------- tests ----------


def test_promotes_at_threshold(monkeypatch, tmp_path, stub_sync):
    """3 distinct runs containing the same violation_id → promoted=1.

    Without --dry-run, lessons.json gets the new entry and the registry
    flips ``promoted: true``.
    """
    workdir = tmp_path / "proj"
    workdir.mkdir()
    vid = "abc123abc123"
    violations_map = {
        vid: {
            "rule_id": "cycle",
            "severity": "error",
            "components": ["src/foo.py", "src/bar.py"],
            "message": "Circular dep between foo and bar",
            "first_seen": "2026-05-01T00:00:00Z",
            "last_seen": "2026-05-05T00:00:00Z",
            "last_seen_count": 3,
            "decision_id": None,
        }
    }
    runs = [
        _make_run("run01", [vid]),
        _make_run("run02", [vid]),
        _make_run("run03", [vid]),
    ]
    _seed_workdir(workdir, violations_map=violations_map, runs=runs)

    buf = _capture_stdout(monkeypatch)
    rc = promote_mod.main(["--workdir", str(workdir)])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["promoted"] == 1
    assert out["skipped_already_promoted"] == 0
    assert out["below_threshold"] == 0

    # lessons.json now exists with one entry.
    lessons_path = workdir / ".build-loop" / "architecture" / "lessons.json"
    assert lessons_path.exists()
    lessons_doc = json.loads(lessons_path.read_text(encoding="utf-8"))
    assert lessons_doc["count"] == 1
    lesson = lessons_doc["lessons"][0]
    assert lesson["id"] == f"lesson-build-loop-{vid}"
    assert lesson["category"] == "data-flow"  # rule_id 'cycle' → data-flow
    assert lesson["promoted"] is True
    assert lesson["severity"] == "error"

    # Registry flipped.
    reg = json.loads(
        (workdir / ".episodic" / "architecture" / "known_violations.json").read_text()
    )
    assert reg["violations"][vid]["promoted"] is True
    assert "promoted_at" in reg["violations"][vid]
    assert reg["violations"][vid]["lesson_id"] == lesson["id"]


def test_dry_run_no_writes(monkeypatch, tmp_path, stub_sync):
    """--dry-run reports promotion intent but writes nothing."""
    workdir = tmp_path / "proj"
    workdir.mkdir()
    vid = "deadbeef0000"
    violations_map = {
        vid: {
            "rule_id": "layer-violation",
            "severity": "warn",
            "components": ["src/api/handlers.py"],
            "message": "API layer crossing storage layer",
            "first_seen": "2026-05-01T00:00:00Z",
            "last_seen": "2026-05-05T00:00:00Z",
            "last_seen_count": 3,
            "decision_id": None,
        }
    }
    runs = [_make_run(f"run0{i}", [vid]) for i in range(1, 5)]
    _seed_workdir(workdir, violations_map=violations_map, runs=runs)

    buf = _capture_stdout(monkeypatch)
    rc = promote_mod.main(["--workdir", str(workdir), "--dry-run"])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["promoted"] == 1
    # lessons.json must not exist after dry-run.
    assert not (workdir / ".build-loop" / "architecture" / "lessons.json").exists()
    # Registry must remain un-flipped.
    reg = json.loads(
        (workdir / ".episodic" / "architecture" / "known_violations.json").read_text()
    )
    assert reg["violations"][vid].get("promoted") in (False, None)
    # Sync must not have been invoked.
    assert stub_sync == []


def test_skips_already_promoted(monkeypatch, tmp_path, stub_sync):
    """Second invocation against the same registry — promoted entries skip."""
    workdir = tmp_path / "proj"
    workdir.mkdir()
    vid = "abcdef000001"
    violations_map = {
        vid: {
            "rule_id": "cycle",
            "severity": "error",
            "components": ["src/a.py", "src/b.py"],
            "message": "cycle a-b",
            "first_seen": "2026-05-01T00:00:00Z",
            "last_seen": "2026-05-05T00:00:00Z",
            "last_seen_count": 3,
            "decision_id": None,
        }
    }
    runs = [_make_run(f"run0{i}", [vid]) for i in range(1, 4)]
    _seed_workdir(workdir, violations_map=violations_map, runs=runs)

    # First run: promotes.
    buf1 = _capture_stdout(monkeypatch)
    promote_mod.main(["--workdir", str(workdir)])
    out1 = json.loads(buf1.getvalue())
    assert out1["promoted"] == 1

    # Second run: same registry (now with promoted: true) — should skip.
    buf2 = _capture_stdout(monkeypatch)
    promote_mod.main(["--workdir", str(workdir)])
    out2 = json.loads(buf2.getvalue())
    assert out2["promoted"] == 0
    assert out2["skipped_already_promoted"] == 1
    assert out2["below_threshold"] == 0


def test_below_threshold_not_promoted(monkeypatch, tmp_path, stub_sync):
    """2 runs only → below threshold (default 3) → no promotion."""
    workdir = tmp_path / "proj"
    workdir.mkdir()
    vid = "fffeeeddd000"
    violations_map = {
        vid: {
            "rule_id": "orphan",
            "severity": "warn",
            "components": ["src/dead.py"],
            "message": "orphan component",
            "first_seen": "2026-05-01T00:00:00Z",
            "last_seen": "2026-05-05T00:00:00Z",
            "last_seen_count": 2,
            "decision_id": None,
        }
    }
    runs = [_make_run("run01", [vid]), _make_run("run02", [vid])]
    _seed_workdir(workdir, violations_map=violations_map, runs=runs)

    buf = _capture_stdout(monkeypatch)
    rc = promote_mod.main(["--workdir", str(workdir)])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["promoted"] == 0
    assert out["below_threshold"] == 1
    assert not (workdir / ".build-loop" / "architecture" / "lessons.json").exists()


def test_idempotent_run(monkeypatch, tmp_path, stub_sync):
    """Promote, then re-run with a fresh run added → still promoted=0 (sticky)."""
    workdir = tmp_path / "proj"
    workdir.mkdir()
    vid = "idemp000ab12"
    violations_map = {
        vid: {
            "rule_id": "hotspot",
            "severity": "warn",
            "components": ["src/hub.py"],
            "message": "hotspot",
            "first_seen": "2026-05-01T00:00:00Z",
            "last_seen": "2026-05-05T00:00:00Z",
            "last_seen_count": 3,
            "decision_id": None,
        }
    }
    runs = [_make_run(f"run0{i}", [vid]) for i in range(1, 4)]
    _seed_workdir(workdir, violations_map=violations_map, runs=runs)

    # First run: promote.
    buf1 = _capture_stdout(monkeypatch)
    promote_mod.main(["--workdir", str(workdir)])
    out1 = json.loads(buf1.getvalue())
    assert out1["promoted"] == 1

    # Add a 4th run mentioning the same vid.
    state_path = workdir / ".build-loop" / "state.json"
    state = json.loads(state_path.read_text())
    state["runs"].append(_make_run("run04", [vid]))
    state_path.write_text(json.dumps(state))

    # Re-run: still no new promotion (registry has promoted=true).
    buf2 = _capture_stdout(monkeypatch)
    promote_mod.main(["--workdir", str(workdir)])
    out2 = json.loads(buf2.getvalue())
    assert out2["promoted"] == 0
    assert out2["skipped_already_promoted"] == 1


def test_subject_prefix_bl(monkeypatch, tmp_path, stub_sync):
    """Promotion invokes sync_navgator_lessons.py with --source-prefix lesson:bl:."""
    workdir = tmp_path / "proj"
    workdir.mkdir()
    vid = "subprefix001"
    violations_map = {
        vid: {
            "rule_id": "cycle",
            "severity": "error",
            "components": ["src/x.py"],
            "message": "cycle x",
            "first_seen": "2026-05-01T00:00:00Z",
            "last_seen": "2026-05-05T00:00:00Z",
            "last_seen_count": 3,
            "decision_id": None,
        }
    }
    runs = [_make_run(f"run0{i}", [vid]) for i in range(1, 4)]
    _seed_workdir(workdir, violations_map=violations_map, runs=runs)

    buf = _capture_stdout(monkeypatch)
    rc = promote_mod.main(["--workdir", str(workdir)])
    assert rc == 0

    # Exactly one subprocess.run call to sync_navgator_lessons.py.
    assert len(stub_sync) == 1
    cmd = stub_sync[0]
    # Find --source-prefix and assert its value.
    assert "--source-prefix" in cmd
    sp_idx = cmd.index("--source-prefix")
    assert cmd[sp_idx + 1] == "lesson:bl:"
    # And the lessons file must point at the local .build-loop file.
    assert "--lessons-file" in cmd
    lf_idx = cmd.index("--lessons-file")
    assert cmd[lf_idx + 1].endswith(".build-loop/architecture/lessons.json")


def test_signature_derived_from_components(monkeypatch, tmp_path, stub_sync):
    """Auto-generated signature regex must match each component path verbatim."""
    workdir = tmp_path / "proj"
    workdir.mkdir()
    vid = "sigderiv0001"
    components = [
        "src/build_loop/architecture/storage.py",
        "src/build_loop/architecture/scanner.py",
    ]
    violations_map = {
        vid: {
            "rule_id": "cycle",
            "severity": "error",
            "components": components,
            "message": "circular storage<->scanner",
            "first_seen": "2026-05-01T00:00:00Z",
            "last_seen": "2026-05-05T00:00:00Z",
            "last_seen_count": 3,
            "decision_id": None,
        }
    }
    runs = [_make_run(f"run0{i}", [vid]) for i in range(1, 4)]
    _seed_workdir(workdir, violations_map=violations_map, runs=runs)

    buf = _capture_stdout(monkeypatch)
    promote_mod.main(["--workdir", str(workdir)])

    lessons_doc = json.loads(
        (workdir / ".build-loop" / "architecture" / "lessons.json").read_text()
    )
    lesson = lessons_doc["lessons"][0]

    # signature is a single combined alternation regex; signature_list (extra)
    # holds per-component patterns.
    sig = lesson["signature"]
    assert sig, "expected non-empty signature"
    rx = re.compile(sig)
    for comp in components:
        assert rx.search(comp), f"signature didn't match component {comp!r}"

    # Per-component regexes carried in extras. Promotion unions registry
    # components with anything seen across runs[]; the registry components
    # MUST appear in the list (positions are stable because the union loop
    # preserves order).
    assert "signature_list" in lesson
    sig_list = lesson["signature_list"]
    assert len(sig_list) >= len(components)
    # The first len(components) entries are exactly the registry components,
    # in registry order, escaped.
    for comp_sig, comp in zip(sig_list[: len(components)], components):
        assert re.compile(comp_sig).search(comp)


def test_no_sync_skips_subprocess(monkeypatch, tmp_path, stub_sync):
    """--no-sync writes the lesson but skips the sync invocation."""
    workdir = tmp_path / "proj"
    workdir.mkdir()
    vid = "nosync000abc"
    violations_map = {
        vid: {
            "rule_id": "cycle",
            "severity": "error",
            "components": ["src/p.py"],
            "message": "cycle p",
            "first_seen": "2026-05-01T00:00:00Z",
            "last_seen": "2026-05-05T00:00:00Z",
            "last_seen_count": 3,
            "decision_id": None,
        }
    }
    runs = [_make_run(f"run0{i}", [vid]) for i in range(1, 4)]
    _seed_workdir(workdir, violations_map=violations_map, runs=runs)

    buf = _capture_stdout(monkeypatch)
    promote_mod.main(["--workdir", str(workdir), "--no-sync"])

    # Lesson written.
    assert (workdir / ".build-loop" / "architecture" / "lessons.json").exists()
    # Sync NOT invoked.
    assert stub_sync == []


def test_empty_runs_clean_exit(monkeypatch, tmp_path, stub_sync):
    """Empty runs[] + non-empty registry → below_threshold counts each entry."""
    workdir = tmp_path / "proj"
    workdir.mkdir()
    vid = "empty0000001"
    violations_map = {
        vid: {
            "rule_id": "cycle",
            "severity": "error",
            "components": ["src/p.py"],
            "message": "cycle p",
            "first_seen": "2026-05-05T00:00:00Z",
            "last_seen": "2026-05-05T00:00:00Z",
            "last_seen_count": 1,
            "decision_id": None,
        }
    }
    _seed_workdir(workdir, violations_map=violations_map, runs=[])

    buf = _capture_stdout(monkeypatch)
    rc = promote_mod.main(["--workdir", str(workdir)])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["promoted"] == 0
    assert out["below_threshold"] == 1


def test_missing_state_and_registry(monkeypatch, tmp_path, stub_sync):
    """Both inputs absent → clean envelope, no errors."""
    workdir = tmp_path / "proj"
    workdir.mkdir()
    buf = _capture_stdout(monkeypatch)
    rc = promote_mod.main(["--workdir", str(workdir)])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out == {
        "promoted": 0,
        "skipped_already_promoted": 0,
        "below_threshold": 0,
        "schema_version": "1.0.0",
    }
