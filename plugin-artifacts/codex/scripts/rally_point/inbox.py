#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Targeted Rally Point inbox helpers.

The shared ``changes.jsonl`` channel is the audit trail. The inbox is the
direct-addressed wake path: a peer can append a small JSONL message under
``inbox/<tool>.jsonl`` and a watcher can detect that without rereading the
whole channel.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

try:  # package import
    from . import actor_identity, channel_paths
    from .backend_adapter import (
        acknowledge as native_acknowledge,
        native_inbox_snapshot,
        recent as native_recent,
        resolve_context,
        room_snapshot,
    )
    from .build_loop_id import rally_fields_for
except ImportError:  # script import
    import actor_identity  # type: ignore
    import channel_paths  # type: ignore
    from backend_adapter import (  # type: ignore
        acknowledge as native_acknowledge,
        native_inbox_snapshot,
        recent as native_recent,
        resolve_context,
        room_snapshot,
    )
    from build_loop_id import rally_fields_for  # type: ignore

_INBOX_DIR = "inbox"
_ACK_DIR = ".acks"
_BROADCAST_TOOL = "all"
_GLOBAL_SESSION = "_global"
_SAFE_TOOL_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_tool(tool: str) -> str:
    cleaned = _SAFE_TOOL_RE.sub("_", (tool or "unknown").strip())
    cleaned = cleaned.strip("._")
    return cleaned or "unknown"


def inbox_path(channel_dir: Path, tool: str) -> Path:
    """Return the JSONL inbox path for ``tool`` under ``channel_dir``."""
    return Path(channel_dir) / _INBOX_DIR / f"{_safe_tool(tool)}.jsonl"


def ack_path(channel_dir: Path, tool: str, session_id: str | None = None) -> Path:
    """Return the ack cursor path for ``tool`` + optional session."""
    session = _safe_tool(session_id or _GLOBAL_SESSION)
    return Path(channel_dir) / _INBOX_DIR / _ACK_DIR / _safe_tool(tool) / f"{session}.json"


def make_message(
    *,
    sender: str,
    recipient: str,
    payload: dict[str, Any] | None = None,
    kind: str = "message",
    requires_ack: bool = False,
    message_id: str | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    """Build a stable direct-message record."""
    now = time.time() if ts is None else float(ts)
    return {
        "schema_version": "1.0",
        "id": message_id or f"{_safe_tool(sender)}-{int(now * 1000)}",
        "kind": kind or "message",
        "from": sender or "unknown",
        "to": recipient or "unknown",
        "requires_ack": bool(requires_ack),
        "ts": now,
        "payload": payload or {},
    }


def write_message(
    channel_dir: Path,
    *,
    sender: str,
    recipient: str,
    payload: dict[str, Any] | None = None,
    kind: str = "message",
    requires_ack: bool = False,
    message_id: str | None = None,
    workdir: Path | None = None,
) -> Path:
    """Append one direct message to ``recipient``'s inbox.

    Uses the same O_APPEND single-write pattern as ``changes.py``. Returns the
    recipient inbox path. Raises OSError/TypeError to callers that want a hard
    failure; higher-level fire-and-forget callers can catch and ignore.

    β1.2: when ``workdir`` is provided and the discovery bridge reports
    ``policy: "migration"`` with a populated ``legacy_channel_dir``
    distinct from ``channel_dir``, mirror-write the same record to the
    legacy inbox. The mirror is fire-and-forget — any failure is
    swallowed and never raises.
    """
    resolved_channel = Path(channel_dir).expanduser().resolve(strict=False)
    if ".rally" in resolved_channel.parts:
        raise ValueError("Build Loop inbox sidecars are forbidden inside .rally")
    path = inbox_path(resolved_channel, recipient)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = make_message(
        sender=sender,
        recipient=recipient,
        payload=payload,
        kind=kind,
        requires_ack=requires_ack,
        message_id=message_id,
    )
    # Top-level run-instance identity (orthogonal to producer_metadata).
    # Absent when workdir is None or no state.execution.build_loop_id —
    # the inbox write proceeds either way.
    rec.update(rally_fields_for(workdir))
    line = json.dumps(rec, separators=(",", ":")) + "\n"
    line_bytes = line.encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line_bytes)
    finally:
        os.close(fd)

    # β1.2: mirror to legacy inbox during migration. Fire-and-forget;
    # canonical write above already succeeded.
    if workdir is not None:
        try:
            try:  # package import
                from .discovery_bridge import resolve as _bridge_resolve
            except ImportError:  # script import
                from discovery_bridge import resolve as _bridge_resolve  # type: ignore

            envelope = _bridge_resolve(workdir)
            legacy = envelope.legacy_channel_dir
            if (
                envelope.policy == "migration"
                and legacy
                and str(Path(legacy).resolve()) != str(Path(channel_dir).resolve())
            ):
                legacy_path = inbox_path(Path(legacy), recipient)
                legacy_path.parent.mkdir(parents=True, exist_ok=True)
                lfd = os.open(
                    str(legacy_path),
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                    0o644,
                )
                try:
                    os.write(lfd, line_bytes)
                finally:
                    os.close(lfd)
        except Exception:
            pass

    return path


def _infer_app_slug(channel_dir: Path) -> str:
    try:
        return Path(channel_dir).resolve().relative_to(
            channel_paths.apps_root().resolve()
        ).as_posix()
    except (OSError, ValueError):
        return Path(channel_dir).name


def send_to_tool(
    channel_dir: Path,
    *,
    sender: str,
    recipient: str,
    payload: dict[str, Any] | None = None,
    kind: str = "message",
    requires_ack: bool = False,
    message_id: str | None = None,
    model: str = "unknown",
    run_id: str = "unknown",
    app_slug: str | None = None,
    mirror_to_channel: bool = True,
    workdir: Path | None = None,
    local_sender: str | None = None,
    local_recipient: str | None = None,
) -> dict[str, Any]:
    """Write a targeted inbox message and optionally mirror it to changes.jsonl.

    The inbox write is the wake path; the channel mirror is the durable audit
    path all agents already poll. Each append is atomic, but the pair is best
    effort rather than a cross-file transaction.

    β1.2: when ``workdir`` is provided, both the direct-inbox write and the
    changes.jsonl mirror dual-write to the legacy channel during migration
    policy. Fire-and-forget; the dual-write never blocks the canonical
    operation.
    """
    context = resolve_context(workdir) if workdir is not None else None
    actual_backend = context.envelope.backend if context is not None else None
    if context is not None and context.envelope.coordination_unavailable:
        return {
            "inbox": None,
            "written": False,
            "channel_revision": None,
            "backend": "unavailable",
            "reason": context.envelope.coordination_unavailable,
        }
    if context is not None and context.native:
        try:
            try:
                from .post import post
            except ImportError:
                from post import post  # type: ignore
            native_identity = actor_identity.resolve_identity(
                sender,
                (payload or {}).get("session_id"),
            )
            fallback_sender = local_sender or native_identity.base_tool
            fallback_recipient = (
                _BROADCAST_TOOL
                if recipient == _BROADCAST_TOOL
                else local_recipient or actor_identity.base_tool(recipient)
            )
            local_session_id = str(
                (payload or {}).get("session_id") or native_identity.session_id
            )
            event_payload: dict[str, Any] = {
                "subject": str((payload or {}).get("subject") or kind or "message"),
                "from": native_identity.base_tool,
                "to": recipient,
                "host_tool": native_identity.base_tool,
                "session_id": native_identity.session_id,
                "requires_ack": bool(requires_ack),
                "message_id": message_id,
                "payload": payload or {},
            }
            native_kind = "handoff" if requires_ack else "message"
            if requires_ack:
                event_payload["ownership"] = {
                    "owns": [],
                    "does_not_own": ["message-only:no-file-ownership"],
                    "allowed_tools": [],
                    "denied_tools": [],
                    "interface_contract": "acknowledge or respond to this targeted message",
                    "integration_checkpoint": "sender observes the Rally read checkpoint or reply",
                }
            outcome: dict[str, Any] = {}
            channel_revision = post(
                channel_dir=Path(context.envelope.channel_dir),
                kind=native_kind,
                tool=native_identity.native_tool,
                model=model,
                run_id=run_id,
                app_slug=context.envelope.app_slug,
                payload=event_payload,
                workdir=context.workdir,
                outcome=outcome,
                local_tool=fallback_sender,
                local_session_id=local_session_id,
            )
            if channel_revision is not None and outcome.get("backend") == "rally":
                return {
                    "inbox": None,
                    "written": True,
                    "channel_revision": channel_revision,
                    "backend": "rally",
                }
            if channel_revision is None or outcome.get("backend") != "build-loop-local":
                return {
                    "inbox": None,
                    "written": False,
                    "channel_revision": channel_revision,
                    "backend": outcome.get("backend") or "rally",
                    "status": outcome.get("status"),
                    "reason": outcome.get("reason"),
                    "event_id": outcome.get("event_id"),
                    "remedy": outcome.get("remedy"),
                }
            # Proven pre-spawn failover: preserve the local inbox wake path;
            # the post above already wrote the fallback audit fact.
            channel_dir = context.local_channel_dir
            actual_backend = "build-loop-local"
            payload = {
                **(payload or {}),
                "rally_from_tool": native_identity.native_tool,
                "rally_to_tool": recipient,
            }
            sender = fallback_sender
            recipient = fallback_recipient
            mirror_to_channel = False
        except Exception as exc:
            return {
                "inbox": None,
                "written": False,
                "channel_revision": None,
                "backend": "rally",
                "reason": str(exc),
            }
    elif context is not None:
        channel_dir = context.local_channel_dir
        actual_backend = "build-loop-local"

    path = write_message(
        Path(channel_dir),
        sender=sender,
        recipient=recipient,
        payload=payload,
        kind=kind,
        requires_ack=requires_ack,
        message_id=message_id,
        workdir=workdir,
    )
    channel_revision: int | None = None
    if mirror_to_channel:
        try:
            try:
                from .post import post
            except ImportError:
                scripts_dir = Path(__file__).resolve().parent.parent
                if str(scripts_dir) not in sys.path:
                    sys.path.insert(0, str(scripts_dir))
                from rally_point.post import post

            channel_revision = post(
                channel_dir=Path(channel_dir),
                kind="message",
                tool=sender,
                model=model,
                run_id=run_id,
                app_slug=app_slug or _infer_app_slug(Path(channel_dir)),
                payload={
                    "from": sender,
                    "to": recipient,
                    "requires_ack": bool(requires_ack),
                    "inbox": str(path),
                    "message_id": message_id,
                    "payload": payload or {},
                },
                workdir=workdir,
            )
        except Exception:
            channel_revision = None
    result = {
        "inbox": str(path),
        "written": True,
        "channel_revision": channel_revision,
    }
    if actual_backend is not None:
        result["backend"] = actual_backend
    return result


def send_to_all(
    channel_dir: Path,
    *,
    sender: str,
    payload: dict[str, Any] | None = None,
    kind: str = "message",
    requires_ack: bool = False,
    message_id: str | None = None,
    model: str = "unknown",
    run_id: str = "unknown",
    app_slug: str | None = None,
    mirror_to_channel: bool = True,
    workdir: Path | None = None,
    local_sender: str | None = None,
) -> dict[str, Any]:
    """Write one common broadcast message to ``inbox/all.jsonl``."""
    return send_to_tool(
        channel_dir,
        sender=sender,
        recipient=_BROADCAST_TOOL,
        payload=payload,
        kind=kind,
        requires_ack=requires_ack,
        message_id=message_id,
        model=model,
        run_id=run_id,
        app_slug=app_slug,
        mirror_to_channel=mirror_to_channel,
        workdir=workdir,
        local_sender=local_sender,
    )


def read_messages(
    channel_dir: Path,
    *,
    tool: str,
    limit: int | None = None,
    include_broadcast: bool = True,
    unread_only: bool = False,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read direct messages for ``tool``.

    Corrupt/blank lines are ignored. ``limit`` returns the most recent N
    messages; omitted means all currently stored messages. By default, reads
    include ``inbox/all.jsonl`` after the direct inbox so every agent can
    consume common broadcast messages through the same call.
    """
    messages: list[dict[str, Any]] = []
    cursor = read_ack_cursor(channel_dir, tool=tool, session_id=session_id) if unread_only else {}
    paths: list[tuple[str, Path]] = [("direct", inbox_path(Path(channel_dir), tool))]
    if include_broadcast and _safe_tool(tool) != _BROADCAST_TOOL:
        paths.append(("broadcast", inbox_path(Path(channel_dir), _BROADCAST_TOOL)))
    for source, path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        acked_lines = _acked_line_count(cursor, source) if unread_only else 0
        for index, line in enumerate(lines, start=1):
            if index <= acked_lines:
                continue
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                messages.append(rec)
    if limit is not None and limit >= 0:
        return messages[-limit:]
    return messages


def read_tool(
    channel_dir: Path,
    *,
    tool: str,
    limit: int | None = None,
    include_broadcast: bool = True,
) -> list[dict[str, Any]]:
    """Alias for callers that want the C9 verb-style API."""
    return read_messages(
        channel_dir,
        tool=tool,
        limit=limit,
        include_broadcast=include_broadcast,
    )


def _line_count(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _acked_line_count(cursor: dict[str, Any], source: str) -> int:
    bucket = cursor.get(source)
    if not isinstance(bucket, dict):
        return 0
    try:
        return max(0, int(bucket.get("line_count", 0)))
    except (TypeError, ValueError):
        return 0


def _last_message_ts(path: Path) -> float | None:
    try:
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        try:
            return float(rec.get("ts"))
        except (TypeError, ValueError):
            return None
    return None


def read_ack_cursor(
    channel_dir: Path,
    *,
    tool: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Read this tool/session's inbox ack cursor, if present."""
    try:
        data = json.loads(
            ack_path(channel_dir, tool, session_id).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def mark_read(
    channel_dir: Path,
    *,
    tool: str,
    session_id: str | None = None,
    include_broadcast: bool = True,
) -> dict[str, Any]:
    """Advance this tool/session's ack cursor to the current inbox tail.

    The inbox remains append-only. Ack state is a separate cursor file, so
    marking messages seen never rewrites peer-authored messages and never loses
    audit history.
    """
    channel = Path(channel_dir)
    direct = inbox_path(channel, tool)
    broadcast = inbox_path(channel, _BROADCAST_TOOL)
    now = time.time()
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "rally-inbox-ack-cursor",
        "tool": _safe_tool(tool),
        "session_id": session_id or _GLOBAL_SESSION,
        "acked_at": now,
        "direct": {
            "line_count": _line_count(direct),
            "last_message_ts": _last_message_ts(direct),
        },
    }
    if include_broadcast and _safe_tool(tool) != _BROADCAST_TOOL:
        payload["broadcast"] = {
            "line_count": _line_count(broadcast),
            "last_message_ts": _last_message_ts(broadcast),
        }
    target = ack_path(channel, tool, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(target)
    return {
        "action": "inbox-ack-written",
        "ack_path": str(target),
        "tool": _safe_tool(tool),
        "session_id": session_id or _GLOBAL_SESSION,
        "direct_line_count": payload["direct"]["line_count"],
        "broadcast_line_count": (
            payload.get("broadcast", {}).get("line_count", 0)
            if isinstance(payload.get("broadcast"), dict)
            else 0
        ),
    }


def direct_unread_count(
    channel_dir: Path,
    tool: str,
    *,
    session_id: str | None = None,
) -> int:
    """Return current unread line count for ``tool``'s direct inbox."""
    current = _line_count(inbox_path(Path(channel_dir), tool))
    cursor = read_ack_cursor(channel_dir, tool=tool, session_id=session_id)
    return max(0, current - min(current, _acked_line_count(cursor, "direct")))


def broadcast_unread_count(channel_dir: Path) -> int:
    """Return current unread line count for the common broadcast inbox."""
    return _line_count(inbox_path(Path(channel_dir), _BROADCAST_TOOL))


def unread_count(
    channel_dir: Path,
    tool: str,
    *,
    include_broadcast: bool = True,
    session_id: str | None = None,
) -> int:
    """Return current unread line count for ``tool`` plus optional broadcast.

    The raw inbox remains append-only; a separate per-session ack cursor marks
    how many direct/broadcast lines this agent has already acted on.
    """
    count = direct_unread_count(channel_dir, tool, session_id=session_id)
    if include_broadcast and _safe_tool(tool) != _BROADCAST_TOOL:
        current = broadcast_unread_count(channel_dir)
        cursor = read_ack_cursor(channel_dir, tool=tool, session_id=session_id)
        count += max(0, current - min(current, _acked_line_count(cursor, "broadcast")))
    return count


def unread_counts(
    channel_dir: Path,
    tool: str,
    *,
    include_broadcast: bool = True,
    session_id: str | None = None,
) -> dict[str, int]:
    """Return direct/broadcast/total unread line counts for ``tool``."""
    direct = direct_unread_count(channel_dir, tool, session_id=session_id)
    broadcast = 0
    if include_broadcast and _safe_tool(tool) != _BROADCAST_TOOL:
        current = broadcast_unread_count(channel_dir)
        cursor = read_ack_cursor(channel_dir, tool=tool, session_id=session_id)
        broadcast = max(0, current - min(current, _acked_line_count(cursor, "broadcast")))
    return {
        "direct": direct,
        "broadcast": broadcast,
        "total": direct + broadcast,
    }


_PREVIEW_PAYLOAD_KEYS = ("subject", "summary", "message", "text", "reason")


def _compact_str(value: Any, *, max_chars: int = 240) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
    else:
        return None
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def summarize_message(record: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Return a compact, prompt-safe inbox message summary.

    Inbox payloads are peer-authored free text. The raw JSONL line remains the
    durable source; status/watch surfaces get only routing metadata, payload
    keys, and one short preview from known fields.
    """
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    out: dict[str, Any] = {
        "source": source,
        "id": str(record.get("id") or ""),
        "kind": str(record.get("kind") or "message"),
        "from": str(record.get("from") or "unknown"),
        "to": str(record.get("to") or "unknown"),
        "requires_ack": bool(record.get("requires_ack")),
    }
    if "ts" in record:
        out["ts"] = record.get("ts")
    if isinstance(payload, dict) and payload:
        keys = sorted(str(k) for k in payload.keys())
        out["payload_keys"] = keys[:12]
        for key in _PREVIEW_PAYLOAD_KEYS:
            preview = _compact_str(payload.get(key))
            if preview:
                out["preview"] = preview
                out["preview_key"] = key
                break
    if "preview" not in out:
        for key in _PREVIEW_PAYLOAD_KEYS:
            preview = _compact_str(record.get(key))
            if preview:
                out["preview"] = preview
                out["preview_key"] = key
                break
    return out


def latest_message_summaries(
    channel_dir: Path,
    *,
    tool: str,
    limit: int = 3,
    include_broadcast: bool = True,
    unread_only: bool = False,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return newest inbox records as compact doorbell summaries.

    This is deliberately separate from ``unread_count``. Counts preserve the
    original raw-line wake contract; summaries make a changed mailbox
    actionable without requiring agents to manually inspect inbox files first.
    """
    if limit <= 0:
        return []
    cursor = read_ack_cursor(channel_dir, tool=tool, session_id=session_id) if unread_only else {}
    paths: list[tuple[str, Path]] = [
        ("direct", inbox_path(Path(channel_dir), tool)),
    ]
    if include_broadcast and _safe_tool(tool) != _BROADCAST_TOOL:
        paths.append(("broadcast", inbox_path(Path(channel_dir), _BROADCAST_TOOL)))

    rows: list[tuple[float, int, str, dict[str, Any]]] = []
    order = 0
    for source, path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        acked_lines = _acked_line_count(cursor, source) if unread_only else 0
        for line_index, line in enumerate(lines, start=1):
            if line_index <= acked_lines:
                continue
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            try:
                ts = float(rec.get("ts", 0.0))
            except (TypeError, ValueError):
                ts = 0.0
            rows.append((ts, order, source, rec))
            order += 1
    rows.sort(key=lambda item: (item[0], item[1]))
    return [
        summarize_message(rec, source=source)
        for _ts, _order, source, rec in rows[-limit:]
    ]


def _channel_from_workdir(workdir: str, *, create: bool) -> tuple[str, Path]:
    """β1: resolve via the shared discovery bridge so inbox CLI writes
    reach the canonical channel when ``agent-rally-point`` is installed.
    """
    try:  # package import
        from .discovery_bridge import resolve as _bridge_resolve
    except ImportError:  # script import
        from discovery_bridge import resolve as _bridge_resolve  # type: ignore
    wd = Path(workdir).expanduser().resolve()
    envelope = _bridge_resolve(wd)
    channel_dir = Path(envelope.channel_dir)
    if create and envelope.resolved_via == "build-loop-internal":
        channel_dir.mkdir(parents=True, exist_ok=True)
    return envelope.app_slug, channel_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workdir", default=".")
    common.add_argument("--tool", required=True)
    common.add_argument("--session-id", default=None)
    common.add_argument("--json", action="store_true")

    read = sub.add_parser("read", parents=[common], help="Read recent inbox messages")
    read.add_argument("--limit", type=int, default=20)

    unread = sub.add_parser("unread", parents=[common], help="Count unread inbox lines")

    ack = sub.add_parser("ack", parents=[common], help="Mark current inbox messages seen")
    ack.add_argument("--no-broadcast", action="store_true")

    write = sub.add_parser("write", help="Append one inbox message")
    write.add_argument("--workdir", default=".")
    write.add_argument("--from-tool", required=True)
    write.add_argument("--to-tool", required=True)
    write.add_argument("--local-from-tool", default=None)
    write.add_argument("--local-to-tool", default=None)
    write.add_argument("--payload-json", default="{}")
    write.add_argument("--kind", default="message")
    write.add_argument("--requires-ack", action="store_true")
    write.add_argument("--model", default="unknown")
    write.add_argument("--run-id", default="unknown")
    write.add_argument("--no-channel", action="store_true")
    write.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve()
    context = resolve_context(workdir)
    slug = context.envelope.app_slug
    if args.command == "write":
        try:
            payload = json.loads(args.payload_json)
        except json.JSONDecodeError:
            payload = {"message": args.payload_json}
        channel_dir = (
            Path(context.envelope.channel_dir)
            if context.native else context.local_channel_dir
        )
        result = send_to_tool(
            channel_dir,
            sender=args.from_tool,
            recipient=args.to_tool,
            payload=payload if isinstance(payload, dict) else {"value": payload},
            kind=args.kind,
            requires_ack=args.requires_ack,
            model=args.model,
            run_id=args.run_id,
            app_slug=slug,
            mirror_to_channel=not args.no_channel,
            workdir=workdir,
            local_sender=args.local_from_tool,
            local_recipient=args.local_to_tool,
        )
        out = {"app_slug": slug, **result}
    elif args.command == "read":
        if context.native:
            native_identity = actor_identity.resolve_identity(
                args.tool, args.session_id
            )
            snapshot = room_snapshot(
                context,
                tool=native_identity.native_tool,
                actor=native_identity.native_tool,
                readers=True,
            )
            projected = native_inbox_snapshot(
                snapshot,
                tool=native_identity.native_tool,
                recent_result=native_recent(context, limit=500),
            )
            out = {
                "app_slug": slug,
                "tool": args.tool,
                "rally_tool": native_identity.native_tool,
                "rally_session_id": native_identity.session_id,
                "messages": projected["latest"][-args.limit:],
                "backend": "rally",
                "accepted": snapshot.ok,
                "reason": snapshot.reason,
                "coverage_incomplete": projected["coverage_incomplete"],
                "coverage": projected["coverage"],
            }
        else:
            out = {
                "app_slug": slug,
                "tool": args.tool,
                "messages": read_messages(
                    context.local_channel_dir,
                    tool=args.tool,
                    limit=args.limit,
                ),
            }
    elif args.command == "ack":
        if context.native:
            if args.no_broadcast:
                out = {
                    "app_slug": slug,
                    "action": "inbox-ack-refused",
                    "accepted": False,
                    "backend": "rally",
                    "reason": (
                        "native Rally checkpoints direct and broadcast messages together; "
                        "selective --no-broadcast acknowledgement is unsupported"
                    ),
                }
                print(json.dumps(out, sort_keys=True))
                return 2
            native_identity = actor_identity.resolve_identity(
                args.tool, args.session_id
            )
            result = native_acknowledge(
                context,
                tool=native_identity.native_tool,
                session_id=native_identity.session_id,
            )
            out = {
                "app_slug": slug,
                "action": "inbox-acknowledged" if result.ok else "inbox-ack-failed",
                "accepted": result.ok,
                "backend": "rally",
                "tool": args.tool,
                "rally_tool": native_identity.native_tool,
                "rally_session_id": native_identity.session_id,
                "channel_revision": result.revision,
                "reason": result.reason,
            }
        else:
            out = {"app_slug": slug, **mark_read(
                context.local_channel_dir,
                tool=args.tool,
                session_id=args.session_id,
                include_broadcast=not args.no_broadcast,
            )}
    else:
        if context.native:
            native_identity = actor_identity.resolve_identity(
                args.tool, args.session_id
            )
            snapshot = room_snapshot(
                context,
                tool=native_identity.native_tool,
                actor=native_identity.native_tool,
                readers=True,
            )
            projected = native_inbox_snapshot(
                snapshot,
                tool=native_identity.native_tool,
                recent_result=native_recent(context, limit=500),
            )
            counts = projected["counts"]
            out = {
                "app_slug": slug,
                "tool": args.tool,
                "rally_tool": native_identity.native_tool,
                "rally_session_id": native_identity.session_id,
                "direct_unread_count": counts["direct"],
                "broadcast_unread_count": counts["broadcast"],
                "unread_count": counts["total"],
                "backend": "rally",
                "accepted": snapshot.ok,
                "reason": snapshot.reason,
                "coverage_incomplete": projected["coverage_incomplete"],
                "coverage": projected["coverage"],
            }
        else:
            channel_dir = context.local_channel_dir
            out = {
                "app_slug": slug,
                "tool": args.tool,
                "direct_unread_count": direct_unread_count(
                    channel_dir,
                    args.tool,
                    session_id=args.session_id,
                ),
                "broadcast_unread_count": (
                    0
                    if _safe_tool(args.tool) == _BROADCAST_TOOL
                    else unread_counts(
                        channel_dir,
                        args.tool,
                        session_id=args.session_id,
                    )["broadcast"]
                ),
                "unread_count": unread_count(
                    channel_dir,
                    args.tool,
                    session_id=args.session_id,
                ),
            }

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
