#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""owed_verification.py — the owed-verification manifest for GAP-1.

WHY
---
A nested / background ``build-orchestrator`` has no Agent tool, so it cannot
dispatch its own Fable-tier verification layer (``plan-critic``,
``independent-auditor``, ``security-reviewer``).  The GAP-1 "auditor dispatch
ladder" already records this honestly in ``auditor_status`` as
``not-run:parent-must-dispatch`` and documents (in prose) that the DISPATCHING
PARENT then owes the audit.  Prose is a memory, not a mechanism: nothing forced
the parent to actually run the owed verifiers, so a run could close having
silently skipped the verdicts the org depends on.

This script turns that prose contract into a machine-checkable MANIFEST.  When
a nested orchestrator reaches Review with verifiers un-run, it ``write``s the
owed list to ``.build-loop/owed-verification.json`` and flips
``state.json.review_incomplete = true``.  Sub-step G surfaces the manifest as a
non-optional "PARENT MUST DISPATCH" block.  The parent (which HAS the Agent
tool) dispatches each owed verifier, then ``clear``s it; when the last owed
verifier is cleared, the flag flips back to ``false`` and the manifest is
removed.  ``check`` answers "is this run's review complete?" for any caller.

The manifest is a plain JSON file so anything — a script, a human, another
agent — can inspect it without depending on this CLI.

CLI
---

::

    owed_verification.py write  --workdir <repo> --run-id <id>
                                --diff-range <base>..<head>
                                --owe independent-auditor [--owe plan-critic ...]
                                [--chunk-id <id>] [--reason "<text>"]
                                [--written-by <label>]
                                [--dispatch-command verifier=<cmd> ...]
                                [--json]

    owed_verification.py check  --workdir <repo> [--json]

    owed_verification.py clear  --workdir <repo>
                                (--verifier <name> ... | --all)
                                [--reason "<text>"] [--json]

Exit codes
----------

- ``write`` — 0 on success (manifest written / refreshed).
- ``check`` — 0 when review is COMPLETE (nothing owed) or NO manifest exists;
              1 when review is INCOMPLETE (verifiers still owed).  This lets a
              parent gate on ``owed_verification.py check && proceed``.
- ``clear`` — 0 on success (verifier(s) cleared, whether or not any remain).
- 2 on argument errors (handled by argparse).

Importable surface
------------------

- ``load_manifest(workdir) -> dict | None``
- ``write_manifest(workdir, *, run_id, diff_range, owed, ...) -> Path``
- ``check_manifest(workdir) -> dict``
- ``clear_verifiers(workdir, *, verifiers=None, clear_all=False, ...) -> dict``
- ``MANIFEST_RELPATH`` / ``STATE_RELPATH``
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MANIFEST_RELPATH = Path(".build-loop") / "owed-verification.json"
STATE_RELPATH = Path(".build-loop") / "state.json"
LOG_RELPATH = Path(".build-loop") / "audit-log.md"

# Canonical verifiers the parent may owe, each with a default dispatch command
# template.  ``{range}`` / ``{plan}`` are filled from the manifest at write time
# so the emitted command is copy-paste runnable by the parent.  A caller can
# override any command via ``--dispatch-command verifier=<cmd>``.
KNOWN_VERIFIERS: dict[str, str] = {
    "independent-auditor": (
        'Agent(subagent_type="build-loop:independent-auditor", '
        'prompt="audit {range} at build scope; append the verdict to '
        '.build-loop/judge-decisions.json")'
    ),
    "plan-critic": (
        'Agent(subagent_type="build-loop:plan-critic", '
        'prompt="critique the Phase 2 plan for {range}; append the verdict to '
        '.build-loop/judge-decisions.json")'
    ),
    "security-reviewer": (
        'Agent(subagent_type="build-loop:security-reviewer", '
        'prompt="adversarial security review of {range}; append findings to '
        '.build-loop/judge-decisions.json")'
    ),
    "scope-auditor": (
        'Agent(subagent_type="build-loop:scope-auditor", '
        'prompt="Plan→Execute boundary check on {range}")'
    ),
}


# ---------------------------------------------------------------------------
# Time / IO helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic-ish write so a concurrent reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _log(workdir: Path, line: str) -> None:
    """Best-effort audit-log line; never fatal."""
    try:
        log = workdir / LOG_RELPATH
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"- {_utcnow_iso()} owed_verification {line}\n")
    except OSError:
        pass


def _dedupe(items: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication of non-empty stripped tokens."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        tok = str(raw).strip()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


# ---------------------------------------------------------------------------
# state.json mirror flag (best-effort, never authoritative)
# ---------------------------------------------------------------------------


def _set_state_flag(workdir: Path, value: bool) -> bool:
    """Mirror ``review_incomplete`` onto state.json.

    Best-effort: the MANIFEST is authoritative.  If state.json is absent or
    unparseable we skip silently and report ``state_updated: False`` — we never
    clobber a schema we can't read.  Returns True iff the flag was written.
    """
    state_path = workdir / STATE_RELPATH
    data = _read_json(state_path)
    if not isinstance(data, dict):
        return False
    data["review_incomplete"] = value
    try:
        _atomic_write_json(state_path, data)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Manifest primitives
# ---------------------------------------------------------------------------


def load_manifest(workdir: Path) -> dict[str, Any] | None:
    """Return the parsed manifest, or None if absent / unreadable."""
    path = workdir / MANIFEST_RELPATH
    if not path.exists():
        return None
    data = _read_json(path)
    if not isinstance(data, dict):
        # Present but unparseable — surface as an active manifest with an
        # unknown owed set so the run is treated as INCOMPLETE (fail safe:
        # prefer one extra audit to silently closing an un-reviewed run).
        return {
            "run_id": None,
            "diff_range": None,
            "owed": ["unknown"],
            "cleared": [],
            "status": "incomplete",
            "_malformed": True,
        }
    return data


def _dispatch_commands(
    owed: list[str],
    diff_range: str,
    plan_path: str | None,
    overrides: dict[str, str],
) -> dict[str, str]:
    """Build the copy-paste dispatch command per owed verifier."""
    out: dict[str, str] = {}
    for name in owed:
        if name in overrides:
            out[name] = overrides[name]
            continue
        template = KNOWN_VERIFIERS.get(name)
        if template is None:
            out[name] = (
                f'Agent(subagent_type="build-loop:{name}", '
                f'prompt="run {name} on {diff_range}")'
            )
            continue
        out[name] = template.format(
            range=diff_range,
            plan=plan_path or "docs/plans/<active-plan>.md",
        )
    return out


def write_manifest(
    workdir: Path,
    *,
    run_id: str,
    diff_range: str,
    owed: Iterable[str],
    chunk_id: str | None = None,
    reason: str | None = None,
    written_by: str = "nested-orchestrator",
    plan_path: str | None = None,
    dispatch_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create/refresh the owed-verification manifest.

    Merges with any existing manifest so a second ``write`` in the same run
    (e.g. a later chunk owing another verifier) accumulates rather than
    clobbers, and never re-adds an already-cleared verifier.  Flips
    ``state.json.review_incomplete = true`` when anything is owed.

    Returns the written manifest dict (plus a ``_state_updated`` key).
    """
    owed_list = _dedupe(owed)
    existing = load_manifest(workdir)
    cleared: list[str] = []
    if isinstance(existing, dict) and not existing.get("_malformed"):
        prior_owed = existing.get("owed")
        if isinstance(prior_owed, list):
            owed_list = _dedupe([*prior_owed, *owed_list])
        prior_cleared = existing.get("cleared")
        if isinstance(prior_cleared, list):
            cleared = _dedupe(prior_cleared)

    # A verifier already cleared must not silently re-appear as owed.
    remaining = [v for v in owed_list if v not in cleared]

    overrides = dispatch_overrides or {}
    payload: dict[str, Any] = {
        "run_id": run_id,
        "chunk_id": chunk_id,
        "diff_range": diff_range,
        "owed": remaining,
        "cleared": cleared,
        "dispatch_commands": _dispatch_commands(remaining, diff_range, plan_path, overrides),
        "written_by": written_by,
        "written_at": _utcnow_iso(),
        "status": "incomplete" if remaining else "complete",
    }
    if reason:
        payload["reason"] = reason

    _atomic_write_json(workdir / MANIFEST_RELPATH, payload)
    state_updated = _set_state_flag(workdir, bool(remaining))
    _log(workdir, f"write run={run_id} owed={','.join(remaining) or 'none'}")
    payload["_state_updated"] = state_updated
    return payload


def check_manifest(workdir: Path) -> dict[str, Any]:
    """Answer 'is this run's review complete?'.

    Returns a dict with ``status`` ∈ {``complete``, ``incomplete``, ``absent``},
    the remaining ``owed`` list, ``cleared`` list, and ``review_incomplete``
    (the boolean a gate keys on).
    """
    manifest = load_manifest(workdir)
    if manifest is None:
        return {
            "status": "absent",
            "owed": [],
            "cleared": [],
            "run_id": None,
            "diff_range": None,
            "review_incomplete": False,
            "manifest_path": str(workdir / MANIFEST_RELPATH),
        }
    owed = manifest.get("owed") if isinstance(manifest.get("owed"), list) else []
    cleared = manifest.get("cleared") if isinstance(manifest.get("cleared"), list) else []
    remaining = [str(v) for v in owed]
    incomplete = bool(remaining)
    return {
        "status": "incomplete" if incomplete else "complete",
        "owed": remaining,
        "cleared": [str(v) for v in cleared],
        "run_id": manifest.get("run_id"),
        "diff_range": manifest.get("diff_range"),
        "dispatch_commands": manifest.get("dispatch_commands", {}),
        "review_incomplete": incomplete,
        "malformed": bool(manifest.get("_malformed")),
        "manifest_path": str(workdir / MANIFEST_RELPATH),
    }


def clear_verifiers(
    workdir: Path,
    *,
    verifiers: Iterable[str] | None = None,
    clear_all: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Mark owed verifier(s) as dispatched-and-cleared by the parent.

    When the last owed verifier is cleared the manifest is REMOVED and
    ``state.json.review_incomplete`` flips to ``false``.  Idempotent: clearing
    an already-cleared or unknown verifier is a no-op for that name.

    Returns ``{"action", "cleared", "remaining", "status", "state_updated",
    "manifest_removed"}``.
    """
    manifest = load_manifest(workdir)
    if manifest is None:
        return {
            "action": "noop_absent",
            "cleared": [],
            "remaining": [],
            "status": "absent",
            "state_updated": False,
            "manifest_removed": False,
        }

    owed = list(manifest.get("owed") or []) if isinstance(manifest.get("owed"), list) else []
    already_cleared = (
        list(manifest.get("cleared") or []) if isinstance(manifest.get("cleared"), list) else []
    )

    to_clear = list(owed) if clear_all else _dedupe(verifiers or [])
    newly_cleared = [v for v in to_clear if v in owed]
    remaining = [v for v in owed if v not in newly_cleared]
    cleared_total = _dedupe([*already_cleared, *newly_cleared])

    manifest_path = workdir / MANIFEST_RELPATH
    if not remaining:
        # Review complete — remove the manifest, flip the state flag.
        removed = False
        try:
            if manifest_path.exists():
                manifest_path.unlink()
                removed = True
        except OSError:
            removed = False
        state_updated = _set_state_flag(workdir, False)
        _log(workdir, f"clear complete cleared={','.join(newly_cleared) or 'none'} ({reason or 'no reason'})")
        return {
            "action": "cleared_complete",
            "cleared": newly_cleared,
            "remaining": [],
            "status": "complete",
            "state_updated": state_updated,
            "manifest_removed": removed,
        }

    # Verifiers still owed — persist the reduced manifest.
    payload = dict(manifest)
    payload.pop("_malformed", None)
    payload["owed"] = remaining
    payload["cleared"] = cleared_total
    payload["status"] = "incomplete"
    payload["dispatch_commands"] = {
        k: v
        for k, v in (manifest.get("dispatch_commands") or {}).items()
        if k in remaining
    }
    payload["updated_at"] = _utcnow_iso()
    _atomic_write_json(manifest_path, payload)
    state_updated = _set_state_flag(workdir, True)
    _log(workdir, f"clear partial cleared={','.join(newly_cleared) or 'none'} remaining={','.join(remaining)}")
    return {
        "action": "cleared_partial",
        "cleared": newly_cleared,
        "remaining": remaining,
        "status": "incomplete",
        "state_updated": state_updated,
        "manifest_removed": False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any], *, as_json: bool, stream=sys.stdout) -> None:
    if as_json:
        stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return
    for key in ("action", "status", "run_id", "diff_range"):
        if key in payload and payload[key] is not None:
            stream.write(f"{key}: {payload[key]}\n")
    if payload.get("owed"):
        stream.write(f"owed: {', '.join(payload['owed'])}\n")
    if payload.get("cleared"):
        stream.write(f"cleared: {', '.join(payload['cleared'])}\n")
    if payload.get("remaining"):
        stream.write(f"remaining: {', '.join(payload['remaining'])}\n")


def _parse_dispatch_overrides(pairs: Iterable[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in pairs or []:
        if "=" not in raw:
            continue
        name, _, cmd = raw.partition("=")
        name = name.strip()
        if name:
            out[name] = cmd
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Owed-verification manifest for GAP-1 — makes the parent-must-"
            "dispatch contract machine-checkable.  See module docstring."
        ),
    )
    sub = parser.add_subparsers(dest="op", required=True)

    p_write = sub.add_parser("write", help="Write/refresh the owed-verification manifest.")
    p_write.add_argument("--workdir", type=Path, default=Path.cwd())
    p_write.add_argument("--run-id", required=True)
    p_write.add_argument("--diff-range", required=True, help="e.g. HEAD~3..HEAD")
    p_write.add_argument(
        "--owe",
        action="append",
        default=[],
        dest="owe",
        help="A verifier the parent owes (repeatable). e.g. independent-auditor",
    )
    p_write.add_argument(
        "--owed",
        default=None,
        help="Comma-separated verifiers (alternative to repeated --owe).",
    )
    p_write.add_argument("--chunk-id", default=None)
    p_write.add_argument("--reason", default=None)
    p_write.add_argument("--written-by", default="nested-orchestrator")
    p_write.add_argument("--plan-path", default=None)
    p_write.add_argument(
        "--dispatch-command",
        action="append",
        default=[],
        dest="dispatch_command",
        help="Override a verifier's dispatch command: verifier=<cmd> (repeatable).",
    )
    p_write.add_argument("--json", action="store_true")

    p_check = sub.add_parser("check", help="Report whether review is complete.")
    p_check.add_argument("--workdir", type=Path, default=Path.cwd())
    p_check.add_argument("--json", action="store_true")

    p_clear = sub.add_parser("clear", help="Clear owed verifier(s) after the parent dispatched them.")
    p_clear.add_argument("--workdir", type=Path, default=Path.cwd())
    p_clear.add_argument(
        "--verifier",
        action="append",
        default=[],
        dest="verifier",
        help="A verifier to clear (repeatable).",
    )
    p_clear.add_argument("--all", action="store_true", dest="clear_all")
    p_clear.add_argument("--reason", default=None)
    p_clear.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    workdir = args.workdir.resolve()

    if args.op == "write":
        owed = list(args.owe)
        if args.owed:
            owed.extend(s for s in args.owed.split(",") if s.strip())
        if not owed:
            p_write.error("write requires at least one --owe / --owed verifier")
        payload = write_manifest(
            workdir,
            run_id=args.run_id,
            diff_range=args.diff_range,
            owed=owed,
            chunk_id=args.chunk_id,
            reason=args.reason,
            written_by=args.written_by,
            plan_path=args.plan_path,
            dispatch_overrides=_parse_dispatch_overrides(args.dispatch_command),
        )
        _emit({"action": "write", **payload}, as_json=args.json)
        return 0

    if args.op == "check":
        result = check_manifest(workdir)
        _emit(result, as_json=args.json)
        # Exit 1 when review is INCOMPLETE so a caller can gate on it.
        return 1 if result["status"] == "incomplete" else 0

    if args.op == "clear":
        if not args.verifier and not args.clear_all:
            p_clear.error("clear requires --verifier <name> (repeatable) or --all")
        result = clear_verifiers(
            workdir,
            verifiers=args.verifier,
            clear_all=args.clear_all,
            reason=args.reason,
        )
        _emit(result, as_json=args.json)
        return 0

    parser.error("no op selected")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
