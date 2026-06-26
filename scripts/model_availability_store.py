#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Single source of truth for the model-outage availability store schema + TTL.

The outage store (``.build-loop/model-availability.json``) records models observed
unavailable at dispatch time so an outage caught once persists across dispatches.
Without a TTL a recorded outage NEVER expires — a ``fable`` outage recorded once
keeps the system off Fable forever, even after the model recovers (the exact bug
this module fixes). Every recorded outage now carries a wall-clock ``recorded_at``
and an effective ``ttl``; on every READ in the resolve/dispatch path, records past
``recorded_at + ttl`` are treated as available (expired) AND lazily pruned from the
store — no background process.

Schema (the ``unavailable`` list accepts BOTH shapes, mixed freely):

  - object record (current):  {"id": "fable", "recorded_at": 1750000000.0, "ttl": 1800}
  - bare string (legacy):     "fable"

BACKWARD COMPAT — a bare string has no timestamp, so it is treated as EXPIRED on
first read (the stale-state bug self-heals immediately) and pruned. An object
record missing ``recorded_at`` is likewise treated as expired (cannot prove it is
fresh). Any non-``unavailable`` top-level key (``hostProviders`` etc.) is preserved.

TTL precedence (highest first):
  1. per-record ``--ttl SECONDS`` (stored on the record at write time)
  2. env ``BUILD_LOOP_OUTAGE_TTL_SECONDS``
  3. ``.build-loop/config.json`` -> ``outageTtlSeconds`` (or ``config.outageTtlSeconds``)
  4. ``DEFAULT_TTL_SECONDS`` (kept here, not in CLAUDE.md — durable tiers only rule)

Real wall clock (``time.time``): these are RUNTIME dispatch-path scripts, not
workflow scripts, so wall-clock time is allowed.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_TTL_SECONDS = 1800  # 30 min — long enough to avoid hammering a down model,
# short enough that recovery is picked up promptly. Override via env/config/--ttl.

ENV_TTL = "BUILD_LOOP_OUTAGE_TTL_SECONDS"
CONFIG_TTL_KEY = "outageTtlSeconds"
AVAILABILITY_FILENAME = "model-availability.json"
CONFIG_FILENAME = "config.json"


def now() -> float:
    """Current wall-clock epoch seconds. Indirected so tests can monkeypatch."""
    return time.time()


def build_loop_dir(workdir: Path) -> Path:
    return workdir.expanduser().resolve() / ".build-loop"


def availability_path(workdir: Path) -> Path:
    return build_loop_dir(workdir) / AVAILABILITY_FILENAME


def _read_raw(workdir: Path) -> dict[str, Any]:
    try:
        data = json.loads(availability_path(workdir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_int(value: Any) -> int | None:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def resolve_ttl(
    workdir: Path,
    *,
    explicit: int | None = None,
    config_path: Path | None = None,
) -> int:
    """Resolve the effective TTL: explicit (--ttl) > env > config > default."""
    if (val := _coerce_int(explicit)) is not None:
        return val
    if (val := _coerce_int(os.environ.get(ENV_TTL))) is not None:
        return val
    cfg = (config_path or (build_loop_dir(workdir) / CONFIG_FILENAME)).expanduser()
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if isinstance(data, dict):
        nested = data.get("config") if isinstance(data.get("config"), dict) else data
        if (val := _coerce_int(nested.get(CONFIG_TTL_KEY))) is not None:
            return val
    return DEFAULT_TTL_SECONDS


def _record_id(record: Any) -> str | None:
    """The model id of a record (object or bare string), trimmed."""
    if isinstance(record, str):
        mid = record.strip()
        return mid or None
    if isinstance(record, dict):
        mid = str(record.get("id", "")).strip()
        return mid or None
    return None


def is_expired(record: Any, *, at: float | None = None) -> bool:
    """True if a record is expired (and so should be pruned + treated available).

    - bare string (legacy, no timestamp) -> expired (self-heals the stale bug)
    - object missing recorded_at -> expired (cannot prove freshness)
    - object with recorded_at + ttl -> expired when now >= recorded_at + ttl
    """
    if isinstance(record, str):
        return True  # legacy: no timestamp -> treat as expired on first read
    if not isinstance(record, dict):
        return True
    recorded_at = record.get("recorded_at")
    try:
        recorded_at = float(recorded_at)
    except (TypeError, ValueError):
        return True  # no usable timestamp -> expired
    ttl = _coerce_int(record.get("ttl"))
    if ttl is None:
        ttl = DEFAULT_TTL_SECONDS
    clock = now() if at is None else at
    return clock >= recorded_at + ttl


def live_unavailable(
    workdir: Path, *, at: float | None = None
) -> tuple[set[str], dict[str, Any], bool]:
    """Return (live_ids, pruned_store_data, changed).

    Walks the persisted ``unavailable`` list, keeping only non-expired records.
    ``pruned_store_data`` is the full store dict with the pruned ``unavailable``
    list (every other key preserved) ready to write back. ``changed`` is True iff
    at least one record was dropped (so the caller can lazily rewrite the file).
    Fail-open: a missing/corrupt file yields an empty live set.
    """
    data = _read_raw(workdir)
    listed = data.get("unavailable")
    if not isinstance(listed, list):
        return set(), data, False

    kept: list[Any] = []
    live: set[str] = set()
    changed = False
    for record in listed:
        mid = _record_id(record)
        if mid is None:
            changed = True  # drop malformed entries
            continue
        if is_expired(record, at=at):
            changed = True
            continue
        kept.append(record)
        live.add(mid)

    pruned = dict(data)
    pruned["unavailable"] = kept
    return live, pruned, changed


def write_store(workdir: Path, data: dict[str, Any]) -> None:
    d = build_loop_dir(workdir)
    d.mkdir(parents=True, exist_ok=True)
    availability_path(workdir).write_text(
        json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
    )


def prune_on_read(workdir: Path, *, at: float | None = None) -> set[str]:
    """Load the live (non-expired) unavailable set, lazily pruning the store.

    This is THE read called on the resolve/dispatch path. Returns the set of model
    ids that are STILL unavailable. If any record expired, the store is rewritten
    with those records removed (migrate-on-read for legacy bare strings, too).
    """
    live, pruned, changed = live_unavailable(workdir, at=at)
    if changed:
        try:
            write_store(workdir, pruned)
        except OSError:
            pass  # fail-open: a read must never raise into the dispatch path
    return live
