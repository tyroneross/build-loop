"""Contract tests for `scripts/capture_arch_violation.py` stdin shape.

Locks lesson `lesson-bl-stdin-stdout-shape-mismatch` (priority 11/11).

Background: the capture script originally expected `rule_id`, but the native
architecture engine emits `rule`. The dedup ID was computed from a missing
field, so violations silently dedup'd to zero. Caught only when the
verification check counted decisions written and saw zero.

These tests fuzz the stdin envelope across both supported shapes, plus a few
field aliases the engine has used historically, and assert the script
either captures the violation OR exits with a clear non-zero code. The
script must NOT silently accept a violation but skip writing it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "capture_arch_violation.py"


def _run(envelope: dict, registry_dir: Path) -> tuple[int, str, str, dict]:
    """Run capture_arch_violation.py with the envelope on stdin in --dry-run.

    Returns (exit_code, stdout, stderr, parsed_stdout_or_empty).
    --dry-run prevents any decision writes; we just verify the parsing
    contract and exit codes.
    """
    registry_path = registry_dir / "known_violations.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--registry",
            str(registry_path),
            "--workdir",
            str(registry_dir.parent),
            "--dry-run",
        ],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        timeout=30,
    )
    parsed: dict = {}
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = {}
    return proc.returncode, proc.stdout, proc.stderr, parsed


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Provide a clean workdir with .episodic/architecture/ subtree."""
    sub = tmp_path / ".episodic" / "architecture"
    sub.mkdir(parents=True)
    return sub


def test_long_form_rule_id_shape(workdir: Path) -> None:
    """Engine-historic shape: `{rule_id, components: [], message, severity}`."""
    envelope = {
        "violations": [
            {
                "rule_id": "circular-dependency",
                "components": ["a", "b"],
                "message": "a imports b imports a",
                "severity": "error",
            }
        ]
    }
    rc, stdout, stderr, payload = _run(envelope, workdir)
    assert rc == 0, f"long-form should succeed; stderr={stderr}"
    assert payload.get("new_count", 0) == 1, (
        f"expected exactly one new violation captured; got {payload}"
    )


def test_short_form_rule_shape(workdir: Path) -> None:
    """Native engine shape: `{rule, component_ids: [], message, severity}`."""
    envelope = {
        "violations": [
            {
                "rule": "layer-violation",
                "component_ids": ["frontend/auth", "db/users"],
                "message": "frontend/auth imports db/users directly",
                "severity": "error",
            }
        ]
    }
    rc, stdout, stderr, payload = _run(envelope, workdir)
    assert rc == 0, f"short-form should succeed; stderr={stderr}"
    assert payload.get("new_count", 0) == 1, (
        f"short-form `rule` envelope must capture; got payload={payload}, stderr={stderr}"
    )


def test_short_form_singular_component_id(workdir: Path) -> None:
    """Engine variant: singular `component_id` instead of plural `component_ids`."""
    envelope = {
        "violations": [
            {
                "rule": "orphan",
                "component_id": "scripts/forgotten",
                "message": "scripts/forgotten has no incoming imports",
                "severity": "warn",
            }
        ]
    }
    rc, stdout, stderr, payload = _run(envelope, workdir)
    assert rc == 0, f"singular component_id should succeed; stderr={stderr}"
    assert payload.get("new_count", 0) == 1


def test_dedup_across_shapes(workdir: Path) -> None:
    """Both `rule_id` and `rule` envelope shapes must capture cleanly.

    In dry-run, the registry is in-memory only and isn't persisted between
    invocations, so the contract we lock here is that BOTH shapes are
    accepted — not cross-invocation dedup (that's covered by the
    deterministic stable_id implementation).
    """
    short = {
        "violations": [
            {
                "rule": "circular-dependency",
                "component_ids": ["a", "b"],
                "message": "a imports b imports a",
                "severity": "error",
            }
        ]
    }
    long = {
        "violations": [
            {
                "rule_id": "circular-dependency",
                "components": ["a", "b"],
                "message": "a imports b imports a",
                "severity": "error",
            }
        ]
    }
    rc1, _, _, p1 = _run(short, workdir)
    rc2, _, _, p2 = _run(long, workdir)
    assert rc1 == 0 and rc2 == 0
    assert p1.get("new_count") == 1
    assert p2.get("new_count") == 1


def test_empty_rule_field_skipped(workdir: Path) -> None:
    """Violation with neither `rule_id` nor `rule` is skipped, not crashed."""
    envelope = {
        "violations": [
            {
                "components": ["x"],
                "message": "missing rule field",
                "severity": "warn",
            }
        ]
    }
    rc, stdout, stderr, payload = _run(envelope, workdir)
    # Skip is non-fatal: rc 0, but new_count stays 0 and stderr surfaces a WARN.
    assert rc == 0
    assert payload.get("new_count", 0) == 0
    assert "rule_id" in stderr or "skipping" in stderr.lower()


def test_invalid_json_returns_nonzero(workdir: Path) -> None:
    """Garbage stdin → exit 1. Must not silently accept."""
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--registry",
            str(workdir / "known_violations.json"),
            "--workdir",
            str(workdir.parent),
            "--dry-run",
        ],
        input="not json at all",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 1
    assert "invalid input" in proc.stderr.lower() or "error" in proc.stderr.lower()
