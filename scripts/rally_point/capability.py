# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Coordination capability contract for the Rust-rally facade.

Build-loop's coordination layer is a thin facade over the canonical Rust
``rally`` binary (agent-rally-point). The facade NEVER runs a shadow policy
implementation — when the binary is unavailable it FAILS LOUD. This module is
the single source of truth for that contract: every coordination envelope
returned to a build-loop caller carries a ``capability_level`` field so the
caller can tell, without inspecting internals, what the coordination layer was
actually able to do.

Three levels (most → least capable):

* ``full`` — the Rust ``rally`` binary is present and owns the active channel.
  Real coordination is available: claim/release ownership, lead lease, adaptive
  liveness, reaper/reclaim, and before-write collision protection. Decay /
  liveness / reaper / reclaim are RUST-ONLY — there is no Python mirror; the
  facade shells the binary.

* ``degraded-breadcrumb`` — the binary is NOT available but a channel directory
  is writable. The facade may write ONLY capability-marked presence/handoff
  *breadcrumb* facts so a later full-capability peer (or a human) can see this
  session existed. In this mode the facade MUST NOT, and structurally CANNOT:
  claim ownership, reclaim, infer liveness, reap, or imply before-write
  protection. A partial fallback that a peer mistakes for real coordination is
  worse than none (Codex constraint), so every breadcrumb fact is stamped
  ``capability_level: degraded-breadcrumb`` + ``coordination_unavailable`` so no
  reader can confuse it with a real claim.

* ``unavailable`` — no coordination is possible at all (unsupported host with no
  fetchable binary, or an incompatible protocol version). The facade is a loud
  no-op: it returns a marked envelope and writes nothing.

The companion ``coordination_unavailable`` reason string explains WHY a level is
below ``full`` (``no_binary`` / ``incompatible_protocol`` / ``unsupported_host``
/ ``binary_error``). ``full`` always pairs with ``coordination_unavailable:
None``.
"""
from __future__ import annotations

from typing import Any, Final

# Capability levels (ordered most → least capable).
FULL: Final = "full"
DEGRADED_BREADCRUMB: Final = "degraded-breadcrumb"
UNAVAILABLE: Final = "unavailable"

LEVELS: Final = (FULL, DEGRADED_BREADCRUMB, UNAVAILABLE)

# coordination_unavailable reason strings (None ⇔ level == FULL).
REASON_NO_BINARY: Final = "no_binary"
REASON_INCOMPATIBLE_PROTOCOL: Final = "incompatible_protocol"
REASON_UNSUPPORTED_HOST: Final = "unsupported_host"
REASON_BINARY_ERROR: Final = "binary_error"

# Operations that REQUIRE full capability. The facade refuses these below FULL —
# they are the operations a peer could mistake for real coordination if a
# degraded path serviced them. Decay/liveness/reaper/reclaim are Rust-only and
# live behind these names.
FULL_ONLY_OPERATIONS: Final = frozenset(
    {
        "claim",
        "release",
        "reclaim",
        "lead",
        "renew_lease",
        "relinquish_lead",
        "reap",
        "liveness",
        "before_write",
        "checkpoint",
    }
)


def is_full(level: str) -> bool:
    """True only when the coordination layer has full Rust-backed capability."""
    return level == FULL


def can_write_breadcrumb(level: str) -> bool:
    """True when the facade may write capability-marked breadcrumb facts.

    Both ``full`` and ``degraded-breadcrumb`` may write breadcrumbs; only
    ``unavailable`` may not (it has no writable channel).
    """
    return level in (FULL, DEGRADED_BREADCRUMB)


def operation_allowed(level: str, operation: str) -> bool:
    """Return whether ``operation`` is permitted at ``level``.

    Full-only operations (claim/reclaim/lead/reap/liveness/before_write/…) are
    permitted ONLY at ``full``. Everything else (breadcrumb presence/handoff
    posts) is permitted at ``full`` and ``degraded-breadcrumb``, never at
    ``unavailable``.
    """
    if operation in FULL_ONLY_OPERATIONS:
        return level == FULL
    return can_write_breadcrumb(level)


def mark(envelope: dict[str, Any], level: str, reason: str | None = None) -> dict[str, Any]:
    """Stamp ``envelope`` with ``capability_level`` (+ ``coordination_unavailable``).

    Mutates and returns the same dict for call-site convenience. ``full`` forces
    ``coordination_unavailable: None``; any sub-full level keeps the supplied
    reason (defaulting to ``no_binary`` so the field is never silently empty).
    """
    envelope["capability_level"] = level
    if level == FULL:
        envelope["coordination_unavailable"] = None
    else:
        envelope["coordination_unavailable"] = reason or REASON_NO_BINARY
    return envelope


def unavailable_envelope(
    operation: str, reason: str = REASON_NO_BINARY, **extra: Any
) -> dict[str, Any]:
    """Build a loud ``capability_level: unavailable`` envelope for ``operation``.

    The canonical return when a full-only operation is requested but the Rust
    binary is not reachable. Carries ``ok: False`` + a human-readable ``detail``
    so a caller that does not branch on ``capability_level`` still sees a failure
    rather than a silent success.
    """
    env: dict[str, Any] = {
        "ok": False,
        "operation": operation,
        "detail": (
            f"coordination operation {operation!r} requires the Rust rally "
            f"binary, which is unavailable ({reason}); refusing to run a shadow "
            f"implementation"
        ),
    }
    env.update(extra)
    return mark(env, UNAVAILABLE, reason)


def full_capability_for_channel(channel_dir: Any, workdir: Any = None) -> bool:
    """True only when a full-capability Rust binary owns this channel.

    The single guard the destructive Python coordination paths
    (``presence.reap_stale``, ``leadership`` reclaim) call before taking a
    destructive action. Resolves capability via ``discovery_bridge``. FAIL-CLOSED:
    any resolution error or sub-full level returns ``False`` so a degraded session
    never reaps/reclaims. When ``workdir`` is not supplied it is derived from the
    channel dir (``.../.build-loop/<channel>`` → two levels up), matching the
    convention used by the presence/policy loaders.
    """
    from pathlib import Path as _Path

    try:  # local imports avoid a hard cycle at module load
        try:
            from .discovery_bridge import resolve as _resolve
        except ImportError:  # script-mode
            from discovery_bridge import resolve as _resolve  # type: ignore
    except ImportError:
        return False
    try:
        wd = _Path(workdir) if workdir is not None else _Path(channel_dir).parent.parent
        env = _resolve(wd)
        level = level_for_resolved_via(env.resolved_via, env.coordination_unavailable)
        return is_full(level)
    except Exception:  # noqa: BLE001 — fail-closed on any resolution error
        return False


def level_for_resolved_via(resolved_via: str, coordination_unavailable: str | None) -> str:
    """Map a ``discovery_bridge`` resolution to a capability level.

    * A native binary source (``rust-cli`` / ``repo-local-rally-cli`` /
      ``path-binary`` / ``python-import`` / ``env-override``) with no
      ``coordination_unavailable`` flag → ``full``.
    * ``incompatible_protocol`` → ``unavailable`` (loud; never breadcrumb).
    * The embedded fallback (``build-loop-internal``) or a ``degraded`` flag →
      ``degraded-breadcrumb`` (breadcrumb-only; never full).
    """
    if coordination_unavailable == REASON_INCOMPATIBLE_PROTOCOL:
        return UNAVAILABLE
    if coordination_unavailable == "incompatible_protocol":  # discovery alias
        return UNAVAILABLE
    if resolved_via == "build-loop-internal":
        return DEGRADED_BREADCRUMB
    if coordination_unavailable in ("degraded", REASON_NO_BINARY):
        return DEGRADED_BREADCRUMB
    return FULL
