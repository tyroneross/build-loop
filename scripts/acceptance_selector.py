#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Select a risk-targeted acceptance set without executing manifest commands.

The selector turns an authored lane manifest plus the current change surface into
an auditable execution plan. It favors lanes that cover the most criteria and
risks per estimated second, prunes redundant lanes, and falls back to a declared
full-suite lane only when a release boundary, explicit force, or uncovered target
requires it.

Exit codes: 0 complete plan, 1 incomplete coverage or budget exceeded,
2 invalid input/manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import posixpath
import re
import sys
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_bytes


SCHEMA = "build-loop.acceptance-lanes.v1"
BOUNDARIES = {"local", "merge", "release"}
TARGET_WEIGHTS = {"path": 1.0, "risk": 4.0, "criterion": 5.0}
METRICS = ("seconds", "tokens", "dollars")
SELECTION_RELPATH = Path(".build-loop") / "acceptance-selection.json"
RESULTS_RELPATH = Path(".build-loop") / "acceptance-results.json"
_ACCEPTANCE_CONTEXT_RE = re.compile(
    r"```acceptance_context[ \t]*\r?\n(?P<body>.*?)\r?\n```",
    re.DOTALL,
)
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ManifestError(ValueError):
    """The acceptance manifest cannot produce a trustworthy plan."""


def _run_artifact_relpaths(run_id: str) -> dict[str, Path]:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ManifestError("run_id must use only letters, numbers, dot, underscore, or hyphen")
    base = Path(".build-loop") / "runs" / run_id
    return {"manifest": base / "acceptance-lanes.json", "goal": base / "plan.md"}


def _pattern_is_universal(pattern: str) -> bool:
    probes = ("src/a.py", "native/App.swift", "docs/readme.md")
    return all(_glob_regex(pattern).match(probe) is not None for probe in probes)


def _string_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ManifestError(f"{where} must be an array of non-empty strings")
    return [item.strip() for item in value]


def _validate_cost(value: Any, where: str) -> dict[str, float | int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ManifestError(f"{where} must be an object")
    unknown = set(value) - set(METRICS)
    if unknown:
        raise ManifestError(f"{where} has unknown fields: {', '.join(sorted(unknown))}")
    out: dict[str, float | int] = {}
    for key, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
            raise ManifestError(f"{where}.{key} must be a non-negative number")
        out[key] = raw
    return out


def validate_manifest(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a JSON object")
    if raw.get("schema") != SCHEMA:
        raise ManifestError(f"manifest.schema must equal {SCHEMA!r}")
    lanes = raw.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise ManifestError("manifest.lanes must be a non-empty array")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    full_count = 0
    for index, lane in enumerate(lanes):
        where = f"lanes[{index}]"
        if not isinstance(lane, dict):
            raise ManifestError(f"{where} must be an object")
        lane_id = lane.get("id")
        if not isinstance(lane_id, str) or not lane_id.strip():
            raise ManifestError(f"{where}.id must be a non-empty string")
        lane_id = lane_id.strip()
        if lane_id in ids:
            raise ManifestError(f"duplicate lane id {lane_id!r}")
        ids.add(lane_id)

        command = lane.get("command")
        if not isinstance(command, list) or not command or any(
            not isinstance(part, str) or not part for part in command
        ):
            raise ManifestError(f"{where}.command must be a non-empty argv string array")

        covers = lane.get("covers", {})
        if not isinstance(covers, dict):
            raise ManifestError(f"{where}.covers must be an object")
        unknown_covers = set(covers) - {"paths", "risks", "criteria", "all"}
        if unknown_covers:
            raise ManifestError(
                f"{where}.covers has unknown fields: {', '.join(sorted(unknown_covers))}"
            )
        all_targets = covers.get("all", False)
        if not isinstance(all_targets, bool):
            raise ManifestError(f"{where}.covers.all must be boolean")

        full_suite = lane.get("full_suite", False)
        if not isinstance(full_suite, bool):
            raise ManifestError(f"{where}.full_suite must be boolean")
        full_count += int(full_suite)
        if all_targets and not full_suite:
            raise ManifestError(f"{where}.covers.all is allowed only on a full_suite lane")
        confidence = lane.get("confidence", 1.0)
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 < confidence <= 1
        ):
            raise ManifestError(f"{where}.confidence must be > 0 and <= 1")

        required_on = _string_list(lane.get("required_on"), f"{where}.required_on")
        invalid_boundaries = set(required_on) - BOUNDARIES - {"always"}
        if invalid_boundaries:
            raise ManifestError(
                f"{where}.required_on has invalid boundaries: "
                + ", ".join(sorted(invalid_boundaries))
            )

        normalized.append(
            {
                "id": lane_id,
                "command": command,
                "covers": {
                    "paths": _string_list(covers.get("paths"), f"{where}.covers.paths"),
                    "risks": _string_list(covers.get("risks"), f"{where}.covers.risks"),
                    "criteria": _string_list(
                        covers.get("criteria"), f"{where}.covers.criteria"
                    ),
                    "all": all_targets,
                },
                "cost": _validate_cost(lane.get("cost"), f"{where}.cost"),
                "confidence": float(confidence),
                "required_on": required_on,
                "full_suite": full_suite,
                "index": index,
            }
        )
    if full_count > 1:
        raise ManifestError("manifest may declare at most one full_suite lane")

    ignore_paths = _string_list(raw.get("ignore_paths"), "ignore_paths")
    if any(_pattern_is_universal(pattern) for pattern in ignore_paths):
        raise ManifestError("ignore_paths may not ignore the entire change surface")
    for lane in normalized:
        if not lane["full_suite"] and any(
            _pattern_is_universal(pattern) for pattern in lane["covers"]["paths"]
        ):
            raise ManifestError(
                f"lane {lane['id']!r} has a universal path pattern; use full_suite"
            )
    minimum_confidence = raw.get("minimum_confidence", 0.8)
    if (
        isinstance(minimum_confidence, bool)
        or not isinstance(minimum_confidence, (int, float))
        or not 0 < minimum_confidence <= 1
    ):
        raise ManifestError("minimum_confidence must be > 0 and <= 1")
    for lane in normalized:
        if lane["full_suite"] and lane["confidence"] < float(minimum_confidence):
            raise ManifestError(
                f"full_suite lane {lane['id']!r} is below minimum_confidence"
            )
    return {
        "schema": SCHEMA,
        "ignore_paths": ignore_paths,
        "minimum_confidence": float(minimum_confidence),
        "lanes": normalized,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    return validate_manifest(raw)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile a POSIX glob where * stops at / and ** crosses directories."""
    out = ""
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    out += "(?:.*/)?"
                    index += 1
                else:
                    out += ".*"
                continue
            out += "[^/]*"
        elif char == "?":
            out += "[^/]"
        else:
            out += re.escape(char)
        index += 1
    return re.compile("^" + out + "$")


def _matches(path: str, patterns: list[str]) -> bool:
    return any(_glob_regex(pattern).match(path) is not None for pattern in patterns)


def _normalize_changed_path(raw: str, workdir: Path | None) -> str:
    candidate = raw.replace("\\", "/")
    path = Path(candidate)
    if path.is_absolute() and workdir is not None:
        try:
            candidate = path.resolve().relative_to(workdir.resolve()).as_posix()
        except ValueError as exc:
            raise ManifestError(f"changed file escapes workdir: {raw}") from exc
    elif path.is_absolute():
        candidate = path.as_posix().lstrip("/")
    clean = posixpath.normpath(candidate.removeprefix("./"))
    if clean in {"", "."} or clean == ".." or clean.startswith("../"):
        raise ManifestError(f"changed file escapes workdir: {raw}")
    return clean


def _change_digest(workdir: Path, changed_files: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(dict.fromkeys(changed_files)):
        digest.update(rel.encode("utf-8") + b"\0")
        path = workdir / rel
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def _targets(
    manifest: dict[str, Any],
    changed_files: list[str],
    risks: list[str],
    criteria: list[str],
    workdir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in dict.fromkeys(changed_files):
        clean = _normalize_changed_path(path, workdir)
        if not _matches(clean, manifest["ignore_paths"]):
            out[f"path:{clean}"] = {
                "kind": "path",
                "value": clean,
                "weight": TARGET_WEIGHTS["path"],
            }
    for risk in dict.fromkeys(risks):
        out[f"risk:{risk}"] = {
            "kind": "risk",
            "value": risk,
            "weight": TARGET_WEIGHTS["risk"],
        }
    for criterion in dict.fromkeys(criteria):
        out[f"criterion:{criterion}"] = {
            "kind": "criterion",
            "value": criterion,
            "weight": TARGET_WEIGHTS["criterion"],
        }
    return out


def _lane_coverage(
    lane: dict[str, Any],
    targets: dict[str, dict[str, Any]],
    minimum_confidence: float,
) -> set[str]:
    if lane["confidence"] < minimum_confidence:
        return set()
    if lane["full_suite"] or lane["covers"]["all"]:
        return set(targets)
    covered: set[str] = set()
    for key, target in targets.items():
        cover_key = "criteria" if target["kind"] == "criterion" else target["kind"] + "s"
        values = lane["covers"][cover_key]
        if target["kind"] == "path":
            if _matches(target["value"], values):
                covered.add(key)
        elif target["value"] in values:
            covered.add(key)
    return covered


def _metric_total(lanes: list[dict[str, Any]], metric: str) -> float | int | None:
    if any(metric not in lane["cost"] for lane in lanes):
        return None
    return sum(lane["cost"][metric] for lane in lanes)


def _cost_summary(selected: list[dict[str, Any]], all_lanes: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for metric in METRICS:
        selected_total = _metric_total(selected, metric)
        all_total = _metric_total(all_lanes, metric)
        out[f"selected_{metric}"] = selected_total
        out[f"run_all_{metric}"] = all_total
        out[f"saved_{metric}"] = (
            all_total - selected_total
            if selected_total is not None and all_total is not None
            else None
        )
    return out


def _subset_cost_key(
    subset: tuple[dict[str, Any], ...],
    coverage: dict[str, set[str]],
    targets: dict[str, dict[str, Any]],
) -> tuple[Any, ...]:
    """Prefer confidence/value, then dollars, tokens, seconds, and lane count."""
    confidence_value = 0.0
    for target_id, target in targets.items():
        confidence_value += target["weight"] * max(
            (lane["confidence"] for lane in subset if target_id in coverage[lane["id"]]),
            default=0.0,
        )

    def total(metric: str) -> float:
        if any(metric not in lane["cost"] for lane in subset):
            return float("inf")
        return float(sum(lane["cost"][metric] for lane in subset))

    return (
        total("dollars"),
        total("tokens"),
        total("seconds"),
        -confidence_value,
        len(subset),
        tuple(lane["id"] for lane in subset),
    )


def _exact_cover(
    candidates: list[dict[str, Any]],
    needed: set[str],
    coverage: dict[str, set[str]],
    targets: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], ...] | None:
    """Return the best complete narrow cover for ordinary small lane manifests."""
    if not needed:
        return tuple()
    if len(candidates) > 18:
        return None
    best: tuple[dict[str, Any], ...] | None = None
    best_key: tuple[Any, ...] | None = None
    for count in range(1, len(candidates) + 1):
        for subset in itertools.combinations(candidates, count):
            combined = set().union(*(coverage[lane["id"]] for lane in subset))
            if not combined >= needed:
                continue
            key = _subset_cost_key(subset, coverage, targets)
            if best_key is None or key < best_key:
                best = subset
                best_key = key
    return best


def select_lanes(
    manifest: dict[str, Any],
    *,
    changed_files: list[str],
    risks: list[str],
    criteria: list[str],
    boundary: str = "local",
    force_full: bool = False,
    max_seconds: float | None = None,
    workdir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    if boundary not in BOUNDARIES:
        raise ManifestError(f"boundary must be one of {', '.join(sorted(BOUNDARIES))}")
    normalized_changed = [
        _normalize_changed_path(path, workdir) for path in dict.fromkeys(changed_files)
    ]
    targets = _targets(manifest, normalized_changed, risks, criteria, workdir=workdir)
    lanes = manifest["lanes"]
    coverage = {
        lane["id"]: _lane_coverage(lane, targets, manifest["minimum_confidence"])
        for lane in lanes
    }
    full_lane = next((lane for lane in lanes if lane["full_suite"]), None)
    policy_errors: list[str] = []
    if not targets:
        policy_errors.append(
            "no acceptance targets remain; provide changed files, criteria, or risks "
            "and do not ignore the complete change surface"
        )
    if normalized_changed and not any(target["kind"] == "path" for target in targets.values()):
        policy_errors.append("every changed path was ignored; targeted path coverage is absent")
    if (force_full or boundary == "release") and full_lane is None:
        policy_errors.append("full-suite evidence is required but no full_suite lane exists")

    selected_ids: set[str] = set()
    mandatory_ids: set[str] = set()
    reasons: dict[str, list[str]] = {}

    def add(lane: dict[str, Any], reason: str) -> None:
        selected_ids.add(lane["id"])
        reasons.setdefault(lane["id"], []).append(reason)

    for lane in lanes:
        required = lane["required_on"]
        if "always" in required or boundary in required:
            add(lane, f"required_on:{'always' if 'always' in required else boundary}")
            mandatory_ids.add(lane["id"])

    if full_lane and force_full:
        add(full_lane, "force_full")
    if full_lane and boundary == "release":
        add(full_lane, "release_boundary")

    covered = set().union(*(coverage[lane_id] for lane_id in selected_ids)) if selected_ids else set()
    uncovered = set(targets) - covered

    narrow_candidates = [
        lane for lane in lanes
        if lane["id"] not in selected_ids
        and not lane["full_suite"]
        and coverage[lane["id"]] & uncovered
    ]
    selection_method = "exact"
    exact = _exact_cover(narrow_candidates, uncovered, coverage, targets)
    if exact:
        for lane in exact:
            newly_covered = coverage[lane["id"]] & uncovered
            add(lane, "covers:" + ",".join(sorted(newly_covered)))
            covered.update(newly_covered)
            uncovered -= newly_covered

    # More than 18 useful lanes can make exhaustive search expensive. The loop
    # below is also a fail-safe for a partial exact cover (normally impossible).
    while uncovered:
        selection_method = "greedy_fallback"
        candidates: list[tuple[float, int, float, dict[str, Any], set[str]]] = []
        for lane in lanes:
            if lane["id"] in selected_ids or lane["full_suite"]:
                continue
            new = coverage[lane["id"]] & uncovered
            if not new:
                continue
            value = sum(targets[key]["weight"] for key in new) * lane["confidence"]
            seconds = float(lane["cost"].get("seconds", 60.0))
            tokens = float(lane["cost"].get("tokens", 1000.0))
            dollars = float(lane["cost"].get("dollars", 1.0))
            normalized_cost = seconds + tokens / 100.0 + dollars * 60.0
            score = value / max(normalized_cost, 0.001)
            candidates.append((score, len(new), -seconds, lane, new))
        if not candidates:
            break
        _, _, _, lane, newly_covered = max(
            candidates, key=lambda item: (item[0], item[1], item[2], -item[3]["index"])
        )
        add(lane, "covers:" + ",".join(sorted(newly_covered)))
        covered.update(newly_covered)
        uncovered -= newly_covered

    if uncovered and full_lane:
        add(full_lane, "fallback_for_uncovered:" + ",".join(sorted(uncovered)))
        covered.update(uncovered)
        uncovered.clear()

    # Remove a non-required lane when the rest still covers every target. This
    # post-pass prevents greedy overlap from retaining avoidable work.
    optional = [
        lane for lane in lanes
        if lane["id"] in selected_ids
        and lane["id"] not in mandatory_ids
        and not lane["full_suite"]
    ]
    optional.sort(key=lambda lane: float(lane["cost"].get("seconds", 60.0)), reverse=True)
    for lane in optional:
        remaining = selected_ids - {lane["id"]}
        remaining_coverage = (
            set().union(*(coverage[lane_id] for lane_id in remaining)) if remaining else set()
        )
        if remaining_coverage >= set(targets):
            selected_ids.remove(lane["id"])
            reasons.pop(lane["id"], None)

    selected = [lane for lane in lanes if lane["id"] in selected_ids]
    selected_coverage = (
        set().union(*(coverage[lane["id"]] for lane in selected)) if selected else set()
    )
    uncovered = sorted(set(targets) - selected_coverage)
    costs = _cost_summary(selected, lanes)
    over_budget = bool(
        max_seconds is not None
        and costs["selected_seconds"] is not None
        and costs["selected_seconds"] > max_seconds
    )
    if max_seconds is not None and costs["selected_seconds"] is None:
        policy_errors.append("max-seconds cannot be checked because a selected lane lacks cost.seconds")
    if not selected:
        policy_errors.append("no acceptance lane was selected")
    verdict = (
        "complete"
        if not uncovered and not over_budget and not policy_errors
        else "incomplete"
    )

    selected_rows = []
    for lane in selected:
        selected_rows.append(
            {
                "id": lane["id"],
                "command": lane["command"],
                "reason": reasons.get(lane["id"], []),
                "covers": sorted(coverage[lane["id"]]),
                "cost": lane["cost"],
                "full_suite": lane["full_suite"],
            }
        )

    return {
        "schema": "build-loop.acceptance-selection.v1",
        "run_id": run_id,
        "changed_files": normalized_changed,
        "change_sha256": _change_digest(workdir, normalized_changed) if workdir else None,
        "criteria": list(dict.fromkeys(criteria)),
        "risks": list(dict.fromkeys(risks)),
        "force_full": force_full,
        "verdict": verdict,
        "boundary": boundary,
        "selected": selected_rows,
        "selected_ids": [row["id"] for row in selected_rows],
        "targets": targets,
        "uncovered": uncovered,
        "policy_errors": policy_errors,
        "selection_method": selection_method,
        "full_suite_selected": bool(full_lane and full_lane["id"] in selected_ids),
        "full_suite_reason": reasons.get(full_lane["id"], []) if full_lane else [],
        "cost": costs,
        "budget": {
            "max_seconds": max_seconds,
            "status": "exceeded" if over_budget else "within_or_unknown",
        },
    }


def verify_results(
    workdir: Path,
    *,
    selection_path: Path | None = None,
    results_path: Path | None = None,
    expected_run_id: str | None = None,
    expected_changed_files: list[str] | None = None,
) -> dict[str, Any]:
    """Bind executed lane receipts to one exact complete selection file."""
    workdir = workdir.resolve()
    selection_path = selection_path or workdir / SELECTION_RELPATH
    results_path = results_path or workdir / RESULTS_RELPATH
    if not selection_path.is_absolute():
        selection_path = workdir / selection_path
    if not results_path.is_absolute():
        results_path = workdir / results_path

    errors: list[str] = []
    try:
        selection_bytes = selection_path.read_bytes()
        selection = json.loads(selection_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        return {"verdict": "fail", "errors": [f"selection is unreadable: {exc}"]}
    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"verdict": "fail", "errors": [f"results are unreadable: {exc}"]}

    if not isinstance(selection, dict):
        return {"verdict": "fail", "errors": ["selection must be a JSON object"]}
    if not isinstance(results, dict):
        return {"verdict": "fail", "errors": ["results must be a JSON object"]}
    if selection.get("schema") != "build-loop.acceptance-selection.v1":
        errors.append("selection schema is invalid")
    if selection.get("verdict") != "complete":
        errors.append("selection verdict is not complete")
    if results.get("schema") != "build-loop.acceptance-results.v1":
        errors.append("results schema is invalid")
    expected_digest = hashlib.sha256(selection_bytes).hexdigest()
    if results.get("selection_sha256") != expected_digest:
        errors.append("results selection_sha256 does not bind this selection file")
    selection_run_id = selection.get("run_id")
    if not isinstance(selection_run_id, str) or not selection_run_id.strip():
        errors.append("selection run_id is missing")
    if results.get("run_id") != selection_run_id:
        errors.append("results run_id does not match selection run_id")
    if not expected_run_id:
        errors.append("expected_run_id is required to verify acceptance results")
    elif selection_run_id != expected_run_id:
        errors.append(
            f"selection run_id {selection_run_id!r} does not match closing run {expected_run_id!r}"
        )

    expected_relpaths: dict[str, Path] = {}
    if isinstance(selection_run_id, str):
        try:
            expected_relpaths = _run_artifact_relpaths(selection_run_id)
        except ManifestError as exc:
            errors.append(str(exc))

    changed_files = selection.get("changed_files")
    if not isinstance(changed_files, list) or not changed_files:
        errors.append("selection changed_files is missing or empty")
        changed_files = []
    elif any(not isinstance(item, str) or not item for item in changed_files):
        errors.append("selection changed_files is invalid")
        changed_files = []
    if changed_files and selection.get("change_sha256") != _change_digest(workdir, changed_files):
        errors.append("selection is stale: changed file content no longer matches change_sha256")
    if expected_changed_files is not None:
        normalized_expected = {
            _normalize_changed_path(item, workdir) for item in expected_changed_files
        }
        if set(changed_files) != normalized_expected:
            errors.append("selection changed_files do not match the closing run")

    bound_paths: dict[str, Path] = {}
    expected_context: dict[str, Any] | None = None
    for label in ("manifest", "goal"):
        binding = selection.get(label)
        if not isinstance(binding, dict):
            errors.append(f"selection {label} binding is missing")
            continue
        rel = binding.get("path")
        if not isinstance(rel, str) or not rel:
            errors.append(f"selection {label} path is missing")
            continue
        if label in expected_relpaths and Path(rel) != expected_relpaths[label]:
            errors.append(
                f"selection {label} path must be {expected_relpaths[label].as_posix()!r}"
            )
        bound_path = (workdir / rel).resolve()
        try:
            bound_path.relative_to(workdir)
        except ValueError:
            errors.append(f"selection {label} path escapes workdir")
            continue
        try:
            actual_digest = hashlib.sha256(bound_path.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f"selection {label} is unreadable: {exc}")
            continue
        if binding.get("sha256") != actual_digest:
            errors.append(f"selection is stale: {label} digest changed")
        bound_paths[label] = bound_path
        if label == "goal":
            try:
                expected_criteria = _goal_criterion_ids(bound_path)
                expected_context = _goal_acceptance_context(bound_path)
            except ManifestError as exc:
                errors.append(str(exc))
            else:
                if set(selection.get("criteria") or []) != expected_criteria:
                    errors.append("selection criteria no longer match goal acceptance IDs")

    raw_risks = selection.get("risks")
    if not isinstance(raw_risks, list) or any(not isinstance(item, str) for item in raw_risks):
        errors.append("selection risks are missing or invalid")
        raw_risks = []
    boundary = selection.get("boundary")
    force_full = selection.get("force_full")
    if boundary not in BOUNDARIES:
        errors.append("selection boundary is invalid")
    if not isinstance(force_full, bool):
        errors.append("selection force_full is invalid")
        force_full = False

    if expected_context is not None:
        if raw_risks != expected_context["risks"]:
            errors.append("selection risks do not match bound goal acceptance context")
        if boundary != expected_context["boundary"]:
            errors.append("selection boundary does not match bound goal acceptance context")
        if force_full != expected_context["force_full"]:
            errors.append("selection force_full does not match bound goal acceptance context")
        raw_risks = expected_context["risks"]
        boundary = expected_context["boundary"]
        force_full = expected_context["force_full"]

    if "manifest" in bound_paths and boundary in BOUNDARIES and changed_files:
        try:
            rebound_manifest = load_manifest(bound_paths["manifest"])
            recomputed = select_lanes(
                rebound_manifest,
                changed_files=changed_files,
                risks=raw_risks,
                criteria=list(selection.get("criteria") or []),
                boundary=boundary,
                force_full=force_full,
                workdir=workdir,
                run_id=selection_run_id if isinstance(selection_run_id, str) else None,
            )
        except ManifestError as exc:
            errors.append(f"selection cannot be recomputed: {exc}")
        else:
            selected_signature = [
                (row.get("id"), row.get("command"), row.get("full_suite"))
                for row in selection.get("selected", []) if isinstance(row, dict)
            ]
            recomputed_signature = [
                (row.get("id"), row.get("command"), row.get("full_suite"))
                for row in recomputed["selected"]
            ]
            if selected_signature != recomputed_signature:
                errors.append("selection lanes do not match deterministic recomputation")
            if selection.get("verdict") != recomputed["verdict"]:
                errors.append("selection verdict does not match deterministic recomputation")
            if selection.get("full_suite_selected") != recomputed["full_suite_selected"]:
                errors.append("selection full-suite decision does not match recomputation")
            if selection.get("full_suite_reason") != recomputed["full_suite_reason"]:
                errors.append("selection full-suite reason does not match recomputation")
            if recomputed["uncovered"] or recomputed["policy_errors"]:
                errors.append("recomputed selection is incomplete")

    selected_rows = selection.get("selected")
    selected_rows = selected_rows if isinstance(selected_rows, list) else []
    selected_ids = [str(row.get("id")) for row in selected_rows if isinstance(row, dict)]
    if len(set(selected_ids)) != len(selected_ids):
        errors.append("selection contains duplicate lane ids")
    if not selected_ids:
        errors.append("selection contains no lanes")
    result_rows = results.get("lanes")
    result_rows = result_rows if isinstance(result_rows, list) else []
    result_ids = [str(row.get("id")) for row in result_rows if isinstance(row, dict)]
    if len(set(result_ids)) != len(result_ids):
        errors.append("results contain duplicate lane ids")
    if set(result_ids) != set(selected_ids):
        missing = sorted(set(selected_ids) - set(result_ids))
        extra = sorted(set(result_ids) - set(selected_ids))
        errors.append(f"executed lane ids differ from selection; missing={missing}, extra={extra}")

    selected_by_id = {
        str(row.get("id")): row for row in selected_rows if isinstance(row, dict)
    }
    evidence_root = (workdir / ".build-loop" / "evidence").resolve()
    selection_mtime = selection_path.stat().st_mtime
    for row in result_rows:
        if not isinstance(row, dict):
            errors.append("every result lane must be an object")
            continue
        lane_id = str(row.get("id") or "")
        if row.get("status") != "passed":
            errors.append(f"lane {lane_id!r} did not pass")
        if row.get("exit_code") != 0:
            errors.append(f"lane {lane_id!r} has no successful exit_code")
        if row.get("command") != selected_by_id.get(lane_id, {}).get("command"):
            errors.append(f"lane {lane_id!r} command does not match the selection")
        evidence = row.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            errors.append(f"lane {lane_id!r} has no evidence path")
            continue
        evidence_path = Path(evidence)
        if not evidence_path.is_absolute():
            evidence_path = workdir / evidence_path
        evidence_path = evidence_path.resolve()
        try:
            evidence_path.relative_to(evidence_root)
        except ValueError:
            errors.append(f"lane {lane_id!r} evidence is outside .build-loop/evidence")
            continue
        if not evidence_path.is_file():
            errors.append(f"lane {lane_id!r} evidence does not exist: {evidence}")
            continue
        if evidence_path.stat().st_mtime < selection_mtime:
            errors.append(f"lane {lane_id!r} evidence predates the selection")
        actual_evidence_digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        if row.get("evidence_sha256") != actual_evidence_digest:
            errors.append(f"lane {lane_id!r} evidence_sha256 does not match")

    if selection.get("full_suite_selected") and not selection.get("full_suite_reason"):
        errors.append("selected full suite has no policy reason")
    return {
        "schema": "build-loop.acceptance-result-verdict.v1",
        "verdict": "pass" if not errors else "fail",
        "selection_path": str(selection_path),
        "results_path": str(results_path),
        "selected_ids": selected_ids,
        "errors": errors,
    }


def _goal_criterion_ids(goal: Path) -> set[str]:
    try:
        from acceptance_probe import parse_goal_probes  # noqa: PLC0415
        criteria = parse_goal_probes(goal)
    except Exception as exc:  # noqa: BLE001
        raise ManifestError(f"cannot read goal acceptance criteria: {exc}") from exc
    return {str(item.get("id") or item.get("criterion") or "unnamed") for item in criteria}


def _goal_acceptance_context(goal: Path) -> dict[str, Any]:
    """Read the SHA-bound source for boundary, risks, and full-suite policy."""
    try:
        text = goal.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read goal acceptance context: {exc}") from exc
    matches = list(_ACCEPTANCE_CONTEXT_RE.finditer(text))
    if len(matches) != 1:
        raise ManifestError("goal must contain exactly one acceptance_context JSON block")
    try:
        raw = json.loads(matches[0].group("body"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"goal acceptance_context is invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("goal acceptance_context must be a JSON object")
    unknown = set(raw) - {"boundary", "risks", "force_full"}
    if unknown:
        raise ManifestError(
            "goal acceptance_context has unknown fields: " + ", ".join(sorted(unknown))
        )
    boundary = raw.get("boundary")
    if boundary not in BOUNDARIES:
        raise ManifestError(
            f"goal acceptance_context.boundary must be one of {', '.join(sorted(BOUNDARIES))}"
        )
    risks = _string_list(raw.get("risks"), "goal acceptance_context.risks")
    if len(risks) != len(set(risks)):
        raise ManifestError("goal acceptance_context.risks must not contain duplicates")
    force_full = raw.get("force_full")
    if not isinstance(force_full, bool):
        raise ManifestError("goal acceptance_context.force_full must be boolean")
    return {"boundary": boundary, "risks": risks, "force_full": force_full}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument("--goal", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--changed-files", nargs="*", default=[])
    parser.add_argument("--risks", nargs="*", default=[])
    parser.add_argument("--criteria", nargs="*", default=[])
    parser.add_argument("--boundary", choices=sorted(BOUNDARIES), default="local")
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-results", action="store_true")
    parser.add_argument("--selection", type=Path, default=SELECTION_RELPATH)
    parser.add_argument("--results", type=Path, default=RESULTS_RELPATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.verify_results:
        if not isinstance(args.run_id, str) or not args.run_id.strip():
            print(json.dumps({"verdict": "invalid", "error": "--run-id is required for result verification"}, separators=(",", ":")))
            return 2
        result = verify_results(
            args.workdir,
            selection_path=args.selection,
            results_path=args.results,
            expected_run_id=args.run_id,
        )
        print(json.dumps(result, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2))
        return 0 if result["verdict"] == "pass" else 1
    try:
        if args.manifest is None:
            raise ManifestError("--manifest is required for selection")
        if not isinstance(args.run_id, str) or not args.run_id.strip():
            raise ManifestError("--run-id is required for selection")
        expected_relpaths = _run_artifact_relpaths(args.run_id)
        if args.goal is None:
            raise ManifestError("--goal is required for selection")
        goal = args.goal if args.goal.is_absolute() else args.workdir / args.goal
        expected_criteria = _goal_criterion_ids(goal)
        expected_context = _goal_acceptance_context(goal)
        if not expected_criteria:
            raise ManifestError("goal has no machine-readable acceptance criteria")
        if set(args.criteria) != expected_criteria:
            raise ManifestError(
                "--criteria must exactly match goal acceptance IDs; "
                f"expected={sorted(expected_criteria)}, provided={sorted(set(args.criteria))}"
            )
        if args.risks != expected_context["risks"]:
            raise ManifestError(
                "--risks must exactly match goal acceptance_context.risks; "
                f"expected={expected_context['risks']}, provided={args.risks}"
            )
        if args.boundary != expected_context["boundary"]:
            raise ManifestError(
                "--boundary must match goal acceptance_context.boundary; "
                f"expected={expected_context['boundary']!r}, provided={args.boundary!r}"
            )
        if args.force_full != expected_context["force_full"]:
            raise ManifestError(
                "--force-full must match goal acceptance_context.force_full; "
                f"expected={expected_context['force_full']}, provided={args.force_full}"
            )
        manifest_path = args.manifest if args.manifest.is_absolute() else args.workdir / args.manifest
        try:
            manifest_rel = manifest_path.resolve().relative_to(args.workdir.resolve())
            goal_rel = goal.resolve().relative_to(args.workdir.resolve())
        except ValueError as exc:
            raise ManifestError("manifest and goal must be inside workdir") from exc
        if manifest_rel != expected_relpaths["manifest"]:
            raise ManifestError(
                f"--manifest must be {expected_relpaths['manifest'].as_posix()!r} for this run"
            )
        if goal_rel != expected_relpaths["goal"]:
            raise ManifestError(
                f"--goal must be {expected_relpaths['goal'].as_posix()!r} for this run"
            )
        manifest = load_manifest(manifest_path)
        result = select_lanes(
            manifest,
            changed_files=args.changed_files,
            risks=expected_context["risks"],
            criteria=args.criteria,
            boundary=expected_context["boundary"],
            force_full=expected_context["force_full"],
            max_seconds=args.max_seconds,
            workdir=args.workdir,
            run_id=args.run_id,
        )
        result["manifest"] = {
            "path": str(manifest_rel),
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        }
        result["goal"] = {
            "path": str(goal_rel),
            "sha256": hashlib.sha256(goal.read_bytes()).hexdigest(),
        }
    except ManifestError as exc:
        print(json.dumps({"verdict": "invalid", "error": str(exc)}, separators=(",", ":")))
        return 2

    if args.compact:
        result = {
            "schema": result["schema"],
            "run_id": result["run_id"],
            "manifest": result["manifest"],
            "goal": result["goal"],
            "changed_files": result["changed_files"],
            "change_sha256": result["change_sha256"],
            "criteria": result["criteria"],
            "risks": result["risks"],
            "force_full": result["force_full"],
            "verdict": result["verdict"],
            "boundary": result["boundary"],
            "selected": result["selected"],
            "uncovered": result["uncovered"],
            "policy_errors": result["policy_errors"],
            "selection_method": result["selection_method"],
            "full_suite_selected": result["full_suite_selected"],
            "full_suite_reason": result["full_suite_reason"],
            "cost": result["cost"],
            "budget": result["budget"],
        }
    rendered = json.dumps(result, indent=None if args.compact else 2, sort_keys=True)
    if args.output is not None:
        output = args.output if args.output.is_absolute() else args.workdir / args.output
        atomic_write_bytes(output, (rendered + "\n").encode("utf-8"))
    print(rendered)
    return 0 if result["verdict"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
