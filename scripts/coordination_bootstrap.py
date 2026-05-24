#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# capability:
#   purpose: Bootstrap a new per-run coordination file from the canonical template.
#   application: coordination
#   status: active
"""Bootstrap a new per-run coordination file from the canonical template.

Reads ``references/coordination-file-template.md``, substitutes the
``{{PLACEHOLDER}}`` tokens, writes the result to
``.build-loop/coordination/<topic>.md``, writes the bootstrapping session's
Rally Point presence, and posts a ``kind=handoff`` channel record so peers
notice.

Idempotent: if the target coord file already exists, this script does NOT
overwrite. Instead it joins the existing coordination — writes presence,
posts a ``kind=phase`` record with ``phase=joined-existing-coord``, returns
the existing coord file path (or a structured JSON envelope when ``--json``
is passed).

Fire-and-forget on channel write errors — per the Rally Point contract,
coordination signals never block a caller. Returns exit 0 on success,
exit 1 only on hard errors (template missing, IO refused).

CLI:

    python3 scripts/coordination_bootstrap.py \\
        --workdir <path> \\
        --topic <run-slug> \\
        --scope <one-liner-scope> \\
        --session-id <id> \\
        [--coord-file <explicit-path>] \\
        [--template <explicit-template-path>] \\
        [--tool <tool-name>] [--model <model-id>] [--run-id <run-id>] \\
        [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from rally_point import channel_paths, presence  # noqa: E402
from rally_point.discovery_bridge import resolve as _bridge_resolve  # noqa: E402
from rally_point.post import post  # noqa: E402


DEFAULT_TEMPLATE_REL = Path("references") / "coordination-file-template.md"


def _today_iso(now: float | None = None) -> str:
    t = time.gmtime(now) if now is not None else time.gmtime()
    return time.strftime("%Y-%m-%d", t)


def _coord_dir(workdir: Path) -> Path:
    return workdir / ".build-loop" / "coordination"


def _active_pointer_path(workdir: Path) -> Path:
    return _coord_dir(workdir) / "active.json"


def _default_coord_path(workdir: Path, topic: str, now: float | None = None) -> Path:
    return _coord_dir(workdir) / f"{topic}-{_today_iso(now)}.md"


def _timestamp_iso(now: float | None = None) -> str:
    t = time.gmtime(now) if now is not None else time.gmtime()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t)


def _write_active_pointer(
    *,
    workdir: Path,
    target: Path,
    session_id: str,
    now: float | None = None,
) -> bool:
    """Atomically write the active coordination pointer for default-pick readers.

    Only points at files inside ``.build-loop/coordination``; an explicit
    ``--coord-file`` outside that directory is allowed, but it should not
    become the repo-global default.
    """
    try:
        root = _coord_dir(workdir).resolve(strict=False)
        resolved_target = target.resolve(strict=False)
        if resolved_target != root and root not in resolved_target.parents:
            return False
        pointer = _active_pointer_path(workdir)
        pointer.parent.mkdir(parents=True, exist_ok=True)
        tmp = pointer.parent / f".{pointer.name}.tmp.{os.getpid()}"
        tmp.write_text(
            json.dumps(
                {
                    "coord_file": str(resolved_target),
                    "session_id": session_id,
                    "created_at": _timestamp_iso(now),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(pointer))
        return True
    except OSError:
        return False


def _render_template(
    template_text: str,
    *,
    topic: str,
    scope: str,
    session_id: str,
    tool: str,
    date_iso: str,
) -> str:
    """Substitute the {{PLACEHOLDER}} tokens with bootstrap values.

    The template carries many placeholders; this minimal substitution
    fills the bootstrap-time-knowable ones. Per-piece MECE packets,
    step-status rows, and acceptance criteria are left as placeholders
    for the orchestrator (or human author) to flesh out.
    """
    title = topic.replace("-", " ").strip()
    substitutions = {
        "{{RUN_TITLE}}": title or topic,
        "{{DATE_YYYY_MM_DD}}": date_iso,
        "{{PRIMARY_TOOL}}": tool,
        "{{PRIMARY_ROLE}}": "implementation owner",
        "{{VERIFIER_TOOL}}": "codex",
        "{{VERIFIER_ROLE}}": "verifier",
        "{{PREVIOUS_RUN_FILE}}": "none (first run)",
        "{{SCOPE_SUMMARY_2_TO_4_SENTENCES}}": scope,
        "{{THIS_FILE_NAME}}": f"{topic}-{date_iso}",
        "{{ANY_RUN_SPECIFIC_OPERATING_AMENDMENTS_OR_NONE}}": "none",
    }
    out = template_text
    for token, value in substitutions.items():
        out = out.replace(token, value)
    return out


def _resolve_template_path(workdir: Path, override: Path | None) -> Path:
    if override is not None:
        p = override.expanduser()
        return p if p.is_absolute() else (workdir / p)
    # Prefer workdir-local template (a worktree copy of the repo).
    local = workdir / DEFAULT_TEMPLATE_REL
    if local.exists():
        return local
    # Fallback: alongside this script's repo root (scripts/ -> repo root).
    repo_root = HERE.parent
    return repo_root / DEFAULT_TEMPLATE_REL


def bootstrap(
    *,
    workdir: Path,
    topic: str,
    scope: str,
    session_id: str,
    coord_file: Path | None = None,
    template_path: Path | None = None,
    tool: str = "claude_code",
    model: str = "claude-opus-4-7",
    run_id: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Bootstrap (or join) a coordination file. Returns a structured envelope.

    Envelope shape:
        {
          "coord_file": "<abs path>",
          "action": "bootstrapped" | "joined-existing-coord",
          "channel_revision": <int or null>,
          "session_id": "<id>",
          "presence_written": <bool>,
          "active_pointer_written": <bool>,
          "errors": [<str>...],
        }
    """
    errors: list[str] = []
    workdir = Path(workdir).resolve()
    target = coord_file or _default_coord_path(workdir, topic, now)
    target = Path(target).expanduser()
    if not target.is_absolute():
        target = (workdir / target).resolve()

    # β1: resolve channel via the shared discovery bridge so this writes
    # to the canonical Rally Point root when available.
    envelope = _bridge_resolve(workdir)
    slug = envelope.app_slug
    channel_dir = Path(envelope.channel_dir)
    effective_run_id = run_id or f"bootstrap-{topic}-{session_id}"
    presence_written = False
    action: str
    channel_rev: int | None = None
    active_pointer_written = False

    coord_dir = target.parent
    try:
        coord_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errors.append(f"could not create coord dir {coord_dir}: {exc}")

    # R1 fix (v0.12.10): atomic create via open(mode='x') eliminates the
    # exists()+write_text() race where two concurrent bootstrappers can both
    # pass the exists() check, both write the file, both post handoff.
    #
    # Optimization: do the fast exists() check first to skip template-load
    # work when joining an existing coord. The exists() check is NOT the
    # correctness mechanism — the open('x') atomic create is. Race-lost
    # bootstrappers (where exists() returns False but open('x') raises
    # FileExistsError because a peer created the file between the two calls)
    # gracefully fall through to action="joined-existing-coord".
    if target.exists():
        action = "joined-existing-coord"
    else:
        template_resolved = _resolve_template_path(workdir, template_path)
        try:
            template_text = template_resolved.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"could not read template at {template_resolved}: {exc}")
            return {
                "coord_file": str(target),
                "action": "error",
                "channel_revision": None,
                "session_id": session_id,
                "presence_written": False,
                "active_pointer_written": False,
                "errors": errors,
            }
        rendered = _render_template(
            template_text,
            topic=topic,
            scope=scope,
            session_id=session_id,
            tool=tool,
            date_iso=_today_iso(now),
        )
        # Atomic create: 'x' mode means exclusive create — raises FileExistsError
        # if the file already exists. This is the actual race-safety mechanism.
        try:
            with open(target, "x", encoding="utf-8") as f:
                f.write(rendered)
            action = "bootstrapped"
        except FileExistsError:
            # Race lost: a peer created the coord file between our exists()
            # check and our open('x'). Fall through to join semantics —
            # do NOT overwrite, do NOT duplicate the handoff post.
            action = "joined-existing-coord"
        except OSError as exc:
            errors.append(f"could not write coord file {target}: {exc}")
            return {
                "coord_file": str(target),
                "action": "error",
                "channel_revision": None,
                "session_id": session_id,
                "presence_written": False,
                "active_pointer_written": False,
                "errors": errors,
            }

    if action == "bootstrapped":
        active_pointer_written = _write_active_pointer(
            workdir=workdir,
            target=target,
            session_id=session_id,
            now=now,
        )
        if not active_pointer_written:
            errors.append(f"could not write active pointer for {target}")

    # Write presence (fire-and-forget; never blocks).
    try:
        presence.write_presence(
            channel_dir,
            session_id=session_id,
            tool=tool,
            model=model,
            run_id=effective_run_id,
            app_slug=slug,
            phase="bootstrap" if action == "bootstrapped" else "joined-existing-coord",
            files_in_flight=[str(target.relative_to(workdir)) if str(target).startswith(str(workdir)) else str(target)],
            cwd=workdir,
        )
        presence_written = True
    except Exception as exc:  # noqa: BLE001 — fire-and-forget
        errors.append(f"presence.write_presence failed: {exc}")

    # Post handoff or join record (fire-and-forget; post() swallows errors).
    # NOTE: kind=handoff payloads require MECE ownership per C4 (mece_gate
    # validator at post() time).  Bootstrap declares an empty initial
    # ownership claim: no chunks owned yet at coordination-open time.
    payload = {
        "from": tool,
        "topic": topic,
        "scope": scope,
        "session_id": session_id,
        "coord_file": str(target),
        "action": action,
    }
    if action == "bootstrapped":
        payload["ownership"] = {
            "owns": [],
            "does_not_own": [],
            "interface_contract": (
                f"Coordination bootstrapped by {tool} for topic '{topic}'. "
                "No chunks claimed yet; peers ack by reading coord file and "
                "posting kind=feedback step=coord-protocol verdict=PASS."
            ),
            "integration_checkpoint": str(target),
        }
    channel_rev = post(
        channel_dir=channel_dir,
        kind="handoff" if action == "bootstrapped" else "phase",
        tool=tool,
        model=model,
        run_id=effective_run_id,
        app_slug=slug,
        payload=(payload if action == "bootstrapped" else {**payload, "phase": "joined-existing-coord"}),
    )

    return {
        "coord_file": str(target),
        "action": action,
        "channel_revision": channel_rev,
        "session_id": session_id,
        "presence_written": presence_written,
        "active_pointer_written": active_pointer_written,
        "errors": errors,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--topic", required=True, help="Run slug, e.g. v0130-feature-x")
    p.add_argument("--scope", required=True, help="One-liner scope summary")
    p.add_argument("--session-id", required=True, help="Rally Point session id")
    p.add_argument("--coord-file", default=None, help="Explicit coord file path (default: .build-loop/coordination/<topic>-YYYY-MM-DD.md)")
    p.add_argument("--template", default=None, help="Explicit template path (default: references/coordination-file-template.md)")
    p.add_argument("--tool", default="claude_code")
    p.add_argument("--model", default="claude-opus-4-7")
    p.add_argument("--run-id", default=None)
    p.add_argument("--json", action="store_true", help="Emit JSON envelope (always JSON; flag is for explicitness)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = bootstrap(
        workdir=Path(args.workdir),
        topic=args.topic,
        scope=args.scope,
        session_id=args.session_id,
        coord_file=Path(args.coord_file) if args.coord_file else None,
        template_path=Path(args.template) if args.template else None,
        tool=args.tool,
        model=args.model,
        run_id=args.run_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    # Exit 0 unless we couldn't write the coord file at all (caught above as action="error").
    return 0 if result["action"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
