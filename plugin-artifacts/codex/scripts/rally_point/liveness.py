# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""In-process ADAPTIVE, MULTI-SIGNAL session-liveness math helper.

Liveness POLICY is Rust-only: the coordination facade delegates reaper/liveness
decisions to the canonical ``rally`` binary and fails loud when it is
unavailable (see ``capability.py`` / ``reaper.py``). This module is no longer a
behavioral mirror of ``crates/rally-cli/src/liveness.rs`` — the cross-repo
golden-fixture parity proof (``liveness_vectors.json``) and its drift-manifest
entry were RETIRED in the Rust-rally migration. What remains is the pure window-
computation math that presence/squad-visibility still uses in-process; it is
verified by ``test_liveness.py``'s own inline unit tests, not against a foreign
suite.

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


# Default consecutive non-actionable re-checks before a task-scoped session
# self-exits (Layer 1). Mirrors the Rust ``DEFAULT_SELF_EXIT_STREAK``.
DEFAULT_SELF_EXIT_STREAK: int = 2


def reapable(liveness: str, parent_alive: bool | None) -> bool:
    """Reaper eligibility — the SINGLE authority for "may this session be killed?".

    PYTHON MIRROR of ``liveness::reapable`` (Rust). Composes the 4-signal
    :func:`is_live` verdict with an OPTIONAL parent-liveness signal (Layer 3
    parent-lifecycle binding). Both the orphan-tmux sweep (Layer 2) and the
    parent-binding reaper (Layer 3) call this, so the "never reap a live
    session" invariant lives in exactly one place.

    ``parent_alive``:
      * ``True``  — the launching parent PID is provably still alive.
      * ``False`` — the launching parent PID is provably dead/gone.
      * ``None``  — no parsable parent info (pre-binding session, unparseable
        metadata, or a session never launched via rally). The parent criterion
        is UNAVAILABLE; fall back to the liveness-window criterion ALONE and
        NEVER reap on the parent criterion.

    Truth table (EXACT contract the shared golden fixture asserts):
      | liveness | parent_alive | reapable |
      |----------|--------------|----------|
      | LIVE     | *            | False    | any of 4 signals fresh → never reap
      | UNKNOWN  | *            | False    | fail-closed: untrustworthy signals
      | STALE    | True         | False    | stale but parent alive → keep (conservative)
      | STALE    | False        | True     | stale AND parent dead → Layer-3 target
      | STALE    | None         | True     | stale; no parent info → window criterion alone
    """
    if liveness == LIVE:
        return False
    if liveness == UNKNOWN:
        return False
    # STALE (or any non-LIVE/UNKNOWN verdict treated as provably stale).
    if parent_alive is True:
        return False
    # parent_alive is False (dead) OR None (no info → window criterion alone).
    return True


def completion_self_exit_eligible(
    work_resolved: bool,
    next_empty_streak: int,
    required_streak: int,
    persistent_optout: bool,
) -> bool:
    """Completion-scoped self-exit eligibility (Layer 1 — prevent at source).

    PYTHON MIRROR of ``liveness::completion_self_exit_eligible`` (Rust). A
    task-scoped agent exits ONLY when BOTH hold for a SUSTAINED re-check, and
    never when opted out:

      * ``work_resolved`` — the agent's owned rally work is all resolved/closed.
      * ``next_empty_streak >= required_streak`` — ``rally next`` returned empty
        for at least ``required_streak`` CONSECUTIVE re-checks (the streak, not a
        single empty read, is what guarantees we never exit mid-task).

    ``persistent_optout`` short-circuits to ``False``: a deliberately-persistent
    session never self-exits on the implicit "work done" path. Pure + time-free.
    """
    if persistent_optout:
        return False
    if not work_resolved:
        return False
    needed = max(1, required_streak)
    return next_empty_streak >= needed
