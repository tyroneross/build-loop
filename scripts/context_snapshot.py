#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Write non-blocking Build Loop context snapshots.

This is the live handoff layer that complements ``context_bootstrap.py``.
Bootstrap builds the Phase 1 memory packet. Snapshot records the current
working state at phase, agent, and commit boundaries so a future agent can
resume from one human-readable file without relying on a blocking Stop hook.

Generated files live under ``.build-loop/context/`` and are runtime state:

  current.md                         latest human resume note
  index.json                         latest snapshot metadata
  snapshots/<timestamp>-<trigger>.json
  agent-briefs.jsonl                 agent dispatch handoff rows
  agent-returns.jsonl                agent return handoff rows
  commit-boundaries.jsonl            pre/post commit rows

All writes are atomic where replacement matters. Append-only logs are
best-effort; failures are reported as ``reasons[]`` in the JSON result and do
not block the caller unless the snapshot JSON/current.md write itself fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import context_bootstrap  # type: ignore  # noqa: E402


SCHEMA_VERSION = 1
DEFAULT_RETENTION = 100
VALID_TRIGGERS = {
    "manual",
    "interval",
    "phase_transition",
    "agent_dispatch",
    "agent_return",
    "pre_commit",
    "post_commit",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def expand_path(raw: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(raw)))).resolve()


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug or "snapshot"


def read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"missing: {path}"
    except json.JSONDecodeError as exc:
        return None, f"json_error: {path}: {exc}"
    except OSError as exc:
        return None, f"read_error: {path}: {exc}"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")


def run_git(workdir: Path, args: list[str]) -> tuple[str | None, str | None]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(workdir),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=4,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git_error: {' '.join(args)}: {exc}"
    if proc.returncode != 0:
        return None, f"git_exit_{proc.returncode}: {' '.join(args)}: {proc.stderr.strip()[:300]}"
    return proc.stdout.strip(), None


def git_context(workdir: Path) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    branch, reason = run_git(workdir, ["branch", "--show-current"])
    if reason:
        reasons.append(reason)
    head, reason = run_git(workdir, ["rev-parse", "--short", "HEAD"])
    if reason:
        reasons.append(reason)
    status_text, reason = run_git(workdir, ["status", "--short"])
    if reason:
        reasons.append(reason)
        status_lines: list[str] = []
    else:
        status_lines = status_text.splitlines() if status_text else []
    return (
        {
            "branch": branch or None,
            "head": head or None,
            "status_short": status_lines,
            "dirty_count": len(status_lines),
            "changed_files": [line[3:] if len(line) > 3 else line for line in status_lines],
        },
        reasons,
    )


def summarize_bootstrap(packet: dict[str, Any]) -> dict[str, Any]:
    sources = packet.get("sources") or {}
    canonical = sources.get("canonical_memory") or {}
    repo = sources.get("repo_local") or {}
    codex = sources.get("codex_memory") or {}
    rally = sources.get("rally") or {}
    return {
        "canonical_hits": len(canonical.get("merged") or []),
        "canonical_files_present": sum(1 for item in canonical.get("files") or [] if item.get("exists")),
        "repo_files_present": sum(1 for item in repo.get("files") or [] if item.get("exists")),
        "coordination_files": len(repo.get("coordination_files") or []),
        "codex_registry_hits": len(codex.get("registry_hits") or []),
        "codex_rollout_hits": len(codex.get("rollout_hits") or []),
        "rally_checked": bool(rally.get("checked")),
        "reasons": {
            "canonical_memory": canonical.get("reasons") or [],
            "repo_local": repo.get("reasons") or [],
            "codex_memory": codex.get("reasons") or [],
            "rally": rally.get("reasons") or [],
        },
    }


def build_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    workdir = expand_path(args.workdir)
    generated_at = utc_now()
    state_summary, state_reason = context_bootstrap.load_state_summary(workdir / ".build-loop" / "state.json")
    working_state, working_reason = read_json(workdir / ".build-loop" / "working-state" / "current.json")
    git, git_reasons = git_context(workdir)
    query = args.query or " ".join(
        item
        for item in [
            args.trigger,
            args.phase or "",
            args.agent or "",
            args.chunk_id or "",
            args.message or "",
        ]
        if item
    )
    bootstrap = context_bootstrap.build_packet(
        workdir=workdir,
        query=query,
        limit=args.limit,
        include_postgres=args.include_postgres,
        include_debugger=args.include_debugger,
        include_rally=args.include_rally,
        max_excerpt_chars=args.max_excerpt_chars,
        rollout_limit=args.rollout_limit,
    )
    execution = (state_summary or {}).get("execution") if isinstance(state_summary, dict) else {}
    snapshot_id = f"ctx-{generated_at.replace(':', '').replace('+00:00', 'Z')}-{slugify(args.trigger)}"
    reasons: list[str] = []
    if state_reason:
        reasons.append(state_reason)
    if working_reason and not working_reason.startswith("missing:"):
        reasons.append(working_reason)
    reasons.extend(git_reasons)
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "generated_at": generated_at,
        "trigger": args.trigger,
        "workdir": str(workdir),
        "project": bootstrap.get("project"),
        "phase": args.phase or (state_summary or {}).get("phase"),
        "run_id": args.run_id or (execution or {}).get("run_id"),
        "build_loop_id": (execution or {}).get("build_loop_id"),
        "agent": args.agent,
        "chunk_id": args.chunk_id,
        "status": args.status,
        "message": args.message,
        "next_action": args.next_action,
        "files": args.files or [],
        "commit_sha": args.commit_sha,
        "validation": {
            "commands": args.validation_command or [],
            "result": args.validation_result,
        },
        "git": git,
        "state_summary": state_summary or {},
        "working_state": working_state or {},
        "bootstrap_summary": summarize_bootstrap(bootstrap),
        "bootstrap_agent_brief": bootstrap.get("agent_brief", ""),
        "reasons": reasons,
    }
    snapshot["fingerprint"] = snapshot_fingerprint(snapshot)
    return snapshot


def snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    stable = {
        "trigger": snapshot.get("trigger"),
        "phase": snapshot.get("phase"),
        "run_id": snapshot.get("run_id"),
        "build_loop_id": snapshot.get("build_loop_id"),
        "agent": snapshot.get("agent"),
        "chunk_id": snapshot.get("chunk_id"),
        "status": snapshot.get("status"),
        "message": snapshot.get("message"),
        "next_action": snapshot.get("next_action"),
        "files": snapshot.get("files"),
        "commit_sha": snapshot.get("commit_sha"),
        "validation": snapshot.get("validation"),
        "git": snapshot.get("git"),
        "state_summary": snapshot.get("state_summary"),
        "working_state": snapshot.get("working_state"),
        "bootstrap_summary": snapshot.get("bootstrap_summary"),
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def current_markdown(snapshot: dict[str, Any]) -> str:
    git = snapshot.get("git") or {}
    working = snapshot.get("working_state") or {}
    bootstrap = snapshot.get("bootstrap_summary") or {}
    validation = snapshot.get("validation") or {}
    changed = git.get("changed_files") or []
    reasons = snapshot.get("reasons") or []
    canonical_reasons = (bootstrap.get("reasons") or {}).get("canonical_memory") or []
    lines = [
        "# Build Loop Context Snapshot",
        "",
        f"- Updated: {snapshot.get('generated_at')}",
        f"- Trigger: {snapshot.get('trigger')}",
        f"- Phase: {snapshot.get('phase') or 'unknown'}",
        f"- Run: {snapshot.get('run_id') or 'unknown'}",
        f"- Build loop ID: {snapshot.get('build_loop_id') or 'unknown'}",
        f"- Branch: {git.get('branch') or 'unknown'} @ {git.get('head') or 'unknown'}",
        "",
        "## Current Work",
        "",
        f"- Agent: {snapshot.get('agent') or working.get('agent') or 'unknown'}",
        f"- Chunk: {snapshot.get('chunk_id') or working.get('chunk_id') or 'unknown'}",
        f"- Status: {snapshot.get('status') or working.get('status') or 'unknown'}",
        f"- Task: {snapshot.get('message') or working.get('current_task_summary') or 'unknown'}",
        f"- Next action: {snapshot.get('next_action') or 'not recorded'}",
        "",
        "## Changed Files",
        "",
        f"- Dirty count: {git.get('dirty_count', 0)}",
    ]
    for path in changed[:20]:
        lines.append(f"- {path}")
    if len(changed) > 20:
        lines.append(f"- ... {len(changed) - 20} more")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Result: {validation.get('result') or 'not recorded'}",
        ]
    )
    for command in validation.get("commands") or []:
        lines.append(f"- Command: `{command}`")
    lines.extend(
        [
            "",
            "## Context Quality",
            "",
            f"- Canonical memory hits: {bootstrap.get('canonical_hits', 0)}",
            f"- Canonical memory files/dirs present: {bootstrap.get('canonical_files_present', 0)}",
            f"- Repo-local files present: {bootstrap.get('repo_files_present', 0)}",
            f"- Codex memory hits: {bootstrap.get('codex_registry_hits', 0)}",
            f"- Rally checked: {bootstrap.get('rally_checked')}",
        ]
    )
    for reason in (reasons + canonical_reasons)[:10]:
        lines.append(f"- Reason: {reason}")
    lines.append("")
    return "\n".join(lines)


def read_index(context_dir: Path) -> dict[str, Any]:
    loaded, _ = read_json(context_dir / "index.json")
    return loaded if isinstance(loaded, dict) else {}


def prune_snapshots(snapshot_dir: Path, keep: int) -> None:
    if keep <= 0 or not snapshot_dir.is_dir():
        return
    snapshots = sorted(snapshot_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in snapshots[keep:]:
        try:
            path.unlink()
        except OSError:
            pass


def event_row(snapshot: dict[str, Any], snapshot_rel: str) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_path": snapshot_rel,
        "generated_at": snapshot.get("generated_at"),
        "trigger": snapshot.get("trigger"),
        "phase": snapshot.get("phase"),
        "run_id": snapshot.get("run_id"),
        "build_loop_id": snapshot.get("build_loop_id"),
        "agent": snapshot.get("agent"),
        "chunk_id": snapshot.get("chunk_id"),
        "status": snapshot.get("status"),
        "files": snapshot.get("files") or [],
        "commit_sha": snapshot.get("commit_sha"),
        "message": snapshot.get("message"),
        "next_action": snapshot.get("next_action"),
    }


def write_snapshot(snapshot: dict[str, Any], workdir: Path, if_changed: bool, retention: int) -> dict[str, Any]:
    context_dir = workdir / ".build-loop" / "context"
    snapshot_dir = context_dir / "snapshots"
    index = read_index(context_dir)
    if if_changed and index.get("last_fingerprint") == snapshot.get("fingerprint"):
        return {
            "ok": True,
            "action": "skipped",
            "reason": "unchanged",
            "snapshot_id": index.get("last_snapshot_id"),
            "current_path": str(context_dir / "current.md"),
        }

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{snapshot['generated_at'].replace(':', '').replace('+00:00', 'Z')}-{slugify(snapshot['trigger'])}.json"
    snapshot_path = snapshot_dir / filename
    current_path = context_dir / "current.md"
    snapshot_rel = str(snapshot_path.relative_to(workdir))

    atomic_write_text(snapshot_path, json.dumps(snapshot, indent=2, sort_keys=True, default=str) + "\n")
    atomic_write_text(current_path, current_markdown(snapshot))

    reasons: list[str] = []
    row = event_row(snapshot, snapshot_rel)
    trigger = snapshot.get("trigger")
    log_map = {
        "agent_dispatch": "agent-briefs.jsonl",
        "agent_return": "agent-returns.jsonl",
        "pre_commit": "commit-boundaries.jsonl",
        "post_commit": "commit-boundaries.jsonl",
    }
    if trigger in log_map:
        try:
            append_jsonl(context_dir / log_map[str(trigger)], row)
        except OSError as exc:
            reasons.append(f"append_error: {log_map[str(trigger)]}: {exc}")

    next_index = {
        "schema_version": SCHEMA_VERSION,
        "last_snapshot_id": snapshot.get("snapshot_id"),
        "last_snapshot_path": snapshot_rel,
        "last_fingerprint": snapshot.get("fingerprint"),
        "last_trigger": trigger,
        "last_updated_at": snapshot.get("generated_at"),
        "snapshot_count": len(list(snapshot_dir.glob("*.json"))),
    }
    atomic_write_text(context_dir / "index.json", json.dumps(next_index, indent=2, sort_keys=True) + "\n")
    prune_snapshots(snapshot_dir, retention)
    return {
        "ok": True,
        "action": "written",
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_path": str(snapshot_path),
        "current_path": str(current_path),
        "reasons": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=os.getcwd())
    parser.add_argument("--trigger", required=True, choices=sorted(VALID_TRIGGERS))
    parser.add_argument("--query", default="")
    parser.add_argument("--phase", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--chunk-id", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--next-action", default="")
    parser.add_argument("--file", action="append", dest="files", default=[])
    parser.add_argument("--commit-sha", default="")
    parser.add_argument("--validation-command", action="append", default=[])
    parser.add_argument("--validation-result", default="")
    parser.add_argument("--if-changed", action="store_true")
    parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--include-postgres", action="store_true")
    parser.add_argument("--include-debugger", action="store_true")
    parser.add_argument("--include-rally", action="store_true")
    parser.add_argument("--max-excerpt-chars", type=int, default=800)
    parser.add_argument("--rollout-limit", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    workdir = expand_path(args.workdir)
    try:
        snapshot = build_snapshot(args)
        result = write_snapshot(snapshot, workdir=workdir, if_changed=args.if_changed, retention=args.retention)
    except Exception as exc:  # noqa: BLE001 - script is a boundary tool; report cleanly.
        payload = {"ok": False, "action": "error", "reason": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        if result.get("action") == "skipped":
            print(f"context_snapshot: skipped unchanged snapshot ({result.get('snapshot_id')})")
        else:
            print(f"context_snapshot: wrote {result.get('snapshot_path')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
