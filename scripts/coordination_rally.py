#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Publish lightweight Rally Point presence + handoff without a durable coord file.
#   application: coordination
#   status: active
"""Publish a lightweight Rally Point rally point without creating a coord file.

Use this when an agent needs to become visible to peers on the current app's
canonical Rally Point channel, but the work does not yet warrant a durable
``.build-loop/coordination/*.md`` ledger. It writes:

1. Presence with ``phase=rally-point`` (or the supplied phase).
2. A ``kind=handoff`` record with a MECE ownership packet.

The script is intentionally smaller than ``coordination_bootstrap.py``:
bootstrap owns durable run files; rally owns "I am here, here is what I do and
do not own" signaling.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from rally_point import changes, channel_paths, presence, revision  # noqa: E402
from rally_point.discovery_bridge import resolve as _bridge_resolve  # noqa: E402
from rally_point.post import post  # noqa: E402


def _timestamp_id(now: float | None = None) -> str:
    t = time.gmtime(now) if now is not None else time.gmtime()
    return time.strftime("%Y%m%d-%H%M%S", t)


def _split_csv(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        for item in value.split(","):
            item = item.strip()
            if item:
                out.append(item)
    return out


def rally(
    *,
    workdir: Path,
    session_id: str,
    message: str,
    tool: str = "codex",
    model: str = "gpt-5",
    run_id: str | None = None,
    phase: str = "rally-point",
    to: str = "peer",
    owns: list[str] | None = None,
    does_not_own: list[str] | None = None,
    interface_contract: str | None = None,
    integration_checkpoint: str | None = None,
    verify: bool = False,
) -> dict[str, Any]:
    """Write presence + handoff and return the visible channel envelope."""
    workdir = Path(workdir).expanduser().resolve()
    # β1: resolve via the shared discovery bridge. When the canonical
    # source is unreachable the bridge returns the internal-fallback
    # channel and we still need to ensure it exists on first use.
    envelope = _bridge_resolve(workdir)
    slug = envelope.app_slug
    channel_dir = Path(envelope.channel_dir)
    if envelope.resolved_via == "build-loop-internal":
        channel_dir.mkdir(parents=True, exist_ok=True)
    owns = list(owns or [])
    does_not_own = list(does_not_own or [])
    effective_run_id = run_id or f"rally-{session_id}"
    contract = interface_contract or (
        f"{tool} is publishing coordination presence only; ownership is "
        "defined by the owns / does_not_own packet."
    )
    checkpoint = integration_checkpoint or (
        "Peer should rerun coordination_status.py for this workdir and confirm "
        "this active presence plus handoff revision are visible."
    )

    presence_written = False
    errors: list[str] = []
    try:
        presence.write_presence(
            channel_dir,
            session_id=session_id,
            tool=tool,
            model=model,
            run_id=effective_run_id,
            app_slug=slug,
            phase=phase,
            files_in_flight=owns,
            cwd=workdir,
        )
        presence_written = True
    except Exception as exc:  # noqa: BLE001 - presence is fire-and-forget
        errors.append(f"presence.write_presence failed: {exc}")

    payload = {
        "from": tool,
        "to": to,
        "step": "agent-rally-point",
        "verdict": "INFO",
        "summary": message,
        "coord_file": None,
        "action": "rally-point",
        "ownership": {
            "owns": owns,
            "does_not_own": does_not_own,
            "interface_contract": contract,
            "integration_checkpoint": checkpoint,
            # Rally is a presence broadcast, not a delegation: lateral
            # limits default to explicit empty boundaries. Callers that
            # want a true delegation should use coordination_bootstrap.
            "allowed_tools": [],
            "denied_tools": [],
        },
    }
    before_revision = revision.read_revision(channel_dir) if verify else None
    channel_rev = post(
        channel_dir=channel_dir,
        kind="handoff",
        tool=tool,
        model=model,
        run_id=effective_run_id,
        app_slug=slug,
        payload=payload,
        workdir=workdir,
    )
    after_revision = revision.read_revision(channel_dir) if verify else None

    verify_result: dict[str, Any] | None = None
    if verify:
        records, _offset = changes.read_changes_since(channel_dir, 0)
        matching_records = [
            r for r in records
            if r.get("revision") == channel_rev
            and r.get("kind") == "handoff"
            and r.get("run_id") == effective_run_id
            and (r.get("payload") or {}).get("action") == "rally-point"
        ]
        verify_result = {
            "posted": bool(
                channel_rev is not None
                and before_revision is not None
                and after_revision is not None
                and after_revision > before_revision
                and matching_records
            ),
            "before_revision": before_revision,
            "after_revision": after_revision,
            "matching_record_count": len(matching_records),
        }

    result = {
        "schema_version": "1.0",
        "action": "rally-point-posted",
        "workdir": str(workdir),
        "app_slug": slug,
        "channel_dir": str(channel_dir),
        "channel_revision": channel_rev,
        "session_id": session_id,
        "run_id": effective_run_id,
        "phase": phase,
        "presence_written": presence_written,
        "ownership": payload["ownership"],
        "errors": errors,
    }
    if verify_result is not None:
        result["verify"] = verify_result
        result["posted"] = verify_result["posted"]
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--session-id", default=None)
    p.add_argument("--message", default="Agent is present and ready to coordinate.")
    p.add_argument("--tool", default="codex")
    p.add_argument("--model", default="gpt-5")
    p.add_argument("--run-id", default=None)
    p.add_argument("--phase", default="rally-point")
    p.add_argument("--to", default="peer")
    p.add_argument("--owns", action="append", default=[], help="Owned file/path. Repeat or comma-separate.")
    p.add_argument("--does-not-own", action="append", default=[], help="Non-owned file/path. Repeat or comma-separate.")
    p.add_argument("--interface-contract", default=None)
    p.add_argument("--integration-checkpoint", default=None)
    p.add_argument("--verify", action="store_true", help="Read back the channel and confirm the rally post landed")
    p.add_argument("--json", action="store_true", help="Emit JSON envelope (always JSON; flag is for explicitness)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session_id = args.session_id or f"{args.tool}-rally-{_timestamp_id()}"
    result = rally(
        workdir=Path(args.workdir),
        session_id=session_id,
        message=args.message,
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
        phase=args.phase,
        to=args.to,
        owns=_split_csv(args.owns),
        does_not_own=_split_csv(args.does_not_own),
        interface_contract=args.interface_contract,
        integration_checkpoint=args.integration_checkpoint,
        verify=args.verify,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
