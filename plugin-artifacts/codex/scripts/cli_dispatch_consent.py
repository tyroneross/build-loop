#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Consent gate for shelling out to another vendor's LLM CLI.

Contract: references/cli-dispatch-consent-contract.md — read it before changing
anything here. This module is one of two implementations; Rally Point has the other,
and the conformance suite grades both.

Shelling to `claude -p`, `codex exec`, `agent -p`, or `ollama run` spends the
operator's money and runs an agent they are not directly supervising. The first such
dispatch per (product, vendor) requires explicit approval, and the answer is
remembered until the operator changes it.

WHAT THIS IS: tamper-EVIDENT. The gated process runs as the operator and can write
this store. What it cannot do is write it without breaking the hash chain and
changing the head hash the operator was last shown. Detection, not prevention.
Prevention needs a separate principal; that is deliberately not built here.

    python3 scripts/cli_dispatch_consent.py --product build-loop --vendor codex --check
    python3 scripts/cli_dispatch_consent.py --product build-loop --vendor codex --set auto
    python3 scripts/cli_dispatch_consent.py --verify-chain
    python3 scripts/cli_dispatch_consent.py --head
"""
from __future__ import annotations

import argparse
import hashlib
import re
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import LockedFile, atomic_write_bytes  # noqa: E402

PRODUCTS = ("build-loop", "rally-point")
VENDORS = ("claude", "codex", "cursor", "ollama")
MODES = ("once", "ask", "auto", "denied")
GRANTING_MODES = ("auto",)  # the ONLY mode that permits without asking
SCHEMA_VERSION = 2

DEPTH_ENV = "AGENT_DISPATCH_DEPTH"
DEPTH_CAP = 2
_TEST_STORE_ENV = "AGENT_CONSENT_STORE_PATH"

# Exit codes — mirrors the autonomy-gate convention.
EXIT_ALLOWED = 0
EXIT_MUST_ASK = 1
EXIT_DENIED = 2
EXIT_CHAIN_BROKEN = 3


def key_for(product: str, vendor: str) -> str:
    """Consent key. Vendor, never model id — model names drift and a key that
    re-prompts on every rename trains the operator to answer `auto` to everything."""
    if product not in PRODUCTS:
        raise ValueError(f"unknown product {product!r}; expected one of {PRODUCTS}")
    if vendor not in VENDORS:
        raise ValueError(f"unknown vendor {vendor!r}; expected one of {VENDORS}")
    return f"{product}:{vendor}"


def store_path() -> Path:
    """Fixed per-operator path, under a VENDOR-NEUTRAL directory.

    Not `~/.build-loop/`: Rally Point is independently installable and implements
    this same contract, so a build-loop-named directory would make a standalone
    Rally Point install write into another product's namespace — the exact boundary
    inversion the separate-implementations decision exists to avoid.

    The env override is honored ONLY inside a test process. As a general override it
    is a one-line bypass of everything above it: an agent points it at a file it just
    wrote and every check passes.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("AGENT_CONSENT_SELFTEST"):
        override = os.environ.get(_TEST_STORE_ENV)
        if override:
            return Path(override).expanduser()
    return Path.home() / ".agent-consent" / "cli-dispatch-consent.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(entry: dict[str, Any]) -> bytes:
    """Canonical bytes for hashing. `ensure_ascii=False` is LOAD-BEARING, not style.

    Python's default escapes non-ASCII to \\uXXXX; serde_json (Rally Point's
    implementation) emits raw UTF-8. `decided_in_repo` is a filesystem path, so a
    single accented character in a repo name made the two implementations compute
    different digests for the same entry — Rally Point would read a chain written by
    Build Loop as BROKEN and refuse every dispatch. Raw UTF-8 is the canonical form
    (RFC 8785) and the one serde_json produces natively, so Python moves, not Rust.
    """
    body = {k: v for k, v in entry.items() if k != "entry_sha256"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def entry_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(entry)).hexdigest()


def load(path: Path | None = None) -> dict[str, Any]:
    """Never raises. An unreadable store is NO consent, not consent."""
    p = path or store_path()
    empty = {"version": SCHEMA_VERSION, "log": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("log"), list):
        return empty
    return data


def verify_chain(data: dict[str, Any] | None = None, path: Path | None = None) -> dict[str, Any]:
    """Recompute every link. Returns {ok, head, length, broken_at, reason}.

    The chain alone proves nothing — an agent can recompute all of it. It works only
    against an anchor outside the agent's reach, which here is the operator holding
    the previously-shown head hash.
    """
    d = data if data is not None else load(path)
    log = d.get("log") or []
    prev: str | None = None
    for i, e in enumerate(log):
        if not isinstance(e, dict):
            return {"ok": False, "head": prev, "length": i, "broken_at": i,
                    "reason": f"entry {i} is not an object"}
        if e.get("seq") != i:
            return {"ok": False, "head": prev, "length": i, "broken_at": i,
                    "reason": f"entry {i} has seq {e.get('seq')!r}, expected {i}"}
        if e.get("prev_sha256") != prev:
            return {"ok": False, "head": prev, "length": i, "broken_at": i,
                    "reason": f"entry {i} prev_sha256 does not match entry {i - 1}"}
        want = entry_hash(e)
        if e.get("entry_sha256") != want:
            return {"ok": False, "head": prev, "length": i, "broken_at": i,
                    "reason": f"entry {i} content does not match its own hash (edited in place)"}
        prev = want
    return {"ok": True, "head": prev, "length": len(log), "broken_at": None, "reason": "chain verifies"}


def replay(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Derive current consent from the log. Last entry per key wins.

    State is derived, never stored alongside the log — a materialized map is one more
    thing that can disagree with the record it claims to summarize.
    """
    state: dict[str, dict[str, Any]] = {}
    for e in data.get("log") or []:
        if isinstance(e, dict) and e.get("key") and e.get("mode") in MODES:
            state[e["key"]] = e
    return state


def depth_status(env: dict[str, str] | None = None) -> dict[str, Any]:
    """One `auto` grant otherwise authorizes unbounded recursion:
    claude -> build-loop -> codex -> claude. Depth caps the cascade, and no consent
    answer the operator can give covers it."""
    raw = (env if env is not None else os.environ).get(DEPTH_ENV, "")
    if raw == "":
        return {"depth": 0, "exceeded": False, "reason": "depth unset; treated as 0"}
    try:
        depth = int(raw)
    except ValueError:
        # A garbage value is the shape a bypass attempt takes, so it reads as
        # exceeded rather than as 0.
        return {"depth": None, "exceeded": True,
                "reason": f"{DEPTH_ENV}={raw!r} is not an integer; refusing"}
    if depth < 0:
        # No legitimate caller counts backwards. A negative value buys recursion
        # headroom above the cap (-1 permits four levels, not two), so it is a
        # bypass wearing the costume of a number. Found by the adversarial suite,
        # 2026-08-21 — the contract said "non-integer" and stopped one step short.
        return {"depth": depth, "exceeded": True,
                "reason": f"{DEPTH_ENV}={raw!r} is negative; refusing"}
    return {"depth": depth, "exceeded": depth > DEPTH_CAP,
            "reason": f"dispatch depth {depth} (cap {DEPTH_CAP})"}


def record(product: str, vendor: str, mode: str, *, path: Path | None = None,
           repo: str | None = None, now: str | None = None,
           decided_via: str = "cli") -> dict[str, Any]:
    """Append one decision to the chain. Returns the stored entry."""
    k = key_for(product, vendor)
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    p = path or store_path()
    with LockedFile(p):
        data = load(p)
        log = data.get("log") or []
        prev = log[-1].get("entry_sha256") if log else None
        entry = {
            "seq": len(log),
            "key": k,
            "mode": mode,
            "decided_at": now or _now(),
            "decided_by": "user",
            "decided_via": decided_via,
            "decided_in_repo": repo or os.getcwd(),
            "prev_sha256": prev,
        }
        entry["entry_sha256"] = entry_hash(entry)
        log.append(entry)
        data["log"] = log
        data["version"] = SCHEMA_VERSION
        atomic_write_bytes(p, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())
    return entry


VENDOR_BINARIES = {"claude": "claude", "codex": "codex",
                   "cursor-agent": "cursor", "ollama": "ollama"}


def detect_vendor(command: str) -> str | None:
    """The vendor a shell command actually INVOKES, or None.

    Leading-token match per segment, so `grep codex f.txt` and `echo "use codex"`
    do not count — a mention is not a dispatch. Mirrors pre_bash_consent.sh's
    detection; kept here so the kill-switch logger can be precise without the
    hook having to extract anything first.
    """
    for seg in re.split(r"[;|&\n]+", command or ""):
        tok = seg.strip().split()
        if not tok:
            continue
        base = os.path.basename(tok[0])
        if base in VENDOR_BINARIES:
            return VENDOR_BINARIES[base]
    return None


def note_kill_switch(*, path: Path | None = None, repo: str | None = None,
                     command: str | None = None, vendor: str | None = None) -> dict[str, Any]:
    """Record that a dispatch proceeded with BUILD_LOOP_HOOKS=off.

    The switch is not removed — the hook's own history records that misfiring gates
    teach operators to disable them permanently. Logged, so it is visible.
    """
    p = path or store_path()
    with LockedFile(p):
        data = load(p)
        log = data.get("log") or []
        prev = log[-1].get("entry_sha256") if log else None
        entry = {
            "seq": len(log),
            "key": "kill_switch_used",
            "mode": "ask",  # never grants
            "decided_at": _now(),
            "decided_by": "environment",
            "decided_via": "BUILD_LOOP_HOOKS=off",
            "decided_in_repo": repo or os.getcwd(),
            # The command is recorded so a reader can tell a real bypassed
            # dispatch from a false positive without re-deriving it. Truncated:
            # this is an audit marker, not a shell history.
            "bypassed_command": (command or "")[:200],
            "bypassed_vendor": vendor or detect_vendor(command or ""),
            "prev_sha256": prev,
        }
        entry["entry_sha256"] = entry_hash(entry)
        log.append(entry)
        data["log"] = log
        data["version"] = SCHEMA_VERSION
        atomic_write_bytes(p, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())
    return entry


def check(product: str, vendor: str, *, path: Path | None = None,
          env: dict[str, str] | None = None) -> dict[str, Any]:
    """May this (product, vendor) pair dispatch WITHOUT asking right now?

    `env` feeds the DEPTH GUARD ONLY. It deliberately does not reach `store_path()`,
    which reads the real environment directly: a caller-supplied mapping that could
    redirect the store would reintroduce exactly the override this contract removes.
    In-process tests must patch `os.environ` itself, not pass `env=`.

    Every non-`auto` outcome returns allowed=False. The branches below are the
    never-relax rule: each way of failing to be a verified, literal `auto` is its own
    code path, not a documentation claim.
    """
    k = key_for(product, vendor)

    # Depth is checked FIRST — it overrides any recorded consent, because no answer
    # the operator gave was an answer about recursion.
    d = depth_status(env)
    if d["exceeded"]:
        return {"allowed": False, "mode": None, "needs_prompt": False, "key": k,
                "exit": EXIT_DENIED, "reason": d["reason"]}

    data = load(path)
    chain = verify_chain(data)
    if not chain["ok"]:
        return {"allowed": False, "mode": None, "needs_prompt": True, "key": k,
                "exit": EXIT_CHAIN_BROKEN,
                "reason": f"consent log does not verify ({chain['reason']}); treating as no consent"}

    entry = replay(data).get(k)
    if not isinstance(entry, dict):
        return {"allowed": False, "mode": None, "needs_prompt": True, "key": k,
                "exit": EXIT_MUST_ASK,
                "reason": f"no consent recorded for {k}; the operator has not been asked"}

    mode = entry["mode"]
    if mode in GRANTING_MODES:
        return {"allowed": True, "mode": mode, "needs_prompt": False, "key": k,
                "exit": EXIT_ALLOWED,
                "reason": f"operator set {k} to {mode} on {entry.get('decided_at')}"}
    if mode == "denied":
        return {"allowed": False, "mode": mode, "needs_prompt": False, "key": k,
                "exit": EXIT_DENIED,
                "reason": f"operator denied {k} on {entry.get('decided_at')}"}
    # ask / once — a prior answer exists, but it grants nothing forward.
    return {"allowed": False, "mode": mode, "needs_prompt": True, "key": k,
            "exit": EXIT_MUST_ASK,
            "reason": f"operator chose {mode!r} for {k}; ask again before dispatching"}


def request_text(product: str, vendor: str, command: str, *, path: Path | None = None) -> str:
    """The approval REQUEST. This module never authors the answer."""
    prior = check(product, vendor, path=path)
    known = "" if prior["mode"] is None else f"\nYou previously chose: {prior['mode']!r}."
    return (
        f"{product} wants to run {vendor} through its command line.\n"
        f"  command: {command}\n"
        f"This spends your API credit and runs an agent you are not directly watching."
        f"{known}\n"
        f"Allow {product} to dispatch to {vendor}?  [once | ask each time | auto | deny]"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", choices=PRODUCTS)
    ap.add_argument("--vendor", choices=VENDORS)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--set", dest="mode", choices=MODES)
    ap.add_argument("--request-for", metavar="COMMAND", help="print the approval request text")
    ap.add_argument("--verify-chain", action="store_true")
    ap.add_argument("--head", action="store_true", help="print the chain head hash")
    ap.add_argument("--note-kill-switch", action="store_true")
    ap.add_argument("--from-event", action="store_true",
                    help="read the raw PreToolUse event on stdin")
    ap.add_argument("--command", default="", help="command being bypassed")
    ap.add_argument("--json", action="store_true", dest="emit_json")
    a = ap.parse_args(argv)

    if a.verify_chain or a.head:
        v = verify_chain()
        if a.head:
            print((v["head"] or "empty") if not a.emit_json else json.dumps(v, indent=2))
        else:
            print(json.dumps(v, indent=2) if a.emit_json else f"{'ok' if v['ok'] else 'BROKEN'}: {v['reason']}")
        return EXIT_ALLOWED if v["ok"] else EXIT_CHAIN_BROKEN

    if a.note_kill_switch:
        cmd, cwd = a.command, None
        if a.from_event:
            # One spawn does parse + detect + append. The hook cannot afford an
            # extraction pass before the kill switch — that is the one place it
            # is contractually required to do no work.
            try:
                ev = json.load(sys.stdin)
                cmd = (ev.get("tool_input") or {}).get("command", "")
                cwd = ev.get("cwd") or None
            except Exception:
                cmd, cwd = cmd or "", None
        v = detect_vendor(cmd or "")
        if a.from_event and not v:
            # A mention is not a dispatch. Nothing bypassed the gate, so nothing
            # is appended — the chain stays a record of real events.
            print(json.dumps({"logged": False, "reason": "no vendor invocation in command"})
                  if a.emit_json else "no vendor invocation; nothing logged")
            return EXIT_ALLOWED
        e = note_kill_switch(repo=cwd, command=cmd, vendor=v)
        print(json.dumps(e, indent=2) if a.emit_json else f"logged kill-switch use at seq {e['seq']}")
        return EXIT_MUST_ASK

    if not (a.product and a.vendor):
        ap.error("--product and --vendor are required for --check/--set/--request-for")

    if a.mode:
        entry = record(a.product, a.vendor, a.mode)
        head = verify_chain()["head"]
        out = {"key": entry["key"], "recorded": entry, "head": head, "store": str(store_path())}
        print(json.dumps(out, indent=2) if a.emit_json
              else f"{entry['key']}: {a.mode}\nconsent head: {head}")
        return EXIT_ALLOWED

    if a.request_for:
        print(request_text(a.product, a.vendor, a.request_for))
        return EXIT_MUST_ASK

    out = {**check(a.product, a.vendor), "store": str(store_path())}
    print(json.dumps(out, indent=2) if a.emit_json else f"{out['key']}: {out['reason']}")
    return int(out["exit"])


if __name__ == "__main__":
    sys.exit(main())
