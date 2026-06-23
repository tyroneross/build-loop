# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Single source of truth for ADAPTIVE, MULTI-SIGNAL session liveness.

PYTHON MIRROR of agent-rally-point's ``crates/rally-cli/src/liveness.rs``.
Build-loop's coordination layer DEFERS to the Rust canonical when present
(discovery_bridge tier 6); this module is the fallback and MUST behave
identically. Parity is double-pinned by the byte-identical golden fixture
``liveness_vectors.json`` (same file in both repos) and the ``_provenance.json``
drift manifest.

Replaces fixed staleness cutoffs for the presence/squad projection with a window
that ADAPTS to each session's planned heartbeat cadence, and decides liveness
from FOUR independent signals — LIVE if ANY is fresh within the adaptive window.

Two fail-directions, each defaulting to the SAFE side:

* **Squad / visibility projection** is FAIL-OPEN — a missing/unparseable signal
  yields ``UNKNOWN``, which the caller treats as visible/alive. Hiding a
  still-alive peer would cause the exact write-collision this system prevents.
* **Reaper removal** is FAIL-CLOSED — ``UNKNOWN`` is NEVER reaped. Removal is
  destructive; we refuse on any signal we cannot trust.

Time is INJECTED (callers pass signal ages) so the math is pure and
deterministically testable — matching ``decay.py`` / ``leadership.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Defaults — identical to crates/rally-cli/src/liveness.rs.
DEFAULT_CADENCE_SECS: int = 300
MISS_MULTIPLIER: int = 6
GRACE_SECS: int = 60

# Liveness verdicts.
LIVE = "live"
STALE = "stale"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class LivenessSignals:
    """The four liveness signals, each an OPTIONAL age in seconds since last
    fresh. ``None`` = the signal was never observed / could not be parsed.

    A ``Some(age)`` with ``age <= window`` is FRESH; ``Some(age)`` with
    ``age > window`` is STALE for that one signal.
    """

    heartbeat_age: int | None = None
    inject_age: int | None = None
    code_progress_age: int | None = None
    plan_age: int | None = None

    def _as_tuple(self) -> tuple[int | None, ...]:
        return (
            self.heartbeat_age,
            self.inject_age,
            self.code_progress_age,
            self.plan_age,
        )


def adaptive_window_secs(
    planned_interval_secs: int,
    default_cadence_secs: int = DEFAULT_CADENCE_SECS,
    miss_multiplier: int = MISS_MULTIPLIER,
    grace_secs: int = GRACE_SECS,
) -> int:
    """The adaptive staleness window (seconds) for a session beating every
    ``planned_interval_secs``. A non-positive interval falls back to the default
    cadence (never a zero/negative window).

    ``window = clamp(interval) * max(1, miss_multiplier) + max(0, grace)``.
    """
    if planned_interval_secs > 0:
        interval = planned_interval_secs
    elif default_cadence_secs > 0:
        interval = default_cadence_secs
    else:
        interval = DEFAULT_CADENCE_SECS
    mult = max(1, miss_multiplier)
    grace = max(0, grace_secs)
    return interval * mult + grace


def is_live(signals: LivenessSignals, window: int) -> str:
    """Decide liveness from the four signals against the adaptive ``window``.

    * any signal ``age`` with ``age <= window`` -> ``LIVE``.
    * else if EVERY signal is present (all parseable) -> ``STALE``.
    * else (no fresh signal AND at least one ``None``) -> ``UNKNOWN``.
    """
    arr = signals._as_tuple()
    if any(s is not None and s <= window for s in arr):
        return LIVE
    if all(s is not None for s in arr):
        return STALE
    return UNKNOWN


def is_live_default(signals: LivenessSignals, planned_interval_secs: int) -> str:
    """Compute the window from a planned interval (pinned default constants) and
    decide liveness in one call. Callers with resolved tunables use
    ``adaptive_window_secs`` + ``is_live`` directly."""
    window = adaptive_window_secs(planned_interval_secs)
    return is_live(signals, window)
