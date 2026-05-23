#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross
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
    from . import channel_paths
except ImportError:  # script import
    import channel_paths  # type: ignore

_INBOX_DIR = "inbox"
_BROADCAST_TOOL = "all"
_SAFE_TOOL_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_tool(tool: str) -> str:
    cleaned = _SAFE_TOOL_RE.sub("_", (tool or "unknown").strip())
    cleaned = cleaned.strip("._")
    return cleaned or "unknown"


def inbox_path(channel_dir: Path, tool: str) -> Path:
    """Return the JSONL inbox path for ``tool`` under ``channel_dir``."""
    return Path(channel_dir) / _INBOX_DIR / f"{_safe_tool(tool)}.jsonl"


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
) -> Path:
    """Append one direct message to ``recipient``'s inbox.

    Uses the same O_APPEND single-write pattern as ``changes.py``. Returns the
    recipient inbox path. Raises OSError/TypeError to callers that want a hard
    failure; higher-level fire-and-forget callers can catch and ignore.
    """
    path = inbox_path(Path(channel_dir), recipient)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = make_message(
        sender=sender,
        recipient=recipient,
        payload=payload,
        kind=kind,
        requires_ack=requires_ack,
        message_id=message_id,
    )
    line = json.dumps(rec, separators=(",", ":")) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
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
) -> dict[str, Any]:
    """Write a targeted inbox message and optionally mirror it to changes.jsonl.

    The inbox write is the wake path; the channel mirror is the durable audit
    path all agents already poll. Each append is atomic, but the pair is best
    effort rather than a cross-file transaction.
    """
    path = write_message(
        Path(channel_dir),
        sender=sender,
        recipient=recipient,
        payload=payload,
        kind=kind,
        requires_ack=requires_ack,
        message_id=message_id,
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
            )
        except Exception:
            channel_revision = None
    return {
        "inbox": str(path),
        "written": True,
        "channel_revision": channel_revision,
    }


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
    )


def read_messages(
    channel_dir: Path,
    *,
    tool: str,
    limit: int | None = None,
    include_broadcast: bool = True,
) -> list[dict[str, Any]]:
    """Read direct messages for ``tool``.

    Corrupt/blank lines are ignored. ``limit`` returns the most recent N
    messages; omitted means all currently stored messages. By default, reads
    include ``inbox/all.jsonl`` after the direct inbox so every agent can
    consume common broadcast messages through the same call.
    """
    messages: list[dict[str, Any]] = []
    paths = [inbox_path(Path(channel_dir), tool)]
    if include_broadcast and _safe_tool(tool) != _BROADCAST_TOOL:
        paths.append(inbox_path(Path(channel_dir), _BROADCAST_TOOL))
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
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


def direct_unread_count(channel_dir: Path, tool: str) -> int:
    """Return current unread line count for ``tool``'s direct inbox."""
    return _line_count(inbox_path(Path(channel_dir), tool))


def broadcast_unread_count(channel_dir: Path) -> int:
    """Return current unread line count for the common broadcast inbox."""
    return _line_count(inbox_path(Path(channel_dir), _BROADCAST_TOOL))


def unread_count(channel_dir: Path, tool: str, *, include_broadcast: bool = True) -> int:
    """Return current unread line count for ``tool`` plus optional broadcast.

    R1 deliberately uses a simple line count because the first requirement is
    wakeup visibility. Per-session ack cursors can layer on later without
    changing the file path or watcher field.
    """
    count = direct_unread_count(channel_dir, tool)
    if include_broadcast and _safe_tool(tool) != _BROADCAST_TOOL:
        count += broadcast_unread_count(channel_dir)
    return count


def unread_counts(
    channel_dir: Path,
    tool: str,
    *,
    include_broadcast: bool = True,
) -> dict[str, int]:
    """Return direct/broadcast/total unread line counts for ``tool``."""
    direct = direct_unread_count(channel_dir, tool)
    broadcast = 0
    if include_broadcast and _safe_tool(tool) != _BROADCAST_TOOL:
        broadcast = broadcast_unread_count(channel_dir)
    return {
        "direct": direct,
        "broadcast": broadcast,
        "total": direct + broadcast,
    }


def _channel_from_workdir(workdir: str, *, create: bool) -> tuple[str, Path]:
    wd = Path(workdir).expanduser().resolve()
    slug = channel_paths.app_slug(wd)
    if create:
        return slug, channel_paths.ensure_channel_dir(slug)
    return slug, channel_paths.app_channel_dir(slug)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workdir", default=".")
    common.add_argument("--tool", required=True)
    common.add_argument("--json", action="store_true")

    read = sub.add_parser("read", parents=[common], help="Read recent inbox messages")
    read.add_argument("--limit", type=int, default=20)

    unread = sub.add_parser("unread", parents=[common], help="Count unread inbox lines")

    write = sub.add_parser("write", help="Append one inbox message")
    write.add_argument("--workdir", default=".")
    write.add_argument("--from-tool", required=True)
    write.add_argument("--to-tool", required=True)
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
    if args.command == "write":
        try:
            payload = json.loads(args.payload_json)
        except json.JSONDecodeError:
            payload = {"message": args.payload_json}
        slug, channel_dir = _channel_from_workdir(args.workdir, create=True)
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
        )
        out = {"app_slug": slug, **result}
    elif args.command == "read":
        slug, channel_dir = _channel_from_workdir(args.workdir, create=False)
        out = {
            "app_slug": slug,
            "tool": args.tool,
            "messages": read_messages(channel_dir, tool=args.tool, limit=args.limit),
        }
    else:
        slug, channel_dir = _channel_from_workdir(args.workdir, create=False)
        out = {
            "app_slug": slug,
            "tool": args.tool,
            "direct_unread_count": direct_unread_count(channel_dir, args.tool),
            "broadcast_unread_count": (
                0
                if _safe_tool(args.tool) == _BROADCAST_TOOL
                else broadcast_unread_count(channel_dir)
            ),
            "unread_count": unread_count(channel_dir, args.tool),
        }

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
