#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from acceptance_selector import (  # noqa: E402
    ManifestError,
    _change_digest,
    _matches,
    select_lanes,
    validate_manifest,
    verify_results,
)


def manifest(*lanes, ignore_paths=None):
    return validate_manifest(
        {
            "schema": "build-loop.acceptance-lanes.v1",
            "ignore_paths": ignore_paths or [],
            "lanes": list(lanes),
        }
    )


def lane(
    lane_id,
    *,
    paths=None,
    risks=None,
    criteria=None,
    seconds=10,
    tokens=0,
    dollars=0,
    required_on=None,
    full_suite=False,
    all_targets=False,
    confidence=1,
):
    return {
        "id": lane_id,
        "command": ["test-runner", lane_id],
        "covers": {
            "paths": paths or [],
            "risks": risks or [],
            "criteria": criteria or [],
            "all": all_targets,
        },
        "cost": {"seconds": seconds, "tokens": tokens, "dollars": dollars},
        "required_on": required_on or [],
        "full_suite": full_suite,
        "confidence": confidence,
    }


def goal_text(probes, *, boundary="local", risks=None, force_full=False):
    return (
        "```acceptance_context\n"
        + json.dumps(
            {"boundary": boundary, "risks": risks or [], "force_full": force_full}
        )
        + "\n```\n\n```acceptance_probe\n"
        + json.dumps(probes)
        + "\n```\n"
    )


def test_selects_low_cost_cover_for_changed_path_risk_and_criterion():
    data = manifest(
        lane("sync-unit", paths=["PhoneApp/Sources/Sync/**"], risks=["authority"], seconds=12),
        lane("ask-ui", criteria=["ASK-READY"], seconds=20),
        lane("broad-ui", risks=["authority"], criteria=["ASK-READY"], seconds=80),
        lane("full", full_suite=True, all_targets=True, seconds=600),
    )
    result = select_lanes(
        data,
        changed_files=["PhoneApp/Sources/Sync/PhoneSyncCoordinator.swift"],
        risks=["authority"],
        criteria=["ASK-READY"],
    )
    assert result["verdict"] == "complete"
    assert result["selected_ids"] == ["sync-unit", "ask-ui"]
    assert result["full_suite_selected"] is False
    assert result["cost"]["selected_seconds"] == 32
    assert result["cost"]["run_all_seconds"] == 712
    assert result["cost"]["saved_seconds"] == 680


def test_required_compile_lane_cannot_be_pruned():
    data = manifest(
        lane("compile", paths=["src/**"], seconds=3, required_on=["always"]),
        lane("unit", paths=["src/**"], seconds=1),
    )
    result = select_lanes(data, changed_files=["src/a.py"], risks=[], criteria=[])
    assert result["selected_ids"] == ["compile"]
    assert result["selected"][0]["reason"] == ["required_on:always"]


def test_release_boundary_selects_full_suite_without_extra_targeted_lanes():
    data = manifest(
        lane("unit", paths=["src/**"], seconds=2),
        lane("full", full_suite=True, all_targets=True, seconds=50),
    )
    result = select_lanes(
        data,
        changed_files=["src/a.py"],
        risks=[],
        criteria=[],
        boundary="release",
    )
    assert result["selected_ids"] == ["full"]
    assert result["full_suite_reason"] == ["release_boundary"]


def test_uncovered_target_escalates_to_full_suite():
    data = manifest(
        lane("unit", paths=["src/**"], seconds=2),
        lane("full", full_suite=True, seconds=50),
    )
    result = select_lanes(data, changed_files=["native/App.swift"], risks=[], criteria=[])
    assert result["verdict"] == "complete"
    assert result["selected_ids"] == ["full"]
    assert result["full_suite_reason"][0].startswith("fallback_for_uncovered:path:native/App.swift")


def test_uncovered_target_without_full_suite_is_incomplete():
    data = manifest(lane("unit", paths=["src/**"]))
    result = select_lanes(data, changed_files=["native/App.swift"], risks=[], criteria=[])
    assert result["verdict"] == "incomplete"
    assert result["uncovered"] == ["path:native/App.swift"]


def test_all_changed_paths_ignored_is_incomplete_not_false_green():
    data = manifest(
        lane("unit", paths=["src/**"]),
        lane("full", full_suite=True, seconds=50),
        ignore_paths=["docs/**"],
    )
    result = select_lanes(data, changed_files=["docs/readme.md"], risks=[], criteria=[])
    assert result["verdict"] == "incomplete"
    assert result["selected_ids"] == []
    assert result["targets"] == {}
    assert "no acceptance targets" in result["policy_errors"][0]


def test_criteria_and_risks_outweigh_path_only_lane_per_second():
    data = manifest(
        lane("cheap-path", paths=["src/**"], seconds=1),
        lane("valuable", risks=["privacy"], criteria=["C1"], seconds=4),
        lane(
            "all-narrow",
            paths=["src/**"],
            risks=["privacy"],
            criteria=["C1"],
            seconds=10,
        ),
    )
    result = select_lanes(
        data,
        changed_files=["src/a.py"],
        risks=["privacy"],
        criteria=["C1"],
    )
    assert result["selected_ids"] == ["cheap-path", "valuable"]


def test_exact_cover_accounts_for_dollars_tokens_then_seconds():
    data = manifest(
        lane("paid-fast", paths=["src/**"], seconds=1, tokens=1, dollars=1),
        lane("local-slow", paths=["src/**"], seconds=10, tokens=0, dollars=0),
    )
    result = select_lanes(data, changed_files=["src/a.py"], risks=[], criteria=[])
    assert result["selection_method"] == "exact"
    assert result["selected_ids"] == ["local-slow"]


def test_force_full_is_auditable():
    data = manifest(
        lane("unit", paths=["src/**"]),
        lane("full", full_suite=True, seconds=50),
    )
    result = select_lanes(
        data,
        changed_files=["src/a.py"],
        risks=[],
        criteria=[],
        force_full=True,
    )
    assert result["selected_ids"] == ["full"]
    assert result["full_suite_reason"] == ["force_full"]


def test_budget_excess_never_drops_required_coverage():
    data = manifest(lane("security", risks=["security"], seconds=30))
    result = select_lanes(
        data,
        changed_files=[],
        risks=["security"],
        criteria=[],
        max_seconds=10,
    )
    assert result["selected_ids"] == ["security"]
    assert result["verdict"] == "incomplete"
    assert result["budget"]["status"] == "exceeded"


def test_unknown_cost_stays_unknown_instead_of_inventing_savings():
    raw_lane = lane("unit", paths=["src/**"])
    raw_lane.pop("cost")
    data = manifest(raw_lane, lane("full", full_suite=True, seconds=50))
    result = select_lanes(data, changed_files=["src/a.py"], risks=[], criteria=[])
    assert result["cost"]["selected_seconds"] is None
    assert result["cost"]["saved_seconds"] is None


def test_unknown_seconds_with_budget_is_incomplete():
    raw_lane = lane("unit", paths=["src/**"])
    raw_lane.pop("cost")
    data = manifest(raw_lane)
    result = select_lanes(
        data,
        changed_files=["src/a.py"],
        risks=[],
        criteria=[],
        max_seconds=10,
    )
    assert result["verdict"] == "incomplete"
    assert "cannot be checked" in result["policy_errors"][0]


def test_release_or_forced_full_without_full_lane_is_incomplete():
    data = manifest(lane("unit", paths=["src/**"]))
    release = select_lanes(
        data, changed_files=["src/a.py"], risks=[], criteria=[], boundary="release"
    )
    forced = select_lanes(
        data, changed_files=["src/a.py"], risks=[], criteria=[], force_full=True
    )
    assert release["verdict"] == "incomplete"
    assert forced["verdict"] == "incomplete"
    assert "no full_suite lane" in release["policy_errors"][0]


def test_globstar_and_single_star_have_path_segment_semantics():
    assert _matches("src/a.py", ["src/**/*.py"])
    assert _matches("src/deep/a.py", ["src/**/*.py"])
    assert _matches("src/a.py", ["src/*.py"])
    assert not _matches("src/deep/a.py", ["src/*.py"])


def test_low_confidence_lane_does_not_claim_coverage():
    data = manifest(
        lane("guess", risks=["authority"], confidence=0.2),
        lane("full", full_suite=True, seconds=50),
    )
    result = select_lanes(data, changed_files=[], risks=["authority"], criteria=[])
    assert result["selected_ids"] == ["full"]


@pytest.mark.parametrize(
    "raw,match",
    [
        ({}, "manifest.schema"),
        ({"schema": "build-loop.acceptance-lanes.v1", "lanes": []}, "non-empty"),
        (
            {
                "schema": "build-loop.acceptance-lanes.v1",
                "lanes": [lane("x"), lane("x")],
            },
            "duplicate lane id",
        ),
        (
            {
                "schema": "build-loop.acceptance-lanes.v1",
                "lanes": [lane("a", full_suite=True), lane("b", full_suite=True)],
            },
            "at most one",
        ),
        (
            {
                "schema": "build-loop.acceptance-lanes.v1",
                "lanes": [lane("fake-all", all_targets=True)],
            },
            "only on a full_suite",
        ),
        (
            {
                "schema": "build-loop.acceptance-lanes.v1",
                "ignore_paths": ["**"],
                "lanes": [lane("unit")],
            },
            "entire change surface",
        ),
        (
            {
                "schema": "build-loop.acceptance-lanes.v1",
                "ignore_paths": ["**/**"],
                "lanes": [lane("unit")],
            },
            "entire change surface",
        ),
        (
            {
                "schema": "build-loop.acceptance-lanes.v1",
                "lanes": [lane("fake-broad", paths=["**"])],
            },
            "universal path pattern",
        ),
    ],
)
def test_manifest_validation_is_fail_closed(raw, match):
    with pytest.raises(ManifestError, match=match):
        validate_manifest(raw)


def test_cli_compact_output_and_exit_codes(tmp_path):
    run_dir = tmp_path / ".build-loop" / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "acceptance-lanes.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "build-loop.acceptance-lanes.v1",
                "lanes": [lane("unit", paths=["src/**"], criteria=["C1"])],
            }
        )
    )
    command = [
        sys.executable,
        str(HERE / "acceptance_selector.py"),
        "--manifest",
        str(manifest_path),
        "--workdir",
        str(tmp_path),
        "--goal",
        str(run_dir / "plan.md"),
        "--changed-files",
        "other/file.py",
        "--run-id",
        "test-run",
        "--criteria",
        "C1",
        "--compact",
    ]
    (run_dir / "plan.md").write_text(
        goal_text(
            [{"id": "C1", "acceptance_probe": "echo ok", "baseline": "bad", "boundary": "console"}]
        )
    )
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "incomplete"
    assert "targets" not in payload


def test_cli_rejects_goal_criterion_omission(tmp_path):
    run_dir = tmp_path / ".build-loop" / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "acceptance-lanes.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "build-loop.acceptance-lanes.v1",
                "lanes": [lane("unit", criteria=["C1"])],
            }
        )
    )
    goal = run_dir / "plan.md"
    goal.write_text(
        goal_text(
            [
                {
                    "id": "C1",
                    "acceptance_probe": "echo ok",
                    "baseline": "bad",
                    "boundary": "console",
                }
            ],
            risks=["authority"],
        )
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(HERE / "acceptance_selector.py"),
            "--manifest",
            str(manifest_path),
            "--workdir",
            str(tmp_path),
            "--goal",
            str(goal),
            "--run-id",
            "test-run",
            "--risks",
            "authority",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "expected=['C1']" in json.loads(proc.stdout)["error"]


def test_selector_never_executes_manifest_command(tmp_path):
    marker = tmp_path / "must-not-exist"
    data = validate_manifest(
        {
            "schema": "build-loop.acceptance-lanes.v1",
            "lanes": [
                {
                    "id": "hostile",
                    "command": ["touch", str(marker)],
                    "covers": {"paths": ["src/**"]},
                }
            ],
        }
    )
    result = select_lanes(data, changed_files=["src/a.py"], risks=[], criteria=[])
    assert result["selected_ids"] == ["hostile"]
    assert not marker.exists()


def test_result_receipt_requires_exact_selection_passes_and_evidence(tmp_path):
    build_loop = tmp_path / ".build-loop"
    evidence_root = build_loop / "evidence"
    evidence_root.mkdir(parents=True)
    source = tmp_path / "src" / "a.py"
    source.parent.mkdir()
    source.write_text("pass\n", encoding="utf-8")
    run_dir = build_loop / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "acceptance-lanes.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "build-loop.acceptance-lanes.v1",
                "lanes": [
                    {
                        "id": "unit",
                        "command": ["pytest", "test_a.py"],
                        "covers": {"paths": ["src/**"], "criteria": ["C1"]},
                        "cost": {"seconds": 1, "tokens": 0, "dollars": 0}
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    goal_path = run_dir / "plan.md"
    goal_path.write_text(
        goal_text(
            [{"id": "C1", "acceptance_probe": "echo ok", "baseline": "bad", "boundary": "console"}]
        ),
        encoding="utf-8",
    )
    selection = build_loop / "acceptance-selection.json"
    selected_command = ["pytest", "test_a.py"]
    selection.write_text(
        json.dumps(
            {
                "schema": "build-loop.acceptance-selection.v1",
                "run_id": "test-run",
                "verdict": "complete",
                "manifest": {"path": ".build-loop/runs/test-run/acceptance-lanes.json", "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()},
                "goal": {"path": ".build-loop/runs/test-run/plan.md", "sha256": hashlib.sha256(goal_path.read_bytes()).hexdigest()},
                "changed_files": ["src/a.py"],
                "change_sha256": _change_digest(tmp_path, ["src/a.py"]),
                "criteria": ["C1"],
                "risks": [],
                "force_full": False,
                "boundary": "local",
                "selected": [{"id": "unit", "command": selected_command, "full_suite": False}],
                "full_suite_selected": False,
                "full_suite_reason": [],
            }
        ),
        encoding="utf-8",
    )
    evidence = evidence_root / "unit.log"
    evidence.write_text("passed", encoding="utf-8")
    results = build_loop / "acceptance-results.json"
    results.write_text(
        json.dumps(
                    {
                    "schema": "build-loop.acceptance-results.v1",
                    "run_id": "test-run",
                    "selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
                    "lanes": [
                    {"id": "unit", "status": "passed", "exit_code": 0, "command": selected_command, "evidence": str(evidence), "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
                    ],
            }
        ),
        encoding="utf-8",
    )
    assert verify_results(tmp_path, expected_run_id="test-run")["verdict"] == "pass"
    missing_run_id = verify_results(tmp_path)
    assert any("expected_run_id is required" in error for error in missing_run_id["errors"])
    mismatched_run_id = verify_results(tmp_path, expected_run_id="other-run")
    assert any("does not match closing run" in error for error in mismatched_run_id["errors"])
    payload = json.loads(results.read_text(encoding="utf-8"))
    payload["lanes"][0]["status"] = "failed"
    results.write_text(json.dumps(payload), encoding="utf-8")
    assert verify_results(tmp_path, expected_run_id="test-run")["verdict"] == "fail"

    payload["lanes"][0]["status"] = "passed"
    results.write_text(json.dumps(payload), encoding="utf-8")
    source.write_text("changed after selection\n", encoding="utf-8")
    stale = verify_results(tmp_path, expected_run_id="test-run")
    assert stale["verdict"] == "fail"
    assert any("change_sha256" in error for error in stale["errors"])

    source.write_text("pass\n", encoding="utf-8")
    baseline_selection = json.loads(selection.read_text(encoding="utf-8"))
    forged_selection = dict(baseline_selection)
    forged_selection["selected"] = [
        {"id": "forged", "command": ["true"], "full_suite": False}
    ]
    selection.write_text(json.dumps(forged_selection), encoding="utf-8")
    evidence.write_text("forged pass", encoding="utf-8")
    results.write_text(
        json.dumps(
            {
                "schema": "build-loop.acceptance-results.v1",
                "run_id": "test-run",
                "selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
                "lanes": [
                    {
                        "id": "forged",
                        "status": "passed",
                        "exit_code": 0,
                        "command": ["true"],
                        "evidence": str(evidence),
                        "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    forged = verify_results(tmp_path, expected_run_id="test-run")
    assert forged["verdict"] == "fail"
    assert "selection lanes do not match deterministic recomputation" in forged["errors"]

    for field, value, message in (
        ("boundary", "release", "selection boundary does not match bound goal"),
        ("risks", ["omitted-risk"], "selection risks do not match bound goal"),
        ("force_full", True, "selection force_full does not match bound goal"),
    ):
        tampered = dict(baseline_selection)
        tampered[field] = value
        selection.write_text(json.dumps(tampered), encoding="utf-8")
        verdict = verify_results(tmp_path, expected_run_id="test-run")
        assert verdict["verdict"] == "fail"
        assert any(message in error for error in verdict["errors"])

    for label, decoy_name in (("goal", "decoy-plan.md"), ("manifest", "decoy-lanes.json")):
        tampered = dict(baseline_selection)
        source_path = goal_path if label == "goal" else manifest_path
        decoy_path = run_dir / decoy_name
        decoy_path.write_bytes(source_path.read_bytes())
        tampered[label] = {
            "path": str(decoy_path.relative_to(tmp_path)),
            "sha256": hashlib.sha256(decoy_path.read_bytes()).hexdigest(),
        }
        selection.write_text(json.dumps(tampered), encoding="utf-8")
        verdict = verify_results(tmp_path, expected_run_id="test-run")
        assert verdict["verdict"] == "fail"
        assert any(f"selection {label} path must be" in error for error in verdict["errors"])


def test_empty_handwritten_selection_and_receipt_are_rejected(tmp_path):
    build_loop = tmp_path / ".build-loop"
    build_loop.mkdir()
    selection = build_loop / "acceptance-selection.json"
    selection.write_text(
        json.dumps(
            {
                "schema": "build-loop.acceptance-selection.v1",
                "run_id": "run-1",
                "verdict": "complete",
                "changed_files": [],
                "criteria": [],
                "risks": [],
                "force_full": False,
                "boundary": "local",
                "selected": [],
                "full_suite_selected": False,
                "full_suite_reason": [],
            }
        )
    )
    (build_loop / "acceptance-results.json").write_text(
        json.dumps(
            {
                "schema": "build-loop.acceptance-results.v1",
                "run_id": "run-1",
                "selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
                "lanes": [],
            }
        )
    )
    verdict = verify_results(tmp_path, expected_run_id="run-1")
    assert verdict["verdict"] == "fail"
    assert "selection contains no lanes" in verdict["errors"]


def test_required_lane_covering_everything_prevents_unneeded_local_lane():
    data = manifest(
        lane("always", paths=["src/**"], required_on=["always"], seconds=5),
        lane("release-only", paths=["src/**"], required_on=["release"], seconds=1),
    )
    result = select_lanes(data, changed_files=["src/a.py"], risks=[], criteria=[])
    assert result["selected_ids"] == ["always"]
