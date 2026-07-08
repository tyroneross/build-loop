#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Stop-hook producer for the per-dispatch cost ledger.

Activation lever for build-loop's cost-attribution pipeline. Runs on the `Stop`
hook (main agent turn end) — the ONLY documented event that reliably fires for
both foreground AND background/parallel `Agent` dispatches. It reconciles the
session transcript against the ledger and appends one row per `Agent` dispatch
that isn't already recorded.

Why Stop + transcript, not PostToolUse/SubagentStop: PreToolUse carries no
tool_use_id (so Pre->Post pairing is impossible), and the PostToolUse /
SubagentStop payload schemas are undocumented. The transcript JSONL is a
documented, stable surface that already contains every dispatch (`Agent`
tool_use with `id` + `input.subagent_type/model/run_in_background`) and pairs
each with its result by `tool_use_id`. This is the same surface
~/.claude/scripts/cache-telemetry.py parses.

Contract:
  - Scoped: writes ONLY when a `.build-loop/state.json` exists under the session
    cwd (i.e. a build-loop-managed context). Otherwise no-op. This keeps
    unrelated sessions' dispatches out of the build-loop ledger and yields run_id.
  - Deterministic task_id: t-<sha256(session_id|tool_use_id)[:8]> — regex-valid
    (^t-[0-9a-f]{8}$), identical across re-scans, so emission is idempotent.
  - Idempotent: a task_id already in the ledger is skipped.
  - Fail-open: ANY error -> exit 0, tool/turn unaffected. Never blocks.
  - tokens_estimate is null in v1 (per-subagent usage is not in the main
    transcript result); attribution PRESENCE is the goal. cache-telemetry.py
    owns per-session token cost. The orchestrator MAY still enrich chunk_id.

stdin: Stop hook JSON ({session_id, transcript_path, cwd, ...}). Env fallbacks:
  CLAUDE_TRANSCRIPT_PATH, CLAUDE_PROJECT_DIR. Exit code is always 0.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def _log(msg: str) -> None:
    print(f"[cost_ledger_hook] {msg}", file=sys.stderr)


def compute_task_id(session_id: str, tool_use_id: str) -> str:
    """Deterministic, regex-valid (^t-[0-9a-f]{8}$) id for a dispatch."""
    digest = hashlib.sha256(f"{session_id}|{tool_use_id}".encode("utf-8")).hexdigest()
    return f"t-{digest[:8]}"


def _read_stdin_json() -> dict:
    try:
        if sys.stdin and not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                return json.loads(raw)
    except Exception:
        pass
    return {}


def _resolve_paths(payload: dict) -> tuple[str, Path | None, Path | None]:
    """Return (session_id, transcript_path, cwd) from stdin JSON + env fallbacks."""
    transcript = payload.get("transcript_path") or os.environ.get("CLAUDE_TRANSCRIPT_PATH")
    transcript_path = Path(transcript) if transcript else None
    session_id = payload.get("session_id") or ""
    if not session_id and transcript_path:
        session_id = transcript_path.stem  # transcript filename is the session uuid
    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return session_id, transcript_path, Path(cwd) if cwd else None


def _run_id_from_state(state_path: Path) -> str | None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    execution = state.get("execution") or {}
    return execution.get("run_id") or state.get("build_loop_id") or state.get("run_id")


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def parse_dispatches(transcript_path: Path) -> list[dict]:
    """Collect Agent dispatches (tool_use) paired with their results by id."""
    uses: dict[str, dict] = {}
    result_ids: set[str] = set()
    try:
        with transcript_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "tool_use" and block.get("name") == "Agent":
                        tuid = block.get("id")
                        if not tuid:
                            continue
                        inp = block.get("input") or {}
                        uses[tuid] = {
                            "tool_use_id": tuid,
                            "subagent_type": inp.get("subagent_type") or "unknown",
                            "model": inp.get("model") or "inherit",
                            "run_in_background": bool(inp.get("run_in_background")),
                            "ts": obj.get("timestamp") or msg.get("timestamp"),
                        }
                    elif btype == "tool_result":
                        tuid = block.get("tool_use_id")
                        if tuid:
                            result_ids.add(tuid)
    except Exception as exc:
        _log(f"transcript parse failed: {exc}")
        return []
    for tuid, d in uses.items():
        d["completed"] = tuid in result_ids
    return list(uses.values())


def _existing_task_ids(ledger_path: Path) -> set[str]:
    seen: set[str] = set()
    try:
        if not ledger_path.exists():
            return seen
        with ledger_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                tid = row.get("task_id")
                if tid:
                    seen.add(tid)
    except Exception:
        pass
    return seen


def _build_namespace(dispatch: dict, task_id: str, run_id: str | None) -> SimpleNamespace:
    """Mirror write_cost_ledger_row's argparse Namespace so we reuse build_row (DRY)."""
    return SimpleNamespace(
        agent=dispatch["subagent_type"],
        task_id=task_id,
        model=dispatch["model"],
        status="completed" if dispatch.get("completed") else "dispatched",
        dispatch_mode="fan-out" if dispatch["run_in_background"] else "inline",
        files_changed_count=None,
        tokens_estimate=None,
        tokens_source="unknown",
        wall_clock_seconds=None,
        started_at=dispatch.get("ts"),
        completed_at=None,
        run_id=run_id,
        chunk_id=None,
        called=None,
        skipped_reason=None,
        failed=None,
        issue_found=None,
        elapsed_seconds=None,
        downstream_iterate_outcome=None,
    )


def main() -> int:
    try:
        import write_cost_ledger_row as wcl  # noqa: WPS433 (deferred: fail-open if absent)
    except Exception as exc:
        _log(f"row builder unavailable: {exc}")
        return 0

    payload = _read_stdin_json()
    session_id, transcript_path, cwd = _resolve_paths(payload)
    if not transcript_path or not transcript_path.exists() or not session_id or not cwd:
        return 0

    state_path = cwd / ".build-loop" / "state.json"
    if not state_path.exists():
        return 0  # not a build-loop-managed context; do not pollute the ledger

    run_id = _run_id_from_state(state_path)
    dispatches = parse_dispatches(transcript_path)
    if not dispatches:
        return 0

    ledger_path = Path(os.environ.get("BUILD_LOOP_COST_LEDGER") or wcl.DEFAULT_LEDGER_PATH)
    seen = _existing_task_ids(ledger_path)

    written = 0
    for dispatch in dispatches:
        task_id = compute_task_id(session_id, dispatch["tool_use_id"])
        if task_id in seen:
            continue
        try:
            row = wcl.build_row(_build_namespace(dispatch, task_id, run_id))
            wcl.append_row(ledger_path, row)
            seen.add(task_id)
            written += 1
        except Exception as exc:
            _log(f"row write failed for {task_id}: {exc}")
            # keep going; fail-open per row
    if written:
        _log(f"wrote {written} dispatch row(s) for session {session_id[:8]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # absolute fail-open backstop
        _log(f"fatal (ignored): {exc}")
        sys.exit(0)
