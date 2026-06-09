#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""orchestrator_heartbeat.py — one fail-open beat: state liveness + rally presence.

WHY (bl-orchestrator-heartbeat-rally-presence, user-flagged 2x)
--------------------------------------------------------------
A background/inline orchestrator ran with ``state.execution.last_heartbeat=None`` and
NO rally presence, so its status was only reconstructable from git + CI. The M2 protocol
already refreshes ``last_heartbeat_at`` at six trigger points, but those are chunk-centric
(dispatch/return) — a long run that is between chunks, or an inline run that never fans
out, can go a long time with no fresh beat and no presence record. Meanwhile a live peer
(e.g. Codex) on the same repo had no coordination surface to read.

This is a THIN wrapper, not a new coordination surface. It composes two EXISTING,
already-fail-open mechanisms in one call so the orchestrator can beat at every phase
boundary AND every commit on long/autonomous runs:

1. ``write_run_entry.update_execution_state(state_path, 'heartbeat')`` — refreshes
   ``state.execution.last_heartbeat_at`` (resume_resolver reads this).
2. ``rally_point.presence.write_presence(...)`` — writes a presence beat any watcher
   (``coordination_status.py``, ``rally room``) can read.

Both are individually fail-open; this wrapper is too. A broken heartbeat NEVER wedges
the run — every failure is swallowed and reported in the JSON envelope, exit code is
ALWAYS 0.

CLI
---
  python3 scripts/orchestrator_heartbeat.py --workdir <repo> --phase <execute|review|...> \
      [--label "<one-liner>"] [--files a.py,b.py] [--json]

Reads run identity (run_id, session_id, app_slug) from state.execution; resolves the
rally channel via discovery_bridge. Skips the presence beat silently when there is no
execution block yet (nothing to beat for). Zero third-party deps. Python 3.11+.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                    # scripts/ on path
sys.path.insert(0, str(HERE / "rally_point"))    # rally_point flat imports


def _load_execution(workdir: Path) -> dict | None:
    state_path = workdir / ".build-loop" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    execution = state.get("execution")
    return execution if isinstance(execution, dict) else None


def _refresh_state_heartbeat(workdir: Path) -> dict:
    """Touch state.execution.last_heartbeat_at via the 'heartbeat' action. Fail-open."""
    state_path = workdir / ".build-loop" / "state.json"
    try:
        from write_run_entry import update_execution_state  # type: ignore
        block = update_execution_state(state_path, "heartbeat")
        return {"state_heartbeat": "ok", "last_heartbeat_at": block.get("last_heartbeat_at")}
    except Exception as exc:  # noqa: BLE001 — fail-open
        return {"state_heartbeat": "skipped", "error": str(exc)}


def _write_presence_beat(workdir: Path, phase: str, label: str | None, files: list[str]) -> dict:
    """Write a rally presence beat from the run's identity. Fail-open."""
    execution = _load_execution(workdir)
    if execution is None:
        return {"presence_beat": "skipped", "reason": "no execution block"}
    run_id = execution.get("run_id") or "unknown"
    session_id = (
        execution.get("current_session_id")
        or execution.get("started_by_session_id")
        or f"orchestrator-{run_id}"
    )
    try:
        from discovery_bridge import resolve  # type: ignore
        envelope = resolve(workdir)
        channel_dir = Path(envelope.channel_dir)
        app_slug = envelope.app_slug
    except Exception as exc:  # noqa: BLE001 — fail-open
        return {"presence_beat": "skipped", "reason": f"channel resolve failed: {exc}"}
    try:
        from presence import write_presence  # type: ignore
        write_presence(
            channel_dir,
            session_id=str(session_id),
            tool="build-orchestrator",
            model="orchestrator",
            run_id=str(run_id),
            app_slug=app_slug,
            phase=phase,
            files_in_flight=list(files),
            cwd=workdir,
            task=label or phase,
        )
        return {"presence_beat": "ok", "channel_dir": str(channel_dir), "session_id": str(session_id)}
    except Exception as exc:  # noqa: BLE001 — fail-open
        return {"presence_beat": "skipped", "reason": f"write_presence failed: {exc}"}


def beat(workdir: Path, *, phase: str, label: str | None = None, files: list[str] | None = None) -> dict:
    """Do both beats in one fail-open call. Returns a combined envelope; never raises."""
    files = files or []
    env: dict = {"phase": phase}
    env.update(_refresh_state_heartbeat(workdir))
    env.update(_write_presence_beat(workdir, phase, label, files))
    return env


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orchestrator_heartbeat", description=__doc__)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--phase", required=True, help="execute | review | iterate | report | assess | plan")
    ap.add_argument("--label", default=None, help="one-line task/boundary summary")
    ap.add_argument("--files", default=None, help="comma-separated files in flight")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    env = beat(
        Path(args.workdir).resolve(),
        phase=args.phase,
        label=args.label,
        files=_split_csv(args.files),
    )
    if args.json:
        print(json.dumps(env, indent=2, sort_keys=True))
    else:
        print(
            f"orchestrator_heartbeat: phase={env['phase']} "
            f"state={env.get('state_heartbeat')} presence={env.get('presence_beat')}"
        )
    return 0  # fail-open: always 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
