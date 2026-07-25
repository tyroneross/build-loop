# Colour: use the engine, don't pick hexes

Short version: **choosing a palette by choosing colours is the wrong move.** Groundwork
owns a colour engine that derives palettes from a *relationship vector* and verifies every
role's contrast mathematically. Build-loop routes colour decisions there instead of
duplicating the theory. This file is the pointer plus the handful of rules that matter
when you are mid-build and don't want to leave the loop.

Canonical docs: `~/dev/git-folder/groundwork/designer/color/README.md`
Engine: `designer/color/relationships.py` · Elicitation: `designer/decide/color_dimensions.py`

## The one idea

A palette is a vector of relationships — contrast targets, chroma structure, hue deltas —
not a list of colours. Fix the relationships, rotate the anchor hue, and you get an
infinite family of equally valid systems. `preview.py --sweep-hue 12` demonstrates it:
same vector, twelve hues, all passing.

**So the hue is the least load-bearing decision in the palette.** What carries the design:
one accent, two stops (light + dark), contrast verified per role, status hues untouched.
If a build is stuck arguing indigo vs teal, that argument is not the blocker.

## When to reach for it

- Any new product surface that needs a palette.
- Any time you are about to type a hex into a component, mockup, or token file.
- Any inherited palette you have not verified (mode briefs, design docs, an existing app).

## The 60-second path

```bash
GW=~/dev/git-folder/groundwork

# Verify a palette you already have — returns solved replacements, same hue + chroma
PYTHONPATH=$GW python3 -m designer.color.combos ingest \
  --name <slug> --surface '#0b0b0f' --text '#f4f4f5' --accent '#818cf8'

# Or generate: both twins from one vector (light 0.985 / dark 0.16)
PYTHONPATH=$GW python3 -m designer.color.preview --sweep-hue 6 \
  --params '{"energy":"balanced","contrast_feel":"crisp","accent_intensity":"clear",
             "harmony":"analogous","anchor_hue":277,"surface_L":0.16}' > /tmp/p.html
```

Use the returned `surface / on_surface / muted / accent / on_accent` roles verbatim.

## Rules worth memorising

1. **One accent.** Status hues are reserved — success/green, warning/amber, error/red. An
   accent that collides with one destroys the signal: if the brand accent *is* the success
   green, "success" stops meaning anything. Check the status set before choosing an anchor.
2. **Two stops, always.** An accent can clear AA on near-black and fail badly on white.
   Solve both twins from the same vector; never invert one and assume.
3. **`clipped: true` is not a choice.** It means sRGB cannot render that chroma, so it will
   render duller than the brief promises. Lower `energy` or `accent_intensity` instead.
4. **The accent is the anchor rotated by the harmony delta**, not the anchor. Default
   split-complementary turns a cool 250° anchor into an orange accent. Want the accent in
   the anchor family? `harmony: analogous`.
5. **Colour encodes state, not decoration.** An accent-coloured kicker or eyebrow carries
   no meaning — drop it. Recorded owner preference, not a style opinion.
6. **Never claim a preference that wasn't elicited.** If `designer.decide.profile show`
   is empty, there is no recorded colour taste — say so rather than inferring one.
7. **Label polarity matches the mode.** `on_accent_prefer` defaults to `auto` → a light
   label on a dark surface. Left to ratio alone the solver picks a near-black label for a
   dark-mode accent: it passes 4.5:1 and reads as inverted, because every other foreground
   on that surface is light. Buying the right polarity may trade fill contrast down to
   `accent_contrast_floor` (3.0) — legal, since a fill is a non-text component under WCAG
   1.4.11. The result flags `accent_contrast_relaxed: true`.
8. **Look at the render, not only the report.** Contrast is a scalar and scores
   white-on-indigo and black-on-indigo identically, so `all_contrast_targets_met: true` is
   a floor, not a verdict. Measure every ratio you show with `contrast_hex()` against the
   hexes actually rendered — never copy a row between options, never round from memory.

## Feeding what you learn back

Palette decisions made during a build should not evaporate when the run ends:

```bash
# Record an accepted system (or a new relationship pattern you invented)
PYTHONPATH=$GW python3 -m designer.color.combos add --name <slug> \
  --params '<vector>' --intent "<where it fits>" --source mine

# Fold elicited dimensions into the cross-project taste profile
PYTHONPATH=$GW python3 -m designer.decide.profile record-dims '{"contrast_feel":"crisp"}'
```

This is the mechanism that lets the system accumulate favourites and new vector
arrangements over time. Skipping it is why the profile stays empty and every project
re-asks settled questions.

## Known trap

The flow that most often breaks the rule is **mockup authoring**, because typing a hex
into HTML feels like design work. It isn't — it's guessing with extra steps, and a mode
brief's hexes were authored for *that brief's* context, not the surface you're building.
Validate or regenerate before use.
