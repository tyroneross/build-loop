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
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import model_resolver  # type: ignore[no-redefine]

AVAILABILITY_FILENAME = "model-availability.json"


def _build_loop_dir(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / ".build-loop"


def availability_path(workdir: Path) -> Path:
    return _build_loop_dir(workdir) / AVAILABILITY_FILENAME


def _read(workdir: Path) -> dict[str, Any]:
    try:
        data = json.loads(availability_path(workdir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalized_unavailable(data: dict[str, Any]) -> list[str]:
    listed = data.get("unavailable")
    return list(listed) if isinstance(listed, list) else []


def _write(workdir: Path, data: dict[str, Any]) -> None:
    d = _build_loop_dir(workdir)
    d.mkdir(parents=True, exist_ok=True)
    availability_path(workdir).write_text(
        json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
    )


def record_unavailable(workdir: Path, model_id: str) -> bool:
    """Add ``model_id`` to the persistent unavailable set. Idempotent.

    Returns True if it was newly added, False if it was already present.
    Preserves any existing ``hostProviders`` and other keys.
    """
    model_id = model_id.strip()
    data = _read(workdir)
    unavailable = _normalized_unavailable(data)
    if model_id in unavailable:
        return False
    unavailable.append(model_id)
    data["unavailable"] = sorted(set(unavailable))
    _write(workdir, data)
    return True


def clear_unavailable(workdir: Path, model_id: str) -> bool:
    """Remove ``model_id`` from the unavailable set. Returns True if removed."""
    model_id = model_id.strip()
    data = _read(workdir)
    unavailable = _normalized_unavailable(data)
    if model_id not in unavailable:
        return False
    data["unavailable"] = sorted(m for m in set(unavailable) if m != model_id)
    _write(workdir, data)
    return True


def fallback(
    *, workdir: Path, tier: str, unavailable_model: str
) -> dict[str, Any]:
    """Record the outage, then re-resolve the tier to the next available model."""
    wd = workdir.expanduser().resolve()
    newly = record_unavailable(wd, unavailable_model)
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
            workdir=workdir, tier=args.tier, unavailable_model=args.unavailable_model
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
