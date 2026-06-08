<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# UI Guidance Source Map

Canonical build-loop entrypoint:

- Skill: `build-loop:ui-design`
- Agent: `agents/design-contract-specialist.md`
- Runtime contract: `.build-loop/app-contract/ui.md`
- Universal principles: `skills/ui-design/references/universal-design-principles.md`
- Implementer prompt: `skills/build-loop/templates/ui-subagent-prompt.md`
- Input/output contract: `skills/build-loop/references/ui-io-contract.md`
- Recent structure picker: `skills/build-loop/references/recent-design-structures.md`
- Validator: `agents/ui-validator.md`

Use this file to choose sources. Do not paste the whole map into implementer prompts.

## Design Guidance Model

Use the guidance as a layered model:

| Layer | Role | Examples |
|---|---|---|
| Product contract | Defines the user job, inputs, outputs, operations, platform, data shape, and risk | `ui-io-contract.md`, `.build-loop/app-contract/ui.md` |
| Universal communication and experience design | Governs answer-first structure, one primary focus, continuity, wayfinding, process visibility, graceful degradation, MECE grouping, semantic hierarchy, evidence integrity, native primitives, accessibility, and rendered QA across UI, writing, images, decks, docs, operational workflows, reports, spreadsheets, and PDFs | `universal-design-principles.md` |
| Structural foundation | Governs cognitive predictability and implementation discipline | Calm Precision |
| Project surface | Preserves current app conventions and brand | tokens, theme files, components, screenshots, selected mockups |
| Structure or style mode | Selects the layout/mood that fits the current surface | Conversational Command, Bento Dashboard, Pipeline Wizard, Outcome Ledger, Pyramid Detail, Glass Workspace, Warm Craft, Data Narrative, native mobile, AI Artifact Canvas |
| Validation | Proves the result works and renders | `ui-validator`, scanner, browser/simulator screenshots, contract traceability |

Do not treat every source as equal. Universal communication principles are the cross-medium baseline. Calm Precision is the UI structural baseline, not a competing mode. Recent structures and style systems are selected after the product contract is known.

## Build-Loop Runtime Guidance

Load these first for active builds:

- `skills/build-loop/references/ui-io-contract.md` - required UI plan contract.
- `skills/ui-design/references/universal-design-principles.md` - cross-medium doctrine for app UI, writing, images, operational workflows, decks, docs, reports, spreadsheets, PDFs, and other communication or information artifacts.
- `skills/build-loop/references/recent-design-structures.md` - compact recent structure library; options, not mandates.
- `skills/build-loop/templates/ui-subagent-prompt.md` - prompt preamble for UI implementers.
- `skills/build-loop/phases/ui-validation.md` - validation gates and visual evidence requirements.
- `skills/build-loop/fallbacks.md` - degraded UI tooling path.
- `references/ui-spotcheck-protocol.md` - visual spot-check protocol.
- `docs/rfcs/2026-05-ui-validator-agent.md` - ui-validator rationale.

## Universal Design Principles

`skills/ui-design/references/universal-design-principles.md` is the detailed overview for principles that should apply across every generated or reviewed artifact, not only app UI.

Boundary:

- Universal principles define the invariant decision process: orient, focus, connect, progress, recover, resolve.
- Routers classify the artifact or workflow and narrow defaults. Use Calm Precision router-style sources for this layer.
- Domain skills provide the exact primitive: UI component, writing transition, image composition, slide pattern, document style, data chart, or operational checkpoint.

What it controls:

- Purpose before presentation: audience, job, context, decision, output, and risk.
- Answer-first structure: governing thought, primary state, recommendation, or conclusion visible early.
- One primary focus per viewport, slide, section, page, chart, table, or artifact unit.
- Wayfinding: current state, prior context, next step, and return path.
- Continuity: transitions that preserve orientation across writing, UI, images, decks, docs, and data.
- Process visibility: purpose, start state, target end state, route, current step, checkpoints, and safe stop for rapidly changing workflows.
- Graceful degradation: clear message and recovery path when the preferred route fails.
- MECE grouping and reader-question discipline.
- Hierarchy before decoration: position, grouping, type, contrast, spacing, and order before visual effects.
- Functional and evidence integrity: real interactions, real data, sourceable claims, and no fake production-looking proof.
- Native primitives: semantic UI components, PowerPoint placeholders/bullets, Word styles/numbering/tables, formulas, chart objects, headings, and alt text.
- Accessibility, state/recovery treatment, and rendered visual QA.

Use it before medium-specific guidance for writing, images, decks, documents, reports, spreadsheets, PDFs, and UI surfaces that present dense or decision-bearing information.

## Calm Precision

Calm Precision is the default structural doctrine for build-loop UI work.

Calm Precision router sources checked:

- `calm-precision-v1-1/0_Router.md`
- `calm-precision-v1-1/1_Navigation_Structure.md`
- `calm-precision-v1-1/5_Motion_States_Identity.md`

Reusable router pattern:

- Classify archetype first.
- Apply defaults before loading a domain file.
- Ask only at high-impact or ambiguous choice points.
- Count steps and validate optional additions.
- Flag decisions made without asking.
- Keep component option catalogs in domain files, not in universal guidance.

Where build-loop uses it:

- `skills/build-loop/templates/ui-subagent-prompt.md` requires `calm-precision` for all UI implementers.
- `skills/build-loop/phases/ui-validation.md` lists `calm-precision` beside `build-loop:ui-design` and `design-contract-specialist` for web, mobile, and native UI routing.
- `skills/build-loop/fallbacks.md` carries a condensed Calm Precision fallback when the external skill is unavailable.
- `agents/implementer.md` uses Calm Precision examples for usability queue fixes such as replacing background status pills with text-color status.
- `agents/synthesis-critic.md` treats Calm Precision copy tone and subjective UI claims as reviewable synthesis dimensions.

What it controls:

- Visual hierarchy and one clear L1 anchor.
- Grouping, borders, dividers, and content-to-chrome ratio.
- Action weight, touch targets, and card/button affordance.
- Disclosure depth, loading/empty/error states, and copy clarity.
- Motion restraint and reduced-motion compliance.
- Functional integrity: no fake buttons, no real-looking mock data, real handlers for interactive UI.
- Perceptual-science traceability: every Calm Precision rule should map to a named foundation such as Gestalt, Fitts' Law, Hick's Law, Cognitive Load, Signal-to-Noise Ratio, Affordance Theory, Temporal Gestalt, Dual-Coding, Pragmatic Inference, Attentional Cascade, the Cooperative Principle, Fault Tolerance, or Information Scent/Density. This is what lets design-contract output defend choices as evidence-backed rather than taste-based.

What it does not decide by itself:

- Whether the surface should use Glass Workspace, Warm Craft, Aurora Deep, Data Narrative, or another style mode.
- Product brand personality, token palette, or final typography roles when a project-specific design system exists.
- Native platform conventions that Apple/Web/etc. impose.

In practice: start with Calm Precision for structure, then select the mode that fits the product. For example, Aurora Glass can layer translucent surfaces over a Calm Precision layout; Warm Craft can change warmth and texture while preserving hierarchy and action discipline; Data Narrative adds evidence/storytelling patterns while keeping decision-first labels and source traceability.

## ui-guidance plugin (canonical home, 2026-05-27+)

The cross-project design library is now available as the `ui-guidance` plugin. Prefer qualified Skill invocations over absolute paths:

| What you need | Invocation |
|---|---|
| Cross-medium doctrine (UI + writing + decks + ops workflows) | `Skill("ui-guidance:principles")` |
| Owned-app design preferences (typography, color, touch targets, motion, error UX) | `Skill("ui-guidance:preferences")` |
| Multi-pattern token framework (mobile / tablet / web / watch resolution) | `Skill("ui-guidance:tokens")` |
| Source-read vs live-IBR-capture routing | `Skill("ui-guidance:evidence-policy")` |
| 4 design modes (Atmospheric / Glass Workspace / Warm Craft / Data Narrative) | Load `references/modes/<mode>.md` directly (catalog, not skill) |
| Chart / KPI / table / sparkline / timeline patterns | `Skill("ui-guidance:data-viz")` |
| Wayfinding, tab/stack/drawer/sheet selection, breadcrumbs | `Skill("ui-guidance:navigation")` |
| L1/L2/L3 ladder, luminance tiers, type scale | `Skill("ui-guidance:hierarchy")` |
| Form factors, breakpoints, viewport-scale tokens, watch glance | `Skill("ui-guidance:responsive")` |
| Action feedback, haptics, loading, confirmation, error UX | `Skill("ui-guidance:feedback")` |
| Motion, transitions, stagger, prefers-reduced-motion | `Skill("ui-guidance:motion")` |
| iOS / iPhone / iPad / SwiftUI | `Skill("ui-guidance:ios")` |
| macOS / Mac native / menu bar / NSToolbar | `Skill("ui-guidance:macos")` |
| Web / Next.js / React / ARIA / WCAG 2.1 AA | `Skill("ui-guidance:web")` |

**ui-guidance v0.2.0** is a flat-but-grouped IA: 10 cross-platform topic skills + 3 platform skills + design-mode catalog. The bulky reference files (`ios/references/full.md`, `web/references/full.md`, `data-viz/references/full.md`) load on demand via the skill body — do not paste them into implementer prompts.

**Bundled in the plugin under `references/`** (organized for cross-reference, not for direct path-based access from outside the plugin):

- `references/design-evidence/` — 8 owned-app evidence files
- `references/screenshots/{speaksavvy, truepace, productpilot}/` — 16 live IBR captures (2026-05-26)
- `references/style-modes/` — `aurora-deep.md`, `aurora-glass.md`, `warm-craft.md` catalog briefs
- `references/tools/mockup-gallery-reviewer.html` — mockup-rating browser tool
- `references/historical/universal-design-principles-original.md` — pre-migration 365-line original

Mockup-gallery session data (`.mockup-gallery/selections.json`, `mockups/` rated HTML files) still live at the repo root for the mockup-gallery plugin's own use; the canonical guidance synthesized from those mockups lives in the skills above.

### Build-loop transition shims (one release cycle)

The four reference files at `skills/ui-design/references/{universal-design-principles.alt.md, design-preferences-from-owned-apps.md, design-patterns-multi.md, evidence-capture-policy.md}` are now deprecation shims pointing at the plugin. Remove after one release cycle. The plugin is the canonical source.

Current pattern families visible here:

- **Calm Precision** - structural default for professional tools and mobile-first workflows.
- **Glass Workspace / Aurora Glass** - structured workspace personality; often a reversible surface layer over Calm Precision.
- **Aurora Deep** - primary dark developer/data workspace when dense atmosphere is useful.
- **Warm Craft** - reflective writing, knowledge work, and human-in-the-loop planning.
- **Data Narrative** - charts, research, trends, market intelligence, and decision-first evidence.
- **Atmospheric / immersive variants** - focus, timer, wellness, and state/mood-driven surfaces.
- **Decision Doctor structures** - Sunrise Hero, Electric Mint, Pipeline Wizard, Bento Dashboard, Bloom Organic, Outcome Ledger, and Pyramid Detail.

## Interface Built Right

IBR has rich design and validation material, but build-loop does not auto-route through IBR.

Additional router sources checked:

- `interface-built-right/references/web-design/0_router.md`
- `interface-built-right/references/ios-design/0_router.md`
- `interface-built-right/skills/ios-design-router/SKILL.md`
- `interface-built-right/.codex-plugin/skills/ui-ux-guidance/SKILL.md`

These reinforce the same split: archetype/router first, then platform/domain references, then validation contract.

High-signal sources:

- `interface-built-right/.codex-plugin/skills/design/SKILL.md`
- `interface-built-right/.codex-plugin/skills/ui-ux-guidance/SKILL.md`
- `interface-built-right/skills/design-director/SKILL.md`
- `interface-built-right/skills/design-guidance/SKILL.md`
- `interface-built-right/skills/design-implementation/SKILL.md`
- `interface-built-right/skills/design-reference/SKILL.md`
- `interface-built-right/skills/design-system/SKILL.md`
- `interface-built-right/skills/design-validation/SKILL.md`
- `interface-built-right/skills/ui-guidance-library/SKILL.md`
- `interface-built-right/references/web-design/`
- `interface-built-right/references/ios-design/`
- `interface-built-right/mobile-ui/`
- `interface-built-right/templates/patterns/`
- `interface-built-right/src/ui-guidance/`

Use only when the user explicitly asks for IBR or when a selected IBR artifact is passed to the specialist as evidence. Do not make IBR the default build route.

## Mockup Gallery

Use for mockup drafting, selection, or review:

- `mockup-gallery/COMMON.md`
- `mockup-gallery/DESIGN.md`
- `mockup-gallery/DESIGN-SELECTED.md`
- `mockup-gallery/AGENTS.md`
- `mockup-gallery/commands/mockup-gallery.md`
- `mockup-gallery/skills/mockup-review/SKILL.md`
- `mockup-gallery/.agents/skills/mockup-review/SKILL.md`
- `mockup-gallery/memories/global/design-preferences.md`

Mockup Gallery helps create and judge candidate visuals. Build-loop still records the selected direction in `.build-loop/app-contract/ui.md`.

Checked sources confirm Mockup Gallery is a lifecycle and approval system, not a universal principle source. Reusable universal lessons are scratch-first exploration, state visibility, rating semantics, approved-target guardrails, implementation tracking, and versioning instead of overwriting.

## Documents, Decks, And Information Artifacts

These sources are not UI routes, but they contain reusable information-design guidance that applies to Build Loop artifacts and UI-generated outputs.

Claude local session sources inspected:

- `deck-builder/SKILL.md` (local Claude session snapshot; path redacted)
- `calm-precision-pptx/SKILL.md` (local Claude session snapshot; path redacted)
- `design-system/SKILL.md` (local Claude session snapshot; path redacted)
- `accessibility-review/SKILL.md` (local Claude session snapshot; path redacted)
- `storyline-builder/SKILL.md` (local Claude session snapshot; path redacted)
- `pyramid-principle-core/SKILL.md` (local Claude session snapshot; path redacted)
- `pptx-design/SKILL.md` (local Claude session snapshot; path redacted)
- `docx/SKILL.md` (local Claude session snapshot; path redacted)

Codex runtime sources inspected:

- `openai-primary-runtime/documents/.../skills/documents/SKILL.md` (Codex runtime snapshot; path redacted)
- `openai-primary-runtime/presentations/.../skills/presentations/SKILL.md` (Codex runtime snapshot; path redacted)
- `openai-primary-runtime/presentations/.../skills/presentations/subagent-instructions.md` (Codex runtime snapshot; path redacted)

Reusable guidance captured into `universal-design-principles.md`:

- Storyline and governing thought before rendering.
- Pyramid/MECE logic and answer-first structure.
- One assertion per slide or artifact unit.
- Cross-medium continuity, wayfinding, and graceful degradation.
- Template, preset, and project-system inheritance before overrides.
- Native Office/document primitives instead of visual fakes.
- Claim, metric, source, and asset provenance.
- Rendered visual QA for decks and docs, not only structural extraction.
- Accessibility as a design baseline across UI and documents.

Note: Local Claude session paths are intentionally redacted from this public package. Use the host's current plugin/session lookup rather than copying historical absolute paths.

## Research And Vault Corpus

Use for background, trend history, or long-term preference synthesis. Summarize before passing to implementers.

High-signal local research:

- `research/topics/design/`
- `research/projects/<project>/design-brief-2026-04-20.md`

High-signal vault folders:

- `vault/raw/engineering/ui-design-research-corpus-2026/`
- `vault/raw/engineering/ui-ux-calm-precision/`
- `vault/raw/engineering/ui-ux-central-library/`
- `vault/raw/engineering/ui-ux-ibr-references/`
- `vault/raw/engineering/ui-ux-spec-review-input-output-coverage-2026/`
- `vault/raw/engineering/native-ios-watchos-ui-research-2026/`
- `vault/raw/engineering/calm-precision-v1-1/`
- `vault/raw/engineering/calm-precision-native-apple-platforms-v1-1-2026/`
- `vault/raw/engineering/ui-ux-private-ios-guidance/`
- `vault/raw/engineering/ui-ux-misc-app-design/`
- `vault/outputs/drafts/2026-05-10-ui-preferences-mobile-first-web-apps-aggregate.md`
- `vault/outputs/drafts/2026-05-10-private-product-mobile-first-ui-guidance.md`

## Build-Loop-Memory

Durable long-term design memory belongs here:

- `build-loop-memory/design/README.md`
- `build-loop-memory/decisions/build-loop/0094-2026-05-24-build-loop-design-structure-memory-policy.md`
- `build-loop-memory/projects/<project>/design/` when project-specific design memory exists.
- `build-loop-memory/indexes/` for discovery after the migration structure settles.

Build-loop's runtime references should be refreshed from build-loop-memory when a design structure becomes repeatedly used, explicitly selected/rejected, or materially changes the selection policy.

## Project-Local Hidden Sources

Check the target project before cross-repo sources:

- `<project>/.build-loop/app-contract/ui.md`
- `<project>/.build-loop/research/*ui*`
- `<project>/.build-loop/research/*design*`
- `<project>/.build-loop/coordination/*ui*`
- `<project>/.build-loop/coordination/*design*`
- `<project>/.research/` when present.
- `<project>/docs/**` for design-system, UX, UI, accessibility, mockup, or visual guidance.
- `<project>/.mockup-gallery/selected.json`, `ratings.json`, `implemented.json`, or `selections.json`.

These override generic guidance when they represent the current project contract.

## Generated Or Non-Canonical Sources

Do not treat these as canonical design guidance unless a plan explicitly names a generated artifact as evidence:

- `.ibr/`
- `.navgator/`
- `.bookmark/`
- `.claude/bookmarks/`
- `node_modules/`
- `.next/`, `dist/`, `build/`
- Playwright reports, screenshots, logs, caches, and `tsconfig.tsbuildinfo`
- Swift `.build/`, DerivedData, module caches, and package artifacts

## Refresh Scan

Use targeted scans instead of broad full-disk loads:

```bash
rg -l --hidden \
  --glob '!**/.git/**' --glob '!**/node_modules/**' --glob '!**/.next/**' \
	  --glob '!**/dist/**' --glob '!**/build/**' --glob '!**/.build/**' \
	  -i '(ui guidance|ui/ux|ux guidance|design guidance|design system|calm precision|interface built right|visual style|design direction|mockup|wireframe|interaction|accessibility|touch target|design contract|app-contract|recent design|data visualization)' \
	  "$BUILD_LOOP_REPO" \
	  "$BUILD_LOOP_MEMORY_REPO" \
	  "$INTERFACE_BUILT_RIGHT_REPO" \
	  "$UI_GUIDANCE_REPO" \
	  "$MOCKUP_GALLERY_REPO" \
	  "$RESEARCH_ROOT" \
	  "$VAULT_ROOT"
```

Record newly durable structures in build-loop-memory; record runtime choices in `.build-loop/app-contract/ui.md`.
