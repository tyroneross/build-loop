<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

> **DEPRECATION SHIM (one release cycle).** Canonical source migrated to the `ui-guidance` plugin → load via `Skill("ui-guidance:principles")`. This file remains in build-loop as a transition aid; future updates land in the plugin. *(Renamed from `universal-experience-principles` in ui-guidance v0.2.0.)*

# Universal Experience Principles

Cross-medium doctrine for any artifact that has to communicate, guide a person through an experience, or expose a changing process. Applies to app UI, writing, images, native screens, slides, documents, spreadsheets, reports, charts, version control, deploys, sync, automation, and design handoff. Build Loop is the runtime home; the principles are not Build Loop-specific.

Core rule: **medium changes the primitive; the principle stays stable.**

## 1. Universal Experience Model

Every artifact does the same six jobs. If a job is missing, the user has to do it themselves.

1. **Orient** — make where they are, why it matters, and the frame they are in obvious.
2. **Focus** — make the primary message, object, decision, or action obvious.
3. **Connect** — show how this piece relates to what came before and what comes next.
4. **Progress** — make movement through the story, task, workflow, or system visible.
5. **Recover** — when something fails, explain what happened, preserve context, give a next step.
6. **Resolve** — end with a conclusion, action, saved state, handoff, source, or intentional stop.

Continuity primitives differ by medium (transition phrase, breadcrumb, motion, visual path, branch state) but the requirement does not.

## 2. Non-Negotiables

These ten apply to every artifact produced or reviewed.

1. **Purpose before presentation.** Name user, job, context, time budget, decision, and output before choosing layout, style, or tool.
2. **Answer first.** The governing thought or primary state is visible early. Support comes after, not before.
3. **One primary focus.** Per viewport, slide, section, page, screen, table, or chart. Competing primaries are a design failure.
4. **Wayfinding is mandatory.** The user knows where they are, what changed, what's next, how to get back. Landmarks stay consistent across the artifact; there are no dead ends.
5. **Process visibility.** Anything that changes in stages exposes purpose, start state, target end state, current step, checkpoint, and recovery.
6. **MECE structure.** Peer ideas are the same kind of thing, non-overlapping, collectively sufficient for the parent claim.
7. **The system tells the truth about its own state.** Interactive controls work or are clearly labeled non-production. Data, metrics, and visual proof are real, sourced, or labeled illustrative — no mock-as-real. Loading, empty, error, stale, permission, and partial-result states are designed, not afterthoughts.
8. **Every action gets perceivable feedback.** Any user action — click, tap, submit, send, commit, run, save, edit, schedule — is acknowledged immediately and the resulting state change is perceivable. Disabled until pre-conditions are met; visibly enabled when actionable; in-progress during work; result confirmed at completion. No silent success, no ambiguous in-flight. Use whichever channels the medium offers: **visual** (state change, animation, toast, indicator), **haptic** (tap / impact / notification on mobile and wearable), **audible** (system sounds, voice confirmation), and **textual** (status line, echo, receipt). On mobile and wearable, haptic is often the primary confirmation when the visual change is small or briefly off-screen; pair it with a visible change wherever possible. Respect platform conventions (iOS `UIImpactFeedbackGenerator` semantic levels, watchOS haptic types, Android `HapticFeedbackConstants`, reduce-motion / silent-mode preferences).
    - *Cited precedent:* SpeakSavvy CTA pairs `Haptics.impact(.light)` with visible state + two-layer `glowShadow()` — see `speaksavvy-ios.md`. ProductPilot enforces 44pt touch target at the `.btn-primary` component class — see `productpilot.md`.
9. **Resilient to imperfect input.** Accept multiple shapes (string / object / markdown / null; title / headline / name). Prefer partial results with warnings over total failure. Degrade along documented fallback paths. Don't reject what can reasonably be interpreted.
10. **Separation is earned.** Borders, boxes, dividers, and backgrounds only when they encode a real relationship break. Whitespace and alignment carry structure first; chrome only when whitespace fails.
    - *Cited precedent:* ProductPilot uses a single accent (`--primary === --accent === --ring === #f0b65e`) and warm-monochromatic palette discipline — strictest single-accent in inventory. See `productpilot.md`. Counter-pattern: the sample decision app's three-theme toggle (`[data-theme="A"|"B"]`) preserves semantic meaning across visual presentation but dilutes brand voice — see `sample-decision-app.md`.
11. **Content fits its container, or the container fits the content.** Clipping, truncation without recovery, awkward wraps, and overflow are design failures. Decide which side flexes (fixed container with overflow recovery, or fluid container that grows). Critical content never shrinks below its reading threshold to fit.
12. **Visual hierarchy is one-glance recoverable.** A user reading at speed knows what is most important, what is next, what is supporting — from position, size, weight, contrast, and spacing alone. If color is removed, the hierarchy still holds.
    - *Cited precedent:* Secrets Vault's `VaultTypography.title/.description/.metadata` (15/13/11pt) maps role names directly to L1/L2/L3 — see `.build-loop/design-evidence/secrets-vault-macos.md`. Anti-pattern: the sample onboarding app's inline pixel sizes across one view (12/13/14/15/16/18/20/34) — see `sample-onboarding-app.md`.
13. **Fit form to information.** Prose, bullets, table, chart, diagram, checklist, form, callout, or slide rhythm chosen for the reading task — not the template.
14. **Native primitives over visual fakes.** Use the medium's semantic structures: UI components, slide placeholders, document styles, table geometry, chart objects, alt text, headings, source notes. Don't fake them with glyphs, manual spacing, or screenshots.
15. **Verify the rendered artifact.** Inspect the real output surface before declaring done. Screenshots for UI; rendered pages for docs and decks; live render for charts.

**Visual-craft inheritance.** Detailed grouping, spacing, type scale, semantic color, progressive disclosure, action weight, and accessibility numbers are inherited from the underlying design system. The default is **Calm Precision**; deviations need a named exception in the artifact contract. Two universal pointers worth naming explicitly:

- **Interaction targets match input precision.** Touch needs more area than mouse; mouse more than keyboard focus; watch, remote, and voice each have their own envelope. The principle: a target the user can hit on the first try, comfortably, with the device in hand. Specific minimums live in platform skills (`accessibility-review`, platform HIGs).
- **Motion serves comprehension, not decoration.** Animate when it explains origin, destination, or state change. Duration short enough not to delay, long enough to be perceived. Respects reduced-motion preferences.
  - *Cited precedent:* The sample decision app's single CSS rule overrides all animations to 0.01ms under `prefers-reduced-motion` (`app/globals.css:65-71`) — see `sample-decision-app.md`. Secrets Vault caps stagger at 400ms total (60ms × N items) — see `secrets-vault-macos.md`. The sample onboarding app documents anti-flicker behavior in code comments — see `sample-onboarding-app.md`.
- **Multi-form-factor via viewport-scale tokens.** When one codebase ships across iPhone, iPad, Mac, and Watch, scale a single hierarchy by a runtime env var rather than forking themes per platform.
  - *Cited precedent:* TruePace's `\.viewportScale` env (1.0× iPhone → 1.5× iPad → live-window-resize Mac → separate watch palette + AOD dim variants); see `truepace.md`. Formalized as Pattern 1 / Pattern 4 in the multi-pattern framework draft at `skills/ui-design/references/design-patterns-multi.md`.

## 3. Medium Adapters

Same six jobs; different primitives.

| Medium | Continuity | Recovery | Proof |
|---|---|---|---|
| **Writing** | Topic sentences, transition phrases, repeated terms, parallel structure, SCQA | State the supported conclusion, name the assumption, name the next input needed | Claims, citations, examples, line of reasoning |
| **Images / media** | Visual path, framing, focal hierarchy, captions, before/after pairing | Alt text, fallback caption, role label (concept / reference / production) | Source image, generation prompt, provenance |
| **App UI** | Breadcrumbs, nav state, preserved scroll, inline expansion, side drawer, bottom sheet, progress stepper, motion that explains origin/destination | Loading / empty / error / stale / permission / offline / retry / saved-state / partial-result / fallback states | Real data, working handlers, source labels, interaction tests, screenshots |
| **Decks** | Claim spine, action titles, section dividers, agenda markers, repeated footer grammar | Mark missing inputs, move unsupported numbers to appendix, preserve template rhythm | Source footnotes, proof objects, rendered slide QA |
| **Documents / reports** | Heading ladder, lead paragraphs, captions, cross-references, page furniture | Explain constraints, preserve edit trail, route unresolved items to appendix | Citations, styles, numbering, table geometry, rendered page QA |
| **Spreadsheets / data** | Frozen headers, stable dimensions/measures, filters, summary-to-detail flow | Formula warnings, missing-data treatment, source flags, named assumptions | Formulas, source lineage, audit checks, charts/tables tied to decisions |
| **Operational workflows** | Purpose, start state, target end state, current step, checkpoint, owner, status delta, next action | Rollback path, retry path, partial-completion note, blocked reason, safe stopping point | Git status, diff, commit SHA, test result, deploy target, migration log, sync record |

## 4. One Idea, Across Media

Two worked examples showing the same principle expressed in seven media.

**Graceful recovery** (principle: preserve context, name what happened, offer a next step):

- **Writing:** "The source does not support that claim yet. Supported conclusion: X. Assumption: Y. Next input needed: Z."
- **Image:** Caption reads "Concept render — labels and quantities not verified."
- **App UI:** Toast: "Refresh failed. Showing last saved version from 14:02. Retry · Switch source · Continue with cached."
- **Deck:** Footnote: "Metric not available from public source as of 2026-05; kept qualitative pending research."
- **Document:** Margin note: "Render check failed; layout unverified. Content edits complete."
- **Spreadsheet:** Cell: `=IF(ISBLANK(A2), "missing source — flagged", LOOKUP(...))` with a visible flag column.
- **Operational:** "Commit blocked: 2 tests failed. Files unchanged on disk. Branch position unchanged. Fix failing tests, unstage, or stop."

**Action feedback** (principle: action acknowledged immediately, state change perceivable, no silent success):

- **Writing:** Reply opens with "Received your X — answering Y below." Reader knows the question landed.
- **Image:** Selection ring + filename pinned on hover; no ambiguity about which asset is active.
- **App UI (desktop / web):** Submit button → spinner with label → success toast + the new row appearing in the list. Disabled until form valid; visibly enabled when ready.
- **App UI (mobile / wearable):** Tap → light haptic on press, success haptic on completion + visible state change (button collapses to checkmark, toast fades in). Long-press, swipe-to-action, and pull-to-refresh each have their own haptic signature so the gesture is confirmed even when the visual change is small or briefly off-screen.
- **Deck:** Click on agenda item dims others, advances to that section, breadcrumb updates in footer.
- **Document:** Track Changes shows the inserted text + author + timestamp; the change is the receipt.
- **Spreadsheet:** Recalc indicator while formulas resolve; updated cells flash briefly so the user sees what moved.
- **Operational:** Command echoes input, prints stepwise progress, exits with status code + summary. No silent commands.

The principle is identical across rows; the primitive is whatever the medium has natively.

## 5. Strict Versus Flexible

**Strict** (do not violate without a named exception):

- Named audience, job, and output.
- Governing thought or primary state is clear early.
- User oriented in current state and next step.
- Changing workflows show purpose, start, target, route, checkpoint, safe stop.
- Transitions preserve continuity or mark the break.
- Peer items are same-kind and ordered.
- Claims, numbers, and data visuals have provenance.
- Interactive controls and generated outputs are functional or labeled non-production.
- Degraded states explain what happened and how to recover.
- Accessibility and visual QA done before "complete."
- Medium primitives are semantic, not visual fakes.

**Flexible** (choose for the project):

- Visual style, mood, density, surface treatment.
- Structure shape: dashboard, narrative, wizard, ledger, canvas, memo, slide sequence.
- The continuity primitive: phrase, heading, animation, drawer, sheet, caption, breadcrumb, drilldown.
- The recovery primitive: inline message, note, fallback, retry, appendix, caveat, cached result.
- The process primitive: status line, checklist, branch summary, progress marker, deploy URL, commit SHA, sync record.
- Token palette, type family, illustration style, layout rhythm when no system exists.
- Polish level, justified by audience, lifespan, and risk.

## 6. Conflict Order

When sources disagree, resolve in this order:

1. Explicit user requirement for the current artifact.
2. Current project or artifact contract.
3. Router classification and archetype defaults.
4. Domain skill or reference for the medium.
5. These universal principles as the floor.

A deliberate exception names the reason in the artifact's contract, not just the visual preference. The host orchestrator (e.g., `build-loop:ui-design`) may layer additional precedence rules above this floor; defer to the host when present.

## 7. Operating Notes

For an artifact:

1. Capture audience, task, decision, purpose, current state, target end state, route, and risk.
2. Apply §1 and §2.
3. Load the medium-specific adapter (§3 row).
4. Render and verify (§2 rule 10).
5. Record deliberate exceptions in the contract with a stated reason.

For a host runtime: classify the use case first (router), apply defaults, route to domain references only when needed, ask at high-impact choice points, flag any choice made without asking.

## 8. Source Anchors

Synthesized from these skills and references. Use them by name; the host runtime resolves paths.

| Source (by name) | What it contributes |
|---|---|
| `calm-precision` skill | Cognitive predictability, grouping, hierarchy, disclosure, action weight, state/error rules, functional integrity |
| `calm-precision-v1-1` (Router, Navigation, Motion/States/Identity) | Router pattern; task economy and step counting; purposeful motion and state-context matching |
| `build-loop:ui-design` (SKILL + `ui-guidance-sources` reference) | Build Loop design route, source map, runtime priority order |
| `interface-built-right` web router | Web archetypes, density defaults, validation-focus risk by surface |
| `interface-built-right` iOS router | iOS archetype router and domain-reference split |
| `interface-built-right` ui-ux-guidance (Codex plugin shape) | Compact guidance order, target roles, imagegen gates, interaction states, validation contract |
| `UI Guidance` library (cross-platform, data-viz) | Cross-platform mode selection, content/chrome ratio, motion, status, responsive structure; decision-first data, chart-title discipline, direct labeling, source attribution |
| `mockup-gallery` (COMMON + mockup-review) | Mockup state model, scratch-first lifecycle, selection/implementation tracking |
| `deck-builder` and `calm-precision-pptx` | Storyline-first deck workflow, template inheritance, native PowerPoint primitives, action titles, one assertion per slide |
| `storyline-builder` and `pyramid-principle-core` | Audience brief, governing thought, SCQA, MECE key line, vertical question/answer logic, horizontal deductive/inductive logic |
| `pptx-design` and `docx` | Encoded deck design systems, density by purpose, visual QA; document presets, real styles/numbering/tables, render-and-inspect workflow |
| `design-system` and `accessibility-review` | Tokens, components, patterns, versioning, migration discipline; WCAG 2.1 AA baseline (contrast, keyboard, focus, touch targets, name/role/value) |

Absolute paths are resolved by the host runtime's skill index; this file does not hard-code per-machine locations.

## 9. Cited Precedents (Live-Capture Addendum, 2026-05-26)

Live IBR captures on the 4 primary apps surfaced texture observations that source-read could not. The principles above hold, with these qualifications:

- **Tokens extend beyond visuals.** SpeakSavvy's `HapticVocabulary.swift` defines 5 semantic haptic events (`.confirm/.reward/.warn/.progress/.selection`) that layer atop UIKit feedback generators the same way `Theme.fontDisplay` layers atop `Font.system(...)`. Treat haptics as first-class tokens when the platform supports them, not as call-site decisions.
- **Selection signaling is platform- and density-specific, not universal.** SpeakSavvy (filter pills, tab bar) uses solid-fill on selected; TruePace (mode cards in modal sheet) uses 1pt border + glyph check. Both are legitimate; the choice is governed by surface density and decision weight, not a single rule. Avoid "selection = fill" claims absent context.
- **Empty-state vocabulary diverges intentionally.** SpeakSavvy floats a muted SF Symbol + two-line copy (`history-empty`); TruePace renders a 3-stop milestone ladder with connecting line (`focus-journal-insights-empty`); ProductPilot uses a text-only loading label (`loading next question…`). Pick the vocabulary that matches the user's reason-for-emptiness — first-use vs unlock-progression vs transient-load — not a single house pattern.
- **AI-assist as input-affordance is preferred when input is the primary action.** ProductPilot's sparkle "Enhance" CTA lives inside the textarea, not adjacent. Conserves vertical real estate and signals AI assistance via universal glyph. Pattern: when typing is the action, AI helpers live inside the field; when picking is the action, helpers can sit alongside.
- **Brand-identity-in-chrome is universal, treatment is product-specific.** All 4 primaries differentiate their header brand: SpeakSavvy plain text title, TruePace custom wordmark asset, the sample reader plain text + theme toggle, ProductPilot 2-color wordmark + diamond glyph. No primary uses a generic system-font header alone. Treatment should match product voice; avoid system-default brand chrome unless the app is intentionally voice-neutral.
- **Live capture is mandatory for these observations.** Source files cannot reveal selection-signaling divergence, empty-state-vocabulary divergence, or input-affordance placement — all are render-time decisions. See `evidence-capture-policy.md`.
- **Two-speed motion on a single control is a deliberate language, not noise.** ProductPilot's primary CTA uses `transition: background 0.2s, transform 0.15s` — geometry settles 50ms faster than color. When a button shifts both color and shape on hover/press, the faster channel feels snappier (responsiveness) while the slower channel adds texture (settling). Avoid "one transition for everything"; tune per channel.
- **Restraint extends to error states, not just primary surfaces.** ProductPilot's 404 has 5 elements total (icon + title + body + CTA + card). No illustration, no error code, no "did you mean", no search. Empty space IS the signal — "go back, this isn't the path." Pattern: **error/empty states inherit the same restraint as the main app; do not overcompensate with chrome.**
- **Friction-removal microcopy has a stable shape.** ProductPilot's landing CTA and auth card both pair a primary action with a short / two-clause / bulleted / muted-color qualifier underneath (`No account required · Free to try` and `No account · Groq Llama 3.3`). When a CTA carries friction concern (cost, account, lock-in), follow with two short clauses separated by `·` in muted ≤12pt. Pattern travels — same recipe, two surfaces.
- **Citation freshness matters.** Lines 171–172 cite ProductPilot's text-only loading label and sparkle "Enhance" inside-textarea pattern; both observed 2026-05-03. The 2026-05-26 capture confirms the landing has been redesigned to a headline-then-CTA model; the textarea-with-Enhance pattern moved one click deeper. The principles still hold; the live citations are as-of-2026-05-03.

Evidence: `.build-loop/design-evidence/{sample-voice-ios, sample-timer, sample-reader, sample-product}.md` §"Live IBR Capture (2026-05-26)" — sample product section refreshed 2026-05-26 on port 3155.
