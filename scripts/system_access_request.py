#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Run an OS-facing command once and make identical callers wait for its result.

The wrapper deliberately never handles passwords or attempts to infer approval.
It records that macOS-facing work was dispatched, then returns the process result.
An identical read-only request made while that work is pending waits for the first
request instead of opening a second system dialog. Only a failure *before*
``subprocess.run`` starts makes the request automatically retryable. A terminal
result remains deduplicated for a bounded interval; a later caller represents a
new request instead of an automatic retry storm.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ACTIVE = {"requested", "dispatched"}
TERMINAL = {"completed", "cancelled", "failed"}
RETRYABLE = "failed_to_dispatch"
_SENSITIVE_ARG = re.compile(r"(?:pass(?:word|wd)?|token|secret|api[-_]?key|authorization)", re.IGNORECASE)


def _state_dir(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    configured = os.environ.get("BUILD_LOOP_SYSTEM_ACCESS_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".build-loop" / "system-access-requests"


@contextmanager
def _locked_ledger(directory: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "ledger.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        ledger_path = directory / "ledger.json"
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            ledger = {"requests": {}}
        if not isinstance(ledger.get("requests"), dict):
            ledger = {"requests": {}}
        try:
            yield ledger_path, ledger
        finally:
            ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _signature(command: list[str], purpose: str, scope: str, risk: str) -> str:
    payload = json.dumps(
        {"command": command, "purpose": purpose, "scope": scope, "risk": risk},
        separators=(",", ":"), sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _display_command(command: list[str]) -> list[str]:
    """Keep the audit record useful without retaining credential arguments."""
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
    return {
        "command": _display_command(command),
        "created_at": _now(),
        "id": str(uuid.uuid4()),
        "purpose": purpose,
        "requester": requester,
        "risk": risk,
        "scope": scope,
        "status": "requested",
        "waiters": [],
    }


def _claim(
    directory: Path,
    signature: str,
    command: list[str],
    purpose: str,
    scope: str,
    risk: str,
    requester: str,
    undispatched_seconds: float,
    dedupe_seconds: float,
) -> tuple[str, dict[str, Any]]:
    """Return ``leader``, ``follower``, or ``terminal`` and the current request."""
    with _locked_ledger(directory) as (_, ledger):
        requests = ledger["requests"]
        prior = requests.get(signature)
        if prior and prior.get("status") == "requested" and _now() - float(prior.get("created_at", 0)) > undispatched_seconds:
            # The first owner died or errored before it marked dispatch. This is
            # the only automatic retry path: no system dialog could be opened yet.
            prior["status"] = RETRYABLE
            prior["finished_at"] = _now()
            prior["detail"] = "owner did not mark the OS request as dispatched"
        if prior and prior.get("status") in ACTIVE:
            waiters = prior.setdefault("waiters", [])
            if requester not in waiters and requester != prior.get("requester"):
                waiters.append(requester)
            return "follower", prior
        if prior and prior.get("status") in TERMINAL:
            finished_at = float(prior.get("finished_at", _now()))
            if _now() - finished_at <= dedupe_seconds:
                return "terminal", prior
        request = _new_request(command, purpose, scope, risk, requester)
        requests[signature] = request
        return "leader", request


def _update(directory: Path, signature: str, **fields: Any) -> dict[str, Any]:
    with _locked_ledger(directory) as (_, ledger):
        request = ledger["requests"][signature]
        request.update(fields)
        return dict(request)


def _get(directory: Path, signature: str) -> dict[str, Any] | None:
    with _locked_ledger(directory) as (_, ledger):
        request = ledger["requests"].get(signature)
        return dict(request) if request else None


def _message(prefix: str, request: dict[str, Any]) -> str:
    command = " ".join(request["command"])
    return (
        f"{prefix}: {request['purpose']} | scope: {request['scope']} | "
        f"command: {command} | request: {request['id']}"
    )


def _wait_for_result(directory: Path, signature: str, seconds: float) -> int:
    deadline = _now() + seconds
    while _now() < deadline:
        request = _get(directory, signature)
        if request and request.get("status") in TERMINAL:
            print(_message("SYSTEM REQUEST FINISHED", request), file=sys.stderr)
            return int(request.get("exit_code", 1))
        time.sleep(0.1)
    print("SYSTEM REQUEST STILL WAITING: the original request remains pending; no duplicate was sent.", file=sys.stderr)
    return 75  # EX_TEMPFAIL: caller may continue waiting, never automatically re-dispatch.


def run_request(args: argparse.Namespace, runner=subprocess.run) -> int:
    command = list(args.command)
    if not command or command[0] == "--":
        raise ValueError("supply the system command after --")
    if args.risk != "read-only":
        raise ValueError("only read-only requests can use this automatic single-flight wrapper")
    directory = _state_dir(args.state_dir)
    signature = _signature(command, args.purpose, args.scope, args.risk)
    role, request = _claim(
        directory,
        signature,
        command,
        args.purpose,
        args.scope,
        args.risk,
        args.requester,
        args.undispatched_seconds,
        args.dedupe_seconds,
    )
    if role == "follower":
        print(_message("SYSTEM REQUEST ALREADY WAITING", request), file=sys.stderr)
        return _wait_for_result(directory, signature, args.wait_seconds)
    if role == "terminal":
        print(_message("SYSTEM REQUEST ALREADY RESOLVED", request), file=sys.stderr)
        return int(request.get("exit_code", 1))

    print(_message("SYSTEM REQUEST PENDING", request), file=sys.stderr)
    try:
        _update(directory, signature, status="dispatched", dispatched_at=_now())
        result = runner(command, check=False)
    except OSError as exc:
        _update(directory, signature, status=RETRYABLE, finished_at=_now(), detail=str(exc), exit_code=127)
        print(f"SYSTEM REQUEST DID NOT START: {exc}; a later call may retry.", file=sys.stderr)
        return 127
    status = "completed" if result.returncode == 0 else "cancelled" if result.returncode == 130 else "failed"
    request = _update(directory, signature, status=status, finished_at=_now(), exit_code=result.returncode)
    print(_message("SYSTEM REQUEST FINISHED", request), file=sys.stderr)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purpose", required=True, help="human-readable reason for the OS request")
    parser.add_argument("--scope", required=True, help="specific data or system scope")
    parser.add_argument("--requester", default="codex", help="task or app initiating the request")
    parser.add_argument("--risk", default="read-only", choices=("read-only", "mutating"))
    parser.add_argument("--state-dir", help="test or explicit state directory")
    parser.add_argument("--wait-seconds", type=float, default=300)
    parser.add_argument("--undispatched-seconds", type=float, default=30)
    parser.add_argument(
        "--dedupe-seconds",
        type=float,
        default=300,
        help="retain a completed/denied/cancelled result before a new caller can request again",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    try:
        return run_request(parser.parse_args(argv))
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
