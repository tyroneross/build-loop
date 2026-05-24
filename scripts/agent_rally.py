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
    where        print the global channel_dir for the current repo (joins it)
    lead claim       claim the leadership lease
    lead renew       renew the current lease (lead only)
    lead transfer    hand the lead to another session (lead only)
    lead relinquish  give up the lead (lead only)
    lead status      read the current lead

Every subcommand accepts `--json` and prints a JSON envelope to stdout.
Stdlib only. Fire-and-forget semantics inherited from rally_point.*.

Examples (all use the generic `example-app` slug — no real app names):
    python3 scripts/agent_rally.py presence --session-id codex-r1 \\
        --tool codex --model gpt-5 --run-id run-1 --phase execute
    python3 scripts/agent_rally.py lead claim --session-id codex-r1 \\
        --tool codex --model gpt-5 --run-id run-1
    python3 scripts/agent_rally.py status --session-id codex-r1
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from rally_point import channel_paths, leadership, presence  # noqa: E402
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
    wd = Path(workdir).expanduser().resolve()
    slug = channel_paths.app_slug(wd)
    return slug, channel_paths.ensure_channel_dir(slug)


# --------------------------------------------------------------------------
# Subcommand handlers
# --------------------------------------------------------------------------

def cmd_presence(args: argparse.Namespace) -> int:
    slug, channel_dir = _resolve_channel(args.workdir)
    presence.write_presence(
        channel_dir,
        session_id=args.session_id,
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        phase=args.phase,
        files_in_flight=_split_csv(args.files_in_flight),
        cwd=Path(args.workdir).expanduser().resolve(),
    )
    return _emit({
        "action": "presence-written",
        "app_slug": slug,
        "session_id": args.session_id,
        "phase": args.phase,
    })


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
    )
    return _emit({
        "action": "escalation-posted",
        "app_slug": slug,
        "channel_revision": new_rev,
    })


def _resolve_via_discover(wd: Path) -> dict[str, Any] | None:
    """Delegate channel resolution to ``agent-rally-point`` when installed.

    Returns the discover() envelope on success, or ``None`` when the
    package isn't importable / discovery degrades / installed=false.
    Never raises — fall-back is the caller's job.
    """
    try:
        from agent_rally_point.discover import discover  # noqa: PLC0415
    except ImportError:
        return None
    try:
        result = discover(wd)
    except Exception:  # noqa: BLE001 — discovery must never crash callers
        return None
    if not isinstance(result, dict) or not result.get("installed"):
        return None
    if not result.get("channel_dir") or not result.get("app_slug"):
        return None
    return result


def cmd_where(args: argparse.Namespace) -> int:
    """Print the GLOBAL channel_dir for the current repo (the dir Rally Point
    joins). When ``agent-rally-point`` is installed, delegate to its
    ``discover()`` (protocol-of-record — canonical→legacy fallback chain).
    Otherwise, fall back to build-loop's internal
    ``channel_paths.app_slug`` / ``app_channel_dir`` resolution (legacy path).
    See ``agent-rally-point/docs/DISCOVERY.md`` for the discovery contract.

    Default output: bare path on stdout (so ``cd "$(rally where)"`` works).
    --json: ``{"channel_dir": "...", "app_slug": "...", "resolved_via": ...}``
    where ``resolved_via`` is ``"agent-rally-point"`` or
    ``"build-loop-internal"`` so callers can tell which path produced the
    answer.
    Exit non-zero with a clear message when cwd is not under a git repo
    (slug resolves to ``_unscoped`` — discovery is meaningless there).
    """
    wd = Path(args.workdir).expanduser().resolve()
    # First try: delegate to agent-rally-point.discover() (protocol-of-record).
    discovered = _resolve_via_discover(wd)
    if discovered is not None:
        channel_dir = discovered["channel_dir"]
        slug = discovered["app_slug"]
        if args.json:
            return _emit({
                "channel_dir": str(channel_dir),
                "app_slug": slug,
                "resolved_via": "agent-rally-point",
            })
        sys.stdout.write(f"{channel_dir}\n")
        return 0
    # Fallback: build-loop-internal resolution (existing PR #48 logic).
    slug = channel_paths.app_slug(wd)
    if slug == "_unscoped":
        sys.stderr.write(
            f"error: {wd} is not under a git repository — channel_paths.app_slug "
            "returned '_unscoped'. Rally Point channels are repo-scoped; run "
            "this from inside a git checkout (main or worktree).\n"
        )
        return 2
    channel_dir = channel_paths.app_channel_dir(slug)
    if args.json:
        return _emit({
            "channel_dir": str(channel_dir),
            "app_slug": slug,
            "resolved_via": "build-loop-internal",
        })
    sys.stdout.write(f"{channel_dir}\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Delegate to coordination_status.py so output stays canonical."""
    cmd = [
        sys.executable,
        str(HERE / "coordination_status.py"),
        "--workdir", args.workdir,
        "--session-id", args.session_id,
        "--json",
    ]
    if args.coordination_file:
        cmd += ["--coordination-file", args.coordination_file]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return _emit({"action": "status-error", "error": str(exc)})
    sys.stdout.write(result.stdout)
    return result.returncode


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
    sp_presence.set_defaults(func=cmd_presence)

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
    sp_status.add_argument("--coordination-file", default=None)
    sp_status.add_argument("--json", action="store_true")
    sp_status.set_defaults(func=cmd_status)

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
