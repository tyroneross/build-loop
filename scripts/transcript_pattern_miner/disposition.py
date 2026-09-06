#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Terminal states for mined candidates, so a closed one stops costing anything.

The miner regenerates `.candidates.json` from scratch on every run and nothing
records what happened to a candidate afterwards. `learn_accruing.py` counts them,
`self_review/gather.py` partitions them by shape, `session_end_retro_sweep.py`
fires the miner again -- three read paths, zero write-backs. So a correction
cluster seen ten times in August is rediscovered in September at full cost, and
the top-5 cap means it also crowds out whatever is new.

This is the same read->effect gap already measured for memory: 41,128 reads and
zero outcome labels. A read loop with no effect loop is a cost with no product.

The vocabulary is build-loop's own, not a parallel one
-----------------------------------------------------
`scripts/waivers.py` states the constitution's C-FINDINGS rule: every finding a
run surfaces reaches exactly one terminal state before "done" -- **fixed**,
**waived** against a durable record, or **escalated** as a new record. A mined
candidate is a finding. It gets the same three states and the same requirement
that each names a record, because a disposition with nothing to point at is an
assertion rather than a disposition.

`open` is also accepted, as the verb that REOPENS a candidate. It is not a
terminal state; it exists so a wrong close is reversible without editing history.

Why suppressed candidates are still reported
--------------------------------------------
A closed candidate that recurs is new information, not noise: it means the fix
did not hold. Dropping it silently would reproduce the append-only-ledger defect
where a resolved item can never be re-raised. So `partition` moves it out of the
ranked list -- which is the cost fix -- and into a `suppressed` list carrying its
disposition and its current count, where a reader can see that the thing came
back.

Local-only, append-only, stdlib. No network.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "build-loop.transcript-candidate-disposition.v1"

# The C-FINDINGS triple, plus the reopen verb. `open` is deliberately not in
# TERMINAL_STATES: a caller asking "is this closed" must not be able to read a
# reopen as a close.
TERMINAL_STATES = ("fixed", "waived", "escalated")
STATES = TERMINAL_STATES + ("open",)

LEDGER_NAME = "dispositions.jsonl"

# Fields that identify a candidate across runs, per shape. Chosen to be stable
# under re-mining the same underlying pattern and to CHANGE when the pattern
# genuinely differs. Counts and timestamps are excluded on purpose: a cluster
# that grows from 4 to 10 occurrences is the same cluster, and keying on the
# count would silently reopen it every week.
IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "user_correction_cluster": ("representative_quote",),
    "repeated_tool_sequence": ("sequence",),
    "bash_ritual": ("command_shape",),
    "cross_project_file": ("file",),
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def identity(candidate: dict[str, Any]) -> dict[str, Any]:
    """The subset of a candidate that decides whether it is 'the same one'."""
    shape = str(candidate.get("shape", ""))
    fields = IDENTITY_FIELDS.get(shape)
    if fields is None:
        # An unknown shape must still get a stable id rather than colliding with
        # every other unknown shape. Fall back to the whole candidate minus the
        # fields that legitimately drift between runs.
        drifting = {"count", "session_count", "last_seen", "first_seen",
                    "sample_sessions", "candidate_id", "rationale", "projects"}
        return {k: v for k, v in sorted(candidate.items()) if k not in drifting}
    return {"shape": shape, **{f: candidate.get(f) for f in fields}}


def candidate_id(candidate: dict[str, Any]) -> str:
    """Stable 12-hex id for a candidate. Same pattern next week -> same id."""
    blob = json.dumps(identity(candidate), sort_keys=True, ensure_ascii=False,
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def stamp(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach `candidate_id` in place and return the list.

    Called before anything else looks at a candidate, so every consumer -- the
    JSON file, the report, the memory router -- names the same thing the same
    way.
    """
    out = []
    for candidate in candidates:
        candidate["candidate_id"] = candidate_id(candidate)
        out.append(candidate)
    return out


def ledger_path(out_dir: Path) -> Path:
    return Path(out_dir) / LEDGER_NAME


def load(path: Path) -> dict[str, dict[str, Any]]:
    """Latest row per candidate_id. Append-only file, last write wins.

    A malformed line is skipped rather than fatal: this ledger is hand-editable
    by design, and one bad line must not hide every good one behind it.
    """
    path = Path(path)
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = row.get("candidate_id")
        if isinstance(cid, str) and cid:
            out[cid] = row
    return out


def is_closed(row: dict[str, Any] | None) -> bool:
    return bool(row) and row.get("state") in TERMINAL_STATES


def append(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Append one row. O_APPEND so concurrent miners cannot lose each other's.

    A read-modify-write of a single JSON document would drop a row whenever the
    SessionEnd sweep and the scheduled run overlapped; the same reasoning that
    put fleet-installs.jsonl beside fleet-policy.json rather than inside it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row


def close(
    out_dir: Path,
    cid: str,
    state: str,
    record: str,
    *,
    rationale: str = "",
    shape: str = "",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Record a terminal state for one candidate. `record` is mandatory.

    A disposition that names nothing to point at is an assertion, not a
    disposition -- the same bar `waivers.py new` sets with --rationale and
    --authority. `fixed` should name a commit sha or the path that changed;
    `waived`, a waiver or decision file; `escalated`, a backlog or task id.
    """
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}, got {state!r}")
    if state in TERMINAL_STATES and not str(record).strip():
        raise ValueError(
            f"state {state!r} needs a --record: a commit sha for fixed, a waiver "
            "or decision path for waived, a backlog/task id for escalated")
    row = {
        "schema": SCHEMA,
        "candidate_id": cid,
        "shape": shape,
        "state": state,
        "record": str(record).strip(),
        "rationale": str(rationale).strip(),
        "ts": (now or _now()).isoformat(),
    }
    return append(ledger_path(out_dir), row)


def mark_routed(out_dir: Path, cid: str, memory_path: str,
                *, shape: str = "", now: dt.datetime | None = None) -> dict[str, Any]:
    """Note that a candidate was drafted into memory. NOT a terminal state.

    Deliberately separate from `close`: drafting a memory entry is an effect, not
    a decision. The draft still carries `status: candidate` and still needs a
    human to confirm it, so recording it as `fixed` here would close a loop that
    nobody has actually closed.
    """
    return append(ledger_path(out_dir), {
        "schema": SCHEMA,
        "candidate_id": cid,
        "shape": shape,
        "state": "routed",
        "record": str(memory_path),
        "rationale": "auto-drafted to memory as a candidate awaiting confirmation",
        "ts": (now or _now()).isoformat(),
    })


def partition(
    candidates: list[dict[str, Any]],
    closed: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (open, suppressed). Suppressed entries keep their disposition.

    Reported rather than dropped: a candidate that was closed and came back
    means the fix did not hold, which is a stronger signal than the original
    finding was.
    """
    open_out: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for candidate in candidates:
        cid = candidate.get("candidate_id") or candidate_id(candidate)
        row = closed.get(cid)
        if is_closed(row):
            entry = dict(candidate)
            entry["disposition"] = {
                "state": row.get("state"),
                "record": row.get("record"),
                "rationale": row.get("rationale"),
                "closed_at": row.get("ts"),
            }
            suppressed.append(entry)
        else:
            open_out.append(candidate)
    return open_out, suppressed


def _default_out_dir() -> Path:
    return Path.home() / ".build-loop" / "transcript-patterns"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="transcript-candidate-disposition",
        description="Give a mined candidate a terminal state so it stops recurring.")
    ap.add_argument("--out-dir", default=str(_default_out_dir()),
                    help="miner output directory holding candidates + ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("close", help="record fixed / waived / escalated / open")
    c.add_argument("candidate_id")
    c.add_argument("--state", required=True, choices=list(STATES))
    c.add_argument("--record", default="",
                   help="commit sha (fixed) | waiver or decision path (waived) | "
                        "backlog or task id (escalated)")
    c.add_argument("--rationale", default="")
    c.add_argument("--shape", default="")
    c.add_argument("--json", action="store_true")

    l = sub.add_parser("list", help="current disposition of every known candidate")
    l.add_argument("--json", action="store_true")
    l.add_argument("--state", choices=list(STATES) + ["routed"],
                   help="filter to one state")

    s = sub.add_parser("show", help="the open + suppressed split of the last mine")
    s.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)

    if args.cmd == "close":
        try:
            row = close(out_dir, args.candidate_id, args.state, args.record,
                        rationale=args.rationale, shape=args.shape)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(row, indent=2) if args.json
              else f"{args.candidate_id}: {args.state} -> {row['record'] or '(reopened)'}")
        return 0

    if args.cmd == "list":
        rows = load(ledger_path(out_dir))
        if args.state:
            rows = {k: v for k, v in rows.items() if v.get("state") == args.state}
        if args.json:
            print(json.dumps(list(rows.values()), indent=2))
        elif not rows:
            print(f"no dispositions recorded in {ledger_path(out_dir)}")
        else:
            for cid, row in sorted(rows.items(), key=lambda kv: kv[1].get("ts", "")):
                print("%s  %-10s %-24s %s" % (cid, row.get("state", "?"),
                                              (row.get("shape") or "-")[:24],
                                              row.get("record", "")))
        return 0

    candidates_path = out_dir / ".candidates.json"
    if not candidates_path.exists():
        print(f"no candidates file at {candidates_path}", file=sys.stderr)
        return 1
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    open_list = payload.get("candidates", [])
    suppressed = payload.get("suppressed", [])
    if args.json:
        print(json.dumps({"open": open_list, "suppressed": suppressed}, indent=2))
        return 0
    print(f"{len(open_list)} open candidate(s), {len(suppressed)} suppressed")
    for candidate in open_list:
        print("  [open]       %s  %s" % (candidate.get("candidate_id", "?"),
                                         candidate.get("shape", "?")))
    for candidate in suppressed:
        disp = candidate.get("disposition", {})
        print("  [%-9s] %s  %s  <- %s" % (disp.get("state", "?"),
                                          candidate.get("candidate_id", "?"),
                                          candidate.get("shape", "?"),
                                          disp.get("record", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
