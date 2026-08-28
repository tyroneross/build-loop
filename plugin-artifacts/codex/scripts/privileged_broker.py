#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Attribute, coalesce, and record every privileged (admin-password) command.

WHY THIS EXISTS
---------------
On 2026-08-20 a Codex task ran ``sfltool dumpbtm`` at 01:29:11 PDT and again at
01:29:25 PDT.  macOS showed two administrator-password dialogs naming only
``sfltool`` — no app, no repository, no reason.  Both invocations came from the
SAME session and the SAME turn: the first returned empty output (the privileged
read was refused), the agent could not distinguish "denied" from "no matches",
so it retried 14 seconds later with a different shell wrapper.  Three separate
Codex sessions reached for the same host fact inside 27 minutes.  Nothing
coalesced them, nothing named them, nothing recorded that a prompt happened.

This module fixes the three separable faults:
  1. ANONYMITY  — a request carries who/where/what/why, printed next to the
                  dialog and appended to a durable ledger, before macOS asks.
  2. DUPLICATION — identical read-only requests single-flight through one
                  authorization; the rest wait and reuse the result.
  3. RETRY STORM — a denial, cancellation, or timeout is remembered for a
                  bounded window, so the next identical request is refused from
                  cache instead of opening another dialog.

THE LOAD-BEARING SAFETY PROPERTIES
----------------------------------
Each is an explicit branch below, not a comment:

  (a) NO PASSWORD EVER.  The broker never reads, stores, forwards, or replays an
      administrator password.  macOS performs the authentication; the broker only
      decides who triggers it and shares the resulting OUTPUT.  ``_reject_password_capture()``
      refuses argv that would route a password through this process
      (``sudo -S``, ``--stdin``, a set ``SUDO_ASKPASS``), and the child process
      never gets a piped stdin.
  (b) COALESCING IS NARROW.  Two requests share an authorization only when the
      resolved argv, scope, trust domain, mutating flag, uid, and registry entry
      are all identical — see ``request_key()``.  A different anything is a
      different key and can never inherit the first one's approval.
  (c) MUTATING NEVER COALESCES.  A mutating request gets a private key directory
      (``_keydir()``), never reads the cache, and never writes one.  It cannot
      join, be joined, or inherit — one request, one prompt.
  (d) A NEGATIVE IS NEVER AN APPROVAL.  A cached terminal state is replayed
      verbatim; ``denied``/``cancelled``/``timeout``/``failed`` replay as
      themselves.  There is no code path that upgrades a cached state.
  (e) UNAVAILABILITY IS NEVER APPROVAL.  When the broker root, the ledger, or the
      Ambient sink is unusable, ``coverage_gap()`` writes a machine-readable
      receipt with ``unattributed_possible: true`` and the configured risk-class
      behaviour applies — mutating refuses outright.  A missing record is
      reported as a gap, never as "no privileged request occurred".
  (f) AMBIENT OBSERVES, NEVER DECIDES.  The Ambient sink is write-only from this
      process.  No branch reads an Ambient response, so no Ambient state can
      approve, deny, terminate, or widen a request.

STATE MACHINE (every transition is a ledger event)
--------------------------------------------------
    requested ──► prompted ──► approved ──► completed
              │            └─► denied
              │            └─► cancelled
              │            └─► timeout
              │            └─► failed
              └─► coalesced ──► (terminal state of the owner, replayed)
              └─► expired    (a cached result aged out; a fresh request follows)

Every event carries ``initiating_task_id`` and the current ``waiter_task_ids``.

STORE LAYOUT (``$BUILD_LOOP_PRIVILEGED_ROOT``, default ``~/.build-loop/privileged``)
------------------------------------------------------------------------------------
    config.json             optional overrides (see DEFAULT_CONFIG)
    ledger.jsonl            append-only, hash-chained event log (durable visibility)
    ledger.lock             append serialization
    gaps.jsonl              coverage-gap receipts
    keys/<key>/owner.json   single-flight lease + heartbeat
    keys/<key>/result.json  terminal result + expires_at
    keys/<key>/waiters.jsonl
    keys/<key>/attempts     prompt-attempt counter (crash-loop bound)
    keys/<key>/stdout.bin   captured output, mode 0600

CLI
---
    privileged_broker.py classify --command "<shell string>" --json
    privileged_broker.py request  --argv sfltool dumpbtm --purpose "..." --task-id T [...] --json
    privileged_broker.py status  --json
    privileged_broker.py cancel  --key <key> --reason "..." --json
    privileged_broker.py verify-ledger --json
    privileged_broker.py gc --json

Exit codes for ``request``:
    0  completed (fresh or replayed from an approved result)
    1  denied / cancelled / timeout / failed
    2  refused by the broker itself (bad request, password-capture attempt,
       exhausted attempts, or a mutating request with no usable record)
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_EVENT = "buildloop.privileged.event/1"
SCHEMA_GAP = "buildloop.privileged.coverage_gap/1"
SCHEMA_RESULT = "buildloop.privileged.result/1"

HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE / "privileged_commands.json"

DEFAULT_CONFIG: dict[str, Any] = {
    # Seconds an owner's heartbeat may go stale before a waiter may take over.
    "lease_seconds": 30,
    # How often the owner refreshes its heartbeat while the command runs.
    "heartbeat_seconds": 2,
    # Hard bound on how many times ONE key may open a dialog inside one TTL
    # window. This is what stops a crash-looping owner from re-prompting.
    "max_prompt_attempts": 2,
    # Default wall-clock a waiter will wait before returning `timeout`.
    "default_timeout_seconds": 120,
    "ambient": {
        # "ledger-only": durable visibility via ledger.jsonl (Ambient ingests it).
        # "live":        additionally pipe each event to notify_command's stdin.
        "mode": "ledger-only",
        "notify_command": None,
        "notify_timeout_seconds": 3,
    },
    # What to do for each risk class when the coordinator cannot be used at all.
    # "proceed_uncoalesced" still prints attribution and still writes a gap receipt.
    "risk_class_behavior": {
        "read_only": "proceed_uncoalesced",
        "mutating": "refuse",
        "unknown": "proceed_uncoalesced",
    },
}

TERMINAL_STATES = {"completed", "denied", "cancelled", "timeout", "failed", "denied_exhausted"}
APPROVED_STATES = {"completed"}

# Shell operators that separate one command from the next.
_SEPARATORS = {"|", "||", "&&", ";", "&", "(", ")", "{", "}"}
_REDIRECTS = {">", ">>", "<", "<<", ">&", "<&"}


# --------------------------------------------------------------------------
# Store + config
# --------------------------------------------------------------------------
def broker_root() -> Path:
    """Per-user store root. Shared across repos, worktrees, and coding hosts.

    Deliberately NOT inside any repository: two Codex sessions in two different
    worktrees of the same repo must land on the same key directory, or they will
    not coalesce — which is exactly the observed failure.
    """
    env = os.environ.get("BUILD_LOOP_PRIVILEGED_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".build-loop" / "privileged"


def load_config(root: Path | None = None) -> dict[str, Any]:
    root = root or broker_root()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    path = root / "config.json"
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def ensure_root(root: Path) -> tuple[bool, str | None]:
    """Create the store with private permissions. Returns (usable, reason).

    The writability probe is UNIQUE PER CALLER and idempotent on removal.  A
    shared probe filename races: two concurrent callers both create it, the
    first unlinks it, and the second's unlink raises FileNotFoundError — which
    would report a perfectly healthy store as unusable and drop every racing
    request into the uncoalesced coverage-gap path.  That is the exact failure
    this module exists to prevent, so the probe must not be able to cause it.
    """
    probe = root / f".writable.{os.getpid()}.{threading.get_ident()}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        (root / "keys").mkdir(exist_ok=True)
        fd = os.open(str(probe), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return True, None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def now() -> float:
    return time.time()


def iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else now()))


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
def load_registry(path: Path | None = None) -> dict[str, Any]:
    path = path or REGISTRY_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "buildloop.privileged.registry/1":
        raise ValueError(f"unexpected registry schema: {data.get('schema')!r}")
    return data


def match_entry(registry: dict[str, Any], argv: list[str]) -> dict[str, Any] | None:
    """Return the most specific registry entry for ``argv``, or None.

    "Most specific" = longest matching ``argv_prefix``, so ``csrutil status``
    (unprivileged, explicitly listed) beats the ``csrutil`` catch-all.
    """
    if not argv:
        return None
    exe = Path(argv[0]).name
    rest = argv[1:]
    best: dict[str, Any] | None = None
    best_len = -1
    for entry in registry.get("entries", []):
        if entry.get("executable") != exe:
            continue
        prefix = entry.get("argv_prefix") or []
        if list(rest[: len(prefix)]) != list(prefix):
            continue
        if len(prefix) > best_len:
            best, best_len = entry, len(prefix)
    return best


def resolve_entry(registry: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Apply registry defaults to a matched entry."""
    merged = dict(registry.get("defaults", {}))
    merged.update(entry)
    merged.setdefault("privileged", True)
    return merged


# --------------------------------------------------------------------------
# Shell parsing — find the privileged argv inside a real command line
# --------------------------------------------------------------------------
def split_segments(command: str) -> list[list[str]]:
    """Split a shell command string into candidate argv segments.

    This is the reason coalescing works on the real incident. The two observed
    invocations were different SHELL STRINGS::

        sfltool dumpbtm 2>/dev/null | rg -n -C 2 'bash|env|...' | sed -n '1,260p'
        set -o pipefail
        sfltool dumpbtm | sed -n '1,120p'

    but the same PRIVILEGED ARGV: ``['sfltool', 'dumpbtm']``.  Matching on the
    string would have treated them as different requests and prompted twice.
    """
    segments: list[list[str]] = []
    for line in command.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError:
            continue  # unbalanced quotes — nothing safe to extract
        current: list[str] = []
        skip_next = False
        for idx, tok in enumerate(tokens):
            if skip_next:
                skip_next = False
                continue
            if tok in _SEPARATORS:
                if current:
                    segments.append(current)
                current = []
                continue
            if tok in _REDIRECTS:
                skip_next = True  # drop the redirect target too
                # a bare fd number immediately before the redirect belongs to it
                if current and current[-1].isdigit():
                    current.pop()
                continue
            # Leading VAR=value assignments are environment, not the executable.
            if not current and "=" in tok and not tok.startswith("=") and " " not in tok:
                name = tok.split("=", 1)[0]
                if name.replace("_", "").isalnum() and not name[:1].isdigit():
                    continue
            current.append(tok)
        if current:
            segments.append(current)
    return [s for s in segments if s]


def resolve_argv(argv: list[str]) -> list[str]:
    """Normalize argv[0] to an absolute path so identity does not depend on PATH."""
    if not argv:
        return argv
    exe = argv[0]
    if os.path.isabs(exe):
        return list(argv)
    found = shutil.which(exe)
    return [found or exe, *argv[1:]]


def classify_command(command: str, registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return one classification per privileged segment found in ``command``."""
    registry = registry or load_registry()
    out: list[dict[str, Any]] = []
    for seg in split_segments(command):
        entry = match_entry(registry, seg)
        if entry is None:
            continue
        merged = resolve_entry(registry, entry)
        if not merged.get("privileged", True):
            continue
        argv = resolve_argv(seg)
        out.append(
            {
                "entry_id": merged["id"],
                "argv": argv,
                "executable": argv[0],
                "scope": merged["scope"],
                "trust_domain": merged["trust_domain"],
                "mutating": bool(merged["mutating"]),
                "cacheable": bool(merged.get("cacheable", False)),
                "ttl_seconds": int(merged.get("ttl_seconds", 0)),
                "negative_ttl_seconds": int(merged.get("negative_ttl_seconds", 0)),
                "confidence": merged.get("confidence", "inferred"),
                "risk_class": "mutating" if merged["mutating"] else "read_only",
                # False for a privileged command that CANNOT open a dialog
                # (`sudo -n`). Still attributed and recorded; just never counted
                # as a prompt.
                "prompts": bool(merged.get("prompts", True)),
            }
        )
    return out


# --------------------------------------------------------------------------
# Request identity
# --------------------------------------------------------------------------
def request_key(
    argv: list[str],
    scope: str,
    trust_domain: str,
    mutating: bool,
    entry_id: str,
    registry_version: str,
) -> str:
    """The coalescing key. Anything that differs here NEVER shares an approval."""
    material = {
        "argv": list(argv),
        "scope": scope,
        "trust_domain": trust_domain,
        "mutating": bool(mutating),
        "entry_id": entry_id,
        "registry_version": registry_version,
        "uid": os.getuid(),
    }
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Ledger — append-only, hash-chained
# --------------------------------------------------------------------------
def _ledger_paths(root: Path) -> tuple[Path, Path]:
    return root / "ledger.jsonl", root / "ledger.lock"


def _acquire_lock(lock: Path, timeout: float = 5.0) -> int | None:
    deadline = now() + timeout
    while now() < deadline:
        try:
            return os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # Break a lock left by a dead process.
            try:
                if now() - lock.stat().st_mtime > timeout:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            time.sleep(0.01)
        except OSError:
            return None
    return None


def _release_lock(fd: int | None, lock: Path) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    lock.unlink(missing_ok=True)


def _last_hash(ledger: Path) -> str:
    try:
        with ledger.open("rb") as fh:
            tail = b""
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, 8192)
            fh.seek(size - block)
            tail = fh.read(block)
        for line in reversed(tail.splitlines()):
            if not line.strip():
                continue
            return json.loads(line).get("hash", "")
    except (OSError, ValueError):
        pass
    return ""


def append_event(root: Path, event: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Append one hash-chained event. Returns the stored record, or None on failure.

    Chain: ``hash = sha256(prev_hash + canonical(event_without_hash))``.  A
    deleted or edited line breaks the chain, so a missing record is DETECTABLE.
    That is what makes "unavailability is never proof that no privileged request
    occurred" checkable rather than aspirational — see ``verify_ledger()``.
    """
    ledger, lock = _ledger_paths(root)
    payload = dict(event)
    payload.setdefault("schema", SCHEMA_EVENT)
    payload.setdefault("timestamp", iso())
    fd = _acquire_lock(lock)
    if fd is None:
        # A lost event is a HOLE in the durable record. Saying nothing here would
        # make the ledger look complete when it is not, which is the one thing
        # this record must never do.
        coverage_gap(
            root, reason="ledger_lock_unavailable", risk_class="unknown",
            behavior="event_dropped",
            detail=f"could not append event {payload.get('event')!r} for key {payload.get('key')}",
        )
        return None
    try:
        prev = _last_hash(ledger)
        payload["prev"] = prev
        digest = hashlib.sha256((prev + canonical(payload)).encode("utf-8")).hexdigest()
        payload["hash"] = digest
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(canonical(payload) + "\n")
        try:
            os.chmod(ledger, 0o600)
        except OSError:
            pass
    except OSError:
        return None
    finally:
        _release_lock(fd, lock)
    _notify_ambient(root, payload, config or load_config(root))
    return payload


def verify_ledger(root: Path) -> dict[str, Any]:
    ledger, _ = _ledger_paths(root)
    if not ledger.exists():
        return {"ok": True, "records": 0, "broken_at": None, "reason": "no ledger yet"}
    prev = ""
    count = 0
    for lineno, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            return {"ok": False, "records": count, "broken_at": lineno, "reason": "unparseable line"}
        stored = rec.pop("hash", None)
        if rec.get("prev") != prev:
            return {"ok": False, "records": count, "broken_at": lineno, "reason": "prev-hash mismatch"}
        digest = hashlib.sha256((prev + canonical(rec)).encode("utf-8")).hexdigest()
        if digest != stored:
            return {"ok": False, "records": count, "broken_at": lineno, "reason": "hash mismatch"}
        prev = stored
        count += 1
    return {"ok": True, "records": count, "broken_at": None, "reason": None}


# --------------------------------------------------------------------------
# Ambient sink — write-only. Ambient observes; it never decides.
# --------------------------------------------------------------------------
_AMBIENT_GAP_ONCE: set[str] = set()


def _notify_ambient(root: Path, event: dict[str, Any], config: dict[str, Any]) -> None:
    """Best-effort live push to the Ambient Agent.

    The DURABLE arm is ledger.jsonl, which is already written by the time we get
    here; Ambient ingests it. This is only the LIVE arm.  The return value is
    intentionally discarded and never inspected: no Ambient response can change
    a broker verdict (safety property (f)).
    """
    ambient = config.get("ambient", {})
    mode = ambient.get("mode", "ledger-only")
    if mode != "live":
        if "ambient_live_unconfigured" not in _AMBIENT_GAP_ONCE:
            _AMBIENT_GAP_ONCE.add("ambient_live_unconfigured")
            coverage_gap(
                root,
                reason="ambient_live_unconfigured",
                risk_class="unknown",
                behavior="durable_only",
                detail="ambient.mode is 'ledger-only'; durable ledger visibility is on, live push is not configured",
            )
        return
    cmd = ambient.get("notify_command")
    if not cmd:
        coverage_gap(
            root,
            reason="ambient_notify_command_missing",
            risk_class="unknown",
            behavior="durable_only",
            detail="ambient.mode is 'live' but no notify_command is set",
        )
        return
    argv = cmd if isinstance(cmd, list) else shlex.split(cmd)
    try:
        subprocess.run(
            argv,
            input=canonical(event).encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=float(ambient.get("notify_timeout_seconds", 3)),
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        coverage_gap(
            root,
            reason="ambient_sink_unreachable",
            risk_class="unknown",
            behavior="durable_only",
            detail=f"notify_command failed: {argv[0] if argv else '<empty>'}",
        )


# --------------------------------------------------------------------------
# Coverage gaps
# --------------------------------------------------------------------------
def coverage_gap(
    root: Path,
    *,
    reason: str,
    risk_class: str,
    behavior: str,
    detail: str = "",
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit a machine-readable coverage-gap receipt.

    ``unattributed_possible`` is always true: the whole point of a gap receipt is
    that during this window a privileged request may have happened WITHOUT a
    ledger record.  Silence here is a gap, never a clean bill of health.
    """
    receipt = {
        "schema": SCHEMA_GAP,
        "timestamp": iso(),
        "reason": reason,
        "risk_class": risk_class,
        "behavior": behavior,
        "detail": detail,
        "unattributed_possible": True,
        "pid": os.getpid(),
    }
    if request:
        receipt["request"] = {
            k: request.get(k)
            for k in ("request_id", "task_id", "argv", "scope", "trust_domain", "repo", "purpose")
        }
    try:
        root.mkdir(parents=True, exist_ok=True)
        with (root / "gaps.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(canonical(receipt) + "\n")
    except OSError:
        # Nowhere to write. Say so on stderr so it is at least observable.
        print(canonical(receipt), file=sys.stderr)
    return receipt


# --------------------------------------------------------------------------
# Password-capture guard
# --------------------------------------------------------------------------
def _reject_password_capture(argv: list[str]) -> str | None:
    """Refuse any shape that would route a password through this process.

    Returns a reason string when the request must be refused, else None.
    """
    exe = Path(argv[0]).name if argv else ""
    flags = set(argv[1:])
    if exe == "sudo":
        if "-S" in flags or "--stdin" in flags:
            return "sudo -S/--stdin would read the password from a pipe this process controls"
        if "-A" in flags or "--askpass" in flags:
            return "sudo -A/--askpass delegates the password to a helper this process controls"
    if os.environ.get("SUDO_ASKPASS"):
        return "SUDO_ASKPASS is set; refusing to run a privileged command through an askpass helper"
    for tok in argv:
        low = tok.lower()
        if low.startswith("--password=") or low.startswith("-p=") or low == "--password":
            return "argv carries a password argument"
    return None


# --------------------------------------------------------------------------
# Attribution block — what the human reads next to the macOS dialog
# --------------------------------------------------------------------------
def attribution_block(req: dict[str, Any], waiters: int = 0, ttl: int = 0) -> str:
    argv = " ".join(shlex.quote(a) for a in req["argv"])
    who = req.get("initiating_app") or "unknown app"
    task = req.get("task_id") or "-"
    thread = req.get("thread_id") or "-"
    repo = req.get("repo") or "-"
    worktree = req.get("worktree")
    where = repo if not worktree or worktree == repo else f"{repo} (worktree {Path(worktree).name})"
    branch = req.get("branch") or "-"
    risk = "mutating — CHANGES HOST STATE" if req.get("mutating") else "read-only"
    lines = [
        "┌ ADMIN PASSWORD REQUEST ─────────────────────────────────────",
        f"│ macOS is about to ask for your admin password for: {Path(req['argv'][0]).name}",
        f"│ Who    {who} · task {task} · thread {thread}",
        f"│ Where  {where} · branch {branch}",
        f"│ What   {argv}",
        f"│ Why    {req.get('purpose', '')}",
        f"│ Scope  {req.get('scope')} · {risk} · trust={req.get('trust_domain')}",
    ]
    if req.get("mutating"):
        lines.append("│ Shared no — mutating requests never reuse another task's approval")
    elif ttl > 0:
        extra = f", {waiters} task(s) already waiting" if waiters else ""
        lines.append(f"│ Shared yes — identical read-only requests reuse this for {ttl}s{extra}")
    else:
        lines.append("│ Shared in-flight only — nothing is kept after this call returns")
    lines.append(f"│ When   {req.get('timestamp')} · request {req.get('request_id')}")
    lines.append("└─────────────────────────────────────────────────────────────")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Single-flight coordinator
# --------------------------------------------------------------------------
def _keydir(root: Path, key: str, mutating: bool, request_id: str) -> Path:
    # Safety property (c): a mutating request gets a PRIVATE directory keyed by
    # its own request id, so it can never join or be joined.
    name = f"{key}.{request_id}" if mutating else key
    return root / "keys" / name


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(canonical(obj), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _read_result(keydir: Path) -> dict[str, Any] | None:
    result = _read_json(keydir / "result.json")
    if result is None:
        return None
    expires = result.get("expires_at")
    if expires is not None and now() >= float(expires):
        return None
    return result


def _waiters(keydir: Path) -> list[str]:
    out: list[str] = []
    try:
        for line in (keydir / "waiters.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line).get("task_id", "?"))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _register_waiter(keydir: Path, req: dict[str, Any]) -> None:
    try:
        with (keydir / "waiters.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(canonical({"task_id": req.get("task_id"), "request_id": req["request_id"], "at": iso()}) + "\n")
    except OSError:
        pass


def _attempts(keydir: Path) -> int:
    try:
        return int((keydir / "attempts").read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _bump_attempts(keydir: Path) -> int:
    n = _attempts(keydir) + 1
    try:
        (keydir / "attempts").write_text(str(n), encoding="utf-8")
    except OSError:
        pass
    return n


def _roll_window(root: Path, keydir: Path, req: dict[str, Any], config: dict[str, Any]) -> None:
    """Retire an expired result and start a fresh TTL window for this key.

    Clears the cached result, its captured output, and the prompt-attempt
    counter together. They must move as a unit: an attempt counter that outlived
    its window would let a key that once hit the cap stay `denied_exhausted`
    permanently, converting a rate limit into a lockout.
    """
    stale = _read_json(keydir / "result.json") or {}
    for name in ("result.json", "stdout.bin"):
        (keydir / name).unlink(missing_ok=True)
    try:
        (keydir / "attempts").write_text("0", encoding="utf-8")
    except OSError:
        pass
    append_event(
        root,
        {
            "event": "expired", "request_id": req["request_id"], "key": req["key"],
            "initiating_task_id": stale.get("owner_task_id"),
            "waiter_task_ids": _waiters(keydir),
            "expired_state": stale.get("state"),
        },
        config,
    )


def _heartbeat_loop(owner_path: Path, stop: threading.Event, interval: float) -> None:
    while not stop.wait(interval):
        data = _read_json(owner_path)
        if data is None:
            return
        data["heartbeat"] = now()
        try:
            _write_json(owner_path, data)
        except OSError:
            return


def _classify_exit(returncode: int, stderr: bytes) -> str:
    """Map a privileged command's exit to a broker state.

    macOS returns a plain non-zero exit (and often EMPTY stdout) when the user
    cancels the SecurityAgent dialog — the observed incident's 10.6s and 30.6s
    invocations both returned nothing at all.  The broker cannot see the dialog,
    so it classifies conservatively: any non-zero exit is a NEGATIVE outcome and
    is remembered as one.  Misreading a genuine command error as a denial costs
    a bounded wait; misreading a denial as "no results" is what caused the retry.
    """
    if returncode == 0:
        return "completed"
    text = stderr.decode("utf-8", "replace").lower()
    if "cancel" in text:
        return "cancelled"
    if any(k in text for k in ("not permitted", "permission denied", "must be run as root", "authoriz", "sorry, try again", "incorrect password")):
        return "denied"
    return "failed"


def _run_privileged(req: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Invoke the command. macOS does the authentication; we never see a password."""
    start = now()
    try:
        proc = subprocess.run(
            req["argv"],
            capture_output=True,
            # stdin is INHERITED, never piped: a pipe here is how a password
            # would end up inside this process. sudo reads /dev/tty and macOS
            # SecurityAgent draws its own dialog, so inheriting is sufficient.
            stdin=None,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"state": "timeout", "exit_code": None, "stdout": b"", "stderr": b"", "duration": now() - start}
    except OSError as exc:
        return {
            "state": "failed",
            "exit_code": None,
            "stdout": b"",
            "stderr": str(exc).encode("utf-8"),
            "duration": now() - start,
        }
    return {
        "state": _classify_exit(proc.returncode, proc.stderr),
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "duration": now() - start,
    }


def _store_result(
    keydir: Path,
    req: dict[str, Any],
    outcome: dict[str, Any],
    ttl: int,
    negative_ttl: int,
    cacheable: bool,
) -> dict[str, Any]:
    approved = outcome["state"] in APPROVED_STATES
    if req.get("mutating"):
        # Safety property (c): a mutating result is never persisted for reuse.
        lifetime = 0.0
    elif approved:
        lifetime = float(ttl) if cacheable else 0.0
    else:
        # Safety property (d) + the anti-retry-storm control: a NEGATIVE is
        # remembered even when a positive would not be cached, because the point
        # is to refuse the retry, not to serve a result.
        lifetime = float(negative_ttl)

    stdout_path: str | None = None
    if outcome["stdout"]:
        blob = keydir / "stdout.bin"
        try:
            blob.write_bytes(outcome["stdout"])
            os.chmod(blob, 0o600)
            stdout_path = str(blob)
        except OSError:
            stdout_path = None

    result = {
        "schema": SCHEMA_RESULT,
        "key": req["key"],
        "state": outcome["state"],
        "exit_code": outcome["exit_code"],
        "stdout_path": stdout_path,
        "stdout_bytes": len(outcome["stdout"]),
        "stderr": outcome["stderr"].decode("utf-8", "replace")[:4000],
        "owner_request_id": req["request_id"],
        "owner_task_id": req.get("task_id"),
        "created_at": now(),
        # A lifetime of 0 means "in-flight join only": the record exists just long
        # enough for current waiters to read it, then reads treat it as expired.
        "expires_at": now() + lifetime if lifetime > 0 else now() + 5.0,
        "cacheable": bool(lifetime > 0),
        "duration_seconds": round(outcome["duration"], 3),
        "attempt": _attempts(keydir),
    }
    _write_json(keydir / "result.json", result)
    return result


def _terminal_event(root: Path, req: dict[str, Any], result: dict[str, Any], keydir: Path, config: dict[str, Any]) -> None:
    append_event(
        root,
        {
            "event": result["state"],
            "request_id": req["request_id"],
            "key": req["key"],
            "initiating_task_id": result.get("owner_task_id"),
            "waiter_task_ids": _waiters(keydir),
            "exit_code": result.get("exit_code"),
            "duration_seconds": result.get("duration_seconds"),
            "cached_until": iso(result["expires_at"]) if result.get("cacheable") else None,
            "scope": req.get("scope"),
            "trust_domain": req.get("trust_domain"),
            "mutating": req.get("mutating"),
            "purpose": req.get("purpose"),
            "initiating_app": req.get("initiating_app"),
            "risk_class": req.get("risk_class"),
        },
        config,
    )


def build_request(
    *,
    argv: list[str],
    purpose: str,
    task_id: str,
    thread_id: str | None = None,
    repo: str | None = None,
    worktree: str | None = None,
    branch: str | None = None,
    initiating_app: str | None = None,
    scope: str | None = None,
    trust_domain: str | None = None,
    mutating: bool | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the machine-readable request record (acceptance criterion 2)."""
    registry = registry or load_registry()
    argv = resolve_argv(argv)
    entry = match_entry(registry, argv)
    merged = resolve_entry(registry, entry) if entry else None

    if merged and not merged.get("privileged", True):
        raise ValueError(f"{' '.join(argv)} is registered as UNPRIVILEGED ({merged['id']}); run it directly")

    req = {
        "schema": "buildloop.privileged.request/1",
        "request_id": hashlib.sha256(f"{os.getpid()}:{now()}:{canonical(argv)}".encode()).hexdigest()[:16],
        "task_id": task_id,
        "thread_id": thread_id,
        "session_id": os.environ.get("SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID"),
        "repo": repo,
        "worktree": worktree,
        "branch": branch,
        "executable": argv[0],
        "argv": argv,
        "purpose": purpose,
        "scope": scope or (merged["scope"] if merged else "unregistered"),
        "trust_domain": trust_domain or (merged["trust_domain"] if merged else "local-admin"),
        "mutating": bool(mutating if mutating is not None else (merged["mutating"] if merged else True)),
        "entry_id": merged["id"] if merged else "unregistered",
        "registry_version": registry.get("version", "0"),
        "initiating_app": initiating_app or os.environ.get("BUILD_LOOP_INITIATING_APP") or "unknown",
        "host_process": Path(sys.argv[0]).name,
        "user": os.environ.get("USER") or "",
        "uid": os.getuid(),
        "timestamp": iso(),
        "confidence": merged.get("confidence", "unregistered") if merged else "unregistered",
    }
    req["risk_class"] = "mutating" if req["mutating"] else "read_only"
    if not merged:
        req["risk_class"] = "unknown"
    req["ttl_seconds"] = int(merged.get("ttl_seconds", 0)) if merged else 0
    req["negative_ttl_seconds"] = int(merged.get("negative_ttl_seconds", 300)) if merged else 300
    req["cacheable"] = bool(merged.get("cacheable", False)) if merged else False
    req["key"] = request_key(
        argv, req["scope"], req["trust_domain"], req["mutating"], req["entry_id"], req["registry_version"]
    )
    return req


def execute(
    req: dict[str, Any],
    *,
    timeout: float | None = None,
    root: Path | None = None,
    config: dict[str, Any] | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    """Run ``req`` through the single-flight coordinator. The whole state machine."""
    root = root or broker_root()
    config = config or load_config(root)
    timeout = float(timeout if timeout is not None else config["default_timeout_seconds"])

    # --- Refusals that happen before anything is touched -------------------
    if not req.get("purpose", "").strip():
        return {"state": "refused", "reason": "purpose_required", "prompt_opened": False, "coalesced": False}
    bad = _reject_password_capture(req["argv"])
    if bad:
        append_event(root, {"event": "refused", "request_id": req["request_id"], "key": req["key"], "reason": bad}, config)
        return {"state": "refused", "reason": bad, "prompt_opened": False, "coalesced": False}

    usable, why = ensure_root(root)
    if not usable:
        # Safety property (e): unavailability is never approval.
        behavior = config["risk_class_behavior"].get(req["risk_class"], "refuse")
        gap = coverage_gap(
            root, reason="broker_root_unusable", risk_class=req["risk_class"],
            behavior=behavior, detail=why or "", request=req,
        )
        if behavior == "refuse":
            return {"state": "refused", "reason": "broker_unavailable", "coverage_gap": gap,
                    "prompt_opened": False, "coalesced": False}
        if not quiet:
            print(attribution_block(req), file=sys.stderr)
            print("│ NOTE   coordinator unavailable — running UNCOALESCED and UNRECORDED", file=sys.stderr)
        outcome = _run_privileged(req, timeout)
        return {
            "state": outcome["state"], "exit_code": outcome["exit_code"],
            "stdout": outcome["stdout"].decode("utf-8", "replace"),
            "prompt_opened": True, "coalesced": False, "coverage_gap": gap, "recorded": False,
        }

    keydir = _keydir(root, req["key"], req["mutating"], req["request_id"])
    keydir.mkdir(parents=True, exist_ok=True)

    append_event(
        root,
        {
            "event": "requested",
            "request_id": req["request_id"], "key": req["key"],
            "initiating_task_id": req["task_id"], "waiter_task_ids": _waiters(keydir),
            "argv": req["argv"], "executable": req["executable"], "purpose": req["purpose"],
            "scope": req["scope"], "trust_domain": req["trust_domain"], "mutating": req["mutating"],
            "repo": req["repo"], "worktree": req["worktree"], "branch": req["branch"],
            "initiating_app": req["initiating_app"], "thread_id": req["thread_id"],
            "entry_id": req["entry_id"], "risk_class": req["risk_class"],
        },
        config,
    )

    owner_path = keydir / "owner.json"
    deadline = now() + timeout
    stole = False

    while True:
        # --- Cache read + TTL-window roll. Mutating skips both entirely
        #     (safety property (c)): it must never read or clear a shared record.
        #
        #     This sits at the TOP of the loop, not before it, on purpose. A
        #     waiter that polls for a result an instant before the owner writes
        #     one, then reads owner.json an instant after the owner removes it,
        #     leaves the wait loop and contests the lease. Re-reading the cache
        #     here is what stops that interleaving from invoking a second time —
        #     an extra prompt in a race is exactly the outcome this module exists
        #     to remove.
        if not req["mutating"]:
            cached = _read_result(keydir)
            if cached is not None and cached["state"] in TERMINAL_STATES:
                append_event(
                    root,
                    {
                        "event": "coalesced", "request_id": req["request_id"], "key": req["key"],
                        "initiating_task_id": cached.get("owner_task_id"),
                        "waiter_task_ids": [*_waiters(keydir), req["task_id"]],
                        "replayed_state": cached["state"], "source": "cache",
                    },
                    config,
                )
                _register_waiter(keydir, req)
                return _replay(cached, coalesced=True, source="cache")
            if (keydir / "result.json").exists() and not _read_json(owner_path):
                # The result aged out and nobody owns the key: a NEW TTL window
                # starts. Clearing the attempt counter with it is load-bearing —
                # without it a key that once hit the prompt cap would stay
                # `denied_exhausted` forever, turning a rate limit into a
                # permanent lockout.
                _roll_window(root, keydir, req, config)

        # --- Try to become the single-flight owner -------------------------
        try:
            fd = os.open(str(owner_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            became_owner = True
        except FileExistsError:
            became_owner = False
        except OSError as exc:
            if exc.errno == errno.EACCES:
                gap = coverage_gap(root, reason="keydir_unwritable", risk_class=req["risk_class"],
                                   behavior="refuse", detail=str(exc), request=req)
                return {"state": "refused", "reason": "keydir_unwritable", "coverage_gap": gap,
                        "prompt_opened": False, "coalesced": False}
            raise

        if became_owner:
            attempt = _bump_attempts(keydir)
            cap = int(config["max_prompt_attempts"])
            if attempt > cap:
                # Acceptance 7: a crashed owner cannot reopen prompts repeatedly.
                # The cap is terminal — every waiter gets a definite answer and
                # NOBODY prompts again for this key until the window rolls over.
                outcome = {"state": "denied_exhausted", "exit_code": None, "stdout": b"",
                           "stderr": f"prompt-attempt cap ({cap}) reached for this request".encode(),
                           "duration": 0.0}
                result = _store_result(keydir, req, outcome, req["ttl_seconds"],
                                       req["negative_ttl_seconds"], req["cacheable"])
                owner_path.unlink(missing_ok=True)
                _terminal_event(root, req, result, keydir, config)
                return _replay(result, coalesced=False, source="attempt_cap", prompt_opened=False)

            _write_json(owner_path, {
                "pid": os.getpid(), "request_id": req["request_id"], "task_id": req["task_id"],
                "started_at": now(), "heartbeat": now(), "attempt": attempt, "stole": stole,
            })
            waiters = _waiters(keydir)
            if not quiet:
                print(attribution_block(req, waiters=len(waiters), ttl=req["ttl_seconds"] if req["cacheable"] else 0),
                      file=sys.stderr)
            append_event(
                root,
                {
                    "event": "prompted", "request_id": req["request_id"], "key": req["key"],
                    "initiating_task_id": req["task_id"], "waiter_task_ids": waiters,
                    "attempt": attempt, "stole_lease": stole,
                },
                config,
            )

            stop = threading.Event()
            hb = threading.Thread(
                target=_heartbeat_loop, args=(owner_path, stop, float(config["heartbeat_seconds"])), daemon=True
            )
            hb.start()
            try:
                outcome = _run_privileged(req, max(1.0, deadline - now()))
            finally:
                stop.set()

            if outcome["state"] in APPROVED_STATES:
                append_event(
                    root,
                    {"event": "approved", "request_id": req["request_id"], "key": req["key"],
                     "initiating_task_id": req["task_id"], "waiter_task_ids": _waiters(keydir)},
                    config,
                )
            result = _store_result(keydir, req, outcome, req["ttl_seconds"],
                                   req["negative_ttl_seconds"], req["cacheable"])
            owner_path.unlink(missing_ok=True)
            _terminal_event(root, req, result, keydir, config)
            return _replay(result, coalesced=False, source="owner", prompt_opened=True)

        # --- I am a waiter -------------------------------------------------
        _register_waiter(keydir, req)
        append_event(
            root,
            {"event": "coalesced", "request_id": req["request_id"], "key": req["key"],
             "initiating_task_id": (_read_json(owner_path) or {}).get("task_id"),
             "waiter_task_ids": _waiters(keydir), "source": "in_flight"},
            config,
        )

        while now() < deadline:
            cached = _read_result(keydir)
            if cached is not None and cached["state"] in TERMINAL_STATES:
                # Safety property (d): replayed verbatim, never upgraded.
                return _replay(cached, coalesced=True, source="in_flight")
            owner = _read_json(owner_path)
            if owner is None:
                break  # owner finished or vanished — loop back and contest
            stale = now() - float(owner.get("heartbeat", 0)) > float(config["lease_seconds"])
            if stale and not _pid_alive(int(owner.get("pid", -1))):
                append_event(
                    root,
                    {"event": "owner_crashed", "request_id": req["request_id"], "key": req["key"],
                     "initiating_task_id": owner.get("task_id"), "waiter_task_ids": _waiters(keydir),
                     "dead_pid": owner.get("pid")},
                    config,
                )
                owner_path.unlink(missing_ok=True)
                stole = True
                break
            time.sleep(0.05)
        else:
            append_event(
                root,
                {"event": "timeout", "request_id": req["request_id"], "key": req["key"],
                 "initiating_task_id": req["task_id"], "waiter_task_ids": _waiters(keydir),
                 "reason": "waiter deadline"},
                config,
            )
            return {"state": "timeout", "exit_code": None, "stdout": "", "stderr": "",
                    "prompt_opened": False, "coalesced": True, "source": "waiter_deadline"}


def _replay(result: dict[str, Any], *, coalesced: bool, source: str, prompt_opened: bool = False) -> dict[str, Any]:
    stdout = ""
    path = result.get("stdout_path")
    if path:
        try:
            stdout = Path(path).read_bytes().decode("utf-8", "replace")
        except OSError:
            stdout = ""
    return {
        "state": result["state"],
        "exit_code": result.get("exit_code"),
        "stdout": stdout,
        "stderr": result.get("stderr", ""),
        "prompt_opened": prompt_opened,
        "coalesced": coalesced,
        "source": source,
        "owner_task_id": result.get("owner_task_id"),
        "attempt": result.get("attempt"),
        "recorded": True,
    }


# --------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------
def status(root: Path) -> dict[str, Any]:
    keys_dir = root / "keys"
    inflight, cached = [], []
    if keys_dir.is_dir():
        for kd in sorted(keys_dir.iterdir()):
            if not kd.is_dir():
                continue
            owner = _read_json(kd / "owner.json")
            if owner:
                inflight.append({"key": kd.name, "owner_task_id": owner.get("task_id"),
                                 "pid": owner.get("pid"), "attempt": owner.get("attempt"),
                                 "waiters": _waiters(kd)})
            result = _read_result(kd)
            if result:
                cached.append({"key": kd.name, "state": result["state"],
                               "expires_in": round(float(result["expires_at"]) - now(), 1),
                               "cacheable": result.get("cacheable")})
    return {"root": str(root), "in_flight": inflight, "cached": cached,
            "ledger": verify_ledger(root)}


def cancel(root: Path, key: str, reason: str, config: dict[str, Any]) -> dict[str, Any]:
    """Cancel an in-flight request. The cancellation reaches every waiter.

    Implemented by writing the terminal result the waiters are already polling
    for — so propagation needs no signalling channel and cannot miss a waiter
    that registered after the cancel was issued.
    """
    keys_dir = root / "keys"
    hits = [kd for kd in keys_dir.iterdir() if kd.is_dir() and kd.name.startswith(key)] if keys_dir.is_dir() else []
    if not hits:
        return {"cancelled": 0, "reason": "no such key"}
    for kd in hits:
        result = {
            "schema": SCHEMA_RESULT, "key": kd.name, "state": "cancelled", "exit_code": None,
            "stdout_path": None, "stdout_bytes": 0, "stderr": reason,
            "owner_request_id": None, "owner_task_id": (_read_json(kd / "owner.json") or {}).get("task_id"),
            "created_at": now(), "expires_at": now() + 300.0, "cacheable": True,
            "duration_seconds": 0.0, "attempt": _attempts(kd),
        }
        _write_json(kd / "result.json", result)
        (kd / "owner.json").unlink(missing_ok=True)
        append_event(root, {"event": "cancelled", "key": kd.name, "reason": reason,
                            "initiating_task_id": result["owner_task_id"],
                            "waiter_task_ids": _waiters(kd)}, config)
    return {"cancelled": len(hits), "keys": [kd.name for kd in hits]}


def forget(root: Path, key: str, config: dict[str, Any]) -> dict[str, Any]:
    """Drop a cached result so the next identical request asks again.

    The negative cache is what stops a retry storm, but it must not become a
    trap: a user who cancels a dialog and then decides to allow it should not
    have to wait out `negative_ttl_seconds`. This is that escape hatch.

    It can only ever CAUSE a prompt, never skip one — removing a cached result
    removes an answer, and the next request has to earn a new one.  A key with a
    live owner is left alone; forgetting a request that is currently running
    would strand its waiters.
    """
    keys_dir = root / "keys"
    if not keys_dir.is_dir():
        return {"forgotten": 0, "reason": "no store"}
    forgotten, skipped = [], []
    for keydir in sorted(keys_dir.iterdir()):
        if not keydir.is_dir() or not keydir.name.startswith(key):
            continue
        if _read_json(keydir / "owner.json"):
            skipped.append(keydir.name)
            continue
        stale = _read_json(keydir / "result.json") or {}
        for name in ("result.json", "stdout.bin", "attempts"):
            (keydir / name).unlink(missing_ok=True)
        append_event(
            root,
            {"event": "forgotten", "key": keydir.name,
             "initiating_task_id": stale.get("owner_task_id"),
             "waiter_task_ids": _waiters(keydir),
             "forgotten_state": stale.get("state")},
            config,
        )
        forgotten.append(keydir.name)
    return {"forgotten": len(forgotten), "keys": forgotten,
            "skipped_in_flight": skipped}


def gc(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    keys_dir = root / "keys"
    removed = []
    if keys_dir.is_dir():
        for kd in sorted(keys_dir.iterdir()):
            if not kd.is_dir():
                continue
            if _read_json(kd / "owner.json"):
                continue
            result = _read_json(kd / "result.json")
            if result is None or now() >= float(result.get("expires_at", 0)):
                append_event(root, {"event": "expired", "key": kd.name,
                                    "initiating_task_id": (result or {}).get("owner_task_id"),
                                    "waiter_task_ids": _waiters(kd)}, config)
                shutil.rmtree(kd, ignore_errors=True)
                removed.append(kd.name)
    return {"removed": removed, "count": len(removed)}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _emit(obj: Any, as_json: bool) -> None:
    print(json.dumps(obj, indent=None if as_json else 2, sort_keys=True))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", help="override the broker store root")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_classify = sub.add_parser("classify", help="find privileged segments in a shell command")
    p_classify.add_argument("--command", required=True)
    p_classify.add_argument("--json", action="store_true")

    p_req = sub.add_parser("request", help="run a privileged command through the coordinator")
    p_req.add_argument("--purpose", required=True, help="why this is needed, in one plain sentence")
    p_req.add_argument("--task-id", required=True)
    p_req.add_argument("--thread-id")
    p_req.add_argument("--repo")
    p_req.add_argument("--worktree")
    p_req.add_argument("--branch")
    p_req.add_argument("--initiating-app")
    p_req.add_argument("--scope")
    p_req.add_argument("--trust-domain")
    p_req.add_argument("--mutating", action="store_true", default=None)
    p_req.add_argument("--timeout", type=float)
    p_req.add_argument("--quiet", action="store_true", help="suppress the human attribution block")
    p_req.add_argument("--json", action="store_true")
    p_req.add_argument("--argv", nargs=argparse.REMAINDER, required=True,
                       help="the privileged command; must be last")

    p_status = sub.add_parser("status")
    p_status.add_argument("--json", action="store_true")

    p_cancel = sub.add_parser("cancel")
    p_cancel.add_argument("--key", required=True)
    p_cancel.add_argument("--reason", default="cancelled by operator")
    p_cancel.add_argument("--json", action="store_true")

    p_forget = sub.add_parser(
        "forget", help="drop a cached result so the next identical request asks again")
    p_forget.add_argument("--key", required=True, help="full key or unique prefix")
    p_forget.add_argument("--json", action="store_true")

    p_verify = sub.add_parser("verify-ledger")
    p_verify.add_argument("--json", action="store_true")

    p_gc = sub.add_parser("gc")
    p_gc.add_argument("--json", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(args.root).expanduser() if args.root else broker_root()
    config = load_config(root)

    if args.cmd == "classify":
        found = classify_command(args.command)
        _emit({"command": args.command, "privileged_segments": found, "count": len(found)}, args.json)
        return 0 if found else 1

    if args.cmd == "request":
        raw = [a for a in (args.argv or []) if a != "--"]
        if not raw:
            _emit({"state": "refused", "reason": "empty argv"}, args.json)
            return 2
        try:
            req = build_request(
                argv=raw, purpose=args.purpose, task_id=args.task_id, thread_id=args.thread_id,
                repo=args.repo, worktree=args.worktree, branch=args.branch,
                initiating_app=args.initiating_app, scope=args.scope,
                trust_domain=args.trust_domain, mutating=args.mutating,
            )
        except ValueError as exc:
            _emit({"state": "refused", "reason": str(exc)}, args.json)
            return 2
        result = execute(req, timeout=args.timeout, root=root, config=config, quiet=args.quiet)
        result["request"] = req
        _emit(result, args.json)
        if result["state"] == "refused":
            return 2
        return 0 if result["state"] in APPROVED_STATES else 1

    if args.cmd == "status":
        _emit(status(root), args.json)
        return 0
    if args.cmd == "cancel":
        _emit(cancel(root, args.key, args.reason, config), args.json)
        return 0
    if args.cmd == "forget":
        _emit(forget(root, args.key, config), args.json)
        return 0
    if args.cmd == "verify-ledger":
        out = verify_ledger(root)
        _emit(out, args.json)
        return 0 if out["ok"] else 1
    if args.cmd == "gc":
        _emit(gc(root, config), args.json)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
