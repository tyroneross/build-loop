---
name: color-engine
description: Generate accessible color systems and solve contrast, instead of guessing hex values. Use when picking or fixing colors for any UI, chart, diagram, doc, or artifact — "what colors should this use", "is this readable", "this text fails contrast", "make a dark theme", "pick an accent". Also use before shipping any palette, to PROVE contrast rather than assert it. NOT for choosing layout, typography, or component structure.
user-invocable: false
---

# Color Engine

Colors are **outputs**. The design is the set of **relationships** — contrast
targets, chroma structure, harmony geometry. Fix the relationships and rotate
the anchor hue and you get an infinite family of distinct-but-equally-valid
systems, because the design lives in what stays invariant.

Practical consequence: **never hand-pick hex values and hope.** State the
relationship you want; let the engine solve for the color that satisfies it.

## When to reach for this

- Choosing colors for a UI, chart, artifact, deck, or diagram.
- Any "is this readable / does this pass contrast" question.
- A design review where a palette needs to be *proven* accessible.
- Building a dark theme, or checking that an existing one survives dark mode.

Skip it when the project already has a committed design system — apply that
system instead. Its tokens win; use this only to fill gaps or verify.

## The three calls

```python
from color_engine import palette, check, fix

# 1. Give me a valid color system.
p = palette(hue=250, profile="aurora-deep")
p["roles"]   # {surface, on_surface, muted, accent, on_accent} — all contrast-solved
p["ramps"]   # tonal ramps for surfaces/borders

# 2. Is this pair readable?
check("#6d7379", "#f6fbff")            # -> ratio 4.6, passes True
check("#888", "#fff", target=7.0)      # AAA

# 3. Make this pair readable.
fix("#999999", "#ffffff")              # -> "#777676", 2.85 -> 4.53
```

`fix` moves **lightness only**, preserving hue and chroma, so the result still
reads as the same color rather than being swapped for a different one. An
already-passing pair is returned unchanged — it never churns a color that was
fine. Check `passes_after`: an extreme target on a clipped gamut can still fall
short, and the engine reports that instead of hiding it.

## Customization — profiles, not forks

A profile is a named set of **relationship defaults**, not a set of colors.
Register your house style once; callers then ask by intent and get on-brand
results. Adding a profile never forks the engine.

```python
from color_engine import register_profile
register_profile("my-app", {
    "surface_L": 0.13,          # dark ground
    "accent_chroma": 0.17,      # vivid accent
    "on_surface_contrast": 13.0,
    "muted_contrast": 4.6,      # keep metadata legible, not invisible
})
palette(hue=210, profile="my-app")
```

Built in: `default` · `aurora-deep` (dark glass) · `reading` (long-form, softer
than max contrast because 21:1 is harsh over minutes) · `wcag-aaa`.

Harmony geometry: `complementary` · `analogous` · `triadic` · `split`.

## Contrast targets

| Target | Use |
|---|---|
| 3.0 | Large text, UI boundaries, icons |
| 4.5 | Body text (WCAG AA) — the default |
| 7.0 | AAA body text |

Deliberately reduce contrast for metadata and disabled states to build
hierarchy, but keep it **above 3.0** so it reads as de-emphasized rather than
broken. Dark mode needs its own audit: a pair that passes on white can fail on
near-black.

## Installing in another project

Copy this directory. It is **pure stdlib with zero dependencies** — no install
step, no lockfile, no version conflict. `_core.py` is vendored from
`groundwork/designer/color/relationships.py` and carries a `source_sha256`
header; re-hash the source to detect drift. Do not hand-edit `_core.py` —
extend through `color_engine.py` or a profile.

Verify after copying:

```bash
python3 -c "import _core; _core._selftest()"   # math anchors + invariance
python3 color_engine.py                        # the three calls
```

The self-test checks the property that matters: hue-rotated palettes hold every
contrast target in **both** light and dark, so the generator spans the space
instead of enumerating points in it.
