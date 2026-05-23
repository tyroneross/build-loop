#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Append a decision entry to .build-loop/state.json runs[].

Single helper used by Mechanism B (auto-pick) and Mechanism A (risky-to-branch).
Writes to two list fields on the current run:

  runs[N].autonomousDefaults[]  — auto-picked decisions with trade-off rationale
  runs[N].riskyBranches[]       — work isolated to a branch for later review

Schema for autonomousDefaults[N]:
  {
    "decision_id": str,
    "phase": str,                 # which build-loop phase surfaced the decision
    "chosen": str,                # option id (e.g. "A")
    "options": [{...}, ...],      # full option list with trade-offs
    "confidence": "high|med|low",
    "rationale": str,
    "ts": iso8601,
    "judge_redirect": {...} | null,   # populated by commit-auditor if it later redirects
    "escalated": bool             # true when normal-mode surfaced this to operator
  }

Schema for riskyBranches[N]:
  {
    "branch": str,                # e.g. "buildloop-risky-c3-a1b2c3"
    "hash": str,                  # commit SHA at branch tip
    "files": [str],               # files touched
    "summary": str,               # one-line what + why isolated
    "trade_offs": str | null,
    "matched_rule": str | null,   # from classify_action
    "ts": iso8601
  }

The script is idempotent on (decision_id) for autonomousDefaults and on (hash)
for riskyBranches — calling twice with the same key is a no-op.

CLI:
  log_decision.py --workdir . --kind autonomous_default --payload-json /path/to/payload.json
  log_decision.py --workdir . --kind risky_branch --payload-json /path/to/payload.json

Exit codes:
  0 — wrote (or no-op idempotent)
  1 — invalid payload / state.json missing or unparseable
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

_VALID_KINDS = {"autonomous_default", "risky_branch"}
_REQUIRED_DEFAULT_FIELDS = {"decision_id", "phase", "chosen", "options", "confidence"}
_REQUIRED_BRANCH_FIELDS = {"branch", "hash"}
_VALID_CONFIDENCE = {"high", "med", "low"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_payload(kind: str, payload: dict[str, Any]) -> str | None:
    """Return None if valid, else an error string."""
    if not isinstance(payload, dict):
        return "payload must be a JSON object"

    if kind == "autonomous_default":
        missing = _REQUIRED_DEFAULT_FIELDS - set(payload.keys())
        if missing:
            return f"autonomous_default missing required fields: {sorted(missing)}"
        if payload.get("confidence") not in _VALID_CONFIDENCE:
            return f"confidence must be one of {sorted(_VALID_CONFIDENCE)}"
        options = payload.get("options")
        if not isinstance(options, list) or len(options) < 1:
            return "options must be a non-empty list"
        chosen = payload.get("chosen")
        option_ids = {opt.get("id") for opt in options if isinstance(opt, dict)}
        if chosen not in option_ids:
            return f"chosen={chosen!r} not in options ids {sorted(option_ids)}"
    elif kind == "risky_branch":
        missing = _REQUIRED_BRANCH_FIELDS - set(payload.keys())
        if missing:
            return f"risky_branch missing required fields: {sorted(missing)}"
    else:
        return f"unknown kind: {kind!r}"

    return None


def _load_state(workdir: Path) -> tuple[dict[str, Any], Path]:
    """Return (state_dict, path_to_state.json). Creates a skeleton if missing."""
    state_path = workdir / ".build-loop" / "state.json"
    if not state_path.exists():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        skeleton = {"runs": [], "schema_version": "1.0.0"}
        state_path.write_text(json.dumps(skeleton, indent=2))
        return skeleton, state_path

    try:
        data = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"state.json unparseable: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit("state.json root must be an object")

    if "runs" not in data or not isinstance(data["runs"], list):
        data["runs"] = []

    return data, state_path


def _current_run(state: dict[str, Any]) -> dict[str, Any]:
    """Return the latest run entry, creating one if none exists.

    A "current run" is the last item in runs[]. Callers should append a new
    run via write_run_entry.py at run-start; this helper does NOT attempt to
    detect run boundaries — it just attaches to the latest entry.
    """
    runs = state["runs"]
    if not runs:
        new_run = {"date": _now(), "phases": {}}
        runs.append(new_run)
        return new_run
    return runs[-1]


def _append_idempotent(
    target_list: list[dict[str, Any]], entry: dict[str, Any], dedupe_key: str
) -> bool:
    """Append entry to target_list unless an entry with the same dedupe_key value exists.

    Returns True when appended, False when skipped (idempotent path).
    """
    key_val = entry.get(dedupe_key)
    if key_val is None:
        target_list.append(entry)
        return True
    for existing in target_list:
        if existing.get(dedupe_key) == key_val:
            return False
    target_list.append(entry)
    return True


def log_autonomous_default(
    workdir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    """Append an autonomousDefault entry. Returns the appended entry (or existing)."""
    err = _validate_payload("autonomous_default", payload)
    if err:
        raise SystemExit(f"invalid payload: {err}")

    state, path = _load_state(workdir)
    run = _current_run(state)
    run.setdefault("autonomousDefaults", [])

    entry = {
        "decision_id": payload["decision_id"],
        "phase": payload["phase"],
        "chosen": payload["chosen"],
        "options": payload["options"],
        "confidence": payload["confidence"],
        "rationale": payload.get("rationale", ""),
        "ts": payload.get("ts") or _now(),
        "judge_redirect": payload.get("judge_redirect"),
        "escalated": bool(payload.get("escalated", False)),
    }
    _append_idempotent(run["autonomousDefaults"], entry, dedupe_key="decision_id")

    path.write_text(json.dumps(state, indent=2) + "\n")
    return entry


def log_risky_branch(workdir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Append a riskyBranches entry. Returns the appended entry (or existing)."""
    err = _validate_payload("risky_branch", payload)
    if err:
        raise SystemExit(f"invalid payload: {err}")

    state, path = _load_state(workdir)
    run = _current_run(state)
    run.setdefault("riskyBranches", [])

    entry = {
        "branch": payload["branch"],
        "hash": payload["hash"],
        "files": payload.get("files", []),
        "summary": payload.get("summary", ""),
        "trade_offs": payload.get("trade_offs"),
        "matched_rule": payload.get("matched_rule"),
        "ts": payload.get("ts") or _now(),
    }
    _append_idempotent(run["riskyBranches"], entry, dedupe_key="hash")

    path.write_text(json.dumps(state, indent=2) + "\n")
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--kind", required=True, choices=sorted(_VALID_KINDS))
    parser.add_argument("--payload-json", required=True, help="Path to a JSON file")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir).resolve()
    payload_path = Path(args.payload_json)
    if not payload_path.exists():
        print(f"payload file not found: {payload_path}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(payload_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"payload JSON unparseable: {exc}", file=sys.stderr)
        return 1

    try:
        if args.kind == "autonomous_default":
            entry = log_autonomous_default(workdir, payload)
        else:
            entry = log_risky_branch(workdir, payload)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(entry, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
