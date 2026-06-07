#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Host-neutral CLI wrapping Rally Point presence, handoff, status, and lead operations.
#   application: coordination
#   status: active
"""Host-neutral Rally Point CLI (G4 — cross-tool parity).

Claude Code reaches Rally Point through the `/agent-rally-point` slash
command; every other host (Codex, Copilot, Cursor, CI verifiers) had to
import the `rally_point` package directly. This CLI closes that gap: one
host-neutral entry point wrapping the coordination primitives so any tool
shells out the same way.

Subcommands:
    presence     write/refresh this session's presence record
    handoff      post a kind=handoff record (MECE + lateral-limits packet)
    status       read the cheap coordination-status envelope
    heartbeat    write a structured task heartbeat for long-running work
    ack-inbox    mark current direct/broadcast inbox messages seen
    where        print the global channel_dir for the current repo (joins it)
    lead claim       claim the leadership lease
    lead renew       renew the current lease (lead only)
    lead transfer    hand the lead to another session (lead only)
    lead relinquish  give up the lead (lead only)
    lead status      read the current lead
    boundary     validate embedded agent-rally extraction boundaries

Every subcommand accepts `--json` and prints a JSON envelope to stdout.
Stdlib only. Fire-and-forget semantics inherited from rally_point.*.

Examples (all use the generic `example-app` slug — no real app names):
    python3 scripts/agent_rally.py presence --session-id codex-r1 \\
        --tool codex --model gpt-5 --run-id run-1 --phase execute
    python3 scripts/agent_rally.py lead claim --session-id codex-r1 \\
        --tool codex --model gpt-5 --run-id run-1
    python3 scripts/agent_rally.py status --session-id codex-r1 --tool codex
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from rally_point import boundary as _boundary
from rally_point import (  # noqa: E402
    inbox,
    leadership,
    presence,
    roster as _roster,
    task_heartbeat,
)
from rally_point.discovery_bridge import (  # noqa: E402
    resolve as _bridge_resolve,
    rust_rally_binary,
)
from rally_point.post import post  # noqa: E402


def _emit(obj: dict[str, Any]) -> int:
    json.dump(obj, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _resolve_channel(workdir: str) -> tuple[str, Path]:
    """β1 protocol-of-record: resolve via the shared discovery bridge.

    Every legacy `_resolve_channel` caller now goes through the bridge so
    canonical Rally Point (when ``agent-rally-discover`` is on PATH /
    ``agent_rally_point`` is importable / ``AGENT_RALLY_DISCOVER`` is set)
    is preferred over the internal ``channel_paths`` fallback. Returns
    ``(app_slug, channel_dir)`` for backward compatibility with the
    existing call sites.
    """
    wd = Path(workdir).expanduser().resolve()
    envelope = _bridge_resolve(wd)
    channel_dir = Path(envelope.channel_dir)
    # The canonical channel is created by agent-rally-point; the legacy
    # internal fallback path may also need a lazy mkdir for first use.
    if envelope.resolved_via == "build-loop-internal":
        channel_dir.mkdir(parents=True, exist_ok=True)
    return envelope.app_slug, channel_dir


# --------------------------------------------------------------------------
# Subcommand handlers
# --------------------------------------------------------------------------

def cmd_presence(args: argparse.Namespace) -> int:
    wd = Path(args.workdir).expanduser().resolve()
    envelope = _bridge_resolve(wd)
    slug = envelope.app_slug
    channel_dir = Path(envelope.channel_dir)
    if envelope.resolved_via == "rust-cli":
        ok = _rust_start(
            workdir=wd,
            session_id=args.session_id,
            tool=args.tool,
            model=args.model,
            run_id=args.run_id,
            intent=args.phase,
            paths=_split_csv(args.files_in_flight),
        )
        return _emit({
            "action": "presence-written" if ok else "presence-error",
            "app_slug": slug,
            "session_id": args.session_id,
            "phase": args.phase,
            "resolved_via": "rust-cli",
        })
    if envelope.resolved_via == "build-loop-internal":
        channel_dir.mkdir(parents=True, exist_ok=True)
    cwd = (
        Path(args.cwd).expanduser().resolve()
        if getattr(args, "cwd", None)
        else Path(args.workdir).expanduser().resolve()
    )
    presence.write_presence(
        channel_dir,
        session_id=args.session_id,
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        phase=args.phase,
        files_in_flight=_split_csv(args.files_in_flight),
        cwd=cwd,
        task=getattr(args, "task", None),
        parent=getattr(args, "parent", None),
        spawned=getattr(args, "spawned", None),
        pid=getattr(args, "pid", None),
        host=getattr(args, "host", None),
    )
    return _emit({
        "action": "presence-written",
        "app_slug": slug,
        "session_id": args.session_id,
        "phase": args.phase,
        "task": getattr(args, "task", None) or args.phase,
        "parent": getattr(args, "parent", None),
        "spawned": presence.parse_spawned(getattr(args, "spawned", None)),
    })


def cmd_stop(args: argparse.Namespace) -> int:
    wd = Path(args.workdir).expanduser().resolve()
    envelope = _bridge_resolve(wd)
    if envelope.resolved_via == "rust-cli":
        binary = rust_rally_binary(wd)
        if not binary:
            return _emit({"action": "stop-error", "error": "rally binary unavailable"})
        cmd = [
            binary,
            "stop",
            args.tool,
            "--json",
            "--session-id",
            args.session_id,
            "--reason",
            args.reason,
        ]
        if args.keep_claims:
            cmd.append("--keep-claims")
        try:
            result = subprocess.run(cmd, cwd=str(wd), capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            return _emit({"action": "stop-error", "error": str(exc)})
        sys.stdout.write(result.stdout)
        return result.returncode

    slug, channel_dir = _resolve_channel(args.workdir)
    removed = []
    sessions_dir = channel_dir / "sessions"
    for path in sessions_dir.glob(f"{args.session_id}.json"):
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            pass
    return _emit({
        "action": "presence-stopped",
        "app_slug": slug,
        "session_id": args.session_id,
        "presence_removed": removed,
        "claims_released": [],
        "claims_kept": True,
        "resolved_via": "build-loop-internal",
    })


def _rust_start(
    *,
    workdir: Path,
    session_id: str,
    tool: str,
    model: str,
    run_id: str,
    intent: str,
    paths: list[str],
) -> bool:
    binary = rust_rally_binary(workdir)
    if not binary:
        return False
    cmd = [
        binary,
        "start",
        tool,
        "--json",
        "--session-id",
        session_id,
        "--model",
        model,
        "--run-id",
        run_id,
        "--intent",
        intent,
    ]
    for path in paths:
        cmd.extend(["--path", path])
    try:
        result = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0 or not result.stdout.strip():
        return False
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        return False
    return bool(payload.get("ok") is True)


def cmd_handoff(args: argparse.Namespace) -> int:
    slug, channel_dir = _resolve_channel(args.workdir)
    payload = {
        "session_id": args.session_id,
        "to": args.to,
        "message": args.message,
        "ownership": {
            "owns": _split_csv(args.owns),
            "does_not_own": _split_csv(args.does_not_own),
            "interface_contract": args.interface_contract,
            "integration_checkpoint": args.integration_checkpoint,
            "allowed_tools": _split_csv(args.allowed_tools),
            "denied_tools": _split_csv(args.denied_tools),
        },
    }
    new_rev = post(
        channel_dir=channel_dir,
        kind="handoff",
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        payload=payload,
        workdir=Path(args.workdir).expanduser().resolve(),
    )
    return _emit({
        "action": "handoff-posted" if new_rev is not None else "handoff-rejected",
        "app_slug": slug,
        "channel_revision": new_rev,
        "accepted": new_rev is not None,
    })


def cmd_escalate(args: argparse.Namespace) -> int:
    slug, channel_dir = _resolve_channel(args.workdir)
    new_rev = post(
        channel_dir=channel_dir,
        kind="escalation",
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        payload={
            "session_id": args.session_id,
            "reason": args.reason,
            "needs": args.needs,
        },
        workdir=Path(args.workdir).expanduser().resolve(),
    )
    return _emit({
        "action": "escalation-posted",
        "app_slug": slug,
        "channel_revision": new_rev,
    })


def cmd_where(args: argparse.Namespace) -> int:
    """Print the GLOBAL channel_dir for the current repo (the dir Rally Point
    joins). β1: delegates to the shared discovery bridge, which prefers
    ``$AGENT_RALLY_DISCOVER`` → PATH ``agent-rally-discover`` → Python
    ``agent_rally_point.discover`` → internal ``channel_paths`` fallback.

    Default output: bare path on stdout (so ``cd "$(rally where)"`` works).
    --json: full envelope including ``channel_dir``, ``app_slug``,
    ``resolved_via``, ``policy``, ``channel_layout``, ``protocol_version``,
    ``legacy_channel_dir`` (during migration), and
    ``coordination_unavailable`` (when set).

    ``resolved_via`` distinguishes between the canonical sources
    (``env-override``, ``path-binary``, ``python-import``) and the
    degraded ``build-loop-internal`` fallback. Callers that need
    canonical-only writes inspect this field.

    Exit non-zero with a clear message when cwd is not under a git repo
    (slug resolves to ``_unscoped`` AND no canonical source is available).
    """
    wd = Path(args.workdir).expanduser().resolve()
    envelope = _bridge_resolve(wd)
    if (
        envelope.resolved_via == "build-loop-internal"
        and envelope.app_slug == "_unscoped"
    ):
        sys.stderr.write(
            f"error: {wd} is not under a git repository — channel resolution "
            "fell back to internal '_unscoped'. Rally Point channels are "
            "repo-scoped; run this from inside a git checkout (main or "
            "worktree).\n"
        )
        return 2
    if args.json:
        # Backward-compatible field set + bridge extras.
        result: dict[str, Any] = {
            "channel_dir": envelope.channel_dir,
            "app_slug": envelope.app_slug,
            "resolved_via": (
                "agent-rally-point"
                if envelope.resolved_via != "build-loop-internal"
                else "build-loop-internal"
            ),
            "resolved_via_detail": envelope.resolved_via,
            "policy": envelope.policy,
            "channel_layout": envelope.channel_layout,
            "protocol_version": envelope.protocol_version,
        }
        if envelope.legacy_channel_dir:
            result["legacy_channel_dir"] = envelope.legacy_channel_dir
        if envelope.coordination_unavailable:
            result["coordination_unavailable"] = envelope.coordination_unavailable
        return _emit(result)
    sys.stdout.write(f"{envelope.channel_dir}\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Delegate to coordination_status.py so output stays canonical."""
    cmd = [
        sys.executable,
        str(HERE / "coordination_status.py"),
        "--workdir", args.workdir,
        "--session-id", args.session_id,
        "--tool", args.tool,
        "--json",
    ]
    if args.coordination_file:
        cmd += ["--coordination-file", args.coordination_file]
    if args.task_ref:
        cmd += ["--task-ref", args.task_ref]
    if args.task_heartbeat_grace_seconds is not None:
        cmd += [
            "--task-heartbeat-grace-seconds",
            str(args.task_heartbeat_grace_seconds),
        ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return _emit({"action": "status-error", "error": str(exc)})
    sys.stdout.write(result.stdout)
    return result.returncode


def cmd_ack_inbox(args: argparse.Namespace) -> int:
    """Mark current direct/broadcast inbox messages seen for this tool/session."""
    slug, channel_dir = _resolve_channel(args.workdir)
    result = inbox.mark_read(
        channel_dir,
        tool=args.tool,
        session_id=args.session_id,
        include_broadcast=not args.no_broadcast,
    )
    return _emit({"app_slug": slug, **result})


def cmd_heartbeat(args: argparse.Namespace) -> int:
    """Write a structured heartbeat for a long-running task."""
    slug, channel_dir = _resolve_channel(args.workdir)
    record = task_heartbeat.write_heartbeat(
        channel_dir,
        session_id=args.session_id,
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        task_ref=args.task_ref,
        status=args.status,
        still_on_task=not args.not_on_task,
        progress_since_last=args.progress,
        evidence_refs=_split_csv(args.evidence),
        attention_reason=args.attention_reason,
        interval_seconds=args.interval_seconds,
    )
    return _emit({
        "action": "task-heartbeat-written",
        "app_slug": slug,
        "session_id": args.session_id,
        "tool": args.tool,
        "task_ref": args.task_ref,
        "status": record["status"],
        "still_on_task": record["still_on_task"],
        "next_check_in_at": record["next_check_in_at"],
    })


def cmd_boundary(args: argparse.Namespace) -> int:
    """Validate the embedded agent-rally extraction boundary."""
    repo = (
        Path(args.repo).expanduser().resolve()
        if args.repo else HERE.parent
    )
    result = _boundary.validate_manifest(repo)
    _emit(result)
    if args.check and not result["ok"]:
        return 1
    return 0


def cmd_roster(args: argparse.Namespace) -> int:
    """Cross-channel live agent roster.

    Walks every ``<apps_root>/*/sessions/*.json`` (all repos at once;
    ``--app`` filters to one), keeps sessions heartbeating within
    ``--stale-secs`` (default 120; ``--all`` keeps stale too), and builds
    the parent/child tree from ``parent`` links + self-reported
    ``spawned`` fan-out. ``--json`` emits the structured roster;
    ``--watch N`` re-renders every N seconds.
    """
    def _once() -> dict[str, Any]:
        return _roster.build_roster(
            app=args.app,
            stale_secs=args.stale_secs,
            include_stale=args.all,
        )

    if args.watch and args.watch > 0 and not args.json:
        try:
            while True:
                sys.stdout.write("\033[2J\033[H")  # clear screen + home
                sys.stdout.write(_roster.render_text(_once()))
                sys.stdout.write(
                    f"\n\n(watching every {args.watch}s — Ctrl-C to stop)\n"
                )
                sys.stdout.flush()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            return 0

    data = _once()
    if args.json:
        return _emit(data)
    sys.stdout.write(_roster.render_text(data) + "\n")
    return 0


def cmd_lead(args: argparse.Namespace) -> int:
    slug, channel_dir = _resolve_channel(args.workdir)
    op = args.lead_op

    if op == "status":
        doc = leadership.read_lead(channel_dir)
        return _emit({
            "action": "lead-status",
            "app_slug": slug,
            "lead": doc,
            "lease_valid": leadership.is_lease_valid(channel_dir),
        })

    if op == "claim":
        result = leadership.claim_lead(
            channel_dir,
            run_id=args.run_id,
            session_id=args.session_id,
            tool=args.tool,
            model=args.model,
            app_slug=slug,
            renew_every_minutes=args.renew_every_minutes,
            workdir=Path(args.workdir).expanduser().resolve(),
        )
        return _emit({
            "action": "lead-claim",
            "app_slug": slug,
            "claimed": result["claimed"],
            "lead": result["lead"],
        })

    if op == "renew":
        result = leadership.renew_lease(
            channel_dir,
            session_id=args.session_id,
            app_slug=slug,
            tool=args.tool,
            model=args.model,
            renew_every_minutes=args.renew_every_minutes,
            workdir=Path(args.workdir).expanduser().resolve(),
        )
        return _emit({
            "action": "lead-renew",
            "app_slug": slug,
            "renewed": result.get("renewed", False),
            "reason": result.get("reason"),
            "lead": result.get("lead"),
        })

    if op == "transfer":
        result = leadership.transfer_lead(
            channel_dir,
            from_session_id=args.session_id,
            to_session_id=args.to_session_id,
            to_tool=args.to_tool,
            to_model=args.to_model,
            app_slug=slug,
            tool=args.tool,
            model=args.model,
            workdir=Path(args.workdir).expanduser().resolve(),
        )
        return _emit({
            "action": "lead-transfer",
            "app_slug": slug,
            "transferred": result.get("transferred", False),
            "reason": result.get("reason"),
            "lead": result.get("lead"),
        })

    if op == "relinquish":
        result = leadership.relinquish_lead(
            channel_dir,
            session_id=args.session_id,
            app_slug=slug,
            tool=args.tool,
            model=args.model,
            workdir=Path(args.workdir).expanduser().resolve(),
        )
        return _emit({
            "action": "lead-relinquish",
            "app_slug": slug,
            "relinquished": result.get("relinquished", False),
            "reason": result.get("reason"),
        })

    return _emit({"action": "lead-error", "error": f"unknown lead op {op!r}"})


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent_rally.py", description=__doc__.splitlines()[0]
    )
    sub = p.add_subparsers(dest="command", required=True)

    def _common(sp: argparse.ArgumentParser, *, need_run: bool = True) -> None:
        sp.add_argument("--workdir", default=".")
        sp.add_argument("--session-id", required=True)
        sp.add_argument("--tool", default="claude_code")
        sp.add_argument("--model", default="inherit")
        if need_run:
            sp.add_argument("--run-id", default="unknown")
        sp.add_argument("--json", action="store_true",
                        help="Output JSON (default — accepted for parity).")

    sp_presence = sub.add_parser("presence", help="Write/refresh presence.")
    _common(sp_presence)
    sp_presence.add_argument("--phase", default="rally-point")
    sp_presence.add_argument("--files-in-flight", default=None)
    # Roster enrichment (all optional/additive — see `roster`).
    sp_presence.add_argument(
        "--cwd", default=None,
        help="Working dir this agent runs from (default: --workdir).")
    sp_presence.add_argument(
        "--pid", type=int, default=None,
        help="OS pid (default: this process).")
    sp_presence.add_argument(
        "--host", default=None,
        help="Hostname (default: socket.gethostname()).")
    sp_presence.add_argument(
        "--task", default=None,
        help="Fuller free-text task (falls back to --phase for display).")
    sp_presence.add_argument(
        "--parent", default=None,
        help="session_id of the agent that spawned this one (None=top-level).")
    sp_presence.add_argument(
        "--spawned", default=None,
        help="Self-reported fan-out as type:count CSV, "
             "e.g. coder:2,workflow:21,independent-auditor:1.")
    sp_presence.set_defaults(func=cmd_presence)

    sp_roster = sub.add_parser(
        "roster",
        help="Cross-channel live agent roster (who/where/what/subagents).",
    )
    sp_roster.add_argument(
        "--app", default=None,
        help="Filter to one app/channel slug (default: all channels).")
    sp_roster.add_argument(
        "--stale-secs", type=int, default=_roster.DEFAULT_STALE_SECS,
        help=f"Liveness window (default {_roster.DEFAULT_STALE_SECS}s).")
    sp_roster.add_argument(
        "--all", action="store_true",
        help="Include stale sessions (default: live only).")
    sp_roster.add_argument(
        "--watch", type=int, default=0, metavar="SECS",
        help="Re-render every SECS seconds (real-time view).")
    sp_roster.add_argument("--json", action="store_true",
                           help="Emit the structured roster as JSON.")
    sp_roster.set_defaults(func=cmd_roster)

    sp_stop = sub.add_parser("stop", help="Stop this session and release active claims when supported.")
    _common(sp_stop, need_run=False)
    sp_stop.add_argument("--reason", default="agent stopped")
    sp_stop.add_argument("--keep-claims", action="store_true")
    sp_stop.set_defaults(func=cmd_stop)

    sp_handoff = sub.add_parser("handoff", help="Post a kind=handoff record.")
    _common(sp_handoff)
    sp_handoff.add_argument("--to", default="peer")
    sp_handoff.add_argument("--message", default="")
    sp_handoff.add_argument("--owns", default=None)
    sp_handoff.add_argument("--does-not-own", default=None)
    sp_handoff.add_argument("--interface-contract", default="")
    sp_handoff.add_argument("--integration-checkpoint", default="")
    sp_handoff.add_argument("--allowed-tools", default=None,
                            help="CSV tool allowlist (G2 lateral limits).")
    sp_handoff.add_argument("--denied-tools", default=None,
                            help="CSV tool denylist (G2 lateral limits).")
    sp_handoff.set_defaults(func=cmd_handoff)

    sp_esc = sub.add_parser("escalate", help="Post a kind=escalation record.")
    _common(sp_esc)
    sp_esc.add_argument("--reason", required=True)
    sp_esc.add_argument("--needs", default="lead-or-user-attention")
    sp_esc.set_defaults(func=cmd_escalate)

    sp_where = sub.add_parser(
        "where",
        help="Print the global channel_dir for the current repo.",
    )
    sp_where.add_argument("--workdir", default=".")
    sp_where.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON envelope with channel_dir + app_slug keys.",
    )
    sp_where.set_defaults(func=cmd_where)

    sp_status = sub.add_parser("status", help="Read coordination status.")
    sp_status.add_argument("--workdir", default=".")
    sp_status.add_argument("--session-id", required=True)
    sp_status.add_argument(
        "--tool",
        default="claude_code",
        help="Tool name for tool-scoped inbox status (default: claude_code).",
    )
    sp_status.add_argument("--coordination-file", default=None)
    sp_status.add_argument(
        "--task-ref",
        default=None,
        help="Expected active task/claim/run ref for task-heartbeat health.",
    )
    sp_status.add_argument(
        "--task-heartbeat-grace-seconds",
        type=int,
        default=None,
        help="Grace window after next_check_in_at before a heartbeat is stale.",
    )
    sp_status.add_argument("--json", action="store_true")
    sp_status.set_defaults(func=cmd_status)

    sp_ack = sub.add_parser(
        "ack-inbox",
        help="Mark current direct/broadcast inbox messages seen.",
    )
    sp_ack.add_argument("--workdir", default=".")
    sp_ack.add_argument("--session-id", required=True)
    sp_ack.add_argument(
        "--tool",
        default="claude_code",
        help="Tool name for tool-scoped inbox ack (default: claude_code).",
    )
    sp_ack.add_argument(
        "--no-broadcast",
        action="store_true",
        help="Ack direct inbox only; leave broadcast inbox unread.",
    )
    sp_ack.add_argument("--json", action="store_true")
    sp_ack.set_defaults(func=cmd_ack_inbox)

    sp_heartbeat = sub.add_parser(
        "heartbeat",
        help="Write a structured task heartbeat for long-running work.",
    )
    _common(sp_heartbeat)
    sp_heartbeat.add_argument("--task-ref", required=True)
    sp_heartbeat.add_argument(
        "--status",
        default="running",
        choices=sorted(task_heartbeat.STATUSES),
    )
    sp_heartbeat.add_argument(
        "--not-on-task",
        action="store_true",
        help="Mark this heartbeat as drift-risk / not still on the active task.",
    )
    sp_heartbeat.add_argument("--progress", default="")
    sp_heartbeat.add_argument(
        "--evidence",
        default=None,
        help="CSV refs such as changed files, tests, commits, or handoff ids.",
    )
    sp_heartbeat.add_argument(
        "--attention-reason",
        default="",
        help="Required by convention for blocked or needs_attention heartbeats.",
    )
    sp_heartbeat.add_argument(
        "--interval-seconds",
        type=int,
        default=task_heartbeat.DEFAULT_INTERVAL_SECONDS,
    )
    sp_heartbeat.set_defaults(func=cmd_heartbeat)

    sp_boundary = sub.add_parser(
        "boundary",
        help="Validate the embedded agent-rally plugin boundary.",
    )
    sp_boundary.add_argument(
        "--workdir",
        default=".",
        help="Accepted for CLI parity; boundary validation uses --repo or plugin root.",
    )
    sp_boundary.add_argument("--repo", default=None)
    sp_boundary.add_argument("--check", action="store_true")
    sp_boundary.add_argument("--json", action="store_true")
    sp_boundary.set_defaults(func=cmd_boundary)

    sp_lead = sub.add_parser("lead", help="Leadership lease operations.")
    lead_sub = sp_lead.add_subparsers(dest="lead_op", required=True)
    for op in ("claim", "renew", "transfer", "relinquish", "status"):
        spo = lead_sub.add_parser(op)
        spo.add_argument("--workdir", default=".")
        spo.add_argument("--session-id", required=(op != "status"))
        spo.add_argument("--tool", default="claude_code")
        spo.add_argument("--model", default="inherit")
        spo.add_argument("--run-id", default="unknown")
        spo.add_argument("--json", action="store_true")
        if op in ("claim", "renew"):
            spo.add_argument("--renew-every-minutes", type=int, default=15)
        if op == "transfer":
            spo.add_argument("--renew-every-minutes", type=int, default=15)
            spo.add_argument("--to-session-id", required=True)
            spo.add_argument("--to-tool", default="codex")
            spo.add_argument("--to-model", default="inherit")
    sp_lead.set_defaults(func=cmd_lead)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # `status` subcommand has no session-id default requirement edge cases;
    # all handlers read what they need off `args`.
    if not hasattr(args, "session_id"):
        args.session_id = "agent-rally"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
