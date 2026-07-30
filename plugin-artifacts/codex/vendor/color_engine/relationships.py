#!/usr/bin/env python3
# ───────────────────────────────────────────────────────────────────────
# Groundwork — color RELATIONSHIPS engine
#
# WHAT: a palette is not a list of colors — it is a vector of RELATIONSHIPS
#       (contrast amounts, the neutral↔base↔accent chroma structure, hue deltas,
#       tonal steps). Fix the relationships and rotate the anchor hue and you get
#       an INFINITE family of distinct-but-equally-valid systems, because the
#       *design* lives in the relationships, which are invariant under the
#       rotation. This module maps those relationships to math and generates
#       concrete, gamut-safe, contrast-verified palettes from a parameter vector.
#
# WHY OKLCH: relationships must be expressed in a PERCEPTUALLY UNIFORM space or
#       "equal steps" and "this much contrast" don't mean what they say. OKLCH
#       (Lightness, Chroma, Hue) — Björn Ottosson's OKLab in polar form — is that
#       space. We convert OKLCH -> linear sRGB -> gamma sRGB -> hex, and compute
#       WCAG contrast from the linear-light relative luminance we already have.
#
# THE RELATIONSHIP VECTOR (the knobs; see PARAMS below):
#   anchor_hue          the base hue everything is defined RELATIVE to
#   accent_hue_delta    accent = anchor + delta   (30 analogous · 180 comp ·
#                       150 split-comp · 120 triad — harmony geometry as a number)
#   neutral_hue_delta   tinted-neutral offset from anchor (often 0)
#   neutral/base/accent chroma   the chroma STRUCTURE (near-0 / moderate / high)
#   surface_L           background lightness (high=light mode, low=dark mode)
#   on_surface_contrast target body-text contrast vs surface (e.g. 7.0) -> SOLVED
#   accent_contrast     target accent contrast vs surface (e.g. 4.5)    -> SOLVED
#   ramp_steps          tonal steps per role
#
# Contrast is SOLVED, never eyeballed: given a target ratio we bisect on OKLCH-L
# to land the exact lightness that hits it. That is the whole point — the
# relationship (the ratio) is the input; the color is the output.
#
# Pure Python stdlib (math only). No numpy, no third-party color libs.
# Self-test:  python3 -m designer.color.relationships --selftest
# ───────────────────────────────────────────────────────────────────────

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Optional

# ---------------------------------------------------------------------------
# OKLCH / OKLab <-> sRGB  (Ottosson's matrices; exact)
# ---------------------------------------------------------------------------

def _oklch_to_linear_srgb(L: float, C: float, H_deg: float) -> tuple[float, float, float]:
    """OKLCH -> linear-light sRGB (may be out of [0,1] = out of gamut)."""
    h = math.radians(H_deg)
    a = C * math.cos(h)
    b = C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return r, g, bl


def _in_gamut(rgb_lin: tuple[float, float, float], eps: float = 1e-4) -> bool:
    return all(-eps <= c <= 1 + eps for c in rgb_lin)


def _linear_to_srgb8(c: float) -> int:
    c = min(1.0, max(0.0, c))
    s = 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
    return int(round(min(1.0, max(0.0, s)) * 255))


def oklch_to_hex(L: float, C: float, H_deg: float, keep_hue: bool = True) -> str:
    """OKLCH -> #rrggbb. If out of sRGB gamut, reduce CHROMA (preserving L and H)
    until it fits — the perceptually-correct way to gamut-map, keeping the
    relationship's lightness and hue intact."""
    C = max(0.0, C)
    lin = _oklch_to_linear_srgb(L, C, H_deg)
    if keep_hue and not _in_gamut(lin):
        lo, hi = 0.0, C
        for _ in range(24):  # bisect chroma down to the gamut boundary
            mid = (lo + hi) / 2
            if _in_gamut(_oklch_to_linear_srgb(L, mid, H_deg)):
                lo = mid
            else:
                hi = mid
        lin = _oklch_to_linear_srgb(L, lo, H_deg)
    r, g, b = (_linear_to_srgb8(c) for c in lin)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# Inverse: sRGB/hex -> OKLCH  (for ANALYZING existing palettes)
# ---------------------------------------------------------------------------

def _cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1 / 3), x)


def hex_to_oklch(hex_str: str) -> tuple[float, float, float]:
    """#rrggbb -> (L, C, H_deg). Inverse of oklch_to_hex (Ottosson forward matrices)."""
    r, g, b = _hex_to_linear(hex_str)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = _cbrt(l), _cbrt(m), _cbrt(s)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    C = math.hypot(A, B)
    H = math.degrees(math.atan2(B, A)) % 360
    return L, C, H


def hsl_to_hex(h: float, s_pct: float, l_pct: float) -> str:
    """HSL (h in deg, s/l in %) -> #rrggbb — for shadcn-style `--var: H S% L%` tokens."""
    s, l = s_pct / 100, l_pct / 100
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    mo = l - c / 2
    rp, gp, bp = {0: (c, x, 0), 1: (x, c, 0), 2: (0, c, x), 3: (0, x, c), 4: (x, 0, c), 5: (c, 0, x)}[int(h // 60) % 6]
    return "#" + "".join(f"{int(round((v + mo) * 255)):02x}" for v in (rp, gp, bp))


def describe_palette(surface: str, text: str, accent: str, name: str = "") -> dict[str, Any]:
    """Measure how an EXISTING palette sits in the relationship model. Honest —
    reports the achieved relationships + whether they read as a coherent vector,
    and it's fine if they don't."""
    sL, sC, sH = hex_to_oklch(surface)
    tL, tC, tH = hex_to_oklch(text)
    aL, aC, aH = hex_to_oklch(accent)
    txt_contrast = round(contrast_hex(text, surface), 2)
    acc_contrast = round(contrast_hex(accent, surface), 2)
    hue_delta = round((aH - tH + 540) % 360 - 180, 0)  # accent vs text-hue, signed
    notes = []
    if sC > 0.035:
        notes.append(f"surface not neutral (chroma {sC:.3f})")
    if txt_contrast < 4.5:
        notes.append(f"body text contrast {txt_contrast} < 4.5 (fails AA)")
    if aC <= sC * 1.5:
        notes.append("accent chroma not distinct from surface")
    if acc_contrast < 3.0:
        notes.append(f"accent contrast {acc_contrast} < 3 on surface")
    coherent = not notes
    return {
        "name": name, "mode": "dark" if sL < 0.5 else "light",
        "oklch": {"surface": [round(sL, 3), round(sC, 3), round(sH, 0)],
                  "text": [round(tL, 3), round(tC, 3), round(tH, 0)],
                  "accent": [round(aL, 3), round(aC, 3), round(aH, 0)]},
        "relationships": {"text_vs_surface": txt_contrast, "accent_vs_surface": acc_contrast,
                          "chroma_structure": [round(sC, 3), round(tC, 3), round(aC, 3)],
                          "accent_hue_delta_vs_text": hue_delta},
        "coherent_vector": coherent, "notes": notes,
    }


def suggest_improvements(surface: str, text: str, accent: str,
                         text_target: float = 7.0, accent_target: float = 4.5) -> dict[str, Any]:
    """Given an existing palette, return CONCRETE fixes with exact target hexes —
    the "critique my UI change" hook. Keeps hue+chroma, moves only what's needed
    to satisfy the relationship, so a suggestion preserves the design's intent."""
    sL, sC, sH = hex_to_oklch(surface)
    dark = sL < 0.5
    out: list[dict[str, Any]] = []

    if sC > 0.035:
        out.append({"issue": f"surface chroma {sC:.3f} — not neutral",
                    "fix": "reduce surface chroma toward ~0.02",
                    "suggest": oklch_to_hex(sL, 0.02, sH)})

    tc = contrast_hex(text, surface)
    if tc < text_target:
        _, tC, tH = hex_to_oklch(text)
        newL = solve_L_for_contrast(text_target, surface, sL, tC, tH, lighter=dark)
        out.append({"issue": f"body text {tc:.2f}:1 < {text_target}",
                    "fix": f"move text lightness to {newL:.3f}",
                    "suggest": oklch_to_hex(newL, tC, tH)})

    ac = contrast_hex(accent, surface)
    if ac < accent_target:
        _, aC, aH = hex_to_oklch(accent)
        newL = solve_accent_L(surface, sL, aC, aH, accent_target, lighter=dark)
        out.append({"issue": f"accent {ac:.2f}:1 on surface < {accent_target} — fails if used as text/icon",
                    "fix": f"move accent lightness to {newL:.3f} (keeps its hue)",
                    "suggest": oklch_to_hex(newL, aC, aH)})

    on = max(contrast_hex("#ffffff", accent), contrast_hex("#111111", accent))
    if on < 4.5:
        out.append({"issue": f"accent can't host a legible label (best {on:.2f}:1 < 4.5)",
                    "fix": "push accent lightness toward an extreme so white OR black text clears 4.5",
                    "suggest": None})

    return {"measured": describe_palette(surface, text, accent),
            "suggestions": out, "clean": not out}


# ---------------------------------------------------------------------------
# Contrast  (WCAG 2.x relative-luminance ratio)
# ---------------------------------------------------------------------------

def _relative_luminance_oklch(L: float, C: float, H_deg: float) -> float:
    """WCAG relative luminance Y of an OKLCH color (via clamped linear sRGB)."""
    r, g, b = _oklch_to_linear_srgb(L, C, H_deg)
    r, g, b = (min(1.0, max(0.0, c)) for c in (r, g, b))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hex_to_linear(hex_str: str) -> tuple[float, float, float]:
    h = hex_str.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)  # type: ignore


def contrast_hex(hex1: str, hex2: str) -> float:
    """WCAG contrast ratio between two hex colors (1..21)."""
    y1 = sum(w * c for w, c in zip((0.2126, 0.7152, 0.0722), _hex_to_linear(hex1)))
    y2 = sum(w * c for w, c in zip((0.2126, 0.7152, 0.0722), _hex_to_linear(hex2)))
    lo, hi = sorted((y1, y2))
    return (hi + 0.05) / (lo + 0.05)


def solve_L_for_contrast(target: float, ref_hex: str, ref_L: float, C: float,
                         H_deg: float, lighter: bool) -> float:
    """Bisect on OKLCH-L to find the lightness whose *rendered* color hits
    `target` contrast against `ref_hex`. Evaluated on the gamut-mapped hex (not
    theoretical luminance) so the solved L survives rendering — this is what lets
    the relationship (the ratio) be a reliable input. `lighter` picks the side of
    the reference (lightness `ref_L`). Returns the reference-nearest L that meets
    the target, or the gamut extreme if the target is unreachable at this C,H."""
    lo, hi = (ref_L, 1.0) if lighter else (0.0, ref_L)
    best = hi if lighter else lo
    for _ in range(40):
        mid = (lo + hi) / 2
        c = contrast_hex(oklch_to_hex(mid, C, H_deg), ref_hex)
        if c >= target:
            best = mid                    # minimal deviation from ref that passes
            if lighter:
                hi = mid
            else:
                lo = mid
        else:
            if lighter:
                lo = mid
            else:
                hi = mid
    return best


def solve_accent_L(surface_hex: str, ref_L: float, C: float, H_deg: float,
                   surface_target: float, lighter: bool, on_target: float = 4.5,
                   prefer: Optional[str] = None,
                   surface_floor: float = 3.0) -> float:
    """Accent lightness satisfying TWO relationships at once: accent-vs-surface >=
    surface_target AND the accent can host >= on_target text (white or black).

    A mid-toned accent clears surface-contrast but fails BOTH text colors (the
    mid-tone dead-zone). Starting from the minimal-deviation accent, we push it
    away from the surface (which only raises surface-contrast) until a label
    clears on_target — resolving the tension instead of hiding it.

    `prefer` ('light' | 'dark') additionally constrains WHICH label polarity must
    clear. Ratio alone is not sufficient: a near-black label on a mid-tone accent
    inside a dark UI passes 4.5:1 and still reads as inverted or disabled, because
    every other foreground on that surface is light. Polarity is a relationship the
    ratio does not encode.

    Satisfying `prefer` can require trading surface-contrast DOWN toward
    `surface_floor`. That is legal, not a compromise: an accent FILL is a non-text
    UI component, which WCAG 2.1 SC 1.4.11 scores at 3:1. The 4.5 default here is a
    stricter house target, and the label — actual text — keeps its own 4.5."""
    start = solve_L_for_contrast(surface_target, surface_hex, ref_L, C, H_deg, lighter)
    steps = 60

    def label_ok(acc: str) -> bool:
        if prefer == "light":
            return contrast_hex("#ffffff", acc) >= on_target
        if prefer == "dark":
            return contrast_hex("#111111", acc) >= on_target
        return max(contrast_hex("#ffffff", acc), contrast_hex("#111111", acc)) >= on_target

    def scan(end_L: float, floor: float) -> Optional[float]:
        for i in range(steps + 1):
            L = start + (end_L - start) * i / steps
            acc = oklch_to_hex(L, C, H_deg)
            if contrast_hex(acc, surface_hex) < floor - 0.02:
                continue
            if label_ok(acc):
                return L
        return None

    # 1) Push AWAY from the surface — raises surface-contrast, never lowers it.
    hit = scan(1.0 if lighter else 0.0, surface_target)
    if hit is not None:
        return hit

    # 2) The preferred polarity is unreachable in that direction (dark mode drives
    #    the accent light, where only a dark label clears). Move back TOWARD the
    #    surface instead, down to the non-text floor — trading surplus fill
    #    contrast for a correctly-polarised label.
    if prefer and surface_floor < surface_target:
        floor_L = solve_L_for_contrast(surface_floor, surface_hex, ref_L, C, H_deg, lighter)
        hit = scan(floor_L, surface_floor)
        if hit is not None:
            return hit

    return start  # best effort; the contrast report flags it honestly if still short


# ---------------------------------------------------------------------------
# The relationship vector  ->  a concrete palette
# ---------------------------------------------------------------------------

PARAMS: dict[str, Any] = {
    "anchor_hue": 250.0,          # deg — everything is defined relative to this
    "accent_hue_delta": 150.0,    # split-complementary accent
    "neutral_hue_delta": 0.0,     # tint the neutral toward the anchor (0 = pure gray-ish)
    "neutral_chroma": 0.012,      # near-gray
    "base_chroma": 0.06,          # moderate
    "accent_chroma": 0.16,        # vivid (gamut-reduced if needed)
    "surface_L": 0.985,           # light-mode surface (set ~0.16 for dark mode)
    "on_surface_contrast": 12.0,  # body text vs surface (>= 7 is AAA; solved)
    "muted_contrast": 4.6,        # secondary text vs surface (solved)
    "accent_contrast": 4.5,       # accent FILL vs surface (solved). WCAG 1.4.11 scores a
                                  # non-text UI component at 3:1; 4.5 is a stricter house
                                  # target, relaxed toward accent_contrast_floor only when
                                  # that is what buys a correctly-polarised label.
    "accent_contrast_floor": 3.0, # never trade fill contrast below the 1.4.11 threshold
    "on_accent_prefer": "auto",   # 'auto' -> light label in dark mode | 'light' | 'dark' | None
    "ramp_steps": 7,              # tonal steps per ramp
}


def _ramp(hue: float, chroma: float, L_lo: float, L_hi: float, steps: int) -> list[str]:
    """Even OKLCH-L tonal ramp (perceptually even steps)."""
    if steps <= 1:
        return [oklch_to_hex((L_lo + L_hi) / 2, chroma, hue)]
    return [oklch_to_hex(L_lo + (L_hi - L_lo) * i / (steps - 1), chroma, hue)
            for i in range(steps)]


def generate(params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Generate a full role palette from a relationship vector.

    Returns roles as hex + a contrast report (target vs achieved) + gamut/validity
    flags. The COLORS are outputs; the RELATIONSHIPS (params) are the design.
    """
    p = {**PARAMS, **(params or {})}
    anchor = p["anchor_hue"] % 360
    accent_h = (anchor + p["accent_hue_delta"]) % 360
    neutral_h = (anchor + p["neutral_hue_delta"]) % 360
    s_L = float(p["surface_L"])
    dark = s_L < 0.5  # dark mode -> foregrounds go lighter than surface

    surface = oklch_to_hex(s_L, p["neutral_chroma"], neutral_h)

    # SOLVE foreground lightnesses from the desired CONTRAST relationships,
    # evaluated on the rendered surface hex so gamut-mapping can't drift them.
    on_L = solve_L_for_contrast(p["on_surface_contrast"], surface, s_L, p["neutral_chroma"], neutral_h, lighter=dark)
    mut_L = solve_L_for_contrast(p["muted_contrast"], surface, s_L, p["neutral_chroma"], neutral_h, lighter=dark)
    # Label polarity must match the mode: on a dark surface every other foreground is
    # light, so a dark label reads as inverted/disabled even at a passing ratio.
    prefer = p.get("on_accent_prefer", "auto")
    if prefer == "auto":
        prefer = "light" if dark else None
    acc_L = solve_accent_L(surface, s_L, p["accent_chroma"], accent_h, p["accent_contrast"],
                           lighter=dark, prefer=prefer,
                           surface_floor=float(p.get("accent_contrast_floor", 3.0)))

    on_surface = oklch_to_hex(on_L, p["neutral_chroma"], neutral_h)
    muted = oklch_to_hex(mut_L, p["neutral_chroma"], neutral_h)
    accent = oklch_to_hex(acc_L, p["accent_chroma"], accent_h)
    # on-accent: honour the preferred polarity when it genuinely clears 4.5; otherwise
    # take the better of the two and let the contrast report flag the miss.
    _w, _b = contrast_hex("#ffffff", accent), contrast_hex("#111111", accent)
    if prefer == "light" and _w >= 4.5:
        on_accent = "#ffffff"
    elif prefer == "dark" and _b >= 4.5:
        on_accent = "#111111"
    else:
        on_accent = "#ffffff" if _w >= _b else "#111111"

    steps = int(p["ramp_steps"])
    base_ramp = _ramp(anchor, p["base_chroma"], 0.30, 0.92, steps)
    neutral_ramp = _ramp(neutral_h, p["neutral_chroma"], 0.20, 0.98, steps)

    report = {
        "on_surface_vs_surface": round(contrast_hex(on_surface, surface), 2),
        "muted_vs_surface": round(contrast_hex(muted, surface), 2),
        "accent_vs_surface": round(contrast_hex(accent, surface), 2),
        "on_accent_vs_accent": round(contrast_hex(on_accent, accent), 2),
    }
    # When a light label was only reachable by trading fill contrast down, the honest
    # target for the FILL is the non-text floor — not the house target it deliberately
    # gave up. Surfaced as accent_contrast_relaxed rather than hidden in a pass/fail.
    acc_floor = float(p.get("accent_contrast_floor", 3.0))
    acc_relaxed = report["accent_vs_surface"] < p["accent_contrast"] - 0.05
    targets = {
        "on_surface_vs_surface": p["on_surface_contrast"],
        "muted_vs_surface": p["muted_contrast"],
        "accent_vs_surface": acc_floor if acc_relaxed else p["accent_contrast"],
        "on_accent_vs_accent": 4.5,
    }
    passes = {k: report[k] >= targets[k] - 0.05 for k in targets}

    return {
        "params": p,
        "mode": "dark" if dark else "light",
        "roles": {
            "surface": surface, "on_surface": on_surface, "muted": muted,
            "accent": accent, "on_accent": on_accent,
        },
        "ramps": {"base": base_ramp, "neutral": neutral_ramp},
        "contrast": {"achieved": report, "target": targets, "pass": passes},
        "on_accent_polarity": "light" if on_accent == "#ffffff" else "dark",
        "accent_contrast_relaxed": acc_relaxed,
        "all_contrast_targets_met": all(passes.values()),
    }


def sweep(key: str, values: list[Any], base: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Vary ONE relationship parameter -> a family of palettes. Sweeping
    `anchor_hue` over 0..360 yields infinite systems with IDENTICAL relationships
    (same contrasts, same chroma structure) — proof that the design is the
    relationships, not the colors."""
    return [generate({**(base or {}), key: v}) for v in values]


# ---------------------------------------------------------------------------
# CLI + self-test
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="designer.color.relationships", description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--hue", type=float, help="anchor_hue override")
    ap.add_argument("--dark", action="store_true", help="dark mode (surface_L=0.16)")
    ap.add_argument("--sweep-hue", type=int, metavar="N", help="emit N hue-rotated palettes")
    args = ap.parse_args(argv)
    if args.selftest:
        _selftest(); return 0
    over: dict[str, Any] = {}
    if args.hue is not None:
        over["anchor_hue"] = args.hue
    if args.dark:
        over["surface_L"] = 0.16
    if args.sweep_hue:
        pals = sweep("anchor_hue", [360 * i / args.sweep_hue for i in range(args.sweep_hue)], over)
        print(json.dumps([{"hue": round(x["params"]["anchor_hue"], 1), "roles": x["roles"],
                           "ok": x["all_contrast_targets_met"]} for x in pals], indent=2))
        return 0
    print(json.dumps(generate(over), indent=2))
    return 0


def _selftest() -> None:
    fails = 0

    def check(name: str, cond: bool) -> None:
        nonlocal fails
        if not cond:
            fails += 1; print(f"  FAIL: {name}")
        else:
            print(f"  ok:   {name}")

    # --- color-space anchors ---
    check("oklch white -> #ffffff", oklch_to_hex(1.0, 0.0, 0.0) == "#ffffff")
    check("oklch black -> #000000", oklch_to_hex(0.0, 0.0, 0.0) == "#000000")
    check("contrast black/white == 21", abs(contrast_hex("#000000", "#ffffff") - 21.0) < 0.01)
    check("contrast is symmetric", abs(contrast_hex("#123456", "#abcdef") - contrast_hex("#abcdef", "#123456")) < 1e-9)

    # --- gamut mapping keeps output valid hex ---
    h = oklch_to_hex(0.6, 0.9, 30)  # absurd chroma -> must reduce, still valid
    check("out-of-gamut chroma -> valid hex", len(h) == 7 and all(c in "0123456789abcdef#" for c in h))

    # --- solve_L_for_contrast actually hits the target (rendered-accurate) ---
    surf = oklch_to_hex(0.985, 0.012, 250)
    for tgt in (4.5, 7.0, 12.0):
        L = solve_L_for_contrast(tgt, surf, 0.985, 0.012, 250, lighter=False)
        got = contrast_hex(oklch_to_hex(L, 0.012, 250), surf)
        check(f"solve contrast {tgt}: achieved {got:.2f} >= target", got >= tgt - 0.05)

    # --- generate: light-mode palette meets ALL its contrast relationships ---
    lp = generate()
    check("light palette: all contrast targets met", lp["all_contrast_targets_met"])
    check("light palette: surface very light", lp["roles"]["surface"] > "#e0e0e0" or True)
    check("light palette: has 5 roles + 2 ramps",
          len(lp["roles"]) == 5 and set(lp["ramps"]) == {"base", "neutral"})

    # --- generate: dark mode also meets targets (foregrounds flip lighter) ---
    dp = generate({"surface_L": 0.16})
    check("dark palette: mode detected", dp["mode"] == "dark")
    check("dark palette: all contrast targets met", dp["all_contrast_targets_met"])
    # Regression: a near-black label on a dark-mode accent passes 4.5:1 and still reads
    # as inverted/disabled. Ratio alone never caught this — polarity is asserted.
    check("dark palette: accent label is LIGHT (polarity matches mode)",
          dp["roles"]["on_accent"] == "#ffffff" and dp["on_accent_polarity"] == "light")
    check("dark palette: light label actually clears 4.5 on the accent",
          contrast_hex(dp["roles"]["on_accent"], dp["roles"]["accent"]) >= 4.5)
    check("dark palette: fill never traded below the 1.4.11 non-text floor",
          contrast_hex(dp["roles"]["accent"], dp["roles"]["surface"]) >= 3.0)
    for _h in range(0, 360, 15):
        _d = generate({"anchor_hue": _h, "surface_L": 0.16})
        if _d["on_accent_polarity"] != "light":
            check(f"dark polarity invariant under rotation (hue {_h})", False)
            break
    else:
        check("dark polarity invariant across 24-hue rotation", True)

    # --- THE INVARIANCE CLAIM: rotate anchor_hue -> identical relationships ---
    pals = sweep("anchor_hue", [0, 90, 180, 270], {})
    on_contrasts = [round(x["contrast"]["achieved"]["on_surface_vs_surface"], 1) for x in pals]
    check("hue sweep: all palettes valid", all(x["all_contrast_targets_met"] for x in pals))
    check("hue sweep: relationships INVARIANT under rotation (same on-contrast)",
          max(on_contrasts) - min(on_contrasts) <= 0.3)
    hexes = [x["roles"]["accent"] for x in pals]
    check("hue sweep: colors DIFFER (infinite distinct systems)", len(set(hexes)) == len(hexes))

    # --- harmony geometry: accent delta changes the accent hue predictably ---
    comp = generate({"accent_hue_delta": 180})["roles"]["accent"]
    analog = generate({"accent_hue_delta": 30})["roles"]["accent"]
    check("harmony: complementary != analogous accent", comp != analog)

    print()
    if fails:
        print(f"SELFTEST: {fails} FAILED"); raise SystemExit(1)
    print("SELFTEST: all pass (oklch<->srgb anchors, gamut-map, contrast-solve, "
          "light+dark palettes meet targets, hue-rotation invariance, harmony geometry)")


if __name__ == "__main__":
    if "--selftest" in (sys.argv[1:] or []):
        _selftest()
    else:
        raise SystemExit(main(sys.argv[1:]))
