"""Simplified color engine — the small surface an AI system actually calls.

The full generator (`_core.py`, vendored from groundwork) exposes ~20 knobs.
An agent mid-task needs three answers:

    palette(...)  "give me a valid color system"
    check(...)    "is this pair readable?"
    fix(...)      "make this pair readable"

Everything else stays reachable via `_core` for power use. This module adds no
new color math — it only chooses sane defaults and names the common intents, so
a caller never has to understand OKLCH to get an accessible result.

Design invariants inherited from the core (do not break them):
  * Colors are OUTPUTS; the relationships (contrast targets, chroma structure)
    are the design. Rotating the anchor hue yields an infinite family of
    equally-valid systems — verified by the core's self-test.
  * Contrast is SOLVED (bisection on OKLCH lightness), never eyeballed.

Pure stdlib. Zero dependencies. Copy this directory into any consumer.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _core  # noqa: E402  (vendored math; path set above)

__all__ = ["palette", "check", "fix", "PROFILES", "register_profile"]


# ---------------------------------------------------------------------------
# Profiles — the "customized as needed" layer.
#
# A profile is a named set of RELATIONSHIP defaults, not a set of colors. Each
# consumer registers its own house style once; callers then ask for a palette by
# intent and get something already on-brand. Adding a profile never forks the
# engine.
# ---------------------------------------------------------------------------
PROFILES: dict[str, dict[str, Any]] = {
    # Balanced default: WCAG AA everywhere, moderate chroma.
    "default": {},
    # Dark-first, deep glass. Mirrors the Aurora Deep direction: near-black
    # surface with a blue undertone, restrained neutral chroma, vivid accent.
    "aurora-deep": {
        "surface_L": 0.13,
        "neutral_chroma": 0.014,
        "accent_chroma": 0.17,
        "on_surface_contrast": 13.0,
        "muted_contrast": 4.6,
    },
    # Long-form reading: softer than max contrast, which is harsh over minutes.
    "reading": {
        "surface_L": 0.985,
        "on_surface_contrast": 13.0,
        "muted_contrast": 4.6,
        "accent_chroma": 0.13,
    },
    # Accessibility-strict: AAA body text.
    "wcag-aaa": {
        "on_surface_contrast": 7.5,
        "muted_contrast": 7.0,
        "accent_contrast": 7.0,
    },
}


def register_profile(name: str, params: dict[str, Any]) -> None:
    """Register a consumer's house style. Call once at import time."""
    PROFILES[name] = dict(params)


# ---------------------------------------------------------------------------
# The three calls
# ---------------------------------------------------------------------------
def palette(
    hue: float = 250.0,
    *,
    dark: Optional[bool] = None,
    profile: str = "default",
    harmony: str = "complementary",
    **overrides: Any,
) -> dict[str, Any]:
    """Generate a complete, contrast-valid role palette.

    hue      anchor hue 0-360. Rotating it gives a different but equally valid
             system — the relationships are invariant.
    dark     True/False to force mode; None keeps the profile's surface.
    profile  a key of PROFILES (see register_profile for your own).
    harmony  complementary | analogous | triadic | split — sets accent geometry.

    Returns {params, mode, roles{surface,on_surface,muted,accent,on_accent},
             ramps, contrast report, valid flags}. `roles` is what most callers
    want; the contrast report is there so a caller can PROVE accessibility
    rather than assert it.
    """
    geometry = {
        "complementary": 180.0,
        "analogous": 30.0,
        "triadic": 120.0,
        "split": 150.0,
    }
    if harmony not in geometry:
        raise ValueError(
            f"unknown harmony {harmony!r}; expected one of {sorted(geometry)}"
        )

    params: dict[str, Any] = {
        **PROFILES.get(profile, {}),
        "anchor_hue": float(hue) % 360,
        "accent_hue_delta": geometry[harmony],
    }
    if dark is not None:
        # Only override the surface when the caller actually asked; otherwise a
        # profile's own surface_L (e.g. aurora-deep) must win.
        params["surface_L"] = 0.13 if dark else 0.985
    params.update(overrides)

    return _core.generate(params)


def check(foreground: str, background: str, *, target: float = 4.5) -> dict[str, Any]:
    """Is this pair readable? Returns the ratio, the target, and a pass flag.

    target 4.5 = WCAG AA body, 3.0 = AA large text / UI, 7.0 = AAA.
    """
    ratio = _core.contrast_hex(foreground, background)
    return {
        "foreground": foreground,
        "background": background,
        "ratio": round(ratio, 2),
        "target": target,
        "passes": ratio >= target,
    }


def fix(foreground: str, background: str, *, target: float = 4.5) -> dict[str, Any]:
    """Return a corrected foreground that MEETS the target on this background.

    Preserves hue and chroma — only lightness moves, so the result still reads
    as the same color rather than being replaced. Already-passing input is
    returned unchanged (never churn a color that was fine).
    """
    before = check(foreground, background, target=target)
    if before["passes"]:
        return {**before, "fixed": foreground, "changed": False}

    L, C, H = _core.hex_to_oklch(foreground)
    bg_L, _, _ = _core.hex_to_oklch(background)
    lighter = bg_L < 0.5  # on a dark ground, the fix must go lighter

    solved_L = _core.solve_L_for_contrast(target, background, bg_L, C, H, lighter=lighter)
    fixed = _core.oklch_to_hex(solved_L, C, H)
    after = check(fixed, background, target=target)

    return {
        **before,
        "fixed": fixed,
        "changed": True,
        "ratio_after": after["ratio"],
        # A gamut-clipped color can fall short of an extreme target; report it
        # rather than silently returning something that still fails.
        "passes_after": after["passes"],
    }


if __name__ == "__main__":  # tiny smoke check
    p = palette(hue=250, profile="aurora-deep")
    print("roles:", p["roles"], "\nmode:", p["mode"], "valid:", p.get("valid"))
    print("check:", check("#6d7379", "#f6fbff"))
    print("fix:  ", fix("#999999", "#ffffff"))
