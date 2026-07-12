#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""promotion_queue.py — durable-store "queue-and-report", not silent-skip.

WHY THIS EXISTS (observed failure, 2026-07-11):
    Three retrospectives DELIBERATELY skipped durable promotion because a peer
    session held build-loop-memory. The agents "pointed ``--memory-root`` at a
    scratch path to skip" — a silent loss of the durable lesson. This module
    replaces skip-on-busy with enqueue-on-busy: when the canonical store is
    busy/peer-held, the durable write is QUEUED into the CONSUMER repo's
    ``.build-loop/pending-promotions/queue.jsonl`` and DRAINED at the next
    closeout / SessionStart sweep — never dropped.

Contract:
    * The queue lives in the consumer repo (``.build-loop/``), NEVER in
      build-loop-memory — so a peer holding the store cannot block enqueue.
    * Append-only JSONL under an fcntl lock (same primitive as
      ``memory_telemetry`` / ``append_milestone``).
    * ``drain()`` replays each queued record against the (now-free) store via
      the EXISTING writers (``append_milestone``, ``memory_writer``,
      ``retrospective.write``) — DRY, no re-implementation. A record that is
      still busy on drain stays queued; a written record moves to the sibling
      ``queue.drained.jsonl`` audit log.
    * Fail-soft everywhere: never raises into a caller; a closeout/hook that
      calls ``drain`` must never be blocked by it.

Busy signal (deterministic + testable — ``store_busy``):
    1. env ``BUILD_LOOP_MEMORY_BUSY`` set to a truthy value, OR
    2. a peer-hold marker file ``<memory_root>/.peer-hold`` exists.
    Either is enough. A peer that holds the store drops ``.peer-hold``; a test
    simulates "busy" by setting the env var or touching the marker.

Kinds (payload schema per kind):
    "milestone"     {summary, commit?, run_id?, project?, repo?}
    "lesson"        {name, description, type, body, run_id, workdir, host,
                     scope?, project?, file?}
    "retro-durable" {run_id, sections, intent_one_line?, repo?}

Stdlib only. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_io import LockedFile, atomic_write_bytes  # type: ignore  # noqa: E402

QUEUE_DIRNAME = "pending-promotions"
QUEUE_FILENAME = "queue.jsonl"
DRAINED_FILENAME = "queue.drained.jsonl"
PEER_HOLD_MARKER = ".peer-hold"
BUSY_ENV = "BUILD_LOOP_MEMORY_BUSY"

VALID_KINDS = ("milestone", "lesson", "retro-durable")

LOCK_TIMEOUT_S = 5


def _iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() not in ("", "0", "false", "no", "off")


def store_busy(memory_root: Path | str | None = None) -> bool:
    """True when the canonical store is peer-held / unavailable for writes.

    Deterministic + injectable: env override OR a ``.peer-hold`` marker under
    ``memory_root``. Never raises — any error is treated as "not busy" so the
    normal write path proceeds (fail-open toward the existing behavior).
    """
    if _truthy(os.environ.get(BUSY_ENV)):
        return True
    if memory_root is None:
        return False
    try:
        return (Path(os.path.expanduser(str(memory_root))) / PEER_HOLD_MARKER).exists()
    except OSError:
        return False


class peer_hold:
    """Producer for the busy signal: hold the canonical store during a long op.

    f2 (auditor): ``store_busy`` had no producer in-repo, leaving enqueue-on-busy
    dormant. A session about to do an extended store operation wraps it in
    ``with peer_hold(memory_root):`` (or calls the ``hold`` / ``release`` CLI) to
    drop a ``<memory_root>/.peer-hold`` marker that peers see as busy. The
    complementary organic producer is ``append_milestone``'s fcntl lock-timeout,
    which already queues without any cooperative marker.

    Fail-soft: a missing/unwritable root degrades to a no-op (never raises).
    """

    def __init__(self, memory_root: Path | str) -> None:
        self.marker = Path(os.path.expanduser(str(memory_root))) / PEER_HOLD_MARKER
        self._held = False

    def __enter__(self) -> "peer_hold":
        try:
            self.marker.parent.mkdir(parents=True, exist_ok=True)
            self.marker.write_text(_iso_utc(), encoding="utf-8")
            self._held = True
        except OSError as exc:
            print(f"WARN: peer_hold could not set marker: {exc}", file=sys.stderr)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._held:
            try:
                self.marker.unlink(missing_ok=True)
            except OSError:
                pass


def queue_path(workdir: Path | str) -> Path:
    return Path(workdir) / ".build-loop" / QUEUE_DIRNAME / QUEUE_FILENAME


def _drained_path(workdir: Path | str) -> Path:
    return Path(workdir) / ".build-loop" / QUEUE_DIRNAME / DRAINED_FILENAME


def _append_row(path: Path, row: dict[str, Any]) -> bool:
    """Atomic append under a sidecar lock. Returns True on success (fail-soft)."""
    try:
        line = (json.dumps(row, separators=(",", ":"), default=str) + "\n").encode("utf-8")
        with LockedFile(path, timeout_s=LOCK_TIMEOUT_S):
            existing = path.read_bytes() if path.exists() else b""
            atomic_write_bytes(path, existing + line)
        return True
    except Exception as exc:  # noqa: BLE001 — fail-soft by contract
        print(f"WARN: promotion_queue append failed: {exc}", file=sys.stderr)
        return False


def enqueue(
    workdir: Path | str,
    *,
    kind: str,
    payload: dict[str, Any],
    reason: str = "",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Append one pending-promotion record. Returns an envelope; never raises.

    A record carries ``status: "pending"`` and a stable ``id`` (ts + kind).
    """
    if kind not in VALID_KINDS:
        return {"queued": False, "reason": f"invalid kind: {kind!r}"}
    ts = _iso_utc()
    row: dict[str, Any] = {
        "id": f"{ts.replace(':', '').replace('-', '')}-{kind}-{secrets.token_hex(3)}",
        "ts": ts,
        "kind": kind,
        "status": "pending",
        "reason": reason or "store busy — queued instead of skipped",
        "run_id": run_id,
        "payload": payload,
    }
    path = queue_path(workdir)
    ok = _append_row(path, row)
    return {
        "queued": ok,
        "kind": kind,
        "id": row["id"],
        "path": str(path),
        "reason": row["reason"] if ok else "queue append failed",
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"WARN: promotion_queue malformed row: {exc}", file=sys.stderr)
    except OSError as exc:
        print(f"WARN: promotion_queue read failed: {exc}", file=sys.stderr)
    return out


def list_pending(workdir: Path | str) -> list[dict[str, Any]]:
    """All still-pending queued promotions (fail-soft -> [])."""
    return [r for r in _read_jsonl(queue_path(workdir)) if r.get("status") == "pending"]


# --------------------------------------------------------------------------
# Drain — replay queued promotions against the now-free store via existing writers.
# --------------------------------------------------------------------------


def _apply_milestone(record: dict[str, Any], workdir: Path, memory_root: Path | None) -> dict[str, Any]:
    import append_milestone  # noqa: PLC0415 — lazy import keeps module load cheap

    p = record.get("payload") or {}
    return append_milestone.append_milestone(
        workdir=str(workdir),
        summary=str(p.get("summary") or ""),
        project=p.get("project"),
        commit=p.get("commit"),
        run_id=record.get("run_id") or p.get("run_id"),
        memory_root=str(memory_root) if memory_root else p.get("memory_root"),
        on_busy="skip",  # drain must not re-queue into the same queue it is draining
    )


def _apply_lesson(record: dict[str, Any], workdir: Path, memory_root: Path | None) -> dict[str, Any]:
    import memory_writer  # noqa: PLC0415
    from _paths import project_root, memory_store_root  # noqa: PLC0415

    p = record.get("payload") or {}
    scope = p.get("scope") or "project"
    project = p.get("project")
    # Enqueue sites never carry an explicit --memory-dir (those writes bypass the
    # queue, per _maybe_queue_lesson_on_busy), so resolve the canonical lane here.
    if scope == "project" and project:
        mem_dir = project_root(project) / "lessons"
    else:
        mem_dir = memory_store_root() / "lessons"
    fm = memory_writer.write(
        memory_dir=mem_dir,
        file_rel=p.get("file") or "",
        body=str(p.get("body") or ""),
        name=str(p.get("name") or "queued-lesson"),
        description=str(p.get("description") or ""),
        type_=str(p.get("type") or "lesson"),
        run_id=str(record.get("run_id") or p.get("run_id") or "queued"),
        workdir=str(workdir),
        host=str(p.get("host") or "claude_code"),
        scope=scope,
        project=project,
    )
    return {"status": "ok", "name": fm.get("name")}


def _apply_retro(record: dict[str, Any], workdir: Path, memory_root: Path | None) -> dict[str, Any]:
    from retrospective import write as retro_write  # noqa: PLC0415

    p = record.get("payload") or {}
    return retro_write.promote_durable(
        workdir=workdir,
        run_id=str(record.get("run_id") or p.get("run_id") or "queued"),
        sections=p.get("sections") or {},
        intent_one_line=p.get("intent_one_line"),
        repo=p.get("repo") or "",
        memory_root=memory_root,
    )


_APPLIERS: dict[str, Callable[[dict[str, Any], Path, Path | None], dict[str, Any]]] = {
    "milestone": _apply_milestone,
    "lesson": _apply_lesson,
    "retro-durable": _apply_retro,
}


def drain(
    workdir: Path | str,
    *,
    memory_root: Path | str | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Replay every pending queued promotion against the store.

    Skips entirely (no-op) when the store is still busy — the queue is preserved
    for the next pass. Each successfully written record is moved to
    ``queue.drained.jsonl``; a still-failing record stays pending. Fail-soft:
    a per-record error is captured, never raised.

    Returns ``{drained, remaining, results[], skipped_reason?}``.
    """
    workdir = Path(workdir)
    mem_root = Path(os.path.expanduser(str(memory_root))) if memory_root else None
    pending = list_pending(workdir)
    if not pending:
        return {"drained": 0, "remaining": 0, "results": []}
    if store_busy(mem_root):
        return {"drained": 0, "remaining": len(pending), "results": [],
                "skipped_reason": "store still busy — queue preserved"}
    if not apply:
        return {"drained": 0, "remaining": len(pending), "results": [],
                "skipped_reason": "apply=False (dry run)"}

    results: list[dict[str, Any]] = []
    drained_rows: list[dict[str, Any]] = []
    still_pending: list[dict[str, Any]] = []
    all_rows = _read_jsonl(queue_path(workdir))

    for row in all_rows:
        if row.get("status") != "pending":
            continue
        kind = row.get("kind")
        applier = _APPLIERS.get(str(kind))
        if applier is None:
            row["status"] = "error"
            row["drain_reason"] = f"no applier for kind {kind!r}"
            drained_rows.append(row)
            results.append({"id": row.get("id"), "kind": kind, "status": "error"})
            continue
        try:
            outcome = applier(row, workdir, mem_root)
            row["status"] = "drained"
            row["drained_at"] = _iso_utc()
            row["drain_result"] = outcome
            drained_rows.append(row)
            results.append({"id": row.get("id"), "kind": kind, "status": "drained",
                            "outcome": outcome})
        except Exception as exc:  # noqa: BLE001 — one bad record must not stop the drain
            row["drain_error"] = f"{type(exc).__name__}: {exc}"
            still_pending.append(row)
            results.append({"id": row.get("id"), "kind": kind, "status": "failed",
                            "error": row["drain_error"]})

    # Rewrite the queue with only still-pending rows; append drained rows to the audit log.
    # f3 (concurrency): the snapshot (all_rows) was read WITHOUT the lock, so a peer
    # may have enqueued between snapshot and here. Re-read UNDER the lock and carry
    # forward any row we did not process (by id) so a concurrent enqueue is never
    # lost — the anti-silent-loss mechanism must not itself drop records.
    processed_ids = {r.get("id") for r in drained_rows} | {r.get("id") for r in still_pending}
    try:
        with LockedFile(queue_path(workdir), timeout_s=LOCK_TIMEOUT_S):
            fresh = _read_jsonl(queue_path(workdir))
            carried = [
                r for r in fresh
                if r.get("status") == "pending" and r.get("id") not in processed_ids
            ]
            keep = still_pending + carried
            body = "".join(
                json.dumps(r, separators=(",", ":"), default=str) + "\n" for r in keep
            ).encode("utf-8")
            atomic_write_bytes(queue_path(workdir), body)
            still_pending = keep
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: promotion_queue rewrite failed: {exc}", file=sys.stderr)
    for r in drained_rows:
        _append_row(_drained_path(workdir), r)

    return {
        "drained": len(drained_rows),
        "remaining": len(still_pending),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Inspect/drain the durable-promotion queue.")
    p.add_argument("--workdir", default=os.getcwd())
    p.add_argument("--memory-root", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List pending queued promotions.")
    d = sub.add_parser("drain", help="Replay pending promotions against the store.")
    d.add_argument("--dry-run", action="store_true")
    sub.add_parser("hold", help="Set the peer-hold marker (requires --memory-root).")
    sub.add_parser("release", help="Clear the peer-hold marker (requires --memory-root).")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    workdir = Path(os.path.expanduser(args.workdir))
    if args.cmd == "list":
        out: dict[str, Any] = {"pending": list_pending(workdir)}
    elif args.cmd in ("hold", "release"):
        if not args.memory_root:
            out = {"ok": False, "reason": "hold/release requires --memory-root"}
        else:
            marker = Path(os.path.expanduser(args.memory_root)) / PEER_HOLD_MARKER
            try:
                if args.cmd == "hold":
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text(_iso_utc(), encoding="utf-8")
                else:
                    marker.unlink(missing_ok=True)
                out = {"ok": True, "cmd": args.cmd, "marker": str(marker)}
            except OSError as exc:
                out = {"ok": False, "reason": str(exc)}
    else:
        out = drain(workdir, memory_root=args.memory_root, apply=not args.dry_run)

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
    else:
        if args.cmd == "list":
            print(f"pending: {len(out['pending'])}")
            for r in out["pending"]:
                print(f"  - {r.get('id')} [{r.get('kind')}] {r.get('reason')}")
        else:
            print(f"drained: {out.get('drained')} remaining: {out.get('remaining')}")
            if out.get("skipped_reason"):
                print(f"  skipped: {out['skipped_reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
