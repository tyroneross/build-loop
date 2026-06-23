# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0

"""Single source of truth for build-loop's coordination time policy.

This is the Python MIRROR of agent-rally-point's ``crates/rally-cli/src/decay.rs``.
Build-loop's rally/coord point DEFERS to the Rust canonical implementation when
both are present (discovery_bridge tier 6); this module is the fallback path and
MUST behave identically:

* **Recency decay** — ``weight(age) = 0.5 ** (age_hours / half_life)``
  (exponential half-life, default 48h). Listings deprioritize by weight; a
  message whose weight falls below the archive floor is archived.
* **Lead / ownership auto-reclaim** — the reclaim timeout scales with the size
  of the claimed work (single-file = small, coarse = large).

Pinned boundary operators (must match the Rust mirror):

* ``is_archivable`` uses STRICT ``<`` — a message exactly at the floor is kept.
* ``age_secs`` is floored to integer seconds before the power, so truncation
  behaves identically across languages.

Time is INJECTED (callers pass ``age_secs`` / ``now``) so the math is pure and
deterministically testable, matching the ``now=`` pattern used in
``leadership.py``.
"""

from __future__ import annotations

# Defaults — identical to crates/rally-cli/src/decay.rs.
DEFAULT_HALF_LIFE_HOURS: float = 48.0
DEFAULT_ARCHIVE_FLOOR: float = 0.05
DEFAULT_RECLAIM_SMALL_MINUTES: int = 30
DEFAULT_RECLAIM_LARGE_MINUTES: int = 120

# Work-size classes.
SMALL = "small"
LARGE = "large"


def recency_weight(age_secs: float, half_life_secs: float) -> float:
    """Recency weight from an age, using an exponential half-life.

    ``weight = 0.5 ** (age_hours / half_life_hours)`` where ``age_hours`` is
    derived from integer (floored) seconds, matching the Rust mirror. A negative
    ``age_secs`` (clock skew) is clamped to 0 -> weight 1.0. A non-positive
    ``half_life_secs`` falls back to the default to avoid division by zero.
    """
    age = max(0, int(age_secs))
    if half_life_secs and half_life_secs > 0:
        half_life = float(half_life_secs)
    else:
        half_life = DEFAULT_HALF_LIFE_HOURS * 3600.0
    return 0.5 ** (age / half_life)


def is_archivable(weight: float, floor: float) -> bool:
    """True when a message's weight has fallen below the archive floor.

    STRICT less-than: a message exactly at the floor is NOT archived. Pinned to
    match the Rust mirror.
    """
    return weight < floor


def reclaim_timeout_seconds(
    work_size: str,
    small_minutes: int = DEFAULT_RECLAIM_SMALL_MINUTES,
    large_minutes: int = DEFAULT_RECLAIM_LARGE_MINUTES,
) -> int:
    """Reclaim timeout (seconds) for a claim of the given size class."""
    if work_size == SMALL:
        return max(0, int(small_minutes)) * 60
    # Anything not explicitly SMALL is treated as LARGE (conservative).
    return max(0, int(large_minutes)) * 60


def classify_work_size(
    *,
    effort: str | None = None,
    scope_paths: list[str] | None = None,
) -> str:
    """Classify a claim's work size as ``"small"`` or ``"large"``.

    Two inputs, checked in order (first decisive one wins):

    * ``effort`` (the backlog XS|S|M|L|XL grade, when threaded from a plan):
      ``XS``/``S`` -> small; ``M``/``L``/``XL`` -> large.
    * ``scope_paths`` (the owned-file list): exactly one path -> small; zero or
      more than one -> large (conservative — we never aggressively reclaim work
      we cannot prove is a single file).

    Mirrors the Rust ``classify_work_size`` intent (single-file = small).
    """
    if effort:
        e = effort.strip().upper()
        if e in ("XS", "S"):
            return SMALL
        if e in ("M", "L", "XL"):
            return LARGE
        # Unknown effort grade falls through to scope-based classification.
    if scope_paths is not None:
        return SMALL if len(scope_paths) == 1 else LARGE
    # No signal at all -> conservative LARGE.
    return LARGE
