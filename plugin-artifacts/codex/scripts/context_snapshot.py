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
import time
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


_TRANSIENT_REASON_PREFIXES = (
    # One-shot side effects of bootstrap that change between identical
    # back-to-back runs (seeding files, etc.). Filtering keeps the fingerprint
    # stable across `--if-changed` calls.
    "constitution_seeded:",
)


def _strip_transient(reasons: list[str] | None) -> list[str]:
    if not reasons:
        return []
    return [r for r in reasons if not any(r.startswith(p) for p in _TRANSIENT_REASON_PREFIXES)]


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
            "canonical_memory": _strip_transient(canonical.get("reasons")),
            "repo_local": _strip_transient(repo.get("reasons")),
            "codex_memory": _strip_transient(codex.get("reasons")),
            "rally": _strip_transient(rally.get("reasons")),
        },
    }


# ---------------------------------------------------------------------------
# Memory backlinks — P0 "pointers DOWN into long-term memory" requirement.
# Reuses P4 prior_art digest + P1 lessons_progressive; the heavy work is
# already in `packet`, we just project it down to a stable backlink list.
# Each entry is {kind, title, path, why?} — pure pointers, no inlined bodies.
# ---------------------------------------------------------------------------
def memory_backlinks_from_packet(packet: dict[str, Any], *, max_links: int = 8) -> list[dict[str, str]]:
    """Return up to `max_links` typed pointers DOWN into long-term memory.

    Sources, in priority order:
      1. packet["prior_art"]["decisions"] — cross-project decisions (P4).
      2. packet["prior_art"]["implementations"] — cross-project impls (P4).
      3. packet["lessons_progressive"]      — project lessons (P1).
      4. packet["sources"]["canonical_memory"]["merged"] — recall hits.

    Pure projection — never raises. Empty packet -> []. Bounded so the
    pointer-dense `current.md` never floods.
    """
    if not isinstance(packet, dict):
        return []
    links: list[dict[str, str]] = []

    prior = packet.get("prior_art") or {}
    for dec in (prior.get("decisions") or [])[:max_links]:
        links.append(
            {
                "kind": "decision",
                "title": str(dec.get("title", "")).strip() or "(untitled)",
                "path": str(dec.get("path", "")),
                "why": str(dec.get("snippet", "")).strip()[:120],
                "project": str(dec.get("project", "")),
            }
        )
        if len(links) >= max_links:
            return links

    for impl in (prior.get("implementations") or [])[:max_links]:
        links.append(
            {
                "kind": "implementation",
                "title": str(impl.get("source", "")).strip() or "(unnamed)",
                "path": str(impl.get("source", "")),
                "why": str(impl.get("snippet", "")).strip()[:120],
                "project": str(impl.get("project", "")),
            }
        )
        if len(links) >= max_links:
            return links

    for lesson in (packet.get("lessons_progressive") or [])[:max_links]:
        links.append(
            {
                "kind": "lesson",
                "title": str(lesson.get("name", "")).strip() or "(unnamed)",
                "path": str(lesson.get("source_path", "")),
                "why": str(lesson.get("description") or lesson.get("snippet") or "").strip()[:120],
                "project": str(packet.get("project", "")),
            }
        )
        if len(links) >= max_links:
            return links

    merged = ((packet.get("sources") or {}).get("canonical_memory") or {}).get("merged") or []
    for hit in merged[:max_links]:
        title = str(hit.get("name") or hit.get("title") or hit.get("decision_id") or "").strip()
        if not title:
            continue
        links.append(
            {
                "kind": str(hit.get("kind", "memory")),
                "title": title,
                "path": str(hit.get("source_path") or hit.get("path") or ""),
                "why": str(hit.get("snippet") or hit.get("rationale") or "").strip()[:120],
                "project": str(hit.get("project", "")),
            }
        )
        if len(links) >= max_links:
            break

    return links


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
        "memory_backlinks": memory_backlinks_from_packet(bootstrap, max_links=8),
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
    """Render a pointer-dense `current.md` (P0 — short-term working context).

    Contract:
      * Every line carries a fact the next agent needs to resume work.
      * Sections are pointers — files, paths, IDs — never inlined dumps.
      * `## Memory Backlinks` links DOWN into long-term memory (P1/P4 surfaces)
        so the working note is a hub, not a silo.
      * Heavy bootstrap reasons / file dumps stay in the JSON snapshot, not here.
    """
    git = snapshot.get("git") or {}
    working = snapshot.get("working_state") or {}
    bootstrap = snapshot.get("bootstrap_summary") or {}
    validation = snapshot.get("validation") or {}
    changed = git.get("changed_files") or []
    snapshot_id = snapshot.get("snapshot_id") or "unknown"
    backlinks = snapshot.get("memory_backlinks") or []

    lines: list[str] = [
        "# Build Loop Working Context",
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
    # Pointer-dense: cap inlined file list at 10 (not 20); the rest are in the snapshot JSON.
    for path in changed[:10]:
        lines.append(f"- {path}")
    if len(changed) > 10:
        lines.append(f"- (+{len(changed) - 10} more — see snapshot JSON)")

    # Validation: one summary line + count, never inline the full command list.
    commands = validation.get("commands") or []
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Result: {validation.get('result') or 'not recorded'}",
            f"- Commands recorded: {len(commands)}",
        ]
    )

    # Memory backlinks — pointers DOWN into long-term memory (P0 req #6).
    # Reuses P4 prior_art + P1 lessons surfaces; never inlines bodies.
    if backlinks:
        lines.extend(["", "## Memory Backlinks", ""])
        for link in backlinks[:8]:
            title = link.get("title") or "(untitled)"
            kind = link.get("kind") or "memory"
            path = link.get("path") or ""
            project = link.get("project") or ""
            proj_tag = f" [{project}]" if project else ""
            path_tag = f" — `{path}`" if path else ""
            lines.append(f"- {kind}: {title}{proj_tag}{path_tag}")
    else:
        lines.extend(
            [
                "",
                "## Memory Backlinks",
                "",
                "- (none — prior_art empty / bootstrap not yet run)",
            ]
        )

    # Pointers: where to look for the heavy detail. Progressive disclosure.
    lines.extend(
        [
            "",
            "## Pointers",
            "",
            f"- Snapshot JSON: `.build-loop/context/snapshots/` (id={snapshot_id})",
            f"- Snapshot index: `.build-loop/context/index.json`",
            f"- Memory store: `~/dev/git-folder/build-loop-memory/projects/{snapshot.get('project') or '<unscoped>'}/`",
            f"- Prior art digest (full): `packet.prior_art.digest_text` via context_bootstrap.py",
        ]
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pointer-density lint — gates the "every token has meaning" requirement.
# Returns {ok: bool, findings: list[str]}; advisory, never raises.
# ---------------------------------------------------------------------------
_POINTER_DENSITY_RULES = {
    "max_file_lines": 80,                  # whole-doc cap
    "max_inlined_changed_files": 10,       # ## Changed Files
    "max_inlined_validation_commands": 0,  # validation commands -> count only
    "max_inlined_backlinks": 8,            # ## Memory Backlinks
    "forbidden_sections": ("## Context Quality",),  # heavy reason dump banned
}


def pointer_density_findings(text: str) -> list[str]:
    """Return advisory pointer-density warnings (non-blocking). Empty = clean.

    The hard caps (max_file_lines, max_inlined_changed_files, etc.) are enforced
    structurally in ``current_markdown()`` — that function generates the text within
    those bounds so a fresh write never violates them.  This function returns
    *advisory* findings only: it does not gate any write, and ``write_snapshot()``
    writes unconditionally regardless of findings.  Use the findings for reporting
    and surfacing to the run report (f8 / context-density rule), not for blocking.
    """
    if not isinstance(text, str):
        return ["non_string_input"]
    findings: list[str] = []
    lines = text.splitlines()
    if len(lines) > _POINTER_DENSITY_RULES["max_file_lines"]:
        findings.append(f"too_many_lines: {len(lines)} > {_POINTER_DENSITY_RULES['max_file_lines']}")

    for forbidden in _POINTER_DENSITY_RULES["forbidden_sections"]:
        if forbidden in text:
            findings.append(f"forbidden_section: {forbidden}")

    # Count list items under ## Changed Files.
    in_changed = False
    inlined = 0
    for line in lines:
        if line.startswith("## Changed Files"):
            in_changed = True
            continue
        if in_changed and line.startswith("## "):
            in_changed = False
        elif in_changed and line.startswith("- ") and not line.startswith("- Dirty count") and "more" not in line:
            inlined += 1
    if inlined > _POINTER_DENSITY_RULES["max_inlined_changed_files"]:
        findings.append(f"too_many_changed_files: {inlined}")

    # Validation: no inlined `Command:` lines.
    in_val = False
    val_cmd_lines = 0
    for line in lines:
        if line.startswith("## Validation"):
            in_val = True
            continue
        if in_val and line.startswith("## "):
            in_val = False
        elif in_val and "Command:" in line:
            val_cmd_lines += 1
    if val_cmd_lines > _POINTER_DENSITY_RULES["max_inlined_validation_commands"]:
        findings.append(f"inlined_validation_commands: {val_cmd_lines}")

    return findings


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
    current_text = current_markdown(snapshot)
    atomic_write_text(current_path, current_text)

    # Measure warm read latency — the time a downstream agent pays to read
    # `current.md` immediately after we wrote it. Non-blocking; pure local FS.
    warm_read_ms: float | None = None
    try:
        t0 = time.perf_counter()
        current_path.read_text(encoding="utf-8")
        warm_read_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    except OSError:
        warm_read_ms = None

    # Advisory pointer-density lint — surfaced in result.reasons[] but never blocks.
    density_findings = pointer_density_findings(current_text)

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
        "warm_read_latency_ms": warm_read_ms,
        "current_md_lines": current_text.count("\n") + 1,
        "memory_backlinks_count": len(snapshot.get("memory_backlinks") or []),
        "pointer_density_findings": density_findings,
    }
    atomic_write_text(context_dir / "index.json", json.dumps(next_index, indent=2, sort_keys=True) + "\n")
    prune_snapshots(snapshot_dir, retention)
    for finding in density_findings:
        reasons.append(f"density_lint: {finding}")
    return {
        "ok": True,
        "action": "written",
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_path": str(snapshot_path),
        "current_path": str(current_path),
        "warm_read_latency_ms": warm_read_ms,
        "memory_backlinks_count": len(snapshot.get("memory_backlinks") or []),
        "pointer_density_findings": density_findings,
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
