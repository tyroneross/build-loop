# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Resolve the effective coordination policy (decay + reclaim tunables).

Mirror of agent-rally-point's `hooks_config::resolve_coordination`. Reads
`.build-loop/config.json` under a `"coordinationPolicy"` object, following the
same shape as `deployment_policy.load_policy`. Defaults come from `decay` so the
constants live in exactly one place per language.

Keys (all optional; an out-of-range or malformed value is ignored, keeping the
default — graceful degradation, never a hard failure):

    {
      "coordinationPolicy": {
        "half_life_hours": 48,
        "archive_floor_weight": 0.05,
        "reclaim_small_minutes": 30,
        "reclaim_large_minutes": 120
      }
    }
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import decay  # noqa: E402
import liveness  # noqa: E402

_CONFIG_KEY = "coordinationPolicy"


@dataclass(frozen=True)
class CoordinationPolicy:
    half_life_hours: float = decay.DEFAULT_HALF_LIFE_HOURS
    archive_floor_weight: float = decay.DEFAULT_ARCHIVE_FLOOR
    reclaim_small_minutes: int = decay.DEFAULT_RECLAIM_SMALL_MINUTES
    reclaim_large_minutes: int = decay.DEFAULT_RECLAIM_LARGE_MINUTES
    # Adaptive-liveness tunables (mirror of CoordinationConfig in Rust).
    default_cadence_secs: int = liveness.DEFAULT_CADENCE_SECS
    miss_multiplier: int = liveness.MISS_MULTIPLIER
    grace_secs: int = liveness.GRACE_SECS

    @property
    def half_life_secs(self) -> int:
        return int(round(self.half_life_hours * 3600.0))


def _coerce_float(value: object, *, lo: float, hi: float) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f <= lo or f >= hi:
        return None
    return f


def _coerce_pos_int(value: object) -> int | None:
    try:
        # bool is an int subclass; reject it explicitly.
        if isinstance(value, bool):
            return None
        i = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return i if i > 0 else None


def _coerce_nonneg_int(value: object) -> int | None:
    """Like _coerce_pos_int but accepts 0 (grace may legitimately be 0)."""
    try:
        if isinstance(value, bool):
            return None
        i = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return i if i >= 0 else None


def load_policy(workdir: Path) -> CoordinationPolicy:
    """Return the effective coordination policy for ``workdir``.

    Absent or malformed config -> defaults (never raises). A malformed top-level
    JSON file is treated as "no config" rather than failing the caller, matching
    the fail-open contract of the rally fallback path.
    """
    config_path = Path(workdir) / ".build-loop" / "config.json"
    if not config_path.exists():
        return CoordinationPolicy()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CoordinationPolicy()
    if not isinstance(config, dict):
        return CoordinationPolicy()
    raw = config.get(_CONFIG_KEY)
    if not isinstance(raw, dict):
        return CoordinationPolicy()

    half_life = _coerce_float(raw.get("half_life_hours"), lo=0.0, hi=float("inf"))
    floor = _coerce_float(raw.get("archive_floor_weight"), lo=0.0, hi=1.0)
    small = _coerce_pos_int(raw.get("reclaim_small_minutes"))
    large = _coerce_pos_int(raw.get("reclaim_large_minutes"))
    cadence = _coerce_pos_int(raw.get("default_cadence_secs"))
    mult = _coerce_pos_int(raw.get("miss_multiplier"))
    grace = _coerce_nonneg_int(raw.get("grace_secs"))

    base = CoordinationPolicy()
    return CoordinationPolicy(
        half_life_hours=half_life if half_life is not None else base.half_life_hours,
        archive_floor_weight=floor if floor is not None else base.archive_floor_weight,
        reclaim_small_minutes=small if small is not None else base.reclaim_small_minutes,
        reclaim_large_minutes=large if large is not None else base.reclaim_large_minutes,
        default_cadence_secs=cadence if cadence is not None else base.default_cadence_secs,
        miss_multiplier=mult if mult is not None else base.miss_multiplier,
        grace_secs=grace if grace is not None else base.grace_secs,
    )
