#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Record one OS-facing attempt per scope; consult the durable record before retrying.

This is dispatch coordination, not macOS authorization. A completed command does
not authorize another command or supply its output. Terminal scope holds never
expire. An explicit resume consumes an exact prior request plus a human-confirmation
reference once. Pending requests cannot be resumed or replaced by elapsed time.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ACTIVE = {"requested", "dispatched"}
TERMINAL = {"completed", "cancelled", "failed", "blocked"}
RETRYABLE = "failed_to_dispatch"
HELD = 75
_SENSITIVE_ARG = re.compile(r"(?:pass(?:word|wd)?|token|secret|api[-_]?key|authorization)", re.IGNORECASE)


def _state_dir(value: str | None) -> Path:
    return Path(value or os.environ.get("BUILD_LOOP_SYSTEM_ACCESS_STATE_DIR") or
                (Path.home() / ".build-loop" / "system-access-requests")).expanduser()


def _scope_key(scope: str) -> str:
    return " ".join(scope.split()).casefold()


def _load_ledger(path: Path) -> dict[str, Any]:
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"requests": {}}
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("system-access ledger is corrupt; no request was dispatched") from exc
    if not isinstance(ledger, dict) or not isinstance(ledger.get("requests"), dict):
        raise ValueError("invalid system-access ledger; no request was dispatched")
    ids = set()
    for record in ledger["requests"].values():
        if (not isinstance(record, dict) or
                not all(isinstance(record.get(key), str) and record[key].strip()
                        for key in ("id", "scope", "status")) or
                record["status"] not in ACTIVE | TERMINAL | {RETRYABLE} or
                record["id"] in ids):
            raise ValueError("invalid system-access request record; no request was dispatched")
        if (not all(isinstance(record.get(key), str) and record[key].strip()
                    for key in ("purpose", "requester", "risk")) or
                not isinstance(record.get("command"), list) or
                not all(isinstance(arg, str) for arg in record["command"]) or
                type(record.get("attempt", 0)) is not int or record.get("attempt", 0) < 0 or
                not isinstance(record.get("waiters", []), list) or
                not all(isinstance(waiter, str) for waiter in record.get("waiters", [])) or
                any(key in record and not isinstance(record[key], str)
                    for key in ("command_signature", "resumed_by", "user_confirmation_ref")) or
                ("exit_code" in record and type(record["exit_code"]) is not int)):
            raise ValueError("invalid system-access request fields; no request was dispatched")
        for key in ("created_at", "finished_at", "dispatched_at"):
            if key in record and (type(record[key]) not in (int, float) or not math.isfinite(record[key])):
                raise ValueError("invalid system-access request timestamp; no request was dispatched")
        ids.add(record["id"])
    return ledger


def _atomic_write(path: Path, ledger: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".ledger-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(ledger, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _locked_ledger(directory: Path, *, write: bool = True) -> Iterator[tuple[Path, dict[str, Any]]]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "ledger.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            path = directory / "ledger.json"
            ledger = _load_ledger(path)
            yield path, ledger
            if write:
                _atomic_write(path, ledger)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _signature(command: list[str], purpose: str, scope: str, risk: str) -> str:
    """Command identity, separate from the durable scope hold; purpose is display only."""
    payload = json.dumps({"command": command, "scope": _scope_key(scope), "risk": risk},
                         separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _display_command(command: list[str]) -> list[str]:
    displayed = list(command)
    redact_next = False
    for index, value in enumerate(displayed):
        if redact_next:
            displayed[index] = "<redacted>"
            redact_next = False
            continue
        key, separator, _ = value.partition("=")
        if _SENSITIVE_ARG.search(key):
            displayed[index] = f"{key}{separator}<redacted>" if separator else value
            redact_next = not bool(separator)
    return displayed


def _now() -> float:
    return time.time()


def _new_request(command: list[str], purpose: str, scope: str, risk: str, requester: str) -> dict[str, Any]:
    return {"command": _display_command(command), "created_at": _now(), "id": str(uuid.uuid4()),
            "command_signature": _signature(command, purpose, scope, risk),
            "purpose": purpose, "requester": requester, "risk": risk, "scope": scope,
            "status": "requested", "waiters": []}


def _scope_records(ledger: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    return [record for record in ledger["requests"].values()
            if _scope_key(record["scope"]) == _scope_key(scope)]


def _head(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    # Any legacy or current active request holds the entire scope. Neither TTL
    # nor an external observation can prove its requester stopped.
    active = [record for record in records if record["status"] in ACTIVE]
    if active:
        return active[0]
    tips = [record for record in records if not record.get("resumed_by")]
    # Legacy signatures could create unrelated attempts in one scope. A newer
    # pre-dispatch failure cannot erase an older, still-held dispatched result.
    terminal = [record for record in tips if record["status"] in TERMINAL]
    return max(terminal or tips, key=lambda record: (record.get("attempt", 0), record.get("created_at", 0), record["id"]), default=None)


def _claim(directory: Path, command: list[str], args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    with _locked_ledger(directory) as (_, ledger):
        records = _scope_records(ledger, args.scope)
        prior = _head(records)
        resume = getattr(args, "resume_request", None)
        if resume:
            if any(record.get("user_confirmation_ref") == args.user_confirmation_ref for record in records):
                raise ValueError("that human-confirmation reference was already consumed in this scope")
            if prior and prior["status"] in ACTIVE:
                raise ValueError("scope is pending; a live request cannot be resumed or replaced")
            if not prior or prior["id"] != resume or prior["status"] not in TERMINAL:
                raise ValueError("resume must name the current terminal request in this exact scope")
            if prior.get("resumed_by"):
                raise ValueError("that request's one-use resume was already consumed")
        elif prior and prior["status"] != RETRYABLE:
            waiters = prior.setdefault("waiters", [])
            if args.requester != prior.get("requester") and args.requester not in waiters:
                waiters.append(args.requester)
            return ("follower" if prior["status"] in ACTIVE else "terminal"), dict(prior)
        request = _new_request(command, args.purpose, args.scope, args.risk, args.requester)
        request["attempt"] = max((record.get("attempt", 0) for record in records), default=0) + 1
        if prior:
            prior["resumed_by"] = request["id"]
        if resume:
            request["resumes_request"] = resume
            request["user_confirmation_ref"] = args.user_confirmation_ref
        ledger["requests"][request["id"]] = request
        return "leader", dict(request)


def _get(directory: Path, request_id: str) -> dict[str, Any] | None:
    with _locked_ledger(directory, write=False) as (_, ledger):
        return next((dict(record) for record in ledger["requests"].values()
                     if record["id"] == request_id), None)


def _update(directory: Path, request_id: str, **fields: Any) -> dict[str, Any]:
    with _locked_ledger(directory) as (_, ledger):
        record = next(record for record in ledger["requests"].values() if record["id"] == request_id)
        record.update(fields)
        return dict(record)


def _message(prefix: str, request: dict[str, Any]) -> str:
    return (f"{prefix}: {request['purpose']} | scope: {request['scope']} | "
            f"command: {' '.join(request.get('command', []))} | request: {request['id']}")


def _replay_code(request: dict[str, Any], command: list[str], args: argparse.Namespace) -> int:
    expected = _signature(command, args.purpose, args.scope, args.risk)
    signature = request.get("command_signature")
    # Legacy records contain redacted argv. Only reconstruct identity when it
    # contains no redaction; otherwise do not claim this command already ran.
    if signature is None and "<redacted>" not in " ".join(request.get("command", [])):
        signature = _signature(request.get("command", []), "", request["scope"], request.get("risk", "read-only"))
    if request["status"] == "blocked" or signature != expected:
        return HELD
    if request["status"] == "completed":
        age = _now() - request.get("finished_at", 0)
        if not 0 <= age <= args.dedupe_seconds:
            return HELD  # stale success is not current evidence; never redispatch
    return int(request.get("exit_code", 1))


def _wait_for_result(directory: Path, request_id: str, command: list[str], args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline:
        request = _get(directory, request_id)
        if request and request["status"] in TERMINAL | {RETRYABLE}:
            print(_message("SYSTEM REQUEST FINISHED (recorded result; not re-executed)", request), file=sys.stderr)
            return _replay_code(request, command, args)
        time.sleep(0.1)
    print("SYSTEM REQUEST STILL WAITING: original request remains pending; no duplicate sent.", file=sys.stderr)
    return HELD


def _metadata(directory: Path, command: list[str], args: argparse.Namespace) -> int:
    record_blocked = getattr(args, "record_blocked", False)
    with _locked_ledger(directory, write=record_blocked) as (_, ledger):
        prior = _head(_scope_records(ledger, args.scope))
        if record_blocked and prior and prior["status"] in ACTIVE:
            raise ValueError("cannot record blocked over a pending request; requester termination is unproven")
        if record_blocked and (prior is None or prior["status"] == RETRYABLE):
            request = _new_request(command, args.purpose, args.scope, args.risk, args.requester)
            request.update(status="blocked", finished_at=_now(), exit_code=HELD,
                           evidence=args.evidence, origin="external_observation", executed=False)
            if prior:
                prior["resumed_by"] = request["id"]
                request["attempt"] = prior.get("attempt", 0) + 1
            ledger["requests"][request["id"]] = request
            prior = request
        print(json.dumps({"scope": args.scope, "request_id": prior["id"] if prior else None,
                          "status": prior["status"] if prior else "not_requested",
                          "dispatch_allowed": prior is None or prior["status"] == RETRYABLE,
                          "executed": False, "purpose": prior.get("purpose") if prior else args.purpose,
                          "evidence": prior.get("evidence") if prior else None}, sort_keys=True))
    return 0


def run_request(args: argparse.Namespace, runner=subprocess.run) -> int:
    for key in ("purpose", "scope", "requester"):
        value = getattr(args, key, None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be nonempty")
        setattr(args, key, value.strip())
    if args.risk != "read-only":
        raise ValueError("only read-only requests may use this wrapper")
    for option in ("wait_seconds", "dedupe_seconds"):
        value = getattr(args, option)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{option.replace('_', '-')} must be finite and nonnegative")
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    check = getattr(args, "check_only", False)
    blocked = getattr(args, "record_blocked", False)
    resume = getattr(args, "resume_request", None)
    confirmation = getattr(args, "user_confirmation_ref", None)
    if confirmation:
        args.user_confirmation_ref = confirmation = confirmation.strip()
    if bool(resume) != bool(confirmation):
        raise ValueError("resume-request and a nonempty user-confirmation-ref are required together")
    if (check and blocked) or (resume and (check or blocked)):
        raise ValueError("check-only, record-blocked and resume-request are mutually exclusive")
    if blocked and not (getattr(args, "evidence", None) or "").strip():
        raise ValueError("record-blocked requires evidence that the original requester has stopped")
    directory = _state_dir(args.state_dir)
    if check or blocked:
        return _metadata(directory, command, args)
    if not command or not command[0].strip():
        raise ValueError("supply the system command after --")
    role, request = _claim(directory, command, args)
    if role == "follower":
        print(_message("SYSTEM REQUEST ALREADY WAITING", request), file=sys.stderr)
        return _wait_for_result(directory, request["id"], command, args)
    if role == "terminal":
        print(_message("SYSTEM REQUEST ALREADY RESOLVED (recorded result; not re-executed)", request), file=sys.stderr)
        return _replay_code(request, command, args)
    print(_message("SYSTEM REQUEST PENDING", request), file=sys.stderr)
    try:
        _update(directory, request["id"], status="dispatched", dispatched_at=_now())
        result = runner(command, check=False)
    except OSError as exc:
        _update(directory, request["id"], status=RETRYABLE, finished_at=_now(), exit_code=127)
        print("SYSTEM REQUEST DID NOT START; a later call may retry.", file=sys.stderr)
        return 127
    status = "completed" if result.returncode == 0 else "cancelled" if result.returncode == 130 else "failed"
    request = _update(directory, request["id"], status=status, finished_at=_now(), exit_code=result.returncode)
    print(_message("SYSTEM REQUEST FINISHED", request), file=sys.stderr)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--scope", required=True, help="stable data/access scope; do not vary this to evade a hold")
    parser.add_argument("--requester", default="codex")
    parser.add_argument("--risk", default="read-only", choices=("read-only", "mutating"))
    parser.add_argument("--state-dir")
    parser.add_argument("--wait-seconds", type=float, default=300)
    parser.add_argument("--undispatched-seconds", type=float, default=30, help="legacy option; pending requests never expire")
    parser.add_argument("--dedupe-seconds", type=float, default=300, help="fresh-success replay interval only; scope holds never expire")
    parser.add_argument("--check-only", action="store_true", help="read scope metadata without dispatch")
    parser.add_argument("--record-blocked", action="store_true", help="record an external prompt only after its requester stopped")
    parser.add_argument("--evidence", help="reference proving the externally observed requester stopped")
    parser.add_argument("--resume-request", help="exact terminal request ID authorized for one new attempt")
    parser.add_argument("--user-confirmation-ref", help="reference to explicit human authorization; not a password")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    try:
        return run_request(parser.parse_args(argv))
    except (OSError, ValueError) as exc:
        print(f"SYSTEM REQUEST REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
