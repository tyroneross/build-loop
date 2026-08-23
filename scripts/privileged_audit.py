#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Read-only forensics over privileged (admin-password) command traffic.

Two jobs, one data model:

  scan    Reconstruct what ALREADY happened, from agent transcripts alone.  This
          is the BEFORE picture — the world with no coordinator, where every
          privileged invocation opens its own dialog.  Read-only: it opens
          transcripts and nothing else, and never runs a privileged command.

  report  Put the transcript baseline next to the broker's own ledger and print
          before/after counts for privileged invocations, OS prompts, coalesced
          requests, retries, and unattributed requests.

COUNTING RULES (stated so the numbers are falsifiable)
------------------------------------------------------
privileged_invocations
    One per privileged SEGMENT found in a tool call's shell command, matched
    against ``privileged_commands.json``.  ``sfltool dumpbtm | rg x`` is one.

os_prompts
    BEFORE: equal to privileged_invocations.  Nothing coalesced, so each
    invocation is modelled as its own dialog.  CAVEAT: ``sudo`` keeps its own
    sudoers timestamp cache, so consecutive ``sudo`` calls inside that window may
    have shown fewer real dialogs than counted.  ``sfltool`` and the other
    SecurityAgent commands have no such cache, so the count is exact for them.
    AFTER: the broker's ``prompted`` ledger events — an exact count of the times
    the broker actually invoked a privileged command.

retries
    Repeat invocations of the SAME coalescing key, from the same session, inside
    ``--window`` seconds.  N invocations in a window count as N-1 retries.  This
    is the metric the 2026-08-20 incident maximises: two identical requests, 14
    seconds apart, same session, same turn.

simultaneous_tasks
    Distinct SESSIONS that invoked the same key inside the window.  This is the
    cross-task duplication a single-flight coordinator removes.

distinct_requests
    Genuinely different work: the number of distinct coalescing keys.  This is
    the floor — the number of prompts an ideal coordinator could not avoid.

unattributed
    Invocations with no recorded purpose/task attribution.  BEFORE this is every
    invocation, by construction: a raw shell call carries no purpose.  AFTER it
    is the count of coverage-gap receipts, because a gap window is exactly when a
    privileged request may have happened with no record.

CLI
---
    privileged_audit.py scan   [--since YYYY-MM-DD] [--window 300] [--source all] --json
    privileged_audit.py report [--since YYYY-MM-DD] [--window 300] --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import privileged_broker as pb  # noqa: E402

SCHEMA_REPORT = "buildloop.privileged.audit/1"

# Cheap pre-filter so we JSON-parse only the lines that could possibly matter.
# Derived from the registry at load time, never hand-maintained.
_TIMESTAMP_KEYS = ("timestamp", "ts", "time")
_COMMAND_KEY_RE = re.compile(r'["\']?(?:cmd|command)["\']?\s*:\s*(?=")')


def default_transcript_roots() -> dict[str, Path]:
    return {
        "codex": Path.home() / ".codex" / "sessions",
        "claude": Path.home() / ".claude" / "projects",
    }


def executables(registry: dict[str, Any]) -> set[str]:
    return {e["executable"] for e in registry.get("entries", [])}


def extract_commands(text: str) -> list[str]:
    """Pull shell command strings out of one already-unescaped source string.

    Codex stores the real command a level deeper than JSON: the tool call's
    ``input`` is a JavaScript SOURCE string containing
    ``tools.exec_command({"cmd": "sfltool dumpbtm", ...})``.  Regex-capturing the
    value would corrupt the embedded quotes and newlines that the observed
    incident actually contained, so find each ``cmd``/``command`` key and
    JSON-DECODE the string literal that follows it instead.
    """
    out: list[str] = []
    decoder = json.JSONDecoder()
    for match in _COMMAND_KEY_RE.finditer(text):
        try:
            value, _ = decoder.raw_decode(text, match.end())
        except ValueError:
            continue
        if isinstance(value, str) and value.strip():
            out.append(value)
    return out


def tool_call_inputs(record: dict[str, Any]) -> list[Any]:
    """Return the INPUT payloads of tool calls in one transcript record.

    Scoped deliberately to calls, never outputs or prose.  A tool-call OUTPUT
    often echoes the command back, and an assistant message may quote a command
    it never ran; counting either would inflate the baseline.  Only what the
    agent actually asked to execute is counted.
    """
    inputs: list[Any] = []

    # Codex rollout: {"type": "response_item", "payload": {"type": "custom_tool_call", "input": "<js>"}}
    payload = record.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") in ("custom_tool_call", "function_call", "local_shell_call"):
            for field in ("input", "arguments", "action"):
                if payload.get(field) is not None:
                    inputs.append(payload[field])

    # Claude Code transcript: {"message": {"content": [{"type": "tool_use", "input": {...}}]}}
    message = record.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    if item.get("input") is not None:
                        inputs.append(item["input"])

    # Hook/host events that carry the command directly.
    if isinstance(record.get("tool_input"), dict):
        inputs.append(record["tool_input"])

    return inputs


def commands_in(value: Any, key: str | None = None) -> Iterator[str]:
    """Yield every shell command reachable inside a tool-call input payload."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from commands_in(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from commands_in(v, key)
    elif isinstance(value, str):
        if key in ("cmd", "command") and value.strip():
            yield value
        else:
            # A nested source string (Codex's JS `input`) may itself hold cmd keys.
            yield from extract_commands(value)


def parse_ts(record: dict[str, Any]) -> float | None:
    for key in _TIMESTAMP_KEYS:
        raw = record.get(key)
        if not isinstance(raw, str):
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return None


def iter_transcript_lines(roots: dict[str, Path], since: float | None) -> Iterator[tuple[str, Path, str]]:
    for source, root in roots.items():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            try:
                if since is not None and path.stat().st_mtime < since - 86400:
                    continue
            except OSError:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        yield source, path, line
            except OSError:
                continue


def scan(
    *,
    roots: dict[str, Path] | None = None,
    since: float | None = None,
    window: float = 300.0,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconstruct the BEFORE picture from transcripts. Read-only."""
    registry = registry or pb.load_registry()
    roots = roots if roots is not None else default_transcript_roots()
    exes = executables(registry)

    invocations: list[dict[str, Any]] = []
    files_scanned: set[str] = set()

    for source, path, line in iter_transcript_lines(roots, since):
        files_scanned.add(str(path))
        if not any(exe in line for exe in exes):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        ts = parse_ts(record)
        if since is not None and ts is not None and ts < since:
            continue
        seen_commands: set[str] = set()
        for payload in tool_call_inputs(record):
            seen_commands.update(commands_in(payload))
        for command in sorted(seen_commands):
            for found in pb.classify_command(command, registry):
                key = pb.request_key(
                    found["argv"], found["scope"], found["trust_domain"],
                    found["mutating"], found["entry_id"], registry.get("version", "0"),
                )
                invocations.append({
                    "source": source,
                    "session": path.stem,
                    "timestamp": ts,
                    "iso": pb.iso(ts) if ts else None,
                    "key": key,
                    "entry_id": found["entry_id"],
                    "argv": found["argv"],
                    "mutating": found["mutating"],
                    "risk_class": found["risk_class"],
                    "prompts": found.get("prompts", True),
                    "ttl_seconds": found["ttl_seconds"],
                    "cacheable": found["cacheable"],
                    "negative_ttl_seconds": found["negative_ttl_seconds"],
                    "command": command,
                })

    invocations.sort(key=lambda i: (i["timestamp"] or 0.0))

    # --- retries: same key, same session, inside the window ----------------
    retries = 0
    retry_detail: list[dict[str, Any]] = []
    last_seen: dict[tuple[str, str], dict[str, Any]] = {}
    for inv in invocations:
        bucket = (inv["session"], inv["key"])
        prev = last_seen.get(bucket)
        if prev and inv["timestamp"] and prev["timestamp"] and inv["timestamp"] - prev["timestamp"] <= window:
            retries += 1
            retry_detail.append({
                "key": inv["key"], "session": inv["session"], "entry_id": inv["entry_id"],
                "first": prev["iso"], "repeat": inv["iso"],
                "gap_seconds": round(inv["timestamp"] - prev["timestamp"], 1),
                "first_command": prev["command"], "repeat_command": inv["command"],
            })
        last_seen[bucket] = inv

    # --- simultaneous tasks: distinct sessions on one key inside the window --
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for inv in invocations:
        by_key[inv["key"]].append(inv)
    simultaneous: list[dict[str, Any]] = []
    for key, group in by_key.items():
        sessions_in_window: set[str] = set()
        for i, inv in enumerate(group):
            for other in group[i + 1:]:
                if not (inv["timestamp"] and other["timestamp"]):
                    continue
                if other["timestamp"] - inv["timestamp"] > window:
                    break
                if other["session"] != inv["session"]:
                    sessions_in_window.update({inv["session"], other["session"]})
        if len(sessions_in_window) > 1:
            simultaneous.append({
                "key": key, "entry_id": group[0]["entry_id"],
                "sessions": sorted(sessions_in_window), "invocations": len(group),
            })

    per_entry = defaultdict(int)
    for inv in invocations:
        per_entry[inv["entry_id"]] += 1

    total = len(invocations)
    prompting = sum(1 for i in invocations if i.get("prompts", True))
    return {
        "schema": SCHEMA_REPORT,
        "mode": "scan",
        "generated_at": pb.iso(),
        "window_seconds": window,
        "files_scanned": len(files_scanned),
        "counts": {
            "privileged_invocations": total,
            # BEFORE: nothing coalesces, so one prompting invocation == one dialog.
            # `sudo -n` and friends are privileged but cannot prompt, so they are
            # attributed and counted as invocations, never as dialogs.
            "os_prompts": prompting,
            "coalesced": 0,
            "retries": retries,
            # BEFORE: a raw shell call carries no purpose or task id at all.
            "unattributed": total,
            "distinct_requests": len(by_key),
            "sessions": len({i["session"] for i in invocations}),
            "simultaneous_task_clusters": len(simultaneous),
        },
        "by_entry": dict(sorted(per_entry.items(), key=lambda kv: -kv[1])),
        "retry_detail": retry_detail,
        "simultaneous": simultaneous,
        "avoidable_prompts": max(0, prompting - len(by_key)),
        "counterfactual": counterfactual(invocations),
    }


def counterfactual(invocations: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay observed invocations through the coalescing rules. No execution.

    Answers one question from the trace alone: how many dialogs would the broker
    have opened for the traffic that ALREADY happened?  It is a counterfactual on
    real input, not a measurement of a running system — the live 'after' numbers
    come from the ledger and fill in as brokered traffic accrues.

    Deliberately conservative in the broker's disfavour: every mutating request
    counts as its own prompt, and a read-only hit only suppresses a prompt when a
    live cache entry exists at that instant.
    """
    cache: dict[str, float] = {}
    prompts = 0
    coalesced = 0
    for inv in sorted(invocations, key=lambda i: (i["timestamp"] or 0.0)):
        if not inv.get("prompts", True):
            continue  # cannot open a dialog either way
        ts = inv["timestamp"] or 0.0
        if inv["mutating"]:
            prompts += 1  # never coalesces, never cached
            continue
        expires = cache.get(inv["key"])
        if expires is not None and ts < expires:
            coalesced += 1
            continue
        prompts += 1
        ttl = inv["ttl_seconds"] if inv["cacheable"] else 0
        cache[inv["key"]] = ts + max(ttl, 0)
    return {"os_prompts": prompts, "coalesced": coalesced, "retries": 0}


def ledger_counts(root: Path | None = None, since: float | None = None) -> dict[str, Any]:
    """The AFTER picture, from the broker's own hash-chained ledger."""
    root = root or pb.broker_root()
    ledger = root / "ledger.jsonl"
    counts = defaultdict(int)
    keys: set[str] = set()
    tasks: set[str] = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            ts = parse_ts(record)
            if since is not None and ts is not None and ts < since:
                continue
            counts[record.get("event", "?")] += 1
            if record.get("key"):
                keys.add(record["key"])
            if record.get("initiating_task_id"):
                tasks.add(record["initiating_task_id"])

    gaps = 0
    gap_reasons = defaultdict(int)
    gaps_path = root / "gaps.jsonl"
    if gaps_path.exists():
        for line in gaps_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            ts = parse_ts(record)
            if since is not None and ts is not None and ts < since:
                continue
            gaps += 1
            gap_reasons[record.get("reason", "?")] += 1

    return {
        "root": str(root),
        "ledger_present": ledger.exists(),
        "integrity": pb.verify_ledger(root),
        "counts": {
            "privileged_invocations": counts["requested"],
            "os_prompts": counts["prompted"],
            "coalesced": counts["coalesced"],
            # The broker never re-invokes on its own; a retry can only appear as
            # a second prompt on a key that already had one.
            "retries": max(0, counts["prompted"] - len(keys)),
            # A gap window is the ONLY place a privileged request can now happen
            # without a record. Unavailability is counted, never assumed clean.
            "unattributed": gaps,
            "distinct_requests": len(keys),
            "sessions": len(tasks),
        },
        "events": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "coverage_gaps": {"total": gaps, "by_reason": dict(gap_reasons)},
    }


def report(
    *,
    roots: dict[str, Path] | None = None,
    root: Path | None = None,
    since: float | None = None,
    window: float = 300.0,
) -> dict[str, Any]:
    before = scan(roots=roots, since=since, window=window)
    after = ledger_counts(root=root, since=since)
    metrics = ("privileged_invocations", "os_prompts", "coalesced", "retries",
               "unattributed", "distinct_requests")
    delta = {m: after["counts"].get(m, 0) - before["counts"].get(m, 0) for m in metrics}
    cf = before["counterfactual"]
    projected = {
        "privileged_invocations": before["counts"]["privileged_invocations"],
        "os_prompts": cf["os_prompts"],
        "coalesced": cf["coalesced"],
        "retries": cf["retries"],
        "unattributed": 0,  # every brokered request carries task id, repo, and purpose
        "distinct_requests": before["counts"]["distinct_requests"],
    }
    return {
        "schema": SCHEMA_REPORT,
        "mode": "report",
        "generated_at": pb.iso(),
        "window_seconds": window,
        "before": {"source": "agent transcripts (no coordinator)", **before["counts"],
                   "by_entry": before["by_entry"], "avoidable_prompts": before["avoidable_prompts"]},
        "after_projected": {
            "source": "the same observed trace replayed through the coalescing rules",
            "measured": False, **projected,
        },
        "after_measured": {"source": f"broker ledger at {after['root']}", **after["counts"],
                           "measured": True,
                           "ledger_present": after["ledger_present"],
                           "ledger_integrity_ok": after["integrity"]["ok"],
                           "coverage_gaps": after["coverage_gaps"]},
        "delta_measured": delta,
        "delta_projected": {m: projected[m] - before["counts"].get(m, 0) for m in metrics},
        "note": (
            "'after_projected' is a counterfactual on the observed trace, not a "
            "measurement. 'after_measured' stays empty until privileged commands "
            "actually route through privileged_broker.py — an empty measured column "
            "means no brokered traffic yet, NOT that no privileged request occurred. "
            "Check coverage_gaps for the windows where a request could have gone "
            "unrecorded."
        ),
    }


def _since_epoch(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def _render(payload: dict[str, Any]) -> str:
    """Human summary. The JSON stays the machine contract."""
    lines: list[str] = []
    if payload["mode"] == "scan":
        c = payload["counts"]
        lines.append(f"Scanned {payload['files_scanned']} transcript files "
                     f"(window {int(payload['window_seconds'])}s)")
        lines.append(f"  privileged invocations : {c['privileged_invocations']}")
        lines.append(f"  distinct requests      : {c['distinct_requests']}")
        lines.append(f"  retries (same key)     : {c['retries']}")
        lines.append(f"  sessions involved      : {c['sessions']}")
        lines.append(f"  simultaneous clusters  : {c['simultaneous_task_clusters']}")
        lines.append(f"  avoidable prompts      : {payload['avoidable_prompts']}")
        if payload["by_entry"]:
            lines.append("  by command:")
            for entry_id, count in payload["by_entry"].items():
                lines.append(f"    {entry_id:24s} {count}")
        return "\n".join(lines)
    b = payload["before"]
    proj = payload["after_projected"]
    meas = payload["after_measured"]
    lines.append(f"{'metric':24s} {'before':>8s} {'projected':>10s} {'measured':>9s}")
    for metric in ("privileged_invocations", "os_prompts", "coalesced", "retries",
                   "unattributed", "distinct_requests"):
        lines.append(f"{metric:24s} {b.get(metric, 0):>8d} {proj.get(metric, 0):>10d} "
                     f"{meas.get(metric, 0):>9d}")
    lines.append("")
    lines.append("projected = the observed trace replayed through the coalescing rules")
    lines.append("measured  = the broker's own ledger; empty until traffic routes through it")
    lines.append(f"ledger integrity : {'ok' if meas['ledger_integrity_ok'] else 'BROKEN'}")
    lines.append(f"coverage gaps    : {meas['coverage_gaps']['total']} "
                 f"{meas['coverage_gaps']['by_reason'] or ''}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "report"):
        p = sub.add_parser(name)
        p.add_argument("--since", help="YYYY-MM-DD; only count activity at or after this date")
        p.add_argument("--window", type=float, default=300.0, help="retry/simultaneity window, seconds")
        p.add_argument("--transcripts", action="append", metavar="LABEL=PATH",
                       help="override a transcript root (repeatable)")
        p.add_argument("--root", help="broker store root (report only)")
        p.add_argument("--json", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)

    roots = default_transcript_roots()
    if args.transcripts:
        roots = {}
        for spec in args.transcripts:
            label, _, path = spec.partition("=")
            roots[label or "custom"] = Path(path).expanduser()

    since = _since_epoch(args.since)
    if args.cmd == "scan":
        payload = scan(roots=roots, since=since, window=args.window)
    else:
        broker_root = Path(args.root).expanduser() if args.root else None
        payload = report(roots=roots, root=broker_root, since=since, window=args.window)

    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(_render(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
