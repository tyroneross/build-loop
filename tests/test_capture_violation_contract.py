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
    """Provide a clean registry directory for dry-run parser tests."""
    sub = tmp_path / "architecture"
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


# ---------------------------------------------------------------------------
# Decision 0092 — low-signal filter + rollup
# ---------------------------------------------------------------------------
#
# Under the filter contract (decision 0092, 2026-05-08):
#   - violations matching {confidence: inferred} AND {rule: orphan|hotspot}
#     skip the per-violation MD and instead append to
#     `projects/<project>/architecture/auto-violations.jsonl` plus aggregate
#     into a single rollup MD per scan.
#   - all other violations (circular-dependency, layer-violation, etc., or
#     any confirmed-confidence violation) write a full per-violation MD as
#     before.
# These tests exercise live (non-dry-run) mode with $AGENT_MEMORY_ROOT
# pointed at a tmp_path so the rollup landing dir stays inside the test
# sandbox.


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Project-root-shaped fixture for live-mode tests.

    Distinct from the existing `workdir` fixture (which points at the
    inner registry dir) — the live-mode rollup path
    requires the script's ``--workdir`` to be a true project root so
    `projects/<project>/architecture/auto-violations.jsonl` lands
    cleanly. Existing tests stay on `workdir` and dry-run, untouched.
    """
    return tmp_path


def _run_live(
    envelope: dict, project_root: Path, agent_memory_root: Path
) -> tuple[int, str, str, dict]:
    """Run the script in non-dry-run mode against an isolated agent_memory root."""
    env = {
        **__import__("os").environ,
        "AGENT_MEMORY_ROOT": str(agent_memory_root),
        # Predictable run-id so the test can reason about the rollup filename.
        "BUILD_LOOP_RUN_ID": "2026-05-09-test",
        # Predictable project tag so the rollup lands at a known path.
        "BUILD_LOOP_PROJECT_TAG": "build-loop-tests",
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workdir",
            str(project_root),
        ],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    parsed: dict = {}
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = {}
    return proc.returncode, proc.stdout, proc.stderr, parsed


def test_filter_skips_inferred_orphan_writes_jsonl_and_rollup(
    project_root: Path, tmp_path: Path
) -> None:
    """Low-signal violations route to jsonl+rollup; signal violations
    keep the per-violation MD path."""
    agent_memory_root = tmp_path / "agent-memory"
    agent_memory_root.mkdir()

    envelope = {
        "violations": [
            # 2x orphan/warn → inferred → filter matches
            {
                "rule_id": "orphan",
                "components": ["scripts/lonely1"],
                "message": "scripts/lonely1 has no incoming imports",
                "severity": "warn",
            },
            {
                "rule_id": "orphan",
                "components": ["scripts/lonely2"],
                "message": "scripts/lonely2 has no incoming imports",
                "severity": "warn",
            },
            # 1x hotspot/warn → inferred → filter matches
            {
                "rule_id": "hotspot",
                "components": ["src/util/helpers"],
                "message": "src/util/helpers has 12 incoming imports",
                "severity": "warn",
            },
            # 1x circular-dependency/error → confirmed → filter does NOT match
            {
                "rule_id": "circular-dependency",
                "components": ["a", "b"],
                "message": "a imports b imports a",
                "severity": "error",
            },
            # 1x orphan/error → confirmed → filter does NOT match
            # (rule is in LOW_SIGNAL_RULES, but confidence is confirmed,
            # and the filter requires both)
            {
                "rule_id": "orphan",
                "components": ["src/critical/orphaned_module"],
                "message": "critical module has no incoming imports",
                "severity": "error",
            },
        ]
    }
    rc, _, stderr, payload = _run_live(envelope, project_root, agent_memory_root)
    assert rc == 0, f"live run should succeed; stderr={stderr}"

    # Registry has all five entries (filter affects MD output, not registry).
    assert payload["new_count"] == 5, payload

    # JSONL has 3 lines: 2 orphan/warn + 1 hotspot/warn (the filter-matched
    # violations).
    architecture_dir = agent_memory_root / "projects" / "build-loop-tests" / "architecture"
    registry = architecture_dir / "known_violations.json"
    assert registry.exists(), f"registry should be written; stderr={stderr}"

    jsonl = architecture_dir / "auto-violations.jsonl"
    assert jsonl.exists(), f"jsonl should be written; stderr={stderr}"
    jsonl_lines = [
        json.loads(line)
        for line in jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(jsonl_lines) == 3, f"jsonl line count={len(jsonl_lines)}; lines={jsonl_lines}"
    # Verify shape and that all entries match the filter predicate.
    expected_keys = {"ts", "project", "rule", "entity", "confidence", "source", "severity"}
    for line in jsonl_lines:
        assert set(line.keys()) == expected_keys, line
        assert line["confidence"] == "inferred"
        assert line["source"] == "auto-inferred"
        assert line["rule"] in {"orphan", "hotspot"}
        assert line["project"] == "build-loop-tests"

    # Rollup MD exists with the expected name and a count of 3.
    rollup_dir = agent_memory_root / "projects" / "build-loop-tests" / "decisions"
    rollups = [
        p
        for p in rollup_dir.iterdir()
        if p.is_file() and p.name.endswith("-2026-05-09-test-architecture-violation-rollup.md")
    ]
    assert len(rollups) == 1, (
        f"exactly one rollup expected; got {[p.name for p in rollups]}; "
        f"stderr={stderr}"
    )
    rollup_body = rollups[0].read_text(encoding="utf-8")
    assert "Total filtered" in rollup_body
    assert "**3**" in rollup_body  # count line says 3
    # Three table rows for the three filtered entries (entities are unique).
    assert "scripts/lonely1" in rollup_body
    assert "scripts/lonely2" in rollup_body
    assert "src/util/helpers" in rollup_body

    # The two confirmed violations got per-violation decision MDs (via the
    # write_decision.py path); the filtered three did NOT. The script
    # records full-decision paths in `decision_files`.
    assert len(payload["decision_files"]) == 2, (
        f"expected exactly 2 per-violation MDs (the confirmed entries); "
        f"got {payload['decision_files']}"
    )

    # stdout exposes the rollup payload.
    assert "rollup" in payload, f"rollup key missing from stdout; payload={payload}"
    assert payload["rollup"]["entries"] == 3
    assert payload["rollup"]["path"].endswith(
        "-2026-05-09-test-architecture-violation-rollup.md"
    )


def test_zero_inferred_orphan_no_jsonl_no_rollup(
    project_root: Path, tmp_path: Path
) -> None:
    """No filter-matched violations → no jsonl, no rollup, no rollup key in stdout."""
    agent_memory_root = tmp_path / "agent-memory"
    agent_memory_root.mkdir()
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
    rc, _, stderr, payload = _run_live(envelope, project_root, agent_memory_root)
    assert rc == 0, stderr
    assert payload["new_count"] == 1
    # No jsonl path was created.
    jsonl = (
        agent_memory_root
        / "projects"
        / "build-loop-tests"
        / "architecture"
        / "auto-violations.jsonl"
    )
    assert not jsonl.exists(), "jsonl must not be created when filter doesn't fire"
    # No rollup directory or files for this run.
    rollup_dir = agent_memory_root / "projects" / "build-loop-tests" / "decisions"
    if rollup_dir.exists():
        rollups = [p for p in rollup_dir.iterdir() if "rollup" in p.name]
        assert rollups == [], f"unexpected rollup files: {[p.name for p in rollups]}"
    # stdout does NOT include the rollup key.
    assert "rollup" not in payload, f"rollup key must be absent; payload={payload}"
    # The confirmed violation got its per-violation MD.
    assert len(payload["decision_files"]) == 1


def test_dry_run_skips_jsonl_and_rollup_even_when_filter_matches(
    project_root: Path, tmp_path: Path
) -> None:
    """Dry-run preserves existing contract: no MDs, no jsonl, no rollup,
    even when the filter would fire."""
    agent_memory_root = tmp_path / "agent-memory"
    agent_memory_root.mkdir()
    env = {
        **__import__("os").environ,
        "AGENT_MEMORY_ROOT": str(agent_memory_root),
        "BUILD_LOOP_RUN_ID": "2026-05-09-dryrun",
        "BUILD_LOOP_PROJECT_TAG": "build-loop-tests",
    }
    envelope = {
        "violations": [
            {
                "rule_id": "orphan",
                "components": ["scripts/lonely1"],
                "message": "no incoming imports",
                "severity": "warn",
            },
            {
                "rule_id": "orphan",
                "components": ["scripts/lonely2"],
                "message": "no incoming imports",
                "severity": "warn",
            },
        ]
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workdir",
            str(project_root),
            "--dry-run",
        ],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    # new_count still reflects what would be captured.
    assert payload.get("new_count") == 2
    # No side effects: no jsonl, no rollup, no rollup key in stdout.
    architecture_dir = agent_memory_root / "projects" / "build-loop-tests" / "architecture"
    jsonl = architecture_dir / "auto-violations.jsonl"
    assert not jsonl.exists()
    assert not (architecture_dir / "known_violations.json").exists()
    rollup_dir = agent_memory_root / "projects" / "build-loop-tests" / "decisions"
    assert not rollup_dir.exists() or not any(rollup_dir.iterdir())
    assert "rollup" not in payload
