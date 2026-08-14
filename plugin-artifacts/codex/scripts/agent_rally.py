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
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from rally_point import boundary as _boundary
from rally_point import (  # noqa: E402
    actor_identity,
    changes,
    inbox,
    leadership,
    presence,
    retraction,
    roster as _roster,
    task_heartbeat,
)
from rally_point.discovery_bridge import (  # noqa: E402
    repo_local_rally_binary,
    resolve as _bridge_resolve,
)
from rally_point.backend_adapter import (  # noqa: E402
    NativeResult,
    acknowledge as native_acknowledge,
    invoke_native,
    lead_command,
    native_room_summary,
    recent as native_recent,
    resolve_context,
    retract_fact as native_retract_fact,
    room_snapshot,
    status_post as native_status_post,
    write_presence as write_backend_presence,
)
from rally_point.post import post  # noqa: E402


def _emit(obj: dict[str, Any]) -> int:
    json.dump(obj, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _emit_coordination_refusal(
    context: Any,
    action: str,
    **fields: Any,
) -> int:
    """Return a typed refusal without touching Build Loop private state."""
    envelope = context.envelope
    _emit({
        "action": action,
        "accepted": False,
        "status": "refused",
        "app_slug": envelope.app_slug,
        "backend": envelope.backend,
        "transport": envelope.transport,
        "coordination_unavailable": envelope.coordination_unavailable,
        "reason": envelope.refusal_reason,
        "remedy": envelope.refusal_remedy,
        **fields,
    })
    return 1


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _native_self_identity(
    context: Any,
    tool: str,
    session_id: str | None,
) -> Any | None:
    """Qualify only native self actors; local routing keeps the base tool."""
    if not context.native:
        return None
    return actor_identity.resolve_identity(tool, session_id)


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
    if envelope.backend == "build-loop-local":
        channel_dir.mkdir(parents=True, exist_ok=True)
    return envelope.app_slug, channel_dir


def _run_repo_local_rally_json(
    workdir: Path,
    argv: list[str],
    *,
    timeout: int = 5,
) -> subprocess.CompletedProcess[str] | None:
    """Run the native repo-local Rally CLI when available.

    The current standalone Rally surface owns `standby` / `wake` / `wake-due`.
    Build-loop's adapter delegates to it first so both surfaces share the same
    ledger semantics; embedded parsing below is only a degraded fallback.
    """
    binary = repo_local_rally_binary(workdir)
    if not binary:
        return None
    try:
        return subprocess.run(
            [binary, *argv],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return None


def _emit_completed_process(proc: subprocess.CompletedProcess[str]) -> int:
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    elif proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_event_id(record: dict[str, Any]) -> str:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return str(
        record.get("event_id")
        or payload.get("event_id")
        or payload.get("standby_event_id")
        or f"revision:{record.get('revision', 0)}"
    )


def _legacy_due_wakes(workdir: Path, tool: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """Best-effort due standby reader for embedded legacy channels."""
    now = now or datetime.now(timezone.utc)
    _slug, channel_dir = _resolve_channel(str(workdir))
    records, _offset = changes.read_changes_since(channel_dir, 0)
    woken_refs: set[str] = set()
    for record in records:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("kind") != "wake":
            continue
        ref = (
            record.get("ref")
            or payload.get("ref")
            or payload.get("ref_standby")
            or payload.get("standby_event_id")
        )
        if ref:
            woken_refs.add(str(ref))

    standbys: list[dict[str, Any]] = []
    for record in records:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("kind") != "standby":
            continue
        owner = (
            payload.get("owner")
            or payload.get("to_tool")
            or payload.get("target")
            or record.get("tool")
        )
        if owner and str(owner) != tool:
            continue
        wake_after = payload.get("wake_after") or payload.get("wake-after")
        due_at = _parse_iso_utc(str(wake_after) if wake_after else None)
        if due_at is None or due_at > now:
            continue
        event_id = _record_event_id(record)
        if event_id in woken_refs:
            continue
        standbys.append({
            "owner": str(owner or tool),
            "reason": str(payload.get("reason") or payload.get("summary") or ""),
            "standby_event_id": event_id,
            "suggested_command": f"python3 scripts/agent_rally.py wake --tool {tool} --ref-standby {event_id} --json",
            "wake_after": wake_after,
        })
    return standbys


def build_wake_due_envelope(
    workdir: str | Path,
    tool: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return the canonical wake-due envelope for `tool`."""
    wd = Path(workdir).expanduser().resolve()
    context = resolve_context(wd)
    if context.native:
        native_identity = actor_identity.resolve_identity(tool, session_id)
        native = invoke_native(
            context,
            ["wake-due", "--tool", native_identity.native_tool, "--json"],
            expected_schema="agent-rally.command.wake-due.v1",
            tool=native_identity.native_tool,
            session_id=native_identity.session_id,
        )
        if native.ok and isinstance(native.payload, dict):
            return native.payload
        return {
            "command": "wake-due",
            "data": {"wake-due": {"due": []}},
            "ok": False,
            "product": "agent_rally",
            "schema": "agent-rally.command.wake-due.v1",
            "error": native.reason or native.status,
        }

    if context.envelope.backend != "build-loop-local":
        return {
            "command": "wake-due",
            "data": {"wake-due": {"due": []}},
            "ok": False,
            "product": "agent_rally",
            "schema": "agent-rally.command.wake-due.v1",
            "status": "refused",
            "backend": context.envelope.backend,
            "coordination_unavailable": context.envelope.coordination_unavailable,
            "reason": context.envelope.refusal_reason,
            "remedy": context.envelope.refusal_remedy,
        }

    return {
        "command": "wake-due",
        "data": {
            "wake-due": {
                "due": _legacy_due_wakes(wd, tool),
            }
        },
        "ok": True,
        "product": "agent_rally",
        "schema": "agent-rally.command.wake-due.v1",
    }


# --------------------------------------------------------------------------
# Subcommand handlers
# --------------------------------------------------------------------------

def cmd_presence(args: argparse.Namespace) -> int:
    wd = Path(args.workdir).expanduser().resolve()
    context = resolve_context(wd)
    envelope = context.envelope
    slug = envelope.app_slug
    channel_dir = Path(envelope.channel_dir)
    native_identity = _native_self_identity(
        context, args.tool, args.session_id
    )
    routing_tool = (
        native_identity.native_tool if native_identity is not None else args.tool
    )
    routing_session_id = (
        native_identity.session_id
        if native_identity is not None
        else args.session_id
    )
    if envelope.backend == "build-loop-local":
        channel_dir.mkdir(parents=True, exist_ok=True)
    elif envelope.backend != "rally":
        return _emit_coordination_refusal(
            context,
            "presence-refused",
            session_id=args.session_id,
            tool=args.tool,
        )
    cwd = (
        Path(args.cwd).expanduser().resolve()
        if getattr(args, "cwd", None)
        else Path(args.workdir).expanduser().resolve()
    )
    result = write_backend_presence(
        context,
        session_id=routing_session_id,
        tool=routing_tool,
        local_session_id=args.session_id,
        local_tool=(
            native_identity.base_tool if native_identity is not None else args.tool
        ),
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
    _emit({
        "action": "presence-written" if result.ok else "presence-failed",
        "accepted": result.ok,
        "backend": result.backend or envelope.backend,
        "transport": result.transport or envelope.transport,
        "reason": result.reason,
        "app_slug": slug,
        "session_id": args.session_id,
        "rally_session_id": routing_session_id,
        "tool": args.tool,
        "rally_tool": routing_tool,
        "phase": args.phase,
        "task": getattr(args, "task", None) or args.phase,
        "parent": getattr(args, "parent", None),
        "spawned": presence.parse_spawned(getattr(args, "spawned", None)),
    })
    return 0 if result.ok else 1


def cmd_stop(args: argparse.Namespace) -> int:
    context = resolve_context(args.workdir)
    slug = context.envelope.app_slug
    if context.native:
        native_identity = actor_identity.resolve_identity(args.tool, args.session_id)
        native_tool = native_identity.native_tool
        native_session_id = native_identity.session_id
        (
            session_state,
            state_reason,
            recent_result,
            protocol_session_id,
        ) = _native_session_presence_state(
            context,
            tool=native_tool,
            session_id=native_session_id,
        )
        if session_state == "done":
            return _emit({
                "action": "presence-stop-already-done",
                "app_slug": slug,
                "accepted": True,
                "session_id": args.session_id,
                "tool": args.tool,
                "idempotent": True,
                "claims_released": [],
                "claims_kept": True,
                "claims_unchanged": True,
                "lead_relinquished": False,
                "lead_policy": "preserved: native Rally lead ownership is tool-scoped",
                "backend": "rally",
                "reason": "exact native session already has state=done",
            })
        if session_state != "active":
            _emit({
                "action": "presence-stop-refused",
                "app_slug": slug,
                "accepted": False,
                "session_id": args.session_id,
                "tool": args.tool,
                "status": session_state,
                "native_status": recent_result.status,
                "reason": state_reason,
                "claims_unchanged": True,
                "lead_relinquished": False,
                "visibility": (
                    "bounded"
                    if session_state == "visibility_incomplete"
                    else "complete" if session_state == "not_found" else "unknown"
                ),
            })
            return 1
        released, release_error = _release_native_session_claims(
            context,
            tool=native_tool,
            session_id=native_session_id,
            protocol_session_id=protocol_session_id,
            reason=args.reason,
        ) if not args.keep_claims else ([], None)
        if release_error is not None:
            _emit({
                "action": "presence-stop-failed",
                "app_slug": slug,
                "accepted": False,
                "status": release_error.status,
                "reason": release_error.reason,
                "claims_released": released,
            })
            return 1
        sha = (
            _git_value(context.workdir, ["rev-parse", "--verify", "HEAD"])
            or "unknown"
        )
        branch = (
            _git_value(context.workdir, ["branch", "--show-current"])
            or "detached"
        )
        stopped = native_status_post(
            context,
            tool=native_tool,
            session_id=native_session_id,
            state="done",
            committed_sha=sha,
            worktree_branch=branch,
        )
        stopped = _accept_exact_native_session_fact(
            stopped,
            schema="agent-rally.command.status_post.v1",
            container="status_post",
            kind="presence",
            tool=native_tool,
            protocol_session_id=protocol_session_id,
            state="done",
        )
        _emit({
            "action": "presence-stopped" if stopped.ok else "presence-stop-failed",
            "app_slug": slug,
            "session_id": args.session_id,
            "rally_session_id": native_session_id,
            "tool": args.tool,
            "rally_tool": native_tool,
            "accepted": stopped.ok,
            "claims_released": released,
            "claims_kept": bool(args.keep_claims),
            # Native Rally's lead seat is attributed to a tool, not a protocol
            # session.  A session-scoped adapter cannot prove that no sibling
            # still relies on it, so stopping one session never vacates it.
            "lead_relinquished": False,
            "lead_policy": "preserved: native Rally lead ownership is tool-scoped",
            "backend": "rally",
            "reason": stopped.reason,
        })
        return 0 if stopped.ok else 1

    if context.envelope.backend != "build-loop-local":
        return _emit_coordination_refusal(
            context,
            "presence-stop-refused",
            session_id=args.session_id,
            tool=args.tool,
            claims_unchanged=True,
            lead_relinquished=False,
        )

    try:
        path = presence.presence_path(context.local_channel_dir, args.session_id)
    except ValueError as exc:
        _emit({
            "action": "presence-stop-refused",
            "app_slug": slug,
            "accepted": False,
            "status": "refused",
            "session_id": args.session_id,
            "reason": str(exc),
            "remedy": "use the exact session id reported by Build Loop presence",
            "presence_removed": [],
            "claims_released": [],
            "claims_kept": True,
        })
        return 1
    removed: list[str] = []
    try:
        path.unlink()
        removed.append(str(path))
    except FileNotFoundError:
        pass
    except OSError:
        pass
    return _emit({
        "action": "presence-stopped",
        "app_slug": slug,
        "session_id": args.session_id,
        "accepted": True,
        "presence_removed": removed,
        "claims_released": [],
        "claims_kept": True,
        "resolved_via": "build-loop-internal",
    })


def _git_value(workdir: Path, argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(workdir), *argv],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def _native_recent_facts(context: Any, *, limit: int = 200) -> tuple[list[dict[str, Any]], Any]:
    """Return current-repo native facts oldest-first."""
    result = native_recent(context, limit=limit)
    if not result.ok:
        return [], result
    data = result.payload.get("data") if isinstance(result.payload, dict) else {}
    recent_data = data.get("recent") if isinstance(data, dict) else {}
    rows = recent_data.get("rows") if isinstance(recent_data, dict) else []
    facts: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("fact"), dict):
            continue
        facts.append(row["fact"])
    facts.sort(
        key=lambda fact: (
            int(fact.get("seq")) if type(fact.get("seq")) is int else -1
        )
    )
    return facts, result


def _native_session_presence_state(
    context: Any,
    *,
    tool: str,
    session_id: str,
    limit: int = 500,
) -> tuple[str, str, Any, str | None]:
    """Resolve one exact native session without inferring from tool state.

    Rally's room/status projections aggregate by tool, while raw presence facts
    retain ``from_session_id``.  The recent API is bounded and does not expose a
    total, so a saturated window cannot prove that an older exact session has
    no newer state outside the visible history.  That case fails closed before
    claims, lead, or status can be mutated.
    """
    identity = invoke_native(
        context,
        ["whoami", "--json", "--tool", tool],
        expected_schema="agent-rally.command.whoami.v1",
        tool=tool,
        session_id=session_id,
    )
    if not identity.ok or not isinstance(identity.payload, dict):
        return (
            "unproven",
            identity.reason or "native Rally could not resolve the protocol session identity",
            identity,
            None,
        )
    identity_data = identity.payload.get("data")
    whoami_outer = identity_data.get("whoami") if isinstance(identity_data, dict) else None
    whoami = (
        whoami_outer.get("whoami")
        if isinstance(whoami_outer, dict)
        and isinstance(whoami_outer.get("whoami"), dict)
        else whoami_outer
    )
    session_identity = (
        whoami.get("session_identity") if isinstance(whoami, dict) else None
    )
    protocol_session_id = (
        session_identity.get("session_id")
        if isinstance(session_identity, dict)
        else None
    )
    if not isinstance(protocol_session_id, str) or not protocol_session_id.strip():
        return (
            "unproven",
            "native Rally whoami omitted the protocol session identity",
            identity,
            None,
        )

    facts, result = _native_recent_facts(context, limit=limit)
    if not result.ok:
        return (
            "unproven",
            result.reason or "native Rally recent read failed",
            result,
            protocol_session_id,
        )
    data = result.payload.get("data") if isinstance(result.payload, dict) else {}
    recent_data = data.get("recent") if isinstance(data, dict) else {}
    rows = recent_data.get("rows") if isinstance(recent_data, dict) else None
    if not isinstance(rows, list):
        return (
            "unproven",
            "native Rally recent response omitted rows",
            result,
            protocol_session_id,
        )
    if len(rows) >= limit:
        return (
            "visibility_incomplete",
            f"native Rally recent history reached the {limit}-row visibility bound; "
            "exact session state is unproven, so no stop mutation was attempted",
            result,
            protocol_session_id,
        )
    if len(facts) != len(rows):
        return (
            "unproven",
            "native Rally recent response contained malformed rows",
            result,
            protocol_session_id,
        )

    session_matches = [
        fact
        for fact in facts
        if fact.get("kind") == "presence"
        and fact.get("from_session_id") == protocol_session_id
    ]
    if not session_matches:
        return (
            "not_found",
            "exact session has no presence fact in the complete native Rally recent view",
            result,
            protocol_session_id,
        )
    session_tools = {
        str(fact.get("tool"))
        for fact in session_matches
        if str(fact.get("tool") or "").strip()
    }
    if session_tools != {tool}:
        return (
            "unproven",
            "exact session identity maps to an unexpected or ambiguous native Rally tool",
            result,
            protocol_session_id,
        )
    matches = session_matches
    if any(type(fact.get("seq")) is not int for fact in matches):
        return (
            "unproven",
            "exact session presence has an invalid sequence",
            result,
            protocol_session_id,
        )
    latest_seq = max(int(fact["seq"]) for fact in matches)
    latest = [fact for fact in matches if fact.get("seq") == latest_seq]
    if len(latest) != 1:
        return (
            "unproven",
            "exact session has ambiguous latest presence facts",
            result,
            protocol_session_id,
        )

    subject = str(latest[0].get("subject") or "")
    markers: dict[str, str] = {}
    for segment in subject.split("|"):
        key, separator, value = segment.strip().partition("=")
        if separator:
            markers[key.strip()] = value.strip()
    state = markers.get("state")
    if state == "done":
        return (
            "done",
            "exact native session already has state=done",
            result,
            protocol_session_id,
        )
    if state in {None, "working", "idle", "blocked"}:
        return (
            "active",
            "exact native session has a stoppable presence state",
            result,
            protocol_session_id,
        )
    return (
        "unproven",
        f"exact native session has unknown state={state!r}",
        result,
        protocol_session_id,
    )


def _release_native_session_claims(
    context: Any,
    *,
    tool: str,
    session_id: str,
    protocol_session_id: str | None,
    reason: str,
) -> tuple[list[str], Any | None]:
    """Release claims filtered by canonical identity, authorized by raw id."""
    if not protocol_session_id:
        return [], None
    snapshot = room_snapshot(context, actor=tool)
    if not snapshot.ok:
        return [], snapshot
    claims = native_room_summary(snapshot).get("active_claims")
    released: list[str] = []
    for item in claims if isinstance(claims, list) else []:
        if not isinstance(item, dict):
            continue
        fact = item.get("fact") if isinstance(item.get("fact"), dict) else item
        if (
            fact.get("tool") != tool
            or fact.get("from_session_id") != protocol_session_id
            or not fact.get("event_id")
        ):
            continue
        event_id = str(fact["event_id"])
        outcome = invoke_native(
            context,
            [
                "say",
                "release",
                "--json",
                "--tool",
                tool,
                "--subject",
                reason or "agent stopped",
                "--ref",
                event_id,
            ],
            expected_schema="agent-rally.command.say.v1",
            tool=tool,
            session_id=session_id,
            mutating=True,
        )
        outcome = _accept_exact_native_session_fact(
            outcome,
            schema="agent-rally.command.say.v1",
            container="say",
            kind="release",
            tool=tool,
            protocol_session_id=protocol_session_id,
            subject=reason or "agent stopped",
            ref=event_id,
        )
        if not outcome.ok:
            return released, outcome
        released.append(event_id)
    return released, None


def _accept_exact_native_session_fact(
    result: NativeResult,
    *,
    schema: str,
    container: str,
    kind: str,
    tool: str,
    protocol_session_id: str,
    state: str | None = None,
    subject: str | None = None,
    ref: str | None = None,
) -> NativeResult:
    """Accept a committed fact rejected only by raw/canonical id mismatch.

    Build Loop passes the raw session id in ``RALLY_SESSION_ID`` so Rally can
    authorize the caller. Rally records that identity canonically (for example
    ``sess:managed:<id>#live``), while the shared adapter currently compares
    the returned fact to the raw id. Preserve every other receipt check and
    recover only when the response itself proves this exact canonical session
    fact with a positive sequence.
    """
    if result.ok or not protocol_session_id:
        return result
    payload = result.payload
    if not isinstance(payload, dict):
        return result
    data = payload.get("data")
    if not isinstance(data, dict):
        return result

    candidates: list[dict[str, Any]] = []
    normal_success = bool(
        result.status == "invalid"
        and result.returncode == 0
        and payload.get("ok") is True
        and payload.get("product") == "rally"
        and payload.get("schema") == schema
    )
    if normal_success:
        wrapper = data.get(container)
        fact = wrapper.get("fact") if isinstance(wrapper, dict) else None
        if isinstance(fact, dict):
            candidates.append(fact)
    partial_commit = bool(
        result.status == "partial_commit"
        and payload.get("product") == "rally"
        and payload.get("command") == "partial_commit"
        and data.get("committed") is True
    )
    if partial_commit:
        outcomes = data.get("append_outcomes")
        for outcome in reversed(outcomes if isinstance(outcomes, list) else []):
            fact = outcome.get("fact") if isinstance(outcome, dict) else None
            if isinstance(fact, dict):
                candidates.append(fact)

    for fact in candidates:
        if (
            fact.get("kind") != kind
            or fact.get("tool") != tool
            or fact.get("from_session_id") != protocol_session_id
            or type(fact.get("seq")) is not int
            or int(fact["seq"]) <= 0
            or not str(fact.get("event_id") or "").strip()
        ):
            continue
        if subject is not None and fact.get("subject") != subject:
            continue
        if ref is not None and fact.get("ref") != ref and fact.get("ref_id") != ref:
            continue
        if state is not None:
            markers: dict[str, str] = {}
            for segment in str(fact.get("subject") or "").split("|"):
                key, separator, value = segment.strip().partition("=")
                if separator:
                    markers[key.strip()] = value.strip()
            if markers.get("state") != state:
                continue
        return NativeResult(
            "ok",
            payload=payload,
            returncode=result.returncode,
            reason=(
                "Rally committed the exact canonical session fact; "
                "the generic adapter compared it to the raw session id"
            ),
            revision=int(fact["seq"]),
            event_id=str(fact["event_id"]),
            backend="rally",
            transport="rally-cli",
        )
    return result


def cmd_handoff(args: argparse.Namespace) -> int:
    slug, channel_dir = _resolve_channel(args.workdir)
    context = resolve_context(args.workdir)
    native_identity = _native_self_identity(
        context, args.tool, args.session_id
    )
    routing_tool = (
        native_identity.native_tool if native_identity is not None else args.tool
    )
    routing_session_id = (
        native_identity.session_id
        if native_identity is not None
        else args.session_id
    )
    payload = {
        "session_id": routing_session_id,
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
    if native_identity is not None:
        payload["host_tool"] = native_identity.base_tool
    new_rev = post(
        channel_dir=channel_dir,
        kind="handoff",
        tool=routing_tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        payload=payload,
        workdir=Path(args.workdir).expanduser().resolve(),
        local_tool=(
            native_identity.base_tool if native_identity is not None else args.tool
        ),
        local_session_id=args.session_id,
    )
    _emit({
        "action": "handoff-posted" if new_rev is not None else "handoff-rejected",
        "app_slug": slug,
        "channel_revision": new_rev,
        "accepted": new_rev is not None,
    })
    return 0 if new_rev is not None else 1


def cmd_escalate(args: argparse.Namespace) -> int:
    slug, channel_dir = _resolve_channel(args.workdir)
    context = resolve_context(args.workdir)
    native_identity = _native_self_identity(
        context, args.tool, args.session_id
    )
    routing_tool = (
        native_identity.native_tool if native_identity is not None else args.tool
    )
    routing_session_id = (
        native_identity.session_id
        if native_identity is not None
        else args.session_id
    )
    payload = {
        "session_id": routing_session_id,
        "reason": args.reason,
        "needs": args.needs,
    }
    if native_identity is not None:
        payload["host_tool"] = native_identity.base_tool
    new_rev = post(
        channel_dir=channel_dir,
        kind="escalation",
        tool=routing_tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        payload=payload,
        workdir=Path(args.workdir).expanduser().resolve(),
        local_tool=(
            native_identity.base_tool if native_identity is not None else args.tool
        ),
        local_session_id=args.session_id,
    )
    _emit({
        "action": (
            "escalation-posted" if new_rev is not None else "escalation-rejected"
        ),
        "app_slug": slug,
        "channel_revision": new_rev,
        "accepted": new_rev is not None,
    })
    return 0 if new_rev is not None else 1


def _read_raw_log(channel_dir: Path) -> list[dict[str, Any]]:
    """Return the UNRESOLVED log (live + archived) — what a retraction author needs.

    The normal read paths drop already-retracted records, so a retraction author
    reading through them could not see its own target. This is the raw view.
    """
    records, _offset = changes.read_changes_since(
        channel_dir, 0, resolve_retractions=False
    )
    return records + changes.read_archived_changes(
        channel_dir, resolve_retractions=False
    )


def cmd_retract(args: argparse.Namespace) -> int:
    """Withdraw a previously posted fact by APPENDING a retraction record.

    The log is immutable — nothing is edited or deleted. The retraction names
    the target's ``event_id`` (and optionally the fact that supersedes it), and
    every build-loop read path stops surfacing the withdrawn claim from then on.

    Exit 1 when the target cannot be found or cannot be retracted, so a caller
    can tell a typo'd id from a landed retraction. ``--force`` posts anyway,
    for a fact that lives only in a store build-loop cannot read back.
    """
    context = resolve_context(args.workdir)
    slug = context.envelope.app_slug
    target = args.fact.strip()
    if context.native:
        native_identity = actor_identity.resolve_identity(args.tool, args.session_id)
        result = native_retract_fact(
            context,
            fact_id=target,
            tool=native_identity.native_tool,
            reason=args.reason,
            superseded_by=args.superseded_by,
            session_id=native_identity.session_id,
        )
        native_data = {}
        if isinstance(result.payload, dict):
            data = result.payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("retract"), dict):
                native_data = data["retract"]
        _emit({
            "action": "retracted" if result.ok else "retract-rejected",
            "app_slug": slug,
            "fact": target,
            "accepted": result.ok,
            "backend": "rally",
            "status": native_data.get("status") or result.status,
            "reason": native_data.get("reason") or result.reason,
            "superseded_by": args.superseded_by,
            "channel_revision": result.revision,
            "event_id": result.event_id,
        })
        return 0 if result.ok else 1

    if context.envelope.backend != "build-loop-local":
        return _emit_coordination_refusal(
            context,
            "retract-refused",
            fact=target,
        )

    channel_dir = context.local_channel_dir
    raw = _read_raw_log(channel_dir)
    existing = retraction.index(raw)
    match = next((r for r in raw if r.get("event_id") == target), None)

    def _reject(action: str, detail: str) -> int:
        _emit({
            "action": action,
            "app_slug": slug,
            "fact": target,
            "accepted": False,
            "detail": detail,
        })
        return 1

    if target in existing:
        return _reject(
            "retract-noop",
            f"already retracted by {existing[target]['retracted_by']}"
            f" ({existing[target]['reason'] or 'no reason recorded'})",
        )
    if match is not None and retraction.is_retraction(match):
        return _reject(
            "retract-refused",
            "target is itself a retraction; retracting it would erase the "
            "correction trail. Post a new corrective fact instead.",
        )
    if match is None and not args.force:
        return _reject(
            "retract-target-not-found",
            "no record with this event_id in the readable log. Re-check the id "
            "from `rally room`, or pass --force if the fact lives in a store "
            "build-loop cannot read back.",
        )

    payload = retraction.build_payload(
        target=target,
        reason=args.reason,
        superseded_by=args.superseded_by,
        session_id=args.session_id,
    )
    new_rev = post(
        channel_dir=channel_dir,
        kind=retraction.RETRACT_KIND,
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        payload=payload,
        workdir=Path(args.workdir).expanduser().resolve(),
        local_tool=args.tool,
        local_session_id=args.session_id,
    )
    _emit({
        "action": "retracted" if new_rev is not None else "retract-rejected",
        "app_slug": slug,
        "fact": target,
        "target_found": match is not None,
        "superseded_by": args.superseded_by,
        "superseded_by_found": (
            None if not args.superseded_by
            else any(r.get("event_id") == args.superseded_by for r in raw)
        ),
        "reason": payload["reason"],
        "channel_revision": new_rev,
        "accepted": new_rev is not None,
    })
    return 0 if new_rev is not None else 1


_STATUS_PTR_RE = re.compile(r"\[file=(?P<file>[^\]\s]+)\s+sha=(?P<sha>[^\]\s]*)\]")


def _is_status_record(record: dict) -> bool:
    """A status pointer survives as kind='status' (fact_v1) OR subject='status'
    (the canonical rally binary remaps the kind but keeps the subject)."""
    if record.get("kind") == "status":
        return True
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return record.get("subject") == "status" or payload.get("subject") == "status"


def cmd_status_post(args: argparse.Namespace) -> int:
    """Post a typed status record pointing at the canonical CURRENT.md.

    Mirrors handoff/escalate so a peer (or a fresh terminal) reading the room has a
    durable pointer to the code-grounded status file + the sha it describes. The
    canonical rally binary only preserves its fixed fact schema (it drops unknown
    payload keys and remaps unknown kinds), so the file+sha are ALSO encoded into
    the summary text — the one free-text field that survives the native store —
    while the structured keys ride along for build-loop's own fact_v1 read-back.
    """
    slug, channel_dir = _resolve_channel(args.workdir)
    context = resolve_context(args.workdir)
    native_identity = _native_self_identity(
        context, args.tool, args.session_id
    )
    routing_tool = (
        native_identity.native_tool if native_identity is not None else args.tool
    )
    routing_session_id = (
        native_identity.session_id
        if native_identity is not None
        else args.session_id
    )
    summary = args.summary or "status refreshed"
    encoded = f"{summary} [file={args.file} sha={args.committed_sha}]"
    payload = {
        "session_id": routing_session_id,
        "summary": encoded,
        "file": args.file,
        "committed_sha": args.committed_sha,
    }
    if native_identity is not None:
        payload["host_tool"] = native_identity.base_tool
    new_rev = post(
        channel_dir=channel_dir,
        kind="status",
        tool=routing_tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        payload=payload,
        workdir=Path(args.workdir).expanduser().resolve(),
        local_tool=(
            native_identity.base_tool if native_identity is not None else args.tool
        ),
        local_session_id=args.session_id,
    )
    _emit({
        "action": "status-posted" if new_rev is not None else "status-rejected",
        "app_slug": slug,
        "channel_revision": new_rev,
        "accepted": new_rev is not None,
    })
    return 0 if new_rev is not None else 1


def cmd_status_read(args: argparse.Namespace) -> int:
    """Read the latest status record + extract its CURRENT.md pointer (cross-store)."""
    context = resolve_context(args.workdir)
    slug = context.envelope.app_slug
    status_tool = args.tool
    native_recent_saturated = False
    if context.native:
        if args.tool:
            status_tool = actor_identity.resolve_identity(
                args.tool, getattr(args, "session_id", None)
            ).native_tool
        facts, native = _native_recent_facts(context, limit=200)
        if not native.ok:
            return _emit({
                "action": "status-read-failed",
                "app_slug": slug,
                "found": False,
                "backend": "rally",
                "reason": native.reason,
            })
        native_data = (
            native.payload.get("data") if isinstance(native.payload, dict) else {}
        )
        native_recent_data = (
            native_data.get("recent") if isinstance(native_data, dict) else {}
        )
        native_rows = (
            native_recent_data.get("rows")
            if isinstance(native_recent_data, dict)
            else None
        )
        native_limit = (
            native_recent_data.get("limit")
            if isinstance(native_recent_data, dict)
            else None
        )
        if type(native_limit) is not int or native_limit <= 0:
            native_limit = 200
        native_recent_saturated = bool(
            isinstance(native_rows, list) and len(native_rows) >= native_limit
        )
        records = []
        for fact in facts:
            records.append(changes.normalize_record({
                "seq": fact.get("seq"),
                "occurred_at": fact.get("created_at"),
                "event_type": fact.get("kind"),
                "payload": fact,
                "engagement": slug,
            }))
    elif context.envelope.backend == "build-loop-local":
        records, _ = changes.read_changes_since(context.local_channel_dir, 0)
    else:
        return _emit_coordination_refusal(
            context,
            "status-read-refused",
            found=False,
        )
    status_records = [r for r in records if _is_status_record(r)]
    if status_tool:
        status_records = [
            r for r in status_records if r.get("tool") == status_tool
        ]
    latest = max(
        status_records,
        key=lambda record: int(record.get("revision") or 0),
        default=None,
    )
    pointer = None
    if latest:
        payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
        text = payload.get("summary") or payload.get("reason") or ""
        file = payload.get("file")
        sha = payload.get("committed_sha")
        if not file:  # native store dropped the structured keys — recover from text
            match = _STATUS_PTR_RE.search(text)
            if match:
                file = match.group("file")
                sha = sha or match.group("sha")
        pointer = {
            "file": file,
            "committed_sha": sha,
            "summary": text,
            "tool": latest.get("tool"),
            "ts": latest.get("ts"),
            "revision": latest.get("revision"),
        }
    coverage_incomplete = bool(
        context.native and latest is None and native_recent_saturated
    )
    return _emit({
        "action": "status-read",
        "app_slug": slug,
        "found": latest is not None,
        "status": (
            "unknown"
            if coverage_incomplete
            else "found" if latest is not None else "not_found"
        ),
        "coverage_incomplete": coverage_incomplete,
        "reason": (
            "native_recent_limit_saturated" if coverage_incomplete else None
        ),
        "pointer": pointer,
        "latest": latest,
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
    context = resolve_context(args.workdir)
    slug = context.envelope.app_slug
    if context.native:
        native_identity = actor_identity.resolve_identity(args.tool, args.session_id)
        if args.no_broadcast:
            _emit({
                "action": "inbox-ack-refused",
                "app_slug": slug,
                "accepted": False,
                "backend": "rally",
                "reason": "native Rally cannot selectively exclude broadcast acknowledgements",
            })
            return 1
        result = native_acknowledge(
            context,
            tool=native_identity.native_tool,
            session_id=native_identity.session_id,
        )
        _emit({
            "action": "inbox-acknowledged" if result.ok else "inbox-ack-failed",
            "app_slug": slug,
            "accepted": result.ok,
            "backend": "rally",
            "status": result.status,
            "reason": result.reason,
            "channel_revision": result.revision,
            "broadcast_included": True,
            "warning": (
                "native Rally checkpoints direct and broadcast messages together"
                if args.no_broadcast else None
            ),
        })
        return 0 if result.ok else 1
    if context.envelope.backend != "build-loop-local":
        return _emit_coordination_refusal(
            context,
            "inbox-ack-refused",
            session_id=args.session_id,
            tool=args.tool,
        )
    result = inbox.mark_read(
        context.local_channel_dir,
        tool=args.tool,
        session_id=args.session_id,
        include_broadcast=not args.no_broadcast,
    )
    return _emit({"app_slug": slug, **result})


def cmd_heartbeat(args: argparse.Namespace) -> int:
    """Write a structured heartbeat for a long-running task."""
    context = resolve_context(args.workdir)
    slug = context.envelope.app_slug
    if not context.native and context.envelope.backend != "build-loop-local":
        return _emit_coordination_refusal(
            context,
            "task-heartbeat-refused",
            session_id=args.session_id,
            tool=args.tool,
            task_ref=args.task_ref,
        )
    native_identity = (
        actor_identity.resolve_identity(args.tool, args.session_id)
        if context.native
        else None
    )
    record = task_heartbeat.make_record(
        session_id=(
            native_identity.session_id
            if native_identity is not None
            else args.session_id
        ),
        tool=(
            native_identity.native_tool
            if native_identity is not None
            else args.tool
        ),
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
    accepted = True
    reason = None
    revision_value = None
    actual_backend = context.envelope.backend
    actual_transport = context.envelope.transport
    post_status = "posted"
    if context.native:
        assert native_identity is not None
        outcome: dict[str, Any] = {}
        revision_value = post(
            channel_dir=Path(context.envelope.channel_dir),
            kind="presence",
            tool=native_identity.native_tool,
            model=args.model,
            run_id=args.run_id,
            app_slug=slug,
            payload={
                "subject": "task-heartbeat",
                "summary": args.progress or args.status,
                "session_id": native_identity.session_id,
                "host_tool": native_identity.base_tool,
                "task_heartbeat": record,
            },
            workdir=context.workdir,
            outcome=outcome,
            local_tool=native_identity.base_tool,
            local_session_id=args.session_id,
        )
        accepted = revision_value is not None and outcome.get("status") == "posted"
        reason = outcome.get("reason")
        actual_backend = str(outcome.get("backend") or actual_backend)
        actual_transport = str(outcome.get("transport") or actual_transport)
        post_status = str(outcome.get("status") or "failed")
        if accepted and actual_backend == "build-loop-local":
            local_record = {
                **record,
                "session_id": args.session_id,
                "tool": native_identity.base_tool,
            }
            task_heartbeat._line_append(
                task_heartbeat.heartbeat_path(
                    context.local_channel_dir, native_identity.base_tool
                ),
                local_record,
            )
    else:
        task_heartbeat._line_append(
            task_heartbeat.heartbeat_path(context.local_channel_dir, args.tool),
            record,
        )
    payload = {
        "action": "task-heartbeat-written" if accepted else "task-heartbeat-failed",
        "app_slug": slug,
        "session_id": args.session_id,
        "tool": args.tool,
        "rally_session_id": (
            native_identity.session_id if context.native else args.session_id
        ),
        "rally_tool": (
            native_identity.native_tool if context.native else args.tool
        ),
        "task_ref": args.task_ref,
        "status": record["status"],
        "still_on_task": record["still_on_task"],
        "next_check_in_at": record["next_check_in_at"],
        "accepted": accepted,
        "backend": actual_backend,
        "transport": actual_transport,
        "post_status": post_status,
        "channel_revision": revision_value,
        "reason": reason,
    }
    _emit(payload)
    return 0 if accepted else 1


def cmd_standby(args: argparse.Namespace) -> int:
    """Post a standby fact with a wake-after time."""
    wd = Path(args.workdir).expanduser().resolve()
    context = resolve_context(wd)
    slug = context.envelope.app_slug
    local_fallback_authorized = context.envelope.backend == "build-loop-local"
    local_fallback_reason = "selected-build-loop-local"
    if context.native:
        native_identity = actor_identity.resolve_identity(args.tool, args.session_id)
        cmd = [
            "say",
            "standby",
            "--tool",
            native_identity.native_tool,
            "--reason",
            args.reason,
            "--wake-after",
            args.wake_after,
            "--json",
        ]
        if args.run_id:
            cmd.extend(["--run", args.run_id])
        if args.step:
            cmd.extend(["--step", args.step])
        if args.parent_step:
            cmd.extend(["--parent-step", args.parent_step])
        native = invoke_native(
            context,
            cmd,
            expected_schema="agent-rally.command.say.v1",
            tool=native_identity.native_tool,
            session_id=native_identity.session_id,
            mutating=True,
        )
        if native.ok and isinstance(native.payload, dict):
            return _emit(native.payload)
        if not native.precommit_unavailable:
            _emit({
                "action": "standby-rejected",
                "app_slug": slug,
                "accepted": False,
                "status": native.status,
                "reason": native.reason,
                "event_id": native.event_id,
                "remedy": native.remedy,
            })
            return 1
        local_fallback_authorized = native.precommit_unavailable
        if local_fallback_authorized:
            local_fallback_reason = "native-precommit-unavailable"
    if not local_fallback_authorized:
        return _emit_coordination_refusal(
            context,
            "standby-refused",
            session_id=args.session_id,
            tool=args.tool,
        )
    channel_dir = context.local_channel_dir
    payload = {
        "session_id": args.session_id,
        "owner": args.tool,
        "reason": args.reason,
        "wake_after": args.wake_after,
        "subject": "agent standby",
    }
    if args.step:
        payload["step"] = args.step
    if args.parent_step:
        payload["parent_step"] = args.parent_step
    new_rev = post(
        channel_dir=channel_dir,
        kind="standby",
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        payload=payload,
        workdir=None,
    )
    _emit({
        "action": "standby-posted" if new_rev is not None else "standby-rejected",
        "app_slug": slug,
        "channel_revision": new_rev,
        "accepted": new_rev is not None,
        "wake_after": args.wake_after,
        "backend": "build-loop-local",
        "transport": "fact-v1",
        "fallback_reason": local_fallback_reason,
    })
    return 0 if new_rev is not None else 1


def cmd_wake(args: argparse.Namespace) -> int:
    """Post a wake fact that resolves a standby fact."""
    wd = Path(args.workdir).expanduser().resolve()
    context = resolve_context(wd)
    slug = context.envelope.app_slug
    local_fallback_authorized = context.envelope.backend == "build-loop-local"
    local_fallback_reason = "selected-build-loop-local"
    if context.native:
        native_identity = actor_identity.resolve_identity(args.tool, args.session_id)
        cmd = [
            "say",
            "wake",
            "--tool",
            native_identity.native_tool,
            "--ref-standby",
            args.ref_standby,
            "--json",
        ]
        if args.run_id:
            cmd.extend(["--run", args.run_id])
        if args.step:
            cmd.extend(["--step", args.step])
        native = invoke_native(
            context,
            cmd,
            expected_schema="agent-rally.command.say.v1",
            tool=native_identity.native_tool,
            session_id=native_identity.session_id,
            mutating=True,
        )
        if native.ok and isinstance(native.payload, dict):
            return _emit(native.payload)
        if not native.precommit_unavailable:
            _emit({
                "action": "wake-rejected",
                "app_slug": slug,
                "accepted": False,
                "status": native.status,
                "reason": native.reason,
                "event_id": native.event_id,
                "remedy": native.remedy,
            })
            return 1
        local_fallback_authorized = native.precommit_unavailable
        if local_fallback_authorized:
            local_fallback_reason = "native-precommit-unavailable"
    if not local_fallback_authorized:
        return _emit_coordination_refusal(
            context,
            "wake-refused",
            session_id=args.session_id,
            tool=args.tool,
        )
    channel_dir = context.local_channel_dir
    payload = {
        "session_id": args.session_id,
        "ref_standby": args.ref_standby,
        "subject": "wake intent",
    }
    if args.step:
        payload["step"] = args.step
    new_rev = post(
        channel_dir=channel_dir,
        kind="wake",
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
        app_slug=slug,
        payload=payload,
        workdir=None,
    )
    _emit({
        "action": "wake-posted" if new_rev is not None else "wake-rejected",
        "app_slug": slug,
        "channel_revision": new_rev,
        "accepted": new_rev is not None,
        "ref_standby": args.ref_standby,
        "backend": "build-loop-local",
        "transport": "fact-v1",
        "fallback_reason": local_fallback_reason,
    })
    return 0 if new_rev is not None else 1


def cmd_wake_due(args: argparse.Namespace) -> int:
    """Read due standby facts for this tool."""
    return _emit(
        build_wake_due_envelope(args.workdir, args.tool, args.session_id)
    )


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
    context = resolve_context(args.workdir)
    slug = context.envelope.app_slug
    op = args.lead_op

    if context.native:
        native_identity = actor_identity.resolve_identity(args.tool, args.session_id)
        native_tool = native_identity.native_tool
        native_session_id = native_identity.session_id
        if op == "status":
            result = lead_command(
                context,
                argv=["show"],
                tool=native_tool,
                session_id=native_session_id,
                mutating=False,
            )
        elif op == "claim":
            result = lead_command(
                context,
                argv=["assign", "--tool", native_tool, "--to", native_tool],
                tool=native_tool,
                session_id=native_session_id,
                mutating=True,
            )
        elif op == "renew":
            # Native Rally's lead seat is ledger-projected rather than leased.
            # A read verifies that this tool still owns it; there is no shadow
            # lease file to renew.
            result = lead_command(
                context,
                argv=["show"],
                tool=native_tool,
                session_id=native_session_id,
                mutating=False,
            )
        elif op == "transfer":
            result = lead_command(
                context,
                argv=["handoff", "--tool", native_tool, "--to", args.to_tool],
                tool=native_tool,
                session_id=native_session_id,
                mutating=True,
            )
        elif op == "relinquish":
            result = lead_command(
                context,
                argv=["relinquish", "--tool", native_tool],
                tool=native_tool,
                session_id=native_session_id,
                mutating=True,
            )
        else:
            return _emit({"action": "lead-error", "error": f"unknown lead op {op!r}"})

        lead_data: dict[str, Any] = {}
        if isinstance(result.payload, dict):
            data = result.payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("lead"), dict):
                lead_data = data["lead"]
        accepted = result.ok
        if op == "renew" and accepted:
            accepted = lead_data.get("current_lead") == native_tool
        _emit({
            "action": f"lead-{op}",
            "app_slug": slug,
            "accepted": accepted,
            "backend": "rally",
            "lead": lead_data,
            "status": result.status,
            "reason": (
                result.reason
                or ("native lead is held by another tool" if result.ok and not accepted else None)
            ),
            "channel_revision": result.revision,
        })
        return 0 if accepted else 1

    if context.envelope.backend != "build-loop-local":
        return _emit_coordination_refusal(
            context,
            f"lead-{op}-refused",
            session_id=args.session_id,
            tool=args.tool,
        )

    channel_dir = context.local_channel_dir

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

    sp_retract = sub.add_parser(
        "retract",
        help="Withdraw a previously posted fact by appending a retraction record.",
    )
    _common(sp_retract)
    sp_retract.add_argument(
        "--fact", required=True,
        help="event_id of the fact to withdraw (the `[fact_...]` id in `rally room`).")
    sp_retract.add_argument(
        "--reason", required=True,
        help="Why the fact is being withdrawn — surfaced to peers in its place.")
    sp_retract.add_argument(
        "--superseded-by", default=None, dest="superseded_by",
        help="Optional event_id of the corrected fact that replaces this one.")
    sp_retract.add_argument(
        "--force", action="store_true",
        help="Post even when the target is not in the readable log.")
    sp_retract.set_defaults(func=cmd_retract)

    sp_status_post = sub.add_parser(
        "status-post",
        help="Post a typed kind=status record pointing at the canonical CURRENT.md.",
    )
    _common(sp_status_post)
    sp_status_post.add_argument(
        "--file", required=True,
        help="Path to the canonical CURRENT.md status file.")
    sp_status_post.add_argument(
        "--committed-sha", default="", dest="committed_sha",
        help="The repo HEAD sha the status file describes.")
    sp_status_post.add_argument(
        "--summary", default="",
        help="One-line status summary surfaced to peers.")
    sp_status_post.set_defaults(func=cmd_status_post)

    sp_status_read = sub.add_parser(
        "status-read",
        help="Read the latest typed kind=status record (canonical-status pointer).",
    )
    sp_status_read.add_argument("--workdir", default=".")
    sp_status_read.add_argument(
        "--tool", default=None,
        help="Optional: prefer this tool's latest status record.")
    sp_status_read.add_argument(
        "--session-id",
        default=None,
        help="Current session for exact native actor status filtering.",
    )
    sp_status_read.add_argument(
        "--json", action="store_true",
        help="Output JSON (default — accepted for parity).")
    sp_status_read.set_defaults(func=cmd_status_read)

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

    sp_standby = sub.add_parser(
        "standby",
        help="Post a standby fact with a wake-after time.",
    )
    _common(sp_standby)
    sp_standby.add_argument("--reason", required=True)
    sp_standby.add_argument(
        "--wake-after",
        required=True,
        help="Relative +30m/+2h/+1d or ISO timestamp, matching native Rally.",
    )
    sp_standby.add_argument("--step", default=None)
    sp_standby.add_argument("--parent-step", default=None)
    sp_standby.set_defaults(func=cmd_standby)

    sp_wake = sub.add_parser(
        "wake",
        help="Post a wake fact for a standby event.",
    )
    _common(sp_wake)
    sp_wake.add_argument("--ref-standby", required=True)
    sp_wake.add_argument("--step", default=None)
    sp_wake.set_defaults(func=cmd_wake)

    sp_wake_due = sub.add_parser(
        "wake-due",
        help="Read due standby facts for this tool.",
    )
    sp_wake_due.add_argument("--workdir", default=".")
    sp_wake_due.add_argument("--tool", default="claude_code")
    sp_wake_due.add_argument("--session-id", default=None)
    sp_wake_due.add_argument("--json", action="store_true")
    sp_wake_due.set_defaults(func=cmd_wake_due)

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
