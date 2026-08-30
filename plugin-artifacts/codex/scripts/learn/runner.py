#!/usr/bin/env python3
"""Host-neutral Phase 6 Learn runner with durable receipts and work orders."""
from __future__ import annotations

import hashlib
import json
import sys
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


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(
        path,
        (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"),
    )


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
    defer_reason: str,
    source: str,
    accrue: bool,
) -> str:
    files: dict[str, str] = {}
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
            files[str(path.relative_to(workdir))] = path.read_text(encoding="utf-8")
    for directory in dynamic:
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    files[str(path.relative_to(workdir))] = path.read_text(encoding="utf-8")
    payload = {
        "run_id": run_id,
        "defer_reason": defer_reason,
        "source": source,
        "accrue": accrue,
        "state": _state_without_learn(state),
        "files": files,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
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


def _collect_patterns(workdir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    procedural = _read_jsonl(workdir / ".procedural" / "_candidates.jsonl")
    for item in procedural:
        key = str(item.get("name") or procedural_governance.slug(str(item.get("root_cause") or "pattern")))
        patterns.append({"key": key, "source": "procedural", "payload": item})

    retro = enforce_retro_signals.scan(workdir)
    for item in retro.get("patterns", []):
        skeleton = item.get("proposal", {}).get("skillSkeleton", {})
        key = str(skeleton.get("name") or procedural_governance.slug(str(item.get("signature") or "pattern")))
        patterns.append({"key": key, "source": "retro", "payload": item})

    objects_path = workdir / ".build-loop" / "learning-objects.json"
    converted = {"proposals": [], "enforcement_specs": [], "skipped": [], "summary": {}}
    if objects_path.exists():
        raw = _read_json(objects_path, [])
        objects = raw.get("learning_objects", []) if isinstance(raw, dict) else raw
        if not isinstance(objects, list):
            raise ValueError(".build-loop/learning-objects.json must contain a list")
        converted = learning_to_draft.convert(objects)
        for item in converted.get("proposals", []):
            skeleton = item.get("proposal", {}).get("skillSkeleton", {})
            key = str(skeleton.get("name") or procedural_governance.slug(str(item.get("signature") or "pattern")))
            patterns.append({"key": key, "source": "learning-object", "payload": item})

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
        "retro_patterns": len(retro.get("patterns", [])),
        "learning_proposals": len(converted.get("proposals", [])),
        "enforcement_specs": len(converted.get("enforcement_specs", [])),
        "selected": len(selected),
        "drafted_skipped": len(unique) - len(undrafted),
        "skipped_by_cap": max(0, len(undrafted) - len(selected)),
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
        rows = _read_jsonl(path)
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


def run(
    workdir: Path | str,
    *,
    run_id: str,
    source: str,
    defer_reason: str = "",
    budget_action: str = "",
    accrue: bool = True,
) -> dict[str, Any]:
    """Run deterministic Learn stages and persist one idempotent receipt."""
    root = Path(workdir).expanduser().resolve()
    state_path = root / ".build-loop" / "state.json"
    receipt_path = root / ".build-loop" / "learn" / f"{run_id}.json"
    runner_lock = root / ".build-loop" / "learn" / ".runner"
    with LockedFile(runner_lock):
        try:
            state = _read_json(state_path, {})
        except (OSError, json.JSONDecodeError) as exc:
            state = {}
            state_error = f"state.json: {exc}"
        else:
            state_error = ""
        runs = state.get("runs", []) if isinstance(state, dict) else []
        runs = runs if isinstance(runs, list) else []
        current = next((row for row in runs if isinstance(row, dict) and str(row.get("run_id")) == run_id), None)
        digest = _digest_inputs(
            root,
            state if isinstance(state, dict) else {},
            run_id,
            defer_reason,
            source,
            accrue,
        )
        if receipt_path.exists():
            existing = _read_json(receipt_path, {})
            if existing.get("input_digest") == digest:
                repaired = _persist_state_summary(root, run_id, existing) if current else False
                existing["already"] = True
                existing["reconciled"] = repaired
                return existing

        stages: dict[str, Any] = {}
        errors: list[str] = []
        stages["record"] = {"status": "complete" if current else "error", "run_id": run_id}
        if state_error:
            errors.append(state_error)
        if current is None:
            errors.append(f"run_id {run_id!r} is absent from .build-loop/state.json.runs[]")

        stages["consolidate"], error = _run_stage(
            "consolidate", lambda: consolidate_memory.main(["--workdir", str(root)])
        )
        if error:
            errors.append(error)
        stages["detect"], error = _run_stage(
            "detect", lambda: procedural_governance.detect_patterns(root)
        )
        if error:
            errors.append(error)

        patterns: list[dict[str, Any]] = []
        try:
            patterns, detection_detail = _collect_patterns(root)
            stages["collect"] = {"status": "complete", **detection_detail}
        except Exception as exc:  # noqa: BLE001 - keep the receipt usable on bad inputs
            stages["collect"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            errors.append(f"collect: {exc}")

        runs_count = len(runs)
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
            work_orders.extend(
                _work_order(
                    run_id,
                    "self-improvement-architect",
                    pattern["key"],
                    pattern["source"],
                    pattern=pattern["payload"],
                )
                for pattern in patterns
            )
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

        status = "error" if errors else (
            "awaiting_agents" if work_orders else ("pending" if accrue_pending else "complete")
        )
        receipt: dict[str, Any] = {
            "schema": SCHEMA,
            "run_id": run_id,
            "source": source,
            "created_at": _now(),
            "input_digest": digest,
            "outcome": outcome,
            "status": status,
            "runs_count": runs_count,
            "patterns_count": len(patterns),
            "stages": stages,
            "work_orders": work_orders,
            "errors": errors,
            "pending_actions": (["rerun Learn outside the Stop hook to fire the accruing miner"] if accrue_pending else []),
            "already": False,
        }
        receipt["learn_line"] = _learn_line(
            outcome, runs_count, len(patterns), work_orders, status, defer_reason
        )
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
) -> dict[str, Any]:
    """Attach agent evidence to a work order without pretending the runner invoked it."""
    if status not in {"complete", "failed"}:
        raise ValueError("attestation status must be complete or failed")
    root = Path(workdir).expanduser().resolve()
    receipt_path = root / ".build-loop" / "learn" / f"{run_id}.json"
    with LockedFile(receipt_path):
        receipt = _read_json(receipt_path, {})
        if receipt.get("schema") != SCHEMA:
            raise ValueError(f"Learn receipt for {run_id!r} is missing or invalid")
        order = next((item for item in receipt.get("work_orders", []) if item.get("id") == work_order_id), None)
        if order is None:
            raise ValueError(f"work order {work_order_id!r} is absent")
        if status == "complete" and order.get("role") == "self-improvement-architect":
            if not artifact or not (root / artifact).is_file():
                raise ValueError("architect completion requires an existing artifact path")
        if status == "complete" and order.get("role") == "promotion-reviewer" and not verdict:
            raise ValueError("promotion-reviewer completion requires a verdict")
        order["status"] = status
        order["attested_at"] = _now()
        if artifact:
            order["artifact"] = artifact
        if verdict:
            order["verdict"] = verdict
        if status == "failed":
            receipt.setdefault("errors", []).append(f"work order {work_order_id} failed")

        if status == "complete" and order.get("role") == "self-improvement-architect":
            reviewer = _work_order(
                run_id,
                "promotion-reviewer",
                str(order.get("pattern_key")),
                f"architect:{work_order_id}",
                artifact_path=artifact,
            )
            if not any(item.get("id") == reviewer["id"] for item in receipt["work_orders"]):
                receipt["work_orders"].append(reviewer)

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
        _write_json(receipt_path, receipt)
    _persist_state_summary(root, run_id, receipt)
    return receipt
