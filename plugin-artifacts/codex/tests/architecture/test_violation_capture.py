"""Tests for scripts/capture_arch_violation.py (Chunk 6).

Covers:
  - new violation writes a decision (write_decision.py invoked, registry
    updated, returns ``new_count: 1``)
  - dedup is idempotent across repeated calls; ``last_seen`` advances
    while violation set stays stable
  - registry created with default schema if missing
  - ``--dry-run`` writes nothing (no decision files, no registry mutation)
  - stable ID is deterministic and reorder-immune over components

All ``write_decision.py`` invocations are mocked via subprocess.run
monkeypatching — we never touch a real decisions directory or DB.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "capture_arch_violation.py"

# Make scripts/ importable so we can drive the module directly.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import capture_arch_violation as cav  # type: ignore  # noqa: E402


# ---------- fixtures ----------


@pytest.fixture
def synth_violation() -> dict:
    return {
        "rule_id": "circular-dep",
        "severity": "error",
        "components": ["service-a", "service-b"],
        "message": "service-a -> service-b -> service-a",
    }


@pytest.fixture
def envelope(synth_violation: dict) -> dict:
    return {"violations": [synth_violation]}


@pytest.fixture
def fake_write_decision(monkeypatch):
    """Stub subprocess.run so write_decision.py is never actually called.

    Records every command that would have been run; returns a fake
    decision_id on stdout.
    """
    calls: list[list[str]] = []
    counter = {"n": 0}

    def fake_run(cmd, *args, **kwargs):  # noqa: ANN001
        calls.append(list(cmd))
        counter["n"] += 1
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout=f"{counter['n']:04d}\n", stderr=""
        )

    monkeypatch.setattr(cav.subprocess, "run", fake_run)
    return calls


def _run(envelope: dict, registry: Path, *, dry_run: bool, workdir: Path) -> dict:
    """Invoke the module's main() in-process; return parsed stdout."""
    argv = ["--registry", str(registry), "--workdir", str(workdir)]
    if dry_run:
        argv.append("--dry-run")
    # Feed envelope via stdin.
    raw = json.dumps(envelope)
    import io
    saved_stdin = sys.stdin
    saved_stdout = sys.stdout
    sys.stdin = io.StringIO(raw)
    captured = io.StringIO()
    sys.stdout = captured
    try:
        rc = cav.main(argv)
    finally:
        sys.stdin = saved_stdin
        sys.stdout = saved_stdout
    assert rc == 0, f"main() exited {rc}"
    return json.loads(captured.getvalue())


# ---------- tests ----------


def test_new_violation_writes_decision(tmp_path, envelope, fake_write_decision):
    registry = tmp_path / ".episodic" / "architecture" / "known_violations.json"
    out = _run(envelope, registry, dry_run=False, workdir=tmp_path)

    assert out["new_count"] == 1
    assert out["dedup_count"] == 0
    assert out["schema_version"] == "1.0.0"
    assert len(out["decision_files"]) == 1

    # write_decision.py was invoked once with the expected core flags.
    assert len(fake_write_decision) == 1
    cmd = fake_write_decision[0]
    assert "--title" in cmd
    title_idx = cmd.index("--title") + 1
    assert "circular-dep" in cmd[title_idx]
    assert "--primary-tag" in cmd
    pt_idx = cmd.index("--primary-tag") + 1
    assert cmd[pt_idx] == "architecture"
    assert "--no-db" in cmd  # db dual-write must be disabled for capture path

    # Registry persisted and contains the violation.
    assert registry.exists()
    data = json.loads(registry.read_text())
    assert data["schema_version"] == "1.0.0"
    assert len(data["violations"]) == 1
    (vid, entry), = data["violations"].items()
    assert entry["rule_id"] == "circular-dep"
    assert entry["components"] == ["service-a", "service-b"]
    assert entry["last_seen_count"] == 1
    assert entry["decision_id"] == "0001"


def test_dedup_idempotent(tmp_path, envelope, fake_write_decision):
    registry = tmp_path / ".episodic" / "architecture" / "known_violations.json"

    out1 = _run(envelope, registry, dry_run=False, workdir=tmp_path)
    assert out1["new_count"] == 1
    first_seen = json.loads(registry.read_text())["violations"]
    (vid1, entry1), = first_seen.items()
    first_last_seen = entry1["last_seen"]

    # Second call with identical envelope.
    out2 = _run(envelope, registry, dry_run=False, workdir=tmp_path)
    assert out2["new_count"] == 0
    assert out2["dedup_count"] == 1

    data = json.loads(registry.read_text())
    assert len(data["violations"]) == 1  # unchanged set
    (vid2, entry2), = data["violations"].items()
    assert vid2 == vid1
    assert entry2["last_seen_count"] == 2
    # last_seen is advanced (or at minimum not regressed).
    assert entry2["last_seen"] >= first_last_seen
    # write_decision.py was NOT invoked the second time.
    assert len(fake_write_decision) == 1


def test_registry_atomic_creation(tmp_path, envelope, fake_write_decision):
    """Missing registry on first call is created with default schema."""
    registry = tmp_path / ".episodic" / "architecture" / "known_violations.json"
    assert not registry.exists()

    out = _run(envelope, registry, dry_run=False, workdir=tmp_path)
    assert out["new_count"] == 1
    assert registry.exists()

    data = json.loads(registry.read_text())
    assert data["schema_version"] == "1.0.0"
    assert "created_at" in data
    assert "violations" in data


def test_dry_run_writes_nothing(tmp_path, envelope, fake_write_decision):
    registry = tmp_path / ".episodic" / "architecture" / "known_violations.json"

    out = _run(envelope, registry, dry_run=True, workdir=tmp_path)
    assert out["new_count"] == 1
    assert out["dedup_count"] == 0
    # No decision invocations and no registry on disk.
    assert len(fake_write_decision) == 0
    assert not registry.exists()


def test_stable_id_deterministic():
    a = cav._stable_id("circular-dep", ["service-a", "service-b"], "msg")
    b = cav._stable_id("circular-dep", ["service-b", "service-a"], "msg")
    c = cav._stable_id("circular-dep", ["service-a", "service-b"], "msg")
    d = cav._stable_id("other-rule", ["service-a", "service-b"], "msg")
    assert a == b, "component reordering must not change ID"
    assert a == c, "same input must yield same ID"
    assert a != d, "different rule_id must yield different ID"
    assert len(a) == 12


def test_graceful_degradation_when_write_decision_missing(
    tmp_path, envelope, monkeypatch
):
    """If write_decision.py cannot be resolved, exit clean with empty decision_files."""
    monkeypatch.setattr(cav, "_resolve_write_decision_script", lambda: None)
    registry = tmp_path / ".episodic" / "architecture" / "known_violations.json"

    out = _run(envelope, registry, dry_run=False, workdir=tmp_path)
    assert out["new_count"] == 1
    assert out["decision_files"] == []
    # Registry still updated even though decision failed.
    data = json.loads(registry.read_text())
    (_, entry), = data["violations"].items()
    assert entry["decision_id"] is None
