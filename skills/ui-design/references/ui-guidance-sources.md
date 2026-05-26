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

- `/Users/tyroneross/ObsidianVault/raw/engineering/calm-precision-v1-1/0_Router.md`
- `/Users/tyroneross/ObsidianVault/raw/engineering/calm-precision-v1-1/1_Navigation_Structure.md`
- `/Users/tyroneross/ObsidianVault/raw/engineering/calm-precision-v1-1/5_Motion_States_Identity.md`

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

What it does not decide by itself:

- Whether the surface should use Glass Workspace, Warm Craft, Aurora Deep, Data Narrative, or another style mode.
- Product brand personality, token palette, or final typography roles when a project-specific design system exists.
- Native platform conventions that Apple/Web/etc. impose.

In practice: start with Calm Precision for structure, then select the mode that fits the product. For example, Aurora Glass can layer translucent surfaces over a Calm Precision layout; Warm Craft can change warmth and texture while preserving hierarchy and action discipline; Data Narrative adds evidence/storytelling patterns while keeping decision-first labels and source traceability.

## Local UI Guidance Folder

Primary cross-project design source:

- `/Users/tyroneross/dev/git-folder/UI Guidance/cross-platform-design-patterns.md`
- `/Users/tyroneross/dev/git-folder/UI Guidance/data-visualization-patterns.md`
- `/Users/tyroneross/dev/git-folder/UI Guidance/aurora-deep.md`
- `/Users/tyroneross/dev/git-folder/UI Guidance/aurora-glass.md`
- `/Users/tyroneross/dev/git-folder/UI Guidance/warm-craft.md`
- `/Users/tyroneross/dev/git-folder/UI Guidance/.mockup-gallery/selections.json`
- `/Users/tyroneross/dev/git-folder/UI Guidance/mockups/`

Use when the build needs a known design mode, chart/data pattern, or recent selected mockup. Treat mockups as reference artifacts; implementation still follows build-loop design rules and the UI input/output contract.

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

- `/Users/tyroneross/dev/git-folder/interface-built-right/references/web-design/0_router.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/references/ios-design/0_router.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/skills/ios-design-router/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/.codex-plugin/skills/ui-ux-guidance/SKILL.md`

These reinforce the same split: archetype/router first, then platform/domain references, then validation contract.

High-signal sources:

- `/Users/tyroneross/dev/git-folder/interface-built-right/.codex-plugin/skills/design/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/.codex-plugin/skills/ui-ux-guidance/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/skills/design-director/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/skills/design-guidance/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/skills/design-implementation/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/skills/design-reference/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/skills/design-system/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/skills/design-validation/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/skills/ui-guidance-library/SKILL.md`
- `/Users/tyroneross/dev/git-folder/interface-built-right/references/web-design/`
- `/Users/tyroneross/dev/git-folder/interface-built-right/references/ios-design/`
- `/Users/tyroneross/dev/git-folder/interface-built-right/mobile-ui/`
- `/Users/tyroneross/dev/git-folder/interface-built-right/templates/patterns/`
- `/Users/tyroneross/dev/git-folder/interface-built-right/src/ui-guidance/`

Use only when the user explicitly asks for IBR or when a selected IBR artifact is passed to the specialist as evidence. Do not make IBR the default build route.

## Mockup Gallery

Use for mockup drafting, selection, or review:

- `/Users/tyroneross/dev/git-folder/mockup-gallery/COMMON.md`
- `/Users/tyroneross/dev/git-folder/mockup-gallery/DESIGN.md`
- `/Users/tyroneross/dev/git-folder/mockup-gallery/DESIGN-SELECTED.md`
- `/Users/tyroneross/dev/git-folder/mockup-gallery/AGENTS.md`
- `/Users/tyroneross/dev/git-folder/mockup-gallery/commands/mockup-gallery.md`
- `/Users/tyroneross/dev/git-folder/mockup-gallery/skills/mockup-review/SKILL.md`
- `/Users/tyroneross/dev/git-folder/mockup-gallery/.agents/skills/mockup-review/SKILL.md`
- `/Users/tyroneross/dev/git-folder/mockup-gallery/memories/global/design-preferences.md`

Mockup Gallery helps create and judge candidate visuals. Build-loop still records the selected direction in `.build-loop/app-contract/ui.md`.

Checked sources confirm Mockup Gallery is a lifecycle and approval system, not a universal principle source. Reusable universal lessons are scratch-first exploration, state visibility, rating semantics, approved-target guardrails, implementation tracking, and versioning instead of overwriting.

## Documents, Decks, And Information Artifacts

These sources are not UI routes, but they contain reusable information-design guidance that applies to Build Loop artifacts and UI-generated outputs.

Claude local session sources inspected:

- `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/d079a27d-83ff-4615-a11e-9eb6cad377f1/a2656a3a-bc97-4eae-9598-a705fad2e796/rpm/plugin_018devm9MHc4viTEy8PNz6yU/skills/deck-builder/SKILL.md`
- `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/d079a27d-83ff-4615-a11e-9eb6cad377f1/a2656a3a-bc97-4eae-9598-a705fad2e796/rpm/plugin_01B9qBXwJFMZce4Tr79fyh6t/skills/calm-precision-pptx/SKILL.md`
- `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/d079a27d-83ff-4615-a11e-9eb6cad377f1/a2656a3a-bc97-4eae-9598-a705fad2e796/rpm/plugin_01XXJmxLXPEhPMmnxmrgntNw/skills/design-system/SKILL.md`
- `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/d079a27d-83ff-4615-a11e-9eb6cad377f1/a2656a3a-bc97-4eae-9598-a705fad2e796/rpm/plugin_01XXJmxLXPEhPMmnxmrgntNw/skills/accessibility-review/SKILL.md`
- `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/a2656a3a-bc97-4eae-9598-a705fad2e796/d079a27d-83ff-4615-a11e-9eb6cad377f1/skills/storyline-builder/SKILL.md`
- `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/a2656a3a-bc97-4eae-9598-a705fad2e796/d079a27d-83ff-4615-a11e-9eb6cad377f1/skills/pyramid-principle-core/SKILL.md`
- `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/a2656a3a-bc97-4eae-9598-a705fad2e796/d079a27d-83ff-4615-a11e-9eb6cad377f1/skills/pptx-design/SKILL.md`
- `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/a2656a3a-bc97-4eae-9598-a705fad2e796/d079a27d-83ff-4615-a11e-9eb6cad377f1/skills/docx/SKILL.md`

Codex runtime sources inspected:

- `/Users/tyroneross/.codex/plugins/cache/openai-primary-runtime/documents/26.521.10419/skills/documents/SKILL.md`
- `/Users/tyroneross/.codex/plugins/cache/openai-primary-runtime/presentations/26.521.10419/skills/presentations/SKILL.md`
- `/Users/tyroneross/.codex/plugins/cache/openai-primary-runtime/presentations/26.521.10419/skills/presentations/subagent-instructions.md`

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

Note: the alternate no-space path `/Users/tyroneross/Library/ApplicationSupport/Claude/local-agent-mode-sessions/.../rpm` was checked and did not exist in this environment. Use the `Application Support` path with the space.

## Research And Vault Corpus

Use for background, trend history, or long-term preference synthesis. Summarize before passing to implementers.

High-signal local research:

- `/Users/tyroneross/research/topics/design/`
- `/Users/tyroneross/research/projects/atomize-ai/design-brief-2026-04-20.md`

High-signal vault folders:

- `/Users/tyroneross/ObsidianVault/raw/engineering/ui-design-research-corpus-2026/`
- `/Users/tyroneross/ObsidianVault/raw/engineering/ui-ux-calm-precision/`
- `/Users/tyroneross/ObsidianVault/raw/engineering/ui-ux-central-library/`
- `/Users/tyroneross/ObsidianVault/raw/engineering/ui-ux-ibr-references/`
- `/Users/tyroneross/ObsidianVault/raw/engineering/ui-ux-spec-review-input-output-coverage-2026/`
- `/Users/tyroneross/ObsidianVault/raw/engineering/native-ios-watchos-ui-research-2026/`
- `/Users/tyroneross/ObsidianVault/raw/engineering/calm-precision-v1-1/`
- `/Users/tyroneross/ObsidianVault/raw/engineering/calm-precision-native-apple-platforms-v1-1-2026/`
- `/Users/tyroneross/ObsidianVault/raw/engineering/ui-ux-private-ios-guidance/`
- `/Users/tyroneross/ObsidianVault/raw/engineering/ui-ux-misc-app-design/`
- `/Users/tyroneross/ObsidianVault/outputs/drafts/2026-05-10-ui-preferences-mobile-first-web-apps-aggregate.md`
- `/Users/tyroneross/ObsidianVault/outputs/drafts/2026-05-10-private-product-mobile-first-ui-guidance.md`

## Build-Loop-Memory

Durable long-term design memory belongs here:

- `/Users/tyroneross/dev/git-folder/build-loop-memory/design/README.md`
- `/Users/tyroneross/dev/git-folder/build-loop-memory/decisions/build-loop/0094-2026-05-24-build-loop-design-structure-memory-policy.md`
- `/Users/tyroneross/dev/git-folder/build-loop-memory/projects/<project>/design/` when project-specific design memory exists.
- `/Users/tyroneross/dev/git-folder/build-loop-memory/indexes/` for discovery after the migration structure settles.

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
  /Users/tyroneross/dev/git-folder/build-loop \
  /Users/tyroneross/dev/git-folder/build-loop-memory \
  /Users/tyroneross/dev/git-folder/interface-built-right \
  '/Users/tyroneross/dev/git-folder/UI Guidance' \
  /Users/tyroneross/dev/git-folder/mockup-gallery \
  /Users/tyroneross/research \
  /Users/tyroneross/ObsidianVault
```

Record newly durable structures in build-loop-memory; record runtime choices in `.build-loop/app-contract/ui.md`.
