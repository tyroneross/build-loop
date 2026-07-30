#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Dispatch-time fallback helper — record a model outage and re-resolve.

The Claude Code Agent tool selects ONE model and ERRORS if it is down (e.g.
"Claude Fable 5 is currently unavailable"). Build-loop cannot wrap that harness
primitive in code, so the auto-fallback lives at the DISPATCH layer: when the
orchestrator observes such an unavailability signal — either a pre-dispatch
availability flag OR a caught dispatch-error string — it calls this helper, which

  1. records the unavailable model id into ``.build-loop/model-availability.json``
     (idempotent — recording the same id twice is a no-op), then
  2. re-resolves the tier through ``model_resolver.py`` (which now sees the model
     as unavailable) and returns the next available model.

Because the availability record PERSISTS, every subsequent resolve of that tier
also returns the fallback — the orchestrator does not have to catch the same
outage again. A human (or a later health signal) clears the entry with
``--clear`` when the model is back.

Fail-open: missing files are treated as empty sets; the helper never raises into
the dispatch path (exit 0 always, the resolved model on stdout).

CLI::

    # Outage observed for the frontier tier's model — record + re-resolve:
    python3 scripts/dispatch_fallback.py --workdir "$PWD" --tier frontier \
        --unavailable-model fable --json
    # -> {"recorded": "fable", "model": "opus", ...}

    # Model is back:
    python3 scripts/dispatch_fallback.py --workdir "$PWD" --clear fable --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:  # pragma: no cover - import shim
    import model_resolver
    import model_availability_store as store
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import model_resolver  # type: ignore[no-redefine]
    import model_availability_store as store  # type: ignore[no-redefine]

AVAILABILITY_FILENAME = store.AVAILABILITY_FILENAME


def availability_path(workdir: Path) -> Path:
    return store.availability_path(workdir)


def record_unavailable(
    workdir: Path, model_id: str, *, ttl: int | None = None
) -> bool:
    """Record ``model_id`` as unavailable with a wall-clock timestamp + TTL.

    Returns True if it was newly added, False if a LIVE (non-expired) record for
    it already exists. Writing also lazily prunes any expired records (so a stale
    legacy entry self-heals here too). Preserves ``hostProviders`` + other keys.
    Re-recording a still-live model refreshes nothing (idempotent); re-recording
    one whose prior record expired writes a fresh record (the outage is back).
    """
    model_id = model_id.strip()
    wd = workdir.expanduser().resolve()
    # Start from the pruned view so expired/legacy records are dropped on write.
    live, data, _changed = store.live_unavailable(wd)
    if model_id in live:
        # Already have a live record — keep it (and the pruned store), no dup.
        store.write_store(wd, data)
        return False
    effective_ttl = store.resolve_ttl(wd, explicit=ttl)
    kept = [r for r in data.get("unavailable", []) if store._record_id(r) != model_id]
    kept.append(
        {"id": model_id, "recorded_at": store.now(), "ttl": effective_ttl}
    )
    data["unavailable"] = kept
    store.write_store(wd, data)
    return True


def clear_unavailable(workdir: Path, model_id: str) -> bool:
    """Remove ``model_id`` from the unavailable set (object OR legacy string).

    Returns True if a record was removed."""
    model_id = model_id.strip()
    wd = workdir.expanduser().resolve()
    data = store._read_raw(wd)
    listed = data.get("unavailable")
    if not isinstance(listed, list):
        return False
    kept = [r for r in listed if store._record_id(r) != model_id]
    if len(kept) == len(listed):
        return False
    data["unavailable"] = kept
    store.write_store(wd, data)
    return True


def fallback(
    *, workdir: Path, tier: str, unavailable_model: str, ttl: int | None = None
) -> dict[str, Any]:
    """Record the outage, then re-resolve the tier to the next available model."""
    wd = workdir.expanduser().resolve()
    newly = record_unavailable(wd, unavailable_model, ttl=ttl)
    resolved = model_resolver.resolve(tier=tier, workdir=wd)
    return {
        "recorded": unavailable_model.strip(),
        "newly_recorded": newly,
        "tier": tier,
        "model": resolved.get("model"),
        "source": resolved.get("source"),
        "resolution_path": resolved.get("resolution_path"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workdir", default=".")
    p.add_argument("--tier", default=None, choices=sorted(model_resolver.TIERS))
    p.add_argument(
        "--unavailable-model",
        default=None,
        help="Model id observed unavailable at dispatch — recorded + re-resolved.",
    )
    p.add_argument("--clear", default=None, help="Mark a model available again.")
    p.add_argument(
        "--ttl",
        type=int,
        default=None,
        help="Seconds this outage stays recorded before it self-expires on read "
        "(per-record override). Precedence: --ttl > BUILD_LOOP_OUTAGE_TTL_SECONDS "
        f"> config.outageTtlSeconds > {store.DEFAULT_TTL_SECONDS}s default.",
    )
    p.add_argument("--plain", action="store_true", help="Print only the model id.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    workdir = Path(args.workdir)

    if args.clear:
        removed = clear_unavailable(workdir, args.clear)
        print(json.dumps({"cleared": args.clear, "removed": removed}, indent=2))
        return 0

    if not args.tier:
        p.error("--tier is required unless --clear is given")

    if args.unavailable_model:
        # Outage observed: record it + re-resolve to the next available model.
        result = fallback(
            workdir=workdir,
            tier=args.tier,
            unavailable_model=args.unavailable_model,
            ttl=args.ttl,
        )
    else:
        # No outage named: just resolve the tier to its primary available model
        # (nothing down -> the tier default). Lets the dispatcher ask "what model
        # for this tier?" through one entrypoint regardless of outage state.
        resolved = model_resolver.resolve(tier=args.tier, workdir=workdir)
        result = {
            "recorded": None,
            "newly_recorded": False,
            "tier": args.tier,
            "model": resolved.get("model"),
            "source": resolved.get("source"),
            "resolution_path": resolved.get("resolution_path"),
        }
    if args.plain and not args.json:
        print(result.get("model") or "")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
