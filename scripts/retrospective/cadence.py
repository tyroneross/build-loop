# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Coalesce completed builds into one record-backed retrospective."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from atomic_io import LockedFile, atomic_write_bytes
from retrospective.sections import _run_identifier
from retrospective.synthesize import run as synthesize

SCHEMA = "build-loop.retrospective-cadence.v1"
MAX_BYTES = 2 * 1024 * 1024


def _read(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{path.name} exceeds retrospective read budget")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must be an object")
    return value


def automatic(workdir: Path, *, trigger: str = "build") -> dict:
    """Advisory capture; contention/failure preserves pending source records.

    The run writer supplies build scope. Historical records without it are not
    guessed into a cohort. Explicit reviews flush pending builds, not an empty
    or already-covered cohort. No model, transcript scan, daemon or new timer.
    """
    root = Path(workdir).resolve()
    checkpoint = root / ".build-loop/retrospectives/cadence.json"
    acquired = False
    try:
        if trigger not in {"build", "review"}:
            raise ValueError("trigger must be build or review")
        # A competing agent never waits behind retrospective publication.
        with LockedFile(checkpoint, timeout_s=0):
            acquired = True
            config = _read(root / ".build-loop/config.json", {})
            policy = config.get("retrospective", {})
            if not isinstance(policy, dict):
                raise ValueError("retrospective configuration must be an object")
            threshold = policy.get("every_builds", 3)
            if type(threshold) is not int or not 1 <= threshold <= 100:
                raise ValueError("retrospective.every_builds must be an integer from 1 to 100")
            state = _read(root / ".build-loop/state.json", {"runs": []})
            rows = state.get("runs", [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("run history must contain run objects")
            completed = {}
            for row in rows:
                if row.get("review_scope") != "build" or row.get("outcome") not in {"pass", "fail"}:
                    continue
                rid = _run_identifier(row)
                if not isinstance(rid, str) or not rid.strip():
                    raise ValueError("completed build has no unambiguous run identity")
                completed[rid] = row
            saved = _read(checkpoint, {"schema": SCHEMA, "covered_versions": {}})
            covered = saved.get("covered_versions")
            if (saved.get("schema") != SCHEMA or not isinstance(covered, dict)
                    or any(not isinstance(rid, str) or not rid or not isinstance(version, str)
                           or len(version) != 64 for rid, version in covered.items())):
                raise ValueError("malformed retrospective coverage checkpoint")
            # A date-only upsert is not another build. New recorded findings or
            # outcomes remain eligible, including after a prior capture.
            versions = {}
            for rid, row in completed.items():
                # Fingerprint the record, not a second list of the fields read
                # by the synthesizer. New evidence fields must remain visible.
                evidence = {key: value for key, value in row.items()
                            if key not in {"date", "learn", "ledger_rows_for_run"}}
                phases = evidence.get("phases")
                if isinstance(phases, dict):
                    evidence["phases"] = {key: value for key, value in phases.items() if key != "learn"}
                versions[rid] = hashlib.sha256(json.dumps(
                    evidence, sort_keys=True, allow_nan=False
                ).encode()).hexdigest()
            pending = [rid for rid in completed if covered.get(rid) != versions[rid]]
            if not pending or (trigger == "build" and len(pending) < threshold):
                return {"status": "accruing", "pending_builds": len(pending), "every_builds": threshold}
            # Stable membership makes a retry target the same output, not a new batch.
            batch_id = "auto-" + hashlib.sha256(json.dumps({rid: versions[rid] for rid in sorted(pending)}).encode()).hexdigest()[:20]
            snapshot = dict(state, runs=list(completed.values()))
            result = synthesize(root, run_id=batch_id, run_ids=pending, state_snapshot=snapshot)
            active = result.get("active_path")
            if result.get("status") != "ok" or not active or not Path(active).is_file():
                return {"status": "degraded", "reason": result.get("reason") or "retrospective publication incomplete",
                        "pending_builds": len(pending)}
            receipt = {"schema": SCHEMA, "covered_versions": {**covered, **{rid: versions[rid] for rid in pending}},
                       "last_batch": {"run_id": batch_id, "run_ids": pending, "trigger": trigger,
                                      "active_path": active, "coverage": "recorded-builds-only",
                                      "durable_path": result.get("durable_path"),
                                      "durable_status": result.get("durable_status")}}
            atomic_write_bytes(checkpoint, (json.dumps(receipt, indent=2) + "\n").encode())
            return {"status": "captured", **receipt["last_batch"]}
    except Exception as exc:  # advisory boundary; evidence remains pending on any failure
        if isinstance(exc, TimeoutError) and not acquired:
            return {"status": "deferred", "reason": "another retrospective owns the repository lock"}
        return {"status": "degraded", "reason": f"{type(exc).__name__}: {exc}"}
