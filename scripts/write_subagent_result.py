#!/usr/bin/env python3
"""M1 — Persist implementer subagent return envelopes.

Writes one JSON file per chunk return to
`.build-loop/subagent-results/<run-id>/<chunk-id>.attempt-<n>.json`.
Atomic temp+rename. Append-only naming so retries get a new suffix.

Schema (validated):
  chunk_id (str, required)
  status (str, required, one of M1_VALID_STATUS)
  files_changed (list[str], required)
  verifications (list[str], required)
  notes (str, optional, default "")
  received_at (str, ISO8601, set by helper if missing)
  attempt (int, required, >= 1)

Exit codes: 0 success / 1 validation error / 2 filesystem error.
Zero dependencies. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

M1_VALID_STATUS = {
    "fixed",
    "partial",
    "scope_breach",
    "deferred_architecture",
    "evidence_stale",
    "plan_malformed",
    "needs_dependency",
    "failed",
    "concurrent_modification_detected",
}
REQUIRED = ("chunk_id", "status", "files_changed", "verifications", "attempt")


def _iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate(envelope: dict) -> None:
    for k in REQUIRED:
        if k not in envelope:
            raise ValueError(f"missing required field: {k}")
    if not isinstance(envelope["chunk_id"], str) or not envelope["chunk_id"]:
        raise ValueError("chunk_id must be a non-empty string")
    if envelope["status"] not in M1_VALID_STATUS:
        raise ValueError(f"status must be one of {sorted(M1_VALID_STATUS)}, got {envelope['status']!r}")
    if not isinstance(envelope["files_changed"], list) or not all(isinstance(f, str) for f in envelope["files_changed"]):
        raise ValueError("files_changed must be list[str]")
    if not isinstance(envelope["verifications"], list) or not all(isinstance(v, str) for v in envelope["verifications"]):
        raise ValueError("verifications must be list[str]")
    if not isinstance(envelope["attempt"], int) or envelope["attempt"] < 1:
        raise ValueError("attempt must be int >= 1")


def write_subagent_result(workdir: Path, run_id: str, envelope: dict) -> Path:
    """Atomic-write the envelope to subagent-results/<run-id>/<chunk-id>.attempt-<n>.json.

    Returns the resolved path. Raises ValueError on schema problems, OSError on filesystem
    failures. Existing files at the target path are NOT overwritten — caller must bump
    `attempt` to retry. Sub-1ms typical (small JSON, single fsync).
    """
    _validate(envelope)
    envelope.setdefault("received_at", _iso_utc())
    envelope.setdefault("notes", "")
    chunk_id = envelope["chunk_id"]
    attempt = envelope["attempt"]
    target_dir = workdir / ".build-loop" / "subagent-results" / run_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{chunk_id}.attempt-{attempt}.json"
    if target.exists():
        raise FileExistsError(f"{target} already exists; bump attempt to retry")
    payload = (json.dumps(envelope, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=str(target_dir))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return target


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atomic-write a subagent return envelope (M1).")
    p.add_argument("--workdir", required=True, help="Project root containing .build-loop/")
    p.add_argument("--run-id", required=True, help="Active run_id from state.json.execution.run_id")
    p.add_argument("--envelope", required=True, help="Path to JSON file (or '-' for stdin)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    raw = sys.stdin.read() if args.envelope == "-" else Path(args.envelope).read_text(encoding="utf-8")
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"validation error: invalid JSON: {e}", file=sys.stderr)
        return 1
    if not isinstance(envelope, dict):
        print("validation error: envelope must be a JSON object", file=sys.stderr)
        return 1
    try:
        target = write_subagent_result(Path(args.workdir).resolve(), args.run_id, envelope)
    except ValueError as e:
        print(f"validation error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"filesystem error: {e}", file=sys.stderr)
        return 2
    print(str(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
