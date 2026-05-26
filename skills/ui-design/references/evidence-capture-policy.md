# Evidence Capture Policy

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

How to gather design evidence from owned apps. Routes a build-loop dispatch to the right collection method based on what's being asked.

## Two evidence types, two methods

### 1. Token extraction — source-of-truth read is canonical

For numbers, role names, palette values, breakpoints, type ladders, spacing units, and any other tokenized design primitive:

- Read the source-of-truth file directly: `Theme.swift`, `globals.css`, `tailwind.config.{ts,js}`, `tokens.json`, `design-tokens/*.scss`, equivalent.
- This is cheaper, more accurate, version-locked to the commit, and immune to render-time variation (theme overrides, accessibility scaling, dynamic type).
- A screenshot of a rendered button cannot tell you whether its corner radius is 8pt or 9pt; the token file can.

### 2. Interaction texture — live IBR capture is mandatory

For motion timing, haptic feedback intensity, real-render gradients / shadows / blur, hit-target responsiveness, scroll feel, transition mid-states, and any moment where the rendered output may diverge from what the token file predicts:

- Boot the simulator / start the dev server / open the binary.
- Capture screens via IBR (`ibr:native-testing`, `ibr:screenshot`) or simctl + idb for iOS, or browser screenshot for web.
- Augment (do not replace) the source-read evidence with observed render behavior.
- Source files cannot show: a glow shadow that renders subtler than its parameters predict; a haptic vocabulary layer above the visual tokens; a selection state that uses border+glyph instead of fill; an empty-state ladder pattern that has no token representation.

## Decision rule for build-loop dispatches

When a build's evidence requirement is:

| Question | Method |
|---|---|
| "What are the tokens?" | Source-read only |
| "How does it feel?" | Live capture only |
| Both | Both, with explicit chunks per evidence type in the plan |

Default: when in doubt, do both. Source-read is cheap; live capture surfaces what source-read structurally cannot.

## Failure protocol

Live capture failures are non-blocking. Record verbatim in the evidence file:

- The specific error string (`xcodebuild` output, `npm run dev` stderr, `idb` failure).
- What was attempted (commands run, env probed).
- What would unblock (missing env var, missing Postgres, missing sim).
- The capture date of any pre-existing IBR artifacts used as fallback live-render evidence (with a freshness window — typically ≤30 days under no-functional-changes conditions).

A documented failure block is acceptable evidence. The synthesis layer must not silently treat missing live capture as "no observation."

## Cross-reference

This policy supersedes the Phase 6 Learn entry from the prior universal-design enrichment run, which reported a "scan-attempt vs source-read tradeoff" as if they were substitutes. They are not substitutes — they are complementary, and a build-loop dispatch must choose explicitly per evidence type. The prior recurring-pattern entry should be re-tagged: "source-read is canonical for tokens, live capture is mandatory for texture; do not collapse one into the other."

When this policy and another reference disagree about evidence sourcing, this policy wins for token-vs-texture routing.
