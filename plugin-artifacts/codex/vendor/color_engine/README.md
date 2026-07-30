# The colour engine

> A palette is not a list of colours. It is a **vector of relationships** — contrast
> targets, the neutral↔base↔accent chroma structure, hue deltas, tonal steps. Fix the
> relationships and rotate the anchor hue and you get an infinite family of
> distinct-but-equally-valid systems.

This is the core claim, and it has a consequence most colour discussions miss: **the hue
is a free variable.** `preview.py --sweep-hue 12` renders the proof — one vector, twelve
hues, every one hitting the same contrast targets. When someone argues indigo vs teal
they are usually arguing about the least load-bearing part of the system.

What *is* load-bearing: one accent, two stops (light + dark), every role contrast-verified,
and status hues left alone.

## Why the engine instead of picking hexes

Three things it guarantees that no amount of taste reproduces:

1. **Contrast is solved, never eyeballed.** Given a target ratio it bisects on OKLCH-L to
   land the exact lightness that hits it — measured against the *rendered* hex, so
   gamut-mapping cannot silently drift a value below AA after you approve it.
2. **Gamut safety.** Out-of-range colours reduce *chroma only*, preserving lightness and
   hue. A `clipped: true` flag means sRGB cannot render the chroma you asked for — such an
   option must never be offered as a real choice, because it will render duller than
   promised. Lower `energy` or `accent_intensity` instead of accepting the drift.
3. **The mid-tone dead-zone.** `solve_accent_L` pushes an accent out of the band where it
   can clear its surface target but host no legible label. Hand-picked accents land here
   constantly — it is why so many buttons have unreadable text.

## The vector

| Dimension | Values → effect |
|---|---|
| `temperature` | cool 250° · fresh 160° · warm 40° · bold 330° · neutral 250° low-chroma → `anchor_hue` |
| `mode` | light 0.985 · dim 0.22 · dark 0.16 → `surface_L` |
| `energy` | calm · balanced · vivid → base/accent chroma |
| `contrast_feel` | soft 8.5 · standard 12 · crisp 15.5 → on-surface contrast |
| `harmony` | analogous 35° · split-complementary 150° · complementary 180° · triadic 120° → `accent_hue_delta` |
| `accent_intensity` | subtle 3.2 · clear 4.5 · bold 6.0 → accent contrast |

Out: five roles (`surface`, `on_surface`, `muted`, `accent`, `on_accent`), two tonal ramps,
and a contrast report of target vs achieved vs pass.

⚠️ **The accent is the anchor rotated by the harmony delta**, not the anchor itself. With
the default split-complementary, a "cool" (250°) anchor yields an *orange* accent. If you
want the accent to sit in the anchor family, set `harmony: analogous`. This surprises
almost everyone the first time.

## Using it

```bash
GW=~/dev/git-folder/groundwork

# Elicit — ask the highest-information-gain question, show real swatches
PYTHONPATH=$GW python3 -m designer.decide.color_dimensions init \
  --goal "<what this product is>" --mode dark \
  --decided '{"contrast_feel":"crisp"}' --out /tmp/c.json
PYTHONPATH=$GW python3 -m designer.decide.color_dimensions ask    --session /tmp/c.json
PYTHONPATH=$GW python3 -m designer.decide.color_dimensions answer --session /tmp/c.json \
  --dim temperature --value cool --decided
PYTHONPATH=$GW python3 -m designer.decide.color_dimensions emit   --session /tmp/c.json > /tmp/r.json

# Render the family (and both twins: surface_L 0.985 light / 0.16 dark)
PYTHONPATH=$GW python3 -m designer.color.preview --params-file /tmp/r.json --sweep-hue 12 > /tmp/p.html

# Audit a palette you already have — returns solved replacements, same hue + chroma
PYTHONPATH=$GW python3 -m designer.color.combos ingest \
  --name my-app --surface '#0b0b0f' --text '#f4f4f5' --accent '#818cf8'
```

Present the swatches, not the words: the option previews exist so a person chooses from
colour, not from adjectives.

## Keeping it adjustable — the learning loop

The engine is deliberately not a fixed palette. Two registries let it accumulate taste and
new structure over time, and both are meant to be written to:

**`combos.jsonl` — the palette registry.** Every accepted system gets recorded, so
favourites become reusable and new *arrangements* enter the vocabulary rather than being
re-derived.

```bash
# Record a favourite (or a NEW relationship pattern you invented)
PYTHONPATH=$GW python3 -m designer.color.combos add \
  --name rosslabs-indigo-dark \
  --params '{"energy":"balanced","contrast_feel":"crisp","accent_intensity":"clear",
             "harmony":"analogous","anchor_hue":277,"surface_L":0.16}' \
  --intent "personal site, brief visits, first-class light+dark" --source mine

PYTHONPATH=$GW python3 -m designer.color.combos list
PYTHONPATH=$GW python3 -m designer.color.combos validate   # re-checks every row
```

A row carries `valid:false` when it fails its own targets — that is a feature. The one
light palette in the registry failed at 3.90:1 and kept its solved fix alongside it, which
is more useful than deleting the mistake.

**`decide/profile.py` — the cross-project taste layer.** Dimensions chosen in one session
warm-start the next, so the same questions stop being asked.

```bash
PYTHONPATH=$GW python3 -m designer.decide.profile record --session /tmp/c.json   # fold in a session
PYTHONPATH=$GW python3 -m designer.decide.profile record-dims '{"contrast_feel":"crisp"}'  # taste seen elsewhere
PYTHONPATH=$GW python3 -m designer.decide.profile seed --out /tmp/next.json       # warm-start
PYTHONPATH=$GW python3 -m designer.decide.profile show
```

**Record every real decision.** An empty profile means every session re-asks settled
questions and the engine never gets smarter. The profile is also the honest record of what
was actually elicited — do not infer a preference that was never chosen from swatches, and
do not treat a template example in a doc as evidence of taste.

**Adding a new dimension** (a relationship the vector cannot currently express): add it to
`PARAMS` in `relationships.py`, give it an entry in `color_dimensions.py` with an
`importance` weight and real `option_previews`, and add a self-test asserting the design
stays invariant under hue rotation. That invariance is the engine's contract; a dimension
that breaks it is a bug, not a feature.

## Rules that survive every project

- One accent. Status hues (success / warning / error) are reserved and can never be the
  brand accent — if the accent is the success green, "success" stops signalling anything.
- Two stops, always. An accent that clears AA on `#09090b` can fail badly on white; solve
  both twins from the same vector rather than inverting one.
- Colour encodes state, not decoration. An accent used as a kicker or eyebrow carries no
  meaning and should be dropped.
- Never present a `clipped` option as a choice.
