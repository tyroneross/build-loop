#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Append a sourced decision to a project's private running log."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path


LOG_REL = Path(".build-loop/plans/DECISION-LOG.md")
RECEIPT_DIR = Path(".build-loop/decisions")
RECEIPT_SCHEMA = "build-loop.decision-update.v1"
CONTEXT_SCHEMA = "build-loop.decision-context.v1"
HEADER = (
    "# Running decision log\n\n"
    "New decisions go first. Superseded entries stay in place for history.\n\n---\n\n"
)


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _check_run_id(run_id: str) -> None:
    if not run_id or run_id in (".", "..") or any(c in run_id for c in "/\\\0"):
        raise ValueError("run_id must be a single safe path component")


def context_packet_path(workdir: Path, run_id: str) -> Path:
    _check_run_id(run_id)
    return workdir.resolve() / RECEIPT_DIR / f"{run_id}-context.json"


def load_context_packet(workdir: Path, run_id: str | None = None) -> tuple[Path, dict]:
    """Prefer the immutable run packet; retain shared-packet support for old runs."""
    shared = workdir.resolve() / ".build-loop/context-bootstrap.json"
    scoped = context_packet_path(workdir, run_id) if run_id else None
    path = scoped if scoped and scoped.exists() else shared
    packet = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(packet, dict):
        raise ValueError("decision context must be a JSON object")
    if scoped and path == scoped and (
        packet.get("schema") != CONTEXT_SCHEMA or packet.get("run_id") != run_id
    ):
        raise ValueError("run-scoped decision context is invalid")
    return path, packet


def _write_receipt(workdir: Path, run_id: str, disposition: str,
                   *, decision_id: str | None = None, reason: str | None = None) -> Path:
    _check_run_id(run_id)
    target = workdir.resolve() / RECEIPT_DIR / f"{run_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": RECEIPT_SCHEMA, "run_id": run_id,
               "disposition": disposition, "decision_id": decision_id,
               "reason": reason}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                     prefix="decision-receipt-", suffix=".tmp", delete=False) as tmp:
        json.dump(payload, tmp, sort_keys=True)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        temporary = Path(tmp.name)
    os.replace(temporary, target)
    return target


def acknowledge_none(workdir: Path, *, run_id: str, reason: str) -> dict[str, object]:
    _check_run_id(run_id)
    reason = _single_line(reason)
    if not reason:
        raise ValueError("a reason is required when no decision is recorded")
    existing = workdir.resolve() / RECEIPT_DIR / f"{run_id}.json"
    if existing.exists():
        try:
            receipt = json.loads(existing.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            receipt = None
        if isinstance(receipt, dict) and receipt.get("disposition") == "recorded":
            raise ValueError("cannot replace a recorded decision with a no-decision receipt")
    receipt = _write_receipt(workdir, run_id, "none", reason=reason)
    return {"disposition": "none", "receipt": str(receipt), "run_id": run_id}


def record(
    workdir: Path, *, title: str, decision: str, rationale: str,
    status: str, evidence: list[str], supersedes: str | None = None,
    decision_date: str | None = None, run_id: str | None = None,
    impacted_files: list[str] | None = None,
) -> dict[str, object]:
    """Insert one idempotent entry without rewriting or deleting prior decisions."""
    title, decision, rationale = map(_single_line, (title, decision, rationale))
    evidence = [_single_line(item) for item in evidence if _single_line(item)]
    impacted_files = [_single_line(item) for item in (impacted_files or []) if _single_line(item)]
    if not title or not decision or not rationale or not evidence:
        raise ValueError("title, decision, rationale, and at least one evidence item are required")
    if run_id is not None:
        _check_run_id(run_id)
    day = decision_date or date.today().isoformat()
    date.fromisoformat(day)
    identifier = hashlib.sha256(f"{day}\0{title}\0{decision}\0{status}".encode()).hexdigest()[:12]
    marker = f"<!-- decision-id:{identifier} -->"
    log = workdir.resolve() / LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    lock = log.with_suffix(".lock")
    with lock.open("a+") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        current = log.read_text(encoding="utf-8") if log.exists() else HEADER
        if marker in current:
            result: dict[str, object] = {"created": False, "path": str(log), "id": identifier}
            if run_id:
                result["receipt"] = str(_write_receipt(workdir, run_id, "recorded",
                                                         decision_id=identifier))
            return result
        entry = (
            f"## {day} · {title}\n\n{marker}\n\n"
            f"**Decision.** {decision}\n\n"
            f"**Rationale.** {rationale}\n\n"
            f"**Status:** {status.upper()}\n\n"
            + (f"**Supersedes:** {_single_line(supersedes)}\n\n" if supersedes else "")
            + ("**Impacted files:** " + ", ".join(f"`{path}`" for path in impacted_files)
               + "\n\n" if impacted_files else "")
            + "**Evidence:** " + " | ".join(evidence) + "\n\n---\n\n"
        )
        divider = current.find("\n---\n")
        if divider >= 0:
            position = divider + len("\n---\n")
            updated = current[:position] + "\n" + entry + current[position:].lstrip("\n")
        else:
            updated = HEADER + entry + current
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=log.parent,
                                         prefix="decision-log-", suffix=".tmp", delete=False) as tmp:
            tmp.write(updated)
            tmp.flush()
            os.fsync(tmp.fileno())
            temporary = Path(tmp.name)
        if log.exists():
            temporary.chmod(log.stat().st_mode)
        os.replace(temporary, log)
        result = {"created": True, "path": str(log), "id": identifier}
        if run_id:
            result["receipt"] = str(_write_receipt(workdir, run_id, "recorded",
                                                     decision_id=identifier))
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument("--title")
    parser.add_argument("--decision")
    parser.add_argument("--rationale")
    parser.add_argument("--status", choices=("decided", "executed", "deferred", "superseded"),
                        default="decided")
    parser.add_argument("--evidence", action="append")
    parser.add_argument("--supersedes")
    parser.add_argument("--impacted-file", action="append")
    parser.add_argument("--date", dest="decision_date")
    parser.add_argument("--run-id")
    parser.add_argument("--none-reason", help="Record that this run made no new decision.")
    args = parser.parse_args()
    try:
        if args.none_reason is not None:
            if not args.run_id or any((args.title, args.decision, args.rationale,
                                        args.evidence, args.supersedes, args.impacted_file)):
                raise ValueError("--none-reason requires --run-id and no decision fields")
            result = acknowledge_none(args.workdir, run_id=args.run_id,
                                      reason=args.none_reason)
        else:
            result = record(args.workdir, title=args.title or "",
                            decision=args.decision or "", rationale=args.rationale or "",
                            status=args.status, evidence=args.evidence or [],
                            supersedes=args.supersedes, impacted_files=args.impacted_file,
                            decision_date=args.decision_date, run_id=args.run_id)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
