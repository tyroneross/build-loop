#!/usr/bin/env python3
# capability:
#   purpose: Host-neutral CLI for build-loop-memory hot context and evidence reads.
#   application: memory
#   status: experimental
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Host-neutral build-loop-memory CLI.

Primary surfaces:
  blm context --workdir "$PWD" --query "<goal>" --mode fast --json
  blm context --workdir "$PWD" --query "<goal>" --mode expand --json
  blm open --id <memory-id-or-artifact-id>
  blm status --workdir "$PWD" --json
  blm serve --host 127.0.0.1 --port 8777
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from memory_context import (  # type: ignore  # noqa: E402
    build_context,
    describe_access,
    open_artifact,
    render_current_markdown,
)

DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8777


def _emit_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, default=str))


def _cmd_context(args: argparse.Namespace) -> int:
    envelope = build_context(
        workdir=Path(args.workdir).resolve(),
        query=args.query,
        mode=args.mode,
        project=args.project,
        write=not args.no_write,
        limit=args.limit,
    )
    if args.json:
        _emit_json(envelope)
        return 0
    current = envelope["current"]
    print(render_current_markdown(current), end="")
    if args.mode == "expand":
        lessons = (envelope.get("expansion") or {}).get("lessons") or []
        if lessons:
            print("\n## Expanded Lessons\n")
            for item in lessons:
                print(f"- {item.get('name')} - {item.get('description')}")
    return 0


def _cmd_open(args: argparse.Namespace) -> int:
    result = open_artifact(
        args.id,
        workdir=Path(args.workdir).resolve(),
        project=args.project,
        max_chars=args.max_chars,
    )
    if args.json:
        _emit_json(result)
        return 0
    if not result.get("exists"):
        print(result.get("reason") or "not found", file=sys.stderr)
        return 1
    print(result.get("text", ""), end="")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    result = describe_access(
        workdir=Path(args.workdir).resolve(),
        project=args.project,
        host=args.host,
        port=args.port,
    )
    if args.json:
        _emit_json(result)
        return 0
    print(f"Project: {result['project']}")
    print(f"Memory root: {result['memory_root']}")
    print(f"Current JSON: {result['current_paths']['json']}")
    print(f"Current exists: {result['current_exists']['json']}")
    print(f"Fast CLI: {result['cli']['fast']}")
    print(f"Expand CLI: {result['cli']['expand']}")
    print(f"API: {result['api']['base_url']}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from blm_api import serve  # type: ignore  # noqa: PLC0415

    try:
        serve(host=args.host, port=args.port, allow_nonlocal=args.allow_nonlocal)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    context = sub.add_parser("context", help="Return fast or expanded project memory context.")
    context.add_argument("--workdir", default=os.getcwd())
    context.add_argument("--query", default="")
    context.add_argument("--project", default=None)
    context.add_argument("--mode", choices=("fast", "expand"), default="fast")
    context.add_argument("--limit", type=int, default=5)
    context.add_argument("--json", action="store_true", help="Print JSON envelope.")
    context.add_argument("--no-write", action="store_true", help="Do not persist CURRENT files.")
    context.set_defaults(func=_cmd_context)

    open_cmd = sub.add_parser("open", help="Read a memory evidence item by id or safe memory-store path.")
    open_cmd.add_argument("--id", required=True)
    open_cmd.add_argument("--workdir", default=os.getcwd())
    open_cmd.add_argument("--project", default=None)
    open_cmd.add_argument("--max-chars", type=int, default=8000)
    open_cmd.add_argument("--json", action="store_true", help="Print JSON envelope.")
    open_cmd.set_defaults(func=_cmd_open)

    status = sub.add_parser("status", help="Show fast-access paths, commands, and API endpoints.")
    status.add_argument("--workdir", default=os.getcwd())
    status.add_argument("--project", default=None)
    status.add_argument("--host", default=DEFAULT_API_HOST)
    status.add_argument("--port", type=int, default=DEFAULT_API_PORT)
    status.add_argument("--json", action="store_true", help="Print JSON envelope.")
    status.set_defaults(func=_cmd_status)

    serve_cmd = sub.add_parser("serve", help="Serve the optional local build-loop-memory HTTP API.")
    serve_cmd.add_argument("--host", default=os.environ.get("BLM_API_HOST", DEFAULT_API_HOST))
    serve_cmd.add_argument("--port", type=int, default=int(os.environ.get("BLM_API_PORT", str(DEFAULT_API_PORT))))
    serve_cmd.add_argument("--allow-nonlocal", action="store_true")
    serve_cmd.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
