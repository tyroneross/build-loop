#!/usr/bin/env python3
"""Host-neutral Phase 6 Learn runner with durable receipts and work orders."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from atomic_io import LockedFile, atomic_write_bytes  # noqa: E402
import consolidate_memory  # noqa: E402
import enforce_retro_signals  # noqa: E402
import learn_accruing  # noqa: E402
import learning_to_draft  # noqa: E402
import procedural_governance  # noqa: E402

SCHEMA = "build-loop.learn-receipt.v1"
PATTERN_CAP = 2
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSONL_BYTES = 512 * 1024
MAX_JSONL_ROWS = 2_000
MAX_JSONL_FIRST_LINE_BYTES = 64 * 1024
MAX_DIGEST_FILES = 100
MAX_DIGEST_DIRS = 200
MAX_DIGEST_FILE_BYTES = 256 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(f"{path} exceeds the {MAX_JSON_BYTES}-byte JSON limit")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(
        path,
        (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"),
    )


def _validated_run_id(value: str) -> str:
    run_id = str(value or "").strip()
    if run_id in {".", ".."} or RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError("run_id must be a single 1..128 character identifier")
    return run_id


def _contained_artifact(root: Path, value: str) -> tuple[Path, str]:
    relative = Path(value)
    if not value or relative.is_absolute():
        raise ValueError("artifact must be a repository-relative file path")
    path = (root / relative).resolve()
    try:
        canonical = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact must stay inside the repository") from exc
    if not path.is_file():
        raise ValueError("artifact must name an existing repository file")
    return path, str(canonical)


def _state_without_learn(state: dict[str, Any]) -> dict[str, Any]:
    clean = json.loads(json.dumps(state))
    for record in clean.get("runs", []) if isinstance(clean.get("runs"), list) else []:
        if isinstance(record, dict):
            record.pop("learn", None)
    return clean


def _digest_inputs(
    workdir: Path,
    state: dict[str, Any],
    run_id: str,
) -> tuple[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    limits = {
        "files_read": 0,
        "truncated_files": [],
        "skipped_outside": [],
        "directory_scan_truncated": False,
    }
    fixed = [
        workdir / ".build-loop" / "config.json",
        workdir / ".build-loop" / "learning-objects.json",
    ]
    dynamic = [
        workdir / ".build-loop" / "proposals" / "enforce-from-retro",
        workdir / ".build-loop" / "experiments",
    ]
    for path in fixed:
        if path.is_file():
            resolved = path.resolve()
            try:
                relative = str(resolved.relative_to(workdir))
            except ValueError:
                limits["skipped_outside"].append(str(path.relative_to(workdir)))
                continue
            fingerprint = _bounded_fingerprint(resolved, MAX_DIGEST_FILE_BYTES)
            files[relative] = fingerprint
            limits["files_read"] += 1
            if fingerprint["truncated"]:
                limits["truncated_files"].append(relative)
    for directory in dynamic:
        if directory.is_dir():
            paths, truncated_scan = _bounded_tree_files(directory, workdir)
            limits["directory_scan_truncated"] = limits["directory_scan_truncated"] or truncated_scan
            for path in paths:
                if limits["files_read"] >= MAX_DIGEST_FILES:
                    limits["directory_scan_truncated"] = True
                    break
                relative = str(path.relative_to(workdir))
                fingerprint = _bounded_fingerprint(path, MAX_DIGEST_FILE_BYTES)
                files[relative] = fingerprint
                limits["files_read"] += 1
                if fingerprint["truncated"]:
                    limits["truncated_files"].append(relative)
    payload = {
        "run_id": run_id,
        "state": _state_without_learn(state),
        "files": files,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), limits


def _bounded_fingerprint(path: Path, max_bytes: int) -> dict[str, Any]:
    """Fingerprint bounded prefix/tail windows plus size for append safety."""
    size = path.stat().st_size
    window = max(1, max_bytes // 2)
    with path.open("rb") as handle:
        prefix = handle.read(window if size > max_bytes else max_bytes)
        tail = b""
        if size > max_bytes:
            handle.seek(max(0, size - window))
            tail = handle.read(window)
    return {
        "size": size,
        "prefix_sha256": hashlib.sha256(prefix).hexdigest(),
        "tail_sha256": hashlib.sha256(tail).hexdigest() if tail else "",
        "truncated": size > max_bytes,
    }


def _bounded_tree_files(directory: Path, workdir: Path) -> tuple[list[Path], bool]:
    paths: list[Path] = []
    directories_seen = 0
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        directories_seen += 1
        if directories_seen > MAX_DIGEST_DIRS:
            return paths, True
        dirnames[:] = sorted(dirnames)
        for name in sorted(filenames):
            path = (Path(current) / name).resolve()
            try:
                path.relative_to(workdir)
            except ValueError:
                continue
            if path.is_file():
                paths.append(path)
                if len(paths) >= MAX_DIGEST_FILES:
                    return paths, True
    return paths, False


def _run_stage(name: str, action: Callable[[], Any]) -> tuple[dict[str, Any], str | None]:
    try:
        detail = action()
        if isinstance(detail, int):
            if detail != 0:
                return {"status": "error", "return_code": detail}, f"{name} returned {detail}"
            detail = {"return_code": detail}
        if detail is None:
            detail = {}
        return {"status": "complete", **(detail if isinstance(detail, dict) else {"result": detail})}, None
    except Exception as exc:  # noqa: BLE001 - the receipt must capture every stage failure
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}, f"{name}: {exc}"


def _read_jsonl(
    path: Path,
    truncated_inputs: list[str] | None = None,
    *,
    preserve_first: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    size = path.stat().st_size
    with path.open("rb") as handle:
        first = handle.readline(MAX_JSONL_FIRST_LINE_BYTES + 1) if preserve_first else b""
        offset = max(0, size - MAX_JSONL_BYTES)
        handle.seek(offset)
        raw = handle.read(MAX_JSONL_BYTES)
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if offset and lines:
        lines = lines[1:]
    truncated = bool(offset or len(lines) > MAX_JSONL_ROWS or len(first) > MAX_JSONL_FIRST_LINE_BYTES)
    if truncated:
        if truncated_inputs is not None:
            truncated_inputs.append(str(path))
    if len(lines) > MAX_JSONL_ROWS:
        lines = lines[-MAX_JSONL_ROWS:]
    if preserve_first and first and len(first) <= MAX_JSONL_FIRST_LINE_BYTES and offset:
        first_line = first.decode("utf-8", errors="replace").rstrip("\r\n")
        if first_line and (not lines or lines[0] != first_line):
            lines = [first_line, *lines[-(MAX_JSONL_ROWS - 1):]]
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _order_id(run_id: str, role: str, key: str, source: str) -> str:
    token = hashlib.sha256(f"{run_id}:{role}:{key}:{source}".encode("utf-8")).hexdigest()[:12]
    return f"learn-{token}"


def _work_order(run_id: str, role: str, key: str, source: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": _order_id(run_id, role, key, source),
        "role": role,
        "pattern_key": key,
        "source": source,
        "status": "pending",
        **extra,
    }


def _recurring_run_patterns(runs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    manual_counts: Counter[str] = Counter()
    security_counts: Counter[str] = Counter()
    for run in runs:
        for item in run.get("manualInterventions", []) if isinstance(run, dict) else []:
            note = str(item.get("note") if isinstance(item, dict) else item).strip()
            if note:
                manual_counts[note] += 1
        for item in run.get("security_findings", []) if isinstance(run, dict) else []:
            if isinstance(item, dict):
                signature = str(item.get("title") or item.get("type") or item.get("finding") or "").strip()
            else:
                signature = str(item).strip()
            if signature:
                security_counts[signature] += 1
    patterns: list[dict[str, Any]] = []
    for signature, count in manual_counts.items():
        if count >= 2:
            patterns.append({
                "key": f"manual-{procedural_governance.slug(signature)}",
                "source": "manual-intervention",
                "role": "self-improvement-architect",
                "payload": {"type": "manual_intervention", "signature": signature, "count": count},
            })
    for signature, count in security_counts.items():
        if count >= 2:
            patterns.append({
                "key": f"security-{procedural_governance.slug(signature)}",
                "source": "security-finding",
                "role": "self-improvement-architect",
                "payload": {"type": "security_finding", "signature": signature, "count": count},
            })
    return (
        patterns,
        sum(count >= 2 for count in manual_counts.values()),
        sum(count >= 2 for count in security_counts.values()),
    )


def _retro_patterns(workdir: Path) -> tuple[list[dict[str, Any]], int]:
    retro = enforce_retro_signals.scan(workdir)
    patterns: list[dict[str, Any]] = []
    for item in retro.get("patterns", []):
        skeleton = item.get("proposal", {}).get("skillSkeleton", {})
        key = str(skeleton.get("name") or procedural_governance.slug(str(item.get("signature") or "pattern")))
        patterns.append({"key": key, "source": "retro", "role": "self-improvement-architect", "payload": item})
    return patterns, len(retro.get("patterns", []))


def _learning_object_patterns(workdir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    objects_path = workdir / ".build-loop" / "learning-objects.json"
    converted = {"proposals": [], "enforcement_specs": [], "skipped": [], "summary": {}}
    patterns: list[dict[str, Any]] = []
    if objects_path.exists():
        raw = _read_json(objects_path, [])
        objects = raw.get("learning_objects", []) if isinstance(raw, dict) else raw
        if not isinstance(objects, list):
            raise ValueError(".build-loop/learning-objects.json must contain a list")
        converted = learning_to_draft.convert(objects)
        for item in converted.get("proposals", []):
            skeleton = item.get("proposal", {}).get("skillSkeleton", {})
            key = str(skeleton.get("name") or procedural_governance.slug(str(item.get("signature") or "pattern")))
            patterns.append({"key": key, "source": "learning-object", "role": "self-improvement-architect", "payload": item})
        for item in converted.get("enforcement_specs", []):
            key = f"enforce-{procedural_governance.slug(str(item.get('condition') or 'control'))}"
            patterns.append({"key": key, "source": "enforcement-spec", "role": "implementer", "payload": item})
    return patterns, converted


def _tool_trace_patterns(
    workdir: Path, truncated_inputs: list[str]
) -> tuple[list[dict[str, Any]], int]:
    trace_counts: Counter[str] = Counter()
    for item in _read_jsonl(
        workdir / ".build-loop" / "telemetry" / "tool-traces.jsonl",
        truncated_inputs,
    ):
        name = str(item.get("tool") or item.get("name") or item.get("operation") or "").strip()
        if name:
            trace_counts[name] += 1
    patterns: list[dict[str, Any]] = []
    for name, count in trace_counts.items():
        if count >= 3:
            patterns.append({
                "key": f"retry-{procedural_governance.slug(name)}",
                "source": "tool-trace",
                "role": "self-improvement-architect",
                "payload": {"type": "diagnostic_repeat", "signature": name, "count": count},
            })
    return patterns, sum(count >= 3 for count in trace_counts.values())


def _collect_patterns(workdir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    truncated_inputs: list[str] = []
    procedural = _read_jsonl(
        workdir / ".procedural" / "_candidates.jsonl", truncated_inputs
    )
    patterns = [
        {
            "key": str(item.get("name") or procedural_governance.slug(str(item.get("root_cause") or "pattern"))),
            "source": "procedural",
            "role": "self-improvement-architect",
            "payload": item,
        }
        for item in procedural
    ]
    recurring, manual_count, security_count = _recurring_run_patterns(
        procedural_governance.load_runs(workdir)
    )
    retro, retro_count = _retro_patterns(workdir)
    learning, converted = _learning_object_patterns(workdir)
    traces, trace_count = _tool_trace_patterns(workdir, truncated_inputs)
    patterns.extend([*recurring, *retro, *learning, *traces])

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for pattern in patterns:
        unique.setdefault((pattern["source"], pattern["key"]), pattern)
    undrafted = [
        pattern for pattern in unique.values()
        if _artifact_path(workdir, pattern["key"]) is None
    ]
    selected = undrafted[:PATTERN_CAP]
    details = {
        "procedural_candidates": len(procedural),
        "retro_patterns": retro_count,
        "learning_proposals": len(converted.get("proposals", [])),
        "enforcement_specs": len(converted.get("enforcement_specs", [])),
        "manual_patterns": manual_count,
        "security_patterns": security_count,
        "tool_trace_patterns": trace_count,
        "selected": len(selected),
        "drafted_skipped": len(unique) - len(undrafted),
        "skipped_by_cap": max(0, len(undrafted) - len(selected)),
        "truncated_inputs": sorted(set(truncated_inputs)),
    }
    return selected, details


def _artifact_path(workdir: Path, name: str) -> Path | None:
    options = [
        workdir / ".build-loop" / "skills" / "experimental" / name / "SKILL.md",
        workdir / ".build-loop" / "agents" / "experimental" / f"{name}.md",
    ]
    return next((path for path in options if path.exists()), None)


def _sample_sweep(workdir: Path, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = _read_json(workdir / ".build-loop" / "config.json", {})
    enabled = isinstance(config, dict) and config.get("autoPromote") is True
    result = {"enabled": enabled, "scanned": 0, "eligible": 0}
    if not enabled:
        return [], result
    orders: list[dict[str, Any]] = []
    experiments = workdir / ".build-loop" / "experiments"
    if not experiments.is_dir():
        return orders, result
    for path in sorted(experiments.glob("*.jsonl")):
        result["scanned"] += 1
        truncated_inputs: list[str] = []
        rows = _read_jsonl(path, truncated_inputs, preserve_first=True)
        if truncated_inputs:
            result.setdefault("truncated_inputs", []).extend(truncated_inputs)
        created = next((row for row in rows if row.get("event") == "created"), None)
        if not created:
            continue
        applied = [
            row for row in rows
            if row.get("event") == "applied" and row.get("confounded") is False
            and isinstance(row.get("metric_value"), (int, float))
        ]
        floor = max(8, int(created.get("sample_size_target") or 8))
        if len(applied) < floor:
            continue
        baseline = created.get("baseline_value")
        target = created.get("target_value")
        if not isinstance(baseline, (int, float)) or not isinstance(target, (int, float)):
            continue
        observed = sum(float(row["metric_value"]) for row in applied) / len(applied)
        met = observed >= target if target >= baseline else observed <= target
        name = str(created.get("artifact") or path.stem)
        artifact = _artifact_path(workdir, name)
        if not met or artifact is None:
            continue
        result["eligible"] += 1
        orders.append(
            _work_order(
                run_id,
                "promotion-reviewer",
                name,
                "sample-sweep",
                artifact_path=str(artifact.relative_to(workdir)),
                experiment_log=str(path.relative_to(workdir)),
                sample_size=len(applied),
                target_metric={
                    "name": created.get("baseline_metric"),
                    "baseline": baseline,
                    "target": target,
                    "observed": observed,
                    "met": True,
                },
            )
        )
    return orders, result


def _summary(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": receipt["status"],
        "outcome": receipt["outcome"],
        "learn_line": receipt["learn_line"],
        "receipt": f".build-loop/learn/{receipt['run_id']}.json",
        "input_digest": receipt["input_digest"],
        "pending_work_orders": sum(1 for order in receipt["work_orders"] if order["status"] == "pending"),
    }


def _persist_state_summary(workdir: Path, run_id: str, receipt: dict[str, Any]) -> bool:
    state_path = workdir / ".build-loop" / "state.json"
    with LockedFile(state_path):
        state = _read_json(state_path, {})
        for record in state.get("runs", []) if isinstance(state.get("runs"), list) else []:
            if isinstance(record, dict) and str(record.get("run_id")) == run_id:
                record["learn"] = _summary(receipt)
                _write_json(state_path, state)
                return True
    return False


def _learn_line(outcome: str, runs_count: int, pattern_count: int, orders: list[dict[str, Any]], status: str, defer_reason: str) -> str:
    if status == "error":
        return "Learn: error — inspect receipt"
    if outcome == "accruing":
        suffix = "; miner pending" if status == "pending" else ""
        return f"Learn: accruing ({runs_count}/3 runs){suffix}"
    if outcome == "deferred":
        return f"Learn: deferred — {defer_reason}"
    pending = [order for order in orders if order.get("status") == "pending"]
    if pending:
        noun = "pattern" if pattern_count == 1 else "patterns"
        return f"Learn: {pattern_count} {noun} awaiting draft review"
    if pattern_count:
        noun = "pattern" if pattern_count == 1 else "patterns"
        return f"Learn: {pattern_count} {noun} drafted and reviewed"
    return f"Learn: 0 patterns above threshold ({runs_count} runs scanned)"


def _refresh_work_stages(receipt: dict[str, Any]) -> None:
    stages = receipt.setdefault("stages", {})
    orders = receipt.get("work_orders", [])
    architects = [order for order in orders if order.get("role") == "self-improvement-architect"]
    implementers = [order for order in orders if order.get("role") == "implementer"]
    reviewers = [order for order in orders if order.get("role") == "promotion-reviewer"]

    def status_for(items: list[dict[str, Any]], empty: str = "not_applicable") -> dict[str, Any]:
        if not items:
            return {"status": empty, "count": 0}
        if any(item.get("status") == "failed" for item in items):
            status = "error"
        elif any(item.get("status") == "pending" for item in items):
            status = "pending"
        else:
            status = "complete"
        return {"status": status, "count": len(items)}

    stages["draft"] = status_for(architects)
    stages["enforcement"] = status_for(implementers)
    stages["signoff"] = status_for(
        reviewers,
        empty="waiting_for_draft" if architects else "not_applicable",
    )
    stages["notify"] = {"status": "complete", "source": "learn_line"}


def _write_deferred_marker(workdir: Path, run_id: str, reason: str, runs_count: int, budget_action: str) -> str:
    path = workdir / ".build-loop" / "proposals" / f"learn-deferred-{run_id}.md"
    body = (
        f"# Learn deferred: {run_id}\n\n"
        f"- Reason: {reason}\n"
        f"- Runs scanned: {runs_count}\n"
        f"- Budget action: {budget_action or 'none'}\n"
    )
    atomic_write_bytes(path, body.encode("utf-8"))
    return str(path.relative_to(workdir))


def _load_run_context(root: Path, run_id: str) -> tuple[dict[str, Any], list[Any], dict[str, Any] | None, str]:
    try:
        state = _read_json(root / ".build-loop" / "state.json", {})
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [], None, f"state.json: {exc}"
    if not isinstance(state, dict):
        return {}, [], None, "state.json must contain an object"
    runs = state.get("runs", [])
    runs = runs if isinstance(runs, list) else []
    current = next(
        (row for row in runs if isinstance(row, dict) and str(row.get("run_id")) == run_id),
        None,
    )
    return state, runs, current, ""


def _run_base_stages(
    root: Path,
    run_id: str,
    current: dict[str, Any] | None,
    state_error: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    stages: dict[str, Any] = {
        "record": {"status": "complete" if current else "error", "run_id": run_id}
    }
    errors = [state_error] if state_error else []
    if current is None:
        errors.append(f"run_id {run_id!r} is absent from .build-loop/state.json.runs[]")

    for name, action in (
        ("consolidate", lambda: consolidate_memory.main(["--workdir", str(root)])),
        ("detect", lambda: procedural_governance.detect_patterns(root)),
    ):
        stages[name], error = _run_stage(name, action)
        if error:
            errors.append(error)

    try:
        patterns, detection_detail = _collect_patterns(root)
        stages["collect"] = {"status": "complete", **detection_detail}
    except Exception as exc:  # noqa: BLE001 - keep the receipt usable on bad inputs
        patterns = []
        stages["collect"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        errors.append(f"collect: {exc}")
    return patterns, stages, errors


def _run_outcome_stages(
    root: Path,
    run_id: str,
    runs_count: int,
    patterns: list[dict[str, Any]],
    stages: dict[str, Any],
    errors: list[str],
    *,
    defer_reason: str,
    budget_action: str,
    accrue: bool,
) -> tuple[str, bool, list[dict[str, Any]]]:
    outcome = "deferred" if defer_reason else ("accruing" if runs_count < 3 else "full")
    accrue_pending = outcome == "accruing" and not accrue
    if outcome == "accruing" and accrue:
        stages["accrue"], error = _run_stage("accrue", lambda: learn_accruing.fire(root))
        if error:
            errors.append(error)
    elif accrue_pending:
        stages["accrue"] = {"status": "pending", "reason": "stop-hook-latency-boundary"}
    else:
        stages["accrue"] = {"status": "skipped", "reason": outcome}

    work_orders: list[dict[str, Any]] = []
    if outcome == "full" and not errors:
        work_orders = [
            _work_order(
                run_id,
                pattern["role"],
                pattern["key"],
                pattern["source"],
                pattern=pattern["payload"],
            )
            for pattern in patterns
        ]
        sample_orders, sample_detail = _sample_sweep(root, run_id)
        work_orders.extend(sample_orders)
        stages["sample_sweep"] = {"status": "complete", **sample_detail}
    elif outcome == "deferred":
        stages["sample_sweep"] = {"status": "skipped", "reason": defer_reason}
        stages["deferred_marker"] = {
            "status": "complete",
            "path": _write_deferred_marker(root, run_id, defer_reason, runs_count, budget_action),
        }
    else:
        stages["sample_sweep"] = {"status": "skipped", "reason": outcome}
    return outcome, accrue_pending, work_orders


def run(
    workdir: Path | str,
    *,
    run_id: str,
    source: str,
    defer_reason: str = "",
    budget_action: str = "",
    accrue: bool = True,
    comment: str = "",
) -> dict[str, Any]:
    """Run deterministic Learn stages and persist one idempotent receipt."""
    root = Path(workdir).expanduser().resolve()
    run_id = _validated_run_id(run_id)
    receipt_path = root / ".build-loop" / "learn" / f"{run_id}.json"
    runner_lock = root / ".build-loop" / "learn" / ".runner"
    with LockedFile(runner_lock):
        state, runs, current, state_error = _load_run_context(root, run_id)
        digest, input_limits = _digest_inputs(
            root,
            state,
            run_id,
        )
        if receipt_path.exists():
            existing = _read_json(receipt_path, {})
            same_inputs = existing.get("input_digest") == digest
            pending_can_advance = (
                existing.get("status") == "pending" and (accrue or bool(defer_reason))
            )
            if same_inputs and not pending_can_advance:
                if comment:
                    existing.setdefault("comments", []).append(
                        {"at": _now(), "source": source, "text": comment}
                    )
                    _write_json(receipt_path, existing)
                repaired = _persist_state_summary(root, run_id, existing) if current else False
                existing["already"] = True
                existing["reconciled"] = repaired
                return existing

        patterns, stages, errors = _run_base_stages(root, run_id, current, state_error)
        runs_count = len(runs)
        outcome, accrue_pending, work_orders = _run_outcome_stages(
            root,
            run_id,
            runs_count,
            patterns,
            stages,
            errors,
            defer_reason=defer_reason,
            budget_action=budget_action,
            accrue=accrue,
        )

        status = "error" if errors else (
            "awaiting_agents" if work_orders else ("pending" if accrue_pending else "complete")
        )
        receipt: dict[str, Any] = {
            "schema": SCHEMA,
            "run_id": run_id,
            "source": source,
            "created_at": _now(),
            "input_digest": digest,
            "input_limits": input_limits,
            "outcome": outcome,
            "status": status,
            "runs_count": runs_count,
            "patterns_count": len(patterns),
            "stages": stages,
            "work_orders": work_orders,
            "errors": errors,
            "pending_actions": (["rerun Learn outside the Stop hook to fire the accruing miner"] if accrue_pending else []),
            "comments": ([{"at": _now(), "source": source, "text": comment}] if comment else []),
            "already": False,
        }
        receipt["learn_line"] = _learn_line(
            outcome, runs_count, len(patterns), work_orders, status, defer_reason
        )
        _refresh_work_stages(receipt)
        _write_json(receipt_path, receipt)
        if current:
            _persist_state_summary(root, run_id, receipt)
        return receipt


def attest(
    workdir: Path | str,
    *,
    run_id: str,
    work_order_id: str,
    status: str,
    artifact: str = "",
    verdict: str = "",
    comment: str = "",
) -> dict[str, Any]:
    """Attach agent evidence to a work order without pretending the runner invoked it."""
    if status not in {"complete", "failed"}:
        raise ValueError("attestation status must be complete or failed")
    root = Path(workdir).expanduser().resolve()
    run_id = _validated_run_id(run_id)
    receipt_path = root / ".build-loop" / "learn" / f"{run_id}.json"
    with LockedFile(receipt_path):
        receipt = _read_json(receipt_path, {})
        if receipt.get("schema") != SCHEMA:
            raise ValueError(f"Learn receipt for {run_id!r} is missing or invalid")
        order = next((item for item in receipt.get("work_orders", []) if item.get("id") == work_order_id), None)
        if order is None:
            raise ValueError(f"work order {work_order_id!r} is absent")
        artifact_sha256 = ""
        if status == "complete" and order.get("role") == "self-improvement-architect":
            artifact_path, artifact = _contained_artifact(root, artifact)
            artifact_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if status == "complete" and order.get("role") == "promotion-reviewer" and not verdict:
            raise ValueError("promotion-reviewer completion requires a verdict")
        order["status"] = status
        order["attested_at"] = _now()
        if artifact:
            order["artifact"] = artifact
        if artifact_sha256:
            order["artifact_sha256"] = artifact_sha256
        if verdict:
            order["verdict"] = verdict
        if comment:
            receipt.setdefault("comments", []).append(
                {"at": _now(), "source": f"attest:{work_order_id}", "text": comment}
            )
        if status == "failed":
            receipt.setdefault("errors", []).append(f"work order {work_order_id} failed")

        if status == "complete" and order.get("role") == "self-improvement-architect":
            reviewer = _work_order(
                run_id,
                "promotion-reviewer",
                str(order.get("pattern_key")),
                f"architect:{work_order_id}",
                artifact_path=artifact,
                artifact_sha256=artifact_sha256,
            )
            existing_reviewer = next(
                (item for item in receipt["work_orders"] if item.get("id") == reviewer["id"]),
                None,
            )
            if existing_reviewer is None:
                receipt["work_orders"].append(reviewer)
            elif (
                existing_reviewer.get("artifact_path") != artifact
                or existing_reviewer.get("artifact_sha256") != artifact_sha256
            ):
                existing_reviewer.setdefault("artifact_revisions", []).append({
                    "artifact_path": existing_reviewer.get("artifact_path"),
                    "artifact_sha256": existing_reviewer.get("artifact_sha256"),
                    "status": existing_reviewer.get("status"),
                    "attested_at": existing_reviewer.get("attested_at"),
                    "verdict": existing_reviewer.get("verdict"),
                })
                existing_reviewer["artifact_path"] = artifact
                existing_reviewer["artifact_sha256"] = artifact_sha256
                existing_reviewer["status"] = "pending"
                existing_reviewer.pop("attested_at", None)
                existing_reviewer.pop("verdict", None)

        pending = [item for item in receipt["work_orders"] if item.get("status") == "pending"]
        failed = [item for item in receipt["work_orders"] if item.get("status") == "failed"]
        receipt["status"] = "error" if failed else ("awaiting_agents" if pending else "complete")
        receipt["updated_at"] = _now()
        receipt["learn_line"] = _learn_line(
            receipt["outcome"],
            int(receipt["runs_count"]),
            int(receipt["patterns_count"]),
            receipt["work_orders"],
            receipt["status"],
            "",
        )
        _refresh_work_stages(receipt)
        _write_json(receipt_path, receipt)
    _persist_state_summary(root, run_id, receipt)
    return receipt
