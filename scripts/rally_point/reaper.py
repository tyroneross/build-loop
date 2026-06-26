#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Rally Point reaper FACADE — delegates physical cleanup to the Rust binary.

REAPING IS RUST-ONLY. This module used to carry a full Python sweep (presence /
claim-index / lead deletion) that mirrored the Rust reaper. That mirror is
retired: a Python process physically deleting coordination records that the Rust
binary owns is the exact shadow-implementation Codex flagged as worse than no
coordination. The facade now shells ``rally sessions --reap`` (the canonical
reaper) when a full-capability binary owns the channel, and FAILS LOUD
otherwise.

Capability contract (see ``capability.py``):

* ``full``     — Rust binary present + owns channel → ``rally sessions --reap``
                 runs; report carries ``capability_level: full``.
* below full   — NO reaping. Reaping is destructive; the facade refuses and
                 returns a loud ``capability_level: unavailable`` /
                 ``degraded-breadcrumb`` report. It never falls back to a Python
                 sweep. A degraded session must never reap a peer it cannot
                 prove is dead.

ReapReport dict shape (stable for callers; new ``capability_level`` +
``coordination_unavailable`` fields)::

    {
        "applied":               bool,   # True = --reap (apply); False = dry-run probe
        "capability_level":      str,    # full | degraded-breadcrumb | unavailable
        "coordination_unavailable": str | None,
        "resolved_via":          str,    # from discovery_bridge
        "reaped":                list,   # session ids the Rust reaper removed (full only)
        "deferred_to_rust":      bool,   # True whenever a sub-full level refused
        "detail":                str,    # human-readable explanation
    }
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Package-relative imports with script-mode fallback
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_SCRIPTS = _HERE.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    from . import capability as _cap
    from . import hook_budget as _budget
    from .discovery_bridge import resolve as _resolve, rust_rally_binary as _rust_binary
except ImportError:  # script-mode
    import capability as _cap  # type: ignore
    import hook_budget as _budget  # type: ignore
    from discovery_bridge import resolve as _resolve  # type: ignore
    from discovery_bridge import rust_rally_binary as _rust_binary  # type: ignore


def _child_timeout() -> float:
    try:
        return _budget.inner_timeout_seconds(_budget.MARGIN_CHILD)
    except Exception:  # noqa: BLE001 — budget helper is best-effort
        return 5.0


def _run_rust_reap(binary: str, workdir: Path, *, apply: bool) -> dict[str, Any] | None:
    """Shell ``rally sessions [--reap] --json``. Returns parsed dict or None on error.

    ``--reap`` physically removes; without it the command is a read-only probe.
    """
    cmd = [binary, "sessions", "--json"]
    if apply:
        cmd.insert(2, "--reap")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=_child_timeout(),
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        out = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    return out if isinstance(out, dict) else None


def _reaped_ids(rust_out: dict[str, Any]) -> list[str]:
    """Best-effort extraction of reaped session ids from the rally JSON envelope."""
    data = rust_out.get("data")
    if not isinstance(data, dict):
        return []
    sessions = data.get("sessions")
    # rally nests sessions.sessions or sessions.reaped depending on version.
    if isinstance(sessions, dict):
        reaped = sessions.get("reaped")
        if isinstance(reaped, list):
            return [str(x) for x in reaped]
    if isinstance(sessions, list):
        return [str(x) for x in sessions]
    return []


def reap_channel(
    channel_dir: Path | str,
    workdir: Path | str,
    *,
    apply: bool = False,
    now: float | None = None,  # retained for signature compat; Rust owns time policy
) -> dict[str, Any]:
    """Reap over-TTL coordination state via the Rust binary, or refuse loudly.

    ``channel_dir`` is accepted for signature compatibility but the Rust binary
    resolves its own channel from ``workdir``. ``now`` is ignored — time-decay
    policy is Rust-only.

    Returns a capability-marked ReapReport. Reaping happens ONLY at ``full``
    capability; any sub-full level returns ``deferred_to_rust: True`` and reaps
    nothing.
    """
    workdir = Path(workdir)

    try:
        env = _resolve(workdir)
        resolved_via = env.resolved_via
        level = _cap.level_for_resolved_via(resolved_via, env.coordination_unavailable)
    except Exception:  # noqa: BLE001 — discovery must never crash the reaper
        resolved_via = "build-loop-internal"
        level = _cap.DEGRADED_BREADCRUMB

    base: dict[str, Any] = {
        "applied": apply,
        "resolved_via": resolved_via,
        "reaped": [],
        "deferred_to_rust": True,
    }

    if not _cap.is_full(level):
        base["detail"] = (
            "reaping is Rust-only and the full-capability binary is unavailable; "
            "refusing to run a shadow Python sweep"
        )
        reason = (
            _cap.REASON_INCOMPATIBLE_PROTOCOL
            if level == _cap.UNAVAILABLE
            else _cap.REASON_NO_BINARY
        )
        return _cap.mark(base, level, reason)

    binary = _rust_binary(workdir)
    if not binary:
        # Resolved full but binary path vanished between resolve and now.
        base["detail"] = "rally binary disappeared after resolution; refusing to reap"
        return _cap.mark(base, _cap.UNAVAILABLE, _cap.REASON_BINARY_ERROR)

    rust_out = _run_rust_reap(binary, workdir, apply=apply)
    if rust_out is None:
        base["detail"] = "rally sessions --reap failed or returned no JSON; reaped nothing"
        return _cap.mark(base, _cap.UNAVAILABLE, _cap.REASON_BINARY_ERROR)

    base["reaped"] = _reaped_ids(rust_out) if apply else []
    base["deferred_to_rust"] = False
    base["detail"] = "delegated to rally sessions --reap"
    return _cap.mark(base, _cap.FULL)


# ---------------------------------------------------------------------------
# CLI entrypoint (manual sweep)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Rally Point reaper facade (delegates to the Rust binary). "
        "Dry-run by default; pass --apply to physically reap via rally."
    )
    parser.add_argument("--workdir", default=None, help="Repo root (default: cwd)")
    parser.add_argument("--apply", action="store_true", default=False)
    parser.add_argument("--json", dest="json_output", action="store_true", default=False)
    args = parser.parse_args(argv)

    workdir = Path(
        args.workdir or os.environ.get("WORKDIR", "") or os.getcwd()
    ).resolve()

    try:
        env = _resolve(workdir)
        channel_dir = Path(env.channel_dir)
    except Exception as exc:  # noqa: BLE001 — fail-open: never crash a session
        if args.json_output:
            print(json.dumps({"error": str(exc), "applied": args.apply}))
        else:
            print(f"[reaper] channel resolution failed: {exc}", file=sys.stderr)
        sys.exit(0)

    report = reap_channel(channel_dir, workdir, apply=args.apply)

    if args.json_output:
        print(json.dumps(report, indent=2))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[reaper] {mode} via={report['resolved_via']} "
              f"capability={report['capability_level']}")
        print(f"  reaped:           {report['reaped']}")
        print(f"  deferred_to_rust: {report['deferred_to_rust']}")
        print(f"  detail:           {report['detail']}")


if __name__ == "__main__":
    main()
