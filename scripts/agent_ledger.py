#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""agent_ledger.py — the build-loop agent-activity ledger (the instrument).

One append-only JSONL file, `.build-loop/agent-ledger.jsonl`, that every
agent-action writes to. Single writer = the orchestrator (sub-agents return
envelopes; the orchestrator appends a line per dispatch and per return). This
replaces today's scattered `state.json.escalations` / `judge-decisions.json` /
`*_status` fields with one joinable trail so you can answer "which model
designed this plan / executed each chunk / where did the Advisor step in / how
often did the fallback fire" at a glance.

Design constraints honored:
- **Stdlib only.** No third-party deps (KISS / minimal-dependencies rule).
- **Append-only JSONL.** Crash-safe and concurrency-safe (a partial final line
  is tolerated on read), matching the "progress in JSON, not markdown" rule.
- **Single writer.** The orchestrator owns the file; nested Mode B writes its
  slice and the parent merges (same parent-owes pattern as the auditor ladder).
- **Fail-open.** A ledger write must never wedge a build. `append()` swallows
  OSErrors and reports them in its return envelope rather than raising.

One line per agent-action, fields (see `LEDGER_FIELDS`):

    ts · run_id · phase · chunk_id ·
    agent · tier · model (resolved id) ·
    action (author|execute|re-plan|take-over|verify|gate) ·
    rung (0-3) · status (pass|fail|blocked|partial|variance) ·
    trigger ("2 fails@opus" | "riskSurfaceChange" | "planning-miss") ·
    refs (input plan / output commit) · note (failure evidence, why retry justified)

The module shells out to nothing — the orchestrator passes the already-resolved
commit SHA / model id in. (If a future caller needs git, resolve the binary
absolutely or `command -v`-guard it per the launchd-minimal-PATH discipline;
this module deliberately stays data-only so that question never arises here.)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# The canonical action + status vocabularies (mirror the spec's ledger schema).
ACTIONS = {"author", "execute", "re-plan", "take-over", "verify", "gate"}
STATUSES = {"pass", "fail", "blocked", "partial", "variance"}

# Ordered field list — the row is a dict, but this names the contract and lets
# `--summarize` and tests assert the shape without re-deriving it.
LEDGER_FIELDS = (
    "ts",
    "run_id",
    "phase",
    "chunk_id",
    "agent",
    "tier",
    "model",
    "action",
    "rung",
    "status",
    "trigger",
    "refs",
    "note",
)

LEDGER_RELPATH = (".build-loop", "agent-ledger.jsonl")


def default_ledger_path(workdir: Path) -> Path:
    """`.build-loop/agent-ledger.jsonl` under the given workdir."""
    return workdir.joinpath(*LEDGER_RELPATH)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_row(
    *,
    run_id: str,
    agent: str,
    action: str,
    phase: str | None = None,
    chunk_id: str | None = None,
    tier: str | None = None,
    model: str | None = None,
    rung: int | None = None,
    status: str | None = None,
    trigger: str | None = None,
    refs: dict[str, Any] | None = None,
    note: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:  # noqa: D401
    """Construct a ledger row dict in canonical field order.

    Required: `run_id`, `agent`, `action`. Everything else is optional so a
    minimal dispatch line is cheap to write. `action` and (when given) `status`
    are validated against the canonical vocabularies — an unknown value raises,
    because a typo'd action silently corrupts the joinable trail the ledger
    exists to provide (this is a build-time author error, not a runtime path,
    so raising here is correct; the fail-open boundary is `append()`'s I/O).
    """
    if not run_id:
        raise ValueError("run_id is required")
    if not agent:
        raise ValueError("agent is required")
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {sorted(ACTIONS)}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {sorted(STATUSES)}")
    if rung is not None and not (0 <= int(rung) <= 3):
        raise ValueError(f"rung must be 0-3, got {rung!r}")
    if refs is not None and not isinstance(refs, dict):
        # `refs` is a {input/output: ...} object by contract; a list/string would
        # silently corrupt downstream ledger consumers that index it as a dict.
        raise ValueError(f"refs must be a JSON object (dict), got {type(refs).__name__}")

    return {
        "ts": ts or _utc_now_iso(),
        "run_id": run_id,
        "phase": phase,
        "chunk_id": chunk_id,
        "agent": agent,
        "tier": tier,
        "model": model,
        "action": action,
        "rung": int(rung) if rung is not None else None,
        "status": status,
        "trigger": trigger,
        "refs": refs or {},
        "note": note,
    }


def append(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Append one row as a JSON line. Fail-open: never raises on I/O error.

    Returns an envelope `{"ok": bool, "path": str, "error": str|None}`. The
    caller (orchestrator) can surface a write failure in its report, but a
    ledger outage must never halt the build — the build is the product, the
    ledger is the instrument.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, sort_keys=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return {"ok": True, "path": str(path), "error": None}
    except OSError as exc:  # fail-open: I/O problems don't wedge the build
        return {"ok": False, "path": str(path), "error": str(exc)}


def read(path: Path) -> list[dict[str, Any]]:
    """Read all rows. Tolerates a torn final line (crash-during-append).

    A JSONL file written append-only can have at most a partial *last* line if
    the process died mid-write; any earlier malformed line is a genuine
    corruption and is skipped (not raised) so a single bad row can't blind the
    whole instrument. Missing file → empty list.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            # Torn/partial line — skip it rather than failing the whole read.
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate rows into the at-a-glance answers the ledger is for.

    Returns counts by action, by status, by (agent, model), by rung, and the
    advisor-specific tally (how often each rung fired, how often the fallback
    fired) — the numbers the A/B test reads to find whether Frontier planning
    actually pays.
    """
    rows = list(rows)
    by_action: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_agent_model: dict[str, int] = {}
    by_rung: dict[str, int] = {}
    advisor_rows: list[dict[str, Any]] = []

    for r in rows:
        a = r.get("action")
        if a:
            by_action[a] = by_action.get(a, 0) + 1
        s = r.get("status")
        if s:
            by_status[s] = by_status.get(s, 0) + 1
        agent = r.get("agent") or "?"
        model = r.get("model") or "?"
        key = f"{agent}:{model}"
        by_agent_model[key] = by_agent_model.get(key, 0) + 1
        rung = r.get("rung")
        if rung is not None:
            by_rung[str(rung)] = by_rung.get(str(rung), 0) + 1
        if agent == "advisor":
            advisor_rows.append(r)

    return {
        "total": len(rows),
        "by_action": by_action,
        "by_status": by_status,
        "by_agent_model": by_agent_model,
        "by_rung": by_rung,
        "advisor_invocations": len(advisor_rows),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--path", default=None, help="Override ledger path (default .build-loop/agent-ledger.jsonl).")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="Append one ledger row.")
    a.add_argument("--run-id", required=True)
    a.add_argument("--agent", required=True)
    a.add_argument("--action", required=True, choices=sorted(ACTIONS))
    a.add_argument("--phase", default=None)
    a.add_argument("--chunk-id", default=None)
    a.add_argument("--tier", default=None)
    a.add_argument("--model", default=None)
    a.add_argument("--rung", type=int, default=None)
    a.add_argument("--status", default=None, choices=sorted(STATUSES))
    a.add_argument("--trigger", default=None)
    a.add_argument("--refs", default=None, help="JSON object of input/output refs.")
    a.add_argument("--note", default=None)

    sub.add_parser("read", help="Print all rows as a JSON array.")
    sub.add_parser("summarize", help="Print the aggregate summary as JSON.")

    args = p.parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve()
    path = Path(args.path).expanduser() if args.path else default_ledger_path(workdir)

    if args.cmd == "append":
        refs = None
        if args.refs:
            try:
                refs = json.loads(args.refs)
            except json.JSONDecodeError as exc:
                print(json.dumps({"ok": False, "error": f"--refs not valid JSON: {exc}"}), file=sys.stderr)
                return 1
        try:
            row = build_row(
                run_id=args.run_id,
                agent=args.agent,
                action=args.action,
                phase=args.phase,
                chunk_id=args.chunk_id,
                tier=args.tier,
                model=args.model,
                rung=args.rung,
                status=args.status,
                trigger=args.trigger,
                refs=refs,
                note=args.note,
            )
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
        envelope = append(path, row)
        print(json.dumps(envelope, indent=2))
        # Fail-OPEN on I/O: a ledger (telemetry) outage must never wedge a build
        # whose only "failure" was that the instrument couldn't write. The build is
        # the product; the ledger is the instrument. Input/caller errors above
        # (bad action / bad --refs JSON) still exit nonzero — those are author
        # mistakes, not runtime outages — but a write failure here exits 0 with
        # ok:false in the envelope so the orchestrator can surface it without halting.
        return 0

    if args.cmd == "read":
        print(json.dumps(read(path), indent=2))
        return 0

    if args.cmd == "summarize":
        print(json.dumps(summarize(read(path)), indent=2))
        return 0

    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
