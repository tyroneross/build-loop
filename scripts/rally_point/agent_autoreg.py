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
    from . import actor_identity, lifecycle, presence
    from .backend_adapter import (
        NativeResult,
        invoke_native,
        recent,
        resolve_context,
        status_post,
        write_presence as write_backend_presence,
    )
except ImportError:  # script import
    from rally_point import actor_identity, lifecycle, presence  # type: ignore
    from rally_point.backend_adapter import (  # type: ignore
        NativeResult,
        invoke_native,
        recent,
        resolve_context,
        status_post,
        write_presence as write_backend_presence,
    )

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
    return f"{agent_tool_id(agent_type)}-{secrets.token_hex(6)}"


def agent_tool_id(agent_type: str) -> str:
    """Return the fallback/base tool label for an agent type."""
    safe = (agent_type or "subagent").replace("_", "-").replace(" ", "-").lower()
    return f"agent:{safe}"


def native_agent_tool_id(agent_type: str, session_id: str) -> str:
    """Return one native Rally actor per spawned child session."""
    agent_label = agent_tool_id(agent_type).split(":", 1)[1]
    return actor_identity.native_tool_id("agent", f"{agent_label}-{session_id}")


def _resolve_channel(workdir: Path):
    """Return the authoritative backend context or ``None``."""
    try:
        context = resolve_context(workdir)
        if context.envelope.backend == "build-loop-local":
            context.local_channel_dir.mkdir(parents=True, exist_ok=True)
        return context
    except Exception:  # noqa: BLE001 — fire-and-forget
        return None


def _surface_coordination_refusal(context, operation: str) -> None:
    envelope = context.envelope
    print(
        f"rally: {operation} refused: {envelope.refusal_reason}; "
        f"remedy: {envelope.refusal_remedy}",
        file=sys.stderr,
    )


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
    Local fallback records the normalized ``agent:<agent-type>`` label. Native
    Rally adds the child session to that label so concurrently-live children
    have distinct routing actors.

    Fire-and-forget — never raises; returns "" on any failure so a
    registration problem can never break the subagent's actual task.
    """
    try:
        wd = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
        resolved = _resolve_channel(wd)
        if resolved is None:
            return ""
        context = resolved
        slug = context.envelope.app_slug
        sid = session_id or child_session_id(agent_type)
        parent = parent or _env(ENV_PARENT)
        run_id = run_id or _env(ENV_RUN_ID, "BUILD_LOOP_RUN_ID", default="unknown")
        model = model or _env(ENV_MODEL, default="unknown")
        task = task or agent_type
        tool_id = (
            native_agent_tool_id(agent_type, sid)
            if context.native
            else agent_tool_id(agent_type)
        )
        result = write_backend_presence(
            context,
            session_id=sid,
            tool=tool_id,
            local_session_id=sid,
            local_tool=agent_tool_id(agent_type),
            model=model or "unknown",
            run_id=run_id or "unknown",
            app_slug=slug,
            phase="subagent",
            cwd=wd,
            task=task,
            parent=parent,
            tier="executing",
        )
        return sid if result.ok else ""
    except Exception:  # noqa: BLE001 — fire-and-forget
        return ""


def deregister(session_id: str, *, workdir: str | Path | None = None) -> bool:
    """Close a child's presence on completion. True when it is now closed.

    Native Rally appends one exact-session ``state=done`` fact; the Build Loop
    fallback reuses ``lifecycle.reap_my_sessions`` to remove its local file.
    Both paths are fire-and-forget and idempotent.
    """
    try:
        wd = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
        resolved = _resolve_channel(wd)
        if resolved is None:
            return False
        context = resolved
        if context.native:
            state, tool, protocol_session_id = _native_session_presence_state(
                context,
                session_id=session_id,
            )
            if not tool:
                return False
            if state == "done":
                return True
            if state != "active":
                return False
            stopped = status_post(
                context,
                tool=tool,
                session_id=session_id,
                state="done",
            )
            stopped = _accept_exact_done_fact(
                stopped,
                tool=tool,
                protocol_session_id=protocol_session_id,
            )
            return stopped.ok
        if context.envelope.backend != "build-loop-local":
            _surface_coordination_refusal(context, "subagent deregistration")
            return False
        return lifecycle.reap_my_sessions(context.local_channel_dir, session_id) > 0
    except Exception:  # noqa: BLE001 — fire-and-forget
        return False


def _native_session_presence_state(
    context,
    *,
    session_id: str,
    limit: int = 500,
) -> tuple[str, str | None, str | None]:
    """Return ``active|done`` only when bounded native history proves it.

    Rally's squad and status projections are tool-wide.  Deregistration needs
    exact ``from_session_id`` state, and therefore refuses a saturated or
    malformed recent window instead of converting an old presence into a new
    ``done`` fact.
    """
    identity = invoke_native(
        context,
        ["whoami", "--json", "--tool", "agent-autoreg"],
        expected_schema="agent-rally.command.whoami.v1",
        tool="agent-autoreg",
        session_id=session_id,
    )
    if not identity.ok or not isinstance(identity.payload, dict):
        return "unproven", None, None
    identity_data = identity.payload.get("data")
    whoami_outer = identity_data.get("whoami") if isinstance(identity_data, dict) else None
    whoami = (
        whoami_outer.get("whoami")
        if isinstance(whoami_outer, dict)
        and isinstance(whoami_outer.get("whoami"), dict)
        else whoami_outer
    )
    identity_fields = (
        whoami.get("session_identity") if isinstance(whoami, dict) else None
    )
    protocol_session_id = (
        identity_fields.get("session_id")
        if isinstance(identity_fields, dict)
        else None
    )
    if not isinstance(protocol_session_id, str) or not protocol_session_id.strip():
        return "unproven", None, None

    result = recent(context, limit=limit)
    if not result.ok or not isinstance(result.payload, dict):
        return "unproven", None, protocol_session_id
    data = result.payload.get("data")
    recent_data = data.get("recent") if isinstance(data, dict) else None
    rows = recent_data.get("rows") if isinstance(recent_data, dict) else None
    if not isinstance(rows, list) or len(rows) >= limit:
        state = "visibility_incomplete" if isinstance(rows, list) else "unproven"
        return state, None, protocol_session_id

    matches: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("fact"), dict):
            return "unproven", None, protocol_session_id
        fact = row["fact"]
        if (
            fact.get("kind") == "presence"
            and fact.get("from_session_id") == protocol_session_id
        ):
            matches.append(fact)
    if not matches or any(type(fact.get("seq")) is not int for fact in matches):
        return (
            ("not_found", None, protocol_session_id)
            if not matches
            else ("unproven", None, protocol_session_id)
        )
    tools = {
        str(fact.get("tool"))
        for fact in matches
        if str(fact.get("tool") or "").strip()
    }
    if len(tools) != 1:
        return "unproven", None, protocol_session_id
    tool = next(iter(tools))
    latest_seq = max(int(fact["seq"]) for fact in matches)
    latest = [fact for fact in matches if fact.get("seq") == latest_seq]
    if len(latest) != 1:
        return "unproven", None, protocol_session_id

    markers: dict[str, str] = {}
    for segment in str(latest[0].get("subject") or "").split("|"):
        key, separator, value = segment.strip().partition("=")
        if separator:
            markers[key.strip()] = value.strip()
    state = markers.get("state")
    if state == "done":
        return "done", tool, protocol_session_id
    if state in {None, "working", "idle", "blocked"}:
        return "active", tool, protocol_session_id
    return "unproven", None, protocol_session_id


def _accept_exact_done_fact(
    result: NativeResult,
    *,
    tool: str,
    protocol_session_id: str | None,
) -> NativeResult:
    """Recover an exact committed done fact after raw/canonical id mismatch."""
    if result.ok or not protocol_session_id or not isinstance(result.payload, dict):
        return result
    payload = result.payload
    data = payload.get("data")
    if not isinstance(data, dict):
        return result

    candidates: list[dict] = []
    if (
        result.status == "invalid"
        and result.returncode == 0
        and payload.get("ok") is True
        and payload.get("product") == "rally"
        and payload.get("schema") == "agent-rally.command.status_post.v1"
    ):
        container = data.get("status_post")
        fact = container.get("fact") if isinstance(container, dict) else None
        if isinstance(fact, dict):
            candidates.append(fact)
    if (
        result.status == "partial_commit"
        and payload.get("product") == "rally"
        and payload.get("command") == "partial_commit"
        and data.get("committed") is True
    ):
        outcomes = data.get("append_outcomes")
        for outcome in reversed(outcomes if isinstance(outcomes, list) else []):
            fact = outcome.get("fact") if isinstance(outcome, dict) else None
            if isinstance(fact, dict):
                candidates.append(fact)

    for fact in candidates:
        if (
            fact.get("kind") != "presence"
            or fact.get("tool") != tool
            or fact.get("from_session_id") != protocol_session_id
            or type(fact.get("seq")) is not int
            or int(fact["seq"]) <= 0
            or not str(fact.get("event_id") or "").strip()
        ):
            continue
        markers: dict[str, str] = {}
        for segment in str(fact.get("subject") or "").split("|"):
            key, separator, value = segment.strip().partition("=")
            if separator:
                markers[key.strip()] = value.strip()
        if markers.get("state") != "done":
            continue
        return NativeResult(
            "ok",
            payload=payload,
            returncode=result.returncode,
            reason=(
                "Rally committed the exact canonical session done fact; "
                "the generic adapter compared it to the raw session id"
            ),
            revision=int(fact["seq"]),
            event_id=str(fact["event_id"]),
            backend="rally",
            transport="rally-cli",
        )
    return result


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
