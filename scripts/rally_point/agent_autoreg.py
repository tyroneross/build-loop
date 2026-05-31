#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Agent-tool auto-registration — make spawned subagents post rally presence.

The tracking gap
----------------
``presence.write_presence`` already models a parent/child agent tree
(``parent`` + ``spawned`` fields) and ``roster.build_roster`` already
nests children under their spawner. But nothing makes a subagent spawned
via the Agent tool actually *post its own presence*: the spawner
self-reports an aggregate ``spawned: {coder: 2}`` count and the children
stay invisible as live agents (no session_id, task, cwd, branch, or
heartbeat of their own). For the minutes a subagent runs real work, the
roster cannot see it.

Why this is a helper, not a hook
--------------------------------
There is no Claude-Code hook that fires *inside* a spawned subagent's
context to auto-run a registration command, and the Agent tool itself
does not post rally presence. So auto-registration is a convention the
**spawner threads through** and the **child executes as step 0**, made
zero-friction by this helper:

    1. The spawner sets identity env vars on the child (``spawn_env``)
       and/or embeds a one-line self-register directive at the top of the
       child's prompt (``preamble``).
    2. The child runs ``register(...)`` once (via that directive or the
       CLI). Zero-config: parent / run-id / model / workdir all resolve
       from explicit args -> env vars -> sane defaults. It posts presence
       with ``parent`` set, so the child appears nested in the roster with
       a real heartbeat, cwd, and branch.
    3. On completion the child (or the spawner's closeout) calls
       ``deregister(...)`` to reap the presence file immediately.

Every entry point is fire-and-forget: registration must never raise into,
or block, the subagent's real work. ``register`` returns the child
session id on success and ``""`` on any failure.

See ``AGENT_AUTOREGISTRATION.md`` for the orchestrator recipe.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

try:  # package import
    from . import lifecycle, presence
    from .discovery_bridge import resolve as _bridge_resolve
except ImportError:  # script import
    from rally_point import lifecycle, presence  # type: ignore
    from rally_point.discovery_bridge import resolve as _bridge_resolve  # type: ignore

# Identity env vars the spawner sets so children self-register with no args.
ENV_PARENT = "RALLY_PARENT_SESSION"
ENV_RUN_ID = "RALLY_POINT_RUN_ID"  # falls back to BUILD_LOOP_RUN_ID
ENV_MODEL = "RALLY_POINT_MODEL"


def _env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def child_session_id(agent_type: str) -> str:
    """Generate a legible, collision-resistant child session id.

    ``agent:<type>-<csprng-hex>`` — the ``agent:`` prefix makes spawned
    subagents distinguishable from top-level sessions in a raw channel
    listing; the CSPRNG suffix (per SEC-007) avoids forgery/collision in
    the shared multi-peer channel.
    """
    safe = (agent_type or "subagent").replace("_", "-").replace(" ", "-").lower()
    return f"agent:{safe}-{secrets.token_hex(6)}"


def _resolve_channel(workdir: Path):
    """Return ``(app_slug, channel_dir)`` or ``None`` when unresolvable."""
    try:
        env = _bridge_resolve(workdir)
        channel_dir = Path(env.channel_dir)
        if env.resolved_via == "build-loop-internal":
            channel_dir.mkdir(parents=True, exist_ok=True)
        return env.app_slug, channel_dir
    except Exception:  # noqa: BLE001 — fire-and-forget
        return None


def register(
    *,
    agent_type: str,
    task: str | None = None,
    parent: str | None = None,
    run_id: str | None = None,
    model: str | None = None,
    workdir: str | Path | None = None,
    session_id: str | None = None,
) -> str:
    """Self-register a spawned subagent's presence. Returns child id or "".

    Zero-config: any unset argument resolves from env vars set by the
    spawner (``RALLY_PARENT_SESSION``, ``RALLY_POINT_RUN_ID`` /
    ``BUILD_LOOP_RUN_ID``, ``RALLY_POINT_MODEL``) then defaults. The child
    is written with ``parent`` linked so ``roster.build_roster`` nests it.
    ``tool`` is recorded as ``agent:<agent_type>`` for at-a-glance origin.

    Fire-and-forget — never raises; returns "" on any failure so a
    registration problem can never break the subagent's actual task.
    """
    try:
        wd = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
        resolved = _resolve_channel(wd)
        if resolved is None:
            return ""
        slug, channel_dir = resolved
        sid = session_id or child_session_id(agent_type)
        parent = parent or _env(ENV_PARENT)
        run_id = run_id or _env(ENV_RUN_ID, "BUILD_LOOP_RUN_ID", default="unknown")
        model = model or _env(ENV_MODEL, default="unknown")
        task = task or agent_type
        presence.write_presence(
            channel_dir,
            session_id=sid,
            tool=f"agent:{agent_type}",
            model=model or "unknown",
            run_id=run_id or "unknown",
            app_slug=slug,
            phase="subagent",
            cwd=wd,
            task=task,
            parent=parent,
        )
        return sid
    except Exception:  # noqa: BLE001 — fire-and-forget
        return ""


def deregister(session_id: str, *, workdir: str | Path | None = None) -> bool:
    """Reap a child's presence file on completion. True if a file was removed.

    Fire-and-forget. Idempotent — reusing ``lifecycle.reap_my_sessions``,
    which the orchestrator's Phase D closeout already calls.
    """
    try:
        wd = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
        resolved = _resolve_channel(wd)
        if resolved is None:
            return False
        _slug, channel_dir = resolved
        return lifecycle.reap_my_sessions(channel_dir, session_id) > 0
    except Exception:  # noqa: BLE001 — fire-and-forget
        return False


def spawn_env(
    *,
    parent_session: str,
    run_id: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    """Identity env vars for the spawner to set on each child it dispatches.

    Merge into the child's environment so its ``register()`` call needs no
    arguments. Only non-empty values are included.
    """
    out = {ENV_PARENT: parent_session}
    if run_id:
        out[ENV_RUN_ID] = run_id
    if model:
        out[ENV_MODEL] = model
    return out


def preamble(
    *,
    agent_type: str,
    task: str,
    parent_session: str,
    run_id: str | None = None,
    workdir: str | Path | None = None,
) -> str:
    """One-line self-register directive for the spawner to embed in a child prompt.

    The spawner prepends the returned line to the subagent's prompt; the
    child runs it as its first action. ``|| true`` keeps registration from
    ever failing the subagent. The script path is resolved from this file's
    location (the spawner runs inside the same plugin checkout).
    """
    script = str(_HERE / "agent_autoreg.py")
    rid = run_id or "${RALLY_POINT_RUN_ID:-${BUILD_LOOP_RUN_ID:-unknown}}"
    wd = f" --workdir {workdir}" if workdir else ""
    return (
        f'python3 "{script}" register '
        f'--agent-type "{agent_type}" --task "{task}" '
        f'--parent "{parent_session}" --run-id "{rid}"{wd} '
        f">/dev/null 2>&1 || true"
    )


# ---------------------------------------------------------------------------
# CLI — the surface the prompt preamble invokes
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("register", help="Self-register a spawned subagent.")
    sp.add_argument("--agent-type", required=True)
    sp.add_argument("--task", default=None)
    sp.add_argument("--parent", default=None)
    sp.add_argument("--run-id", default=None)
    sp.add_argument("--model", default=None)
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--session-id", default=None)

    dp = sub.add_parser("deregister", help="Reap a subagent's presence file.")
    dp.add_argument("--session-id", required=True)
    dp.add_argument("--workdir", default=None)

    pp = sub.add_parser("preamble", help="Print the prompt self-register directive.")
    pp.add_argument("--agent-type", required=True)
    pp.add_argument("--task", required=True)
    pp.add_argument("--parent", required=True)
    pp.add_argument("--run-id", default=None)
    pp.add_argument("--workdir", default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "register":
        sid = register(
            agent_type=args.agent_type,
            task=args.task,
            parent=args.parent,
            run_id=args.run_id,
            model=args.model,
            workdir=args.workdir,
            session_id=args.session_id,
        )
        if sid:
            print(sid)
            return 0
        return 0  # fire-and-forget: never fail the caller
    if args.command == "deregister":
        deregister(args.session_id, workdir=args.workdir)
        return 0
    if args.command == "preamble":
        print(preamble(
            agent_type=args.agent_type,
            task=args.task,
            parent_session=args.parent,
            run_id=args.run_id,
            workdir=args.workdir,
        ))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
