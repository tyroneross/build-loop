"""Tests for scripts/complexity_detector.py — the Sub-step E deep-mode detector.

Locks every detector kind against the seeded fixtures (true positives,
file+line correct), asserts zero false positives on the clean controls
(FC-2/T-05), and asserts diff-scope + graceful-skip + envelope-shape
(T-06/T-07). Invokes the detector both in-process (fast path) and as a
subprocess (CLI contract), mirroring tests/test_capability_registry.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DETECTOR = REPO / "scripts" / "complexity_detector.py"
FIXTURES = REPO / "tests" / "fixtures" / "complexity_detector"

sys.path.insert(0, str(REPO / "scripts"))
import complexity_detector as cd  # noqa: E402


def _fx(name: str) -> str:
    return str(FIXTURES / name)


def _scan(*names: str) -> dict:
    return cd.scan([_fx(n) for n in names])


# --------------------------------------------------------------------------
# T-01..T-04 — every kind detected at the right file + line
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture,kind,expected_line",
    [
        ("seed_high_complexity.py", "high_complexity", 7),
        ("seed_deep_nesting.py", "deep_nesting", 13),
        ("seed_accidental_quadratic.py", "accidental_quadratic", 12),
        ("seed_redundant_multipass.py", "redundant_multipass", 16),
        ("seed_needless_indirection.py", "needless_indirection", 10),
    ],
)
def test_each_kind_detected(fixture, kind, expected_line):
    env = _scan(fixture)
    matches = [h for h in env["hotspots"] if h["kind"] == kind]
    assert matches, f"{kind} not detected in {fixture}: {env['hotspots']}"
    assert any(h["line"] == expected_line for h in matches), (
        f"{kind} expected at line {expected_line}, got "
        f"{[h['line'] for h in matches]}"
    )
    assert all(h["file"] == _fx(fixture) for h in matches)


def test_accidental_quadratic_finds_both_seeded_cases():
    env = _scan("seed_accidental_quadratic.py")
    aq = sorted(h["line"] for h in env["hotspots"]
                if h["kind"] == "accidental_quadratic")
    # find_dupes (.count over same iterable) + cross_pairs (nested loop)
    assert 12 in aq and 21 in aq, aq


# --------------------------------------------------------------------------
# T-05 / FC-2 — zero false positives on clean controls
# --------------------------------------------------------------------------

def test_zero_false_positives_on_controls():
    env = _scan("clean_controls.py")
    assert env["hotspots"] == [], (
        f"clean controls produced hotspots: {env['hotspots']}"
    )
    assert env["scanned_files"] == [_fx("clean_controls.py")]
    assert env["skipped"] == []


# --------------------------------------------------------------------------
# T-06 — diff-scoped only + graceful skip
# --------------------------------------------------------------------------

def test_diff_scoped_only_no_out_of_scope_entries():
    env = _scan("seed_deep_nesting.py")
    for h in env["hotspots"]:
        assert h["file"] == _fx("seed_deep_nesting.py")


def test_unparseable_python_is_skipped_not_crashed(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(\n", encoding="utf-8")
    env = cd.scan([str(broken)])
    assert env["hotspots"] == []
    assert env["scanned_files"] == []
    assert len(env["skipped"]) == 1
    assert "syntax error" in env["skipped"][0]["reason"]


def test_non_python_and_missing_paths_are_skipped(tmp_path):
    md = tmp_path / "x.md"
    md.write_text("# not python", encoding="utf-8")
    env = cd.scan([str(md), str(tmp_path / "nope.py")])
    reasons = sorted(s["reason"] for s in env["skipped"])
    assert any("not a .py file" in r for r in reasons)
    assert any("does not exist" in r for r in reasons)
    assert env["scanned_files"] == []


# --------------------------------------------------------------------------
# T-07 / FC-5 — envelope shape stable + CLI contract
# --------------------------------------------------------------------------

def test_envelope_shape():
    env = _scan("seed_high_complexity.py")
    assert set(env.keys()) == {"hotspots", "scanned_files", "skipped"}
    for h in env["hotspots"]:
        assert set(h.keys()) == {
            "file", "line", "kind", "reason", "severity", "score"
        }
        assert h["kind"] in cd.KINDS
        assert h["severity"] in ("high", "advisory")
        assert isinstance(h["line"], int)
        assert isinstance(h["score"], (int, float))


def test_cli_json_subprocess_contract():
    proc = subprocess.run(
        [sys.executable, str(DETECTOR), "--changed-files",
         _fx("seed_accidental_quadratic.py"), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert set(payload.keys()) == {"hotspots", "scanned_files", "skipped"}
    assert any(h["kind"] == "accidental_quadratic"
               for h in payload["hotspots"])


def test_cli_requires_changed_files_arg():
    proc = subprocess.run(
        [sys.executable, str(DETECTOR)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2  # argparse usage error


# --------------------------------------------------------------------------
# QC-4 — detector is clean against itself (dogfood)
# --------------------------------------------------------------------------

def test_detector_is_self_clean():
    env = cd.scan([str(DETECTOR)])
    high = [h for h in env["hotspots"] if h["severity"] == "high"]
    assert high == [], f"detector has high-severity self-hotspots: {high}"
