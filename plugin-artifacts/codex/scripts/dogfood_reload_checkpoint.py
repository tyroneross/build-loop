#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Create and validate dogfood reload checkpoints for self-recursive build-loop work.
#   application: coordination
#   status: active
"""Dogfood reload checkpoint helper."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from atomic_io import LockedFile, atomic_write_bytes  # type: ignore  # noqa: E402

CHECKPOINT_DIR = Path(".build-loop") / "reload-checkpoints"
ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")

RUNTIME_SURFACE_PATTERNS: list[tuple[str, str]] = [
    (r"^skills/.+/SKILL\.md$", "skill"),
    (r"^skills/build-loop/", "skill-reference"),
    (r"^agents/.+\.md$", "agent"),
    (r"^commands/.+\.md$", "command"),
    (r"^hooks/", "hook"),
    (r"^\.claude-plugin/", "plugin-manifest"),
    (r"^\.codex-plugin/", "plugin-manifest"),
    (r"^\.mcp\.json$", "mcp"),
    (r"^scripts/rally_point/", "rally"),
    (r"^scripts/agent_rally\.py$", "rally"),
    (r"^scripts/coordination_", "rally"),
    (r"^scripts/dogfood_reload_checkpoint\.py$", "coordination"),
    (r"^scripts/context_bootstrap\.py$", "memory"),
    (r"^scripts/memory_", "memory"),
    (r"^scripts/memory_facade/", "memory"),
    (r"^scripts/recall", "memory"),
    (r"^scripts/research_trigger\.py$", "research"),
    (r"^scripts/detect_self_recursive\.py$", "self-recursive"),
    (r"^references/rally-point-protocol\.md$", "rally-reference"),
    (r"^references/memory-systems\.md$", "memory-reference"),
    (r"^references/research-trigger-policy\.md$", "research-reference"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_id(value: str) -> str:
    cleaned = ID_RE.sub("-", value.strip()).strip("-")
    return cleaned or "reload-checkpoint"


def checkpoint_path(workdir: Path, checkpoint_id: str) -> Path:
    return workdir / CHECKPOINT_DIR / f"{safe_id(checkpoint_id)}.json"


def current_branch(workdir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=workdir,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def current_commit(workdir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=workdir,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def detect_runtime_surfaces(files: list[str]) -> dict[str, Any]:
    matched: dict[str, set[str]] = {}
    for raw in files:
        path = raw.strip().lstrip("./")
        for pattern, surface in RUNTIME_SURFACE_PATTERNS:
            if re.search(pattern, path):
                matched.setdefault(surface, set()).add(path)
    surfaces = sorted(matched)
    return {
        "runtime_change_required": bool(surfaces),
        "surfaces": surfaces,
        "matched_files": {
            surface: sorted(paths) for surface, paths in sorted(matched.items())
        },
    }


def load_checkpoint(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    with LockedFile(path):
        atomic_write_bytes(
            path,
            (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )


def checkpoint_status(payload: dict[str, Any]) -> dict[str, Any]:
    expected = sorted(set(payload.get("expected_tools") or []))
    acked = sorted((payload.get("acks") or {}).keys())
    fallbacked = sorted((payload.get("fallbacks") or {}).keys())
    satisfied = set(acked) | set(fallbacked)
    missing = [tool for tool in expected if tool not in satisfied]
    return {
        "checkpoint_id": payload.get("checkpoint_id"),
        "ready": not missing,
        "expected_tools": expected,
        "acked_tools": acked,
        "fallback_tools": fallbacked,
        "missing_tools": missing,
        "runtime_surfaces": payload.get("runtime_surfaces") or [],
        "commit": payload.get("commit"),
        "branch": payload.get("branch"),
    }


def cmd_detect(args: argparse.Namespace) -> int:
    print(json.dumps(detect_runtime_surfaces(args.changed_file or []), indent=2, sort_keys=True))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    surfaces = sorted(set(args.surface or []))
    detection = detect_runtime_surfaces(args.changed_file or [])
    surfaces = sorted(set(surfaces) | set(detection["surfaces"]))
    checkpoint_id = safe_id(args.checkpoint_id or f"reload-{(args.commit or current_commit(workdir))[:8]}")
    payload = {
        "checkpoint_id": checkpoint_id,
        "created_at": utc_now(),
        "commit": args.commit or current_commit(workdir),
        "branch": args.branch or current_branch(workdir),
        "runtime_surfaces": surfaces,
        "changed_files": sorted(args.changed_file or []),
        "expected_tools": sorted(set(args.expect_tool or [])),
        "reload_instructions": args.instructions or "",
        "acks": {},
        "fallbacks": {},
        "status": "waiting_for_ack",
    }
    payload["status"] = "ready" if checkpoint_status(payload)["ready"] else "waiting_for_ack"
    path = checkpoint_path(workdir, checkpoint_id)
    save_checkpoint(path, payload)
    payload["path"] = str(path)
    payload["detection"] = detection
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    path = checkpoint_path(workdir, args.checkpoint_id)
    payload = load_checkpoint(path)
    tool = args.tool
    payload.setdefault("acks", {})[tool] = {
        "acked_at": utc_now(),
        "tool": tool,
        "session_id": args.session_id,
        "runtime_root": args.runtime_root,
        "runtime_commit": args.runtime_commit,
        "reload_method": args.reload_method,
        "hooks_agents_restarted": args.hooks_agents_restarted,
        "rally_next_status": args.rally_next_status,
    }
    payload["status"] = "ready" if checkpoint_status(payload)["ready"] else "waiting_for_ack"
    save_checkpoint(path, payload)
    result = checkpoint_status(payload)
    result["path"] = str(path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_fallback(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    path = checkpoint_path(workdir, args.checkpoint_id)
    payload = load_checkpoint(path)
    payload.setdefault("fallbacks", {})[args.tool] = {
        "recorded_at": utc_now(),
        "tool": args.tool,
        "decision": args.decision,
        "reason": args.reason,
    }
    payload["status"] = "ready" if checkpoint_status(payload)["ready"] else "waiting_for_ack"
    save_checkpoint(path, payload)
    result = checkpoint_status(payload)
    result["path"] = str(path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).expanduser().resolve()
    path = checkpoint_path(workdir, args.checkpoint_id)
    payload = load_checkpoint(path)
    result = checkpoint_status(payload)
    result["path"] = str(path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="Detect runtime-changing surfaces.")
    detect.add_argument("--changed-file", action="append", default=[])
    detect.set_defaults(func=cmd_detect)

    create = sub.add_parser("create", help="Create a reload checkpoint.")
    create.add_argument("--workdir", default=".")
    create.add_argument("--checkpoint-id")
    create.add_argument("--commit")
    create.add_argument("--branch")
    create.add_argument("--surface", action="append", default=[])
    create.add_argument("--changed-file", action="append", default=[])
    create.add_argument("--expect-tool", action="append", default=[])
    create.add_argument("--instructions", default="")
    create.set_defaults(func=cmd_create)

    ack = sub.add_parser("ack", help="Record a terminal reload ACK.")
    ack.add_argument("--workdir", default=".")
    ack.add_argument("--checkpoint-id", required=True)
    ack.add_argument("--tool", required=True)
    ack.add_argument("--session-id", required=True)
    ack.add_argument("--runtime-root", required=True)
    ack.add_argument("--runtime-commit", required=True)
    ack.add_argument("--reload-method", required=True)
    ack.add_argument("--rally-next-status", default="")
    ack.add_argument("--hooks-agents-restarted", action="store_true")
    ack.set_defaults(func=cmd_ack)

    fallback = sub.add_parser("fallback", help="Record missing-terminal fallback.")
    fallback.add_argument("--workdir", default=".")
    fallback.add_argument("--checkpoint-id", required=True)
    fallback.add_argument("--tool", required=True)
    fallback.add_argument(
        "--decision",
        required=True,
        choices=["reassign", "defer", "continue_solo"],
    )
    fallback.add_argument("--reason", required=True)
    fallback.set_defaults(func=cmd_fallback)

    status = sub.add_parser("status", help="Report checkpoint readiness.")
    status.add_argument("--workdir", default=".")
    status.add_argument("--checkpoint-id", required=True)
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"dogfood_reload_checkpoint: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"dogfood_reload_checkpoint: invalid checkpoint JSON: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
