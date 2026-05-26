<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Recent Design Structures

Runtime reference for `design-contract-specialist` when choosing UI design direction. This file is a compact structure library, not a style mandate.

## Use Rule

Pick the structure that fits the current product job, workflow frequency, information density, data shape, platform, and risk. Do not apply a structure because it is recent, rated well, visually attractive, or available in this file.

Every Phase 2 `## Design Direction` should record:

1. `selected_structure`
2. `why_it_fits`
3. `rejected_structure`
4. `why_rejected`
5. `source_refs`
6. `validation_implications`

## Selection Axes

Classify the surface before choosing structure:

| Axis | Questions |
|---|---|
| Product job | Is the user operating, deciding, conversing, building, analyzing, configuring, recovering, or learning? |
| Workflow frequency | Is this a repeated work surface, a one-time onboarding path, or an occasional settings/detail surface? |
| Data shape | Is the core object a message, list, table, step sequence, chart, document, graph, timeline, or media artifact? |
| Density | Does the user need scan, compare, decide, or deeply read? |
| Platform | Web desktop, mobile web, native iOS, macOS, watchOS, or cross-platform? |
| Risk | Could a wrong click, fake affordance, hidden source, or misleading chart cause harm? |

## Structures

### 1. Conversational Command Surface

Use when the product starts from user intent in natural language but must produce a concrete artifact, decision, skill, or task output.

Structure:
- Header with product identity and one primary action.
- Two-lane layout: conversation/input lane plus outcome/status lane.
- Composer is prominent but not the whole product when outputs matter.
- Suggested prompts are subordinate; they are accelerators, not competing primary actions.
- Output cards show concrete value, feasibility, or next step.

Good fit:
- AI assistants that build artifacts.
- Intake-to-plan flows.
- "Tell me the problem, then ship the output" products.

Reject when:
- The user primarily monitors many standing objects.
- The user needs direct table/list management more than conversation.

Recent refs:
- `UI Guidance/mockups/decision-doctor--v2-01-sunrise-hero.html`
- `UI Guidance/mockups/decision-doctor--v2-02-electric-mint.html`
- `UI Guidance/mockups/decision-doctor--v2-05-bloom-organic.html`

### 2. Bento Operating Dashboard

Use when the product is a repeated home base with multiple live objects, one central creation/ask action, and several scannable operational tiles.

Structure:
- Hero ask/action tile dominates.
- Secondary tiles show ledger, active objects, queue, and quick decisions.
- Use a 12-column grid on desktop; cards span according to importance.
- Keep the chat/action as one tile, not the whole app.
- The dashboard should answer: what is active, what changed, what is next, what can I do now?

Good fit:
- Personal operating systems.
- Product home screens where the user returns often.
- Workflow dashboards with useful standing state.

Reject when:
- The user has one linear task.
- The product has no real standing data.

Recent refs:
- `UI Guidance/mockups/decision-doctor--v2-04-bento-dashboard.html` (rated yay)

### 3. Pipeline Wizard

Use when a multi-step process needs visible progress, traceable decisions, and contextual input for each step.

Structure:
- Top stepper with 3-5 named steps.
- Current step panel owns the main content.
- Contextual chat/input lives beside or below the current step.
- Each step exposes criteria, intermediate output, and next action.
- Final step produces a concrete artifact or decision.

Good fit:
- MCDA, evaluation, diagnosis, onboarding, build pipelines.
- Workflows where the user should understand why the next step exists.

Reject when:
- Steps are decorative or could be one form.
- The user needs fast repeated scanning instead of guidance.

Recent refs:
- `UI Guidance/mockups/decision-doctor--v2-03-pipeline-wizard.html`

### 4. Outcome Ledger List

Use when the user needs to scan a collection of prior decisions, jobs, skills, or outcomes and understand value quickly.

Structure:
- Summary/ledger hero at top.
- Filter chips directly below the hero.
- List cards include title, category/status, primary outcome metric, and next affordance.
- Rows must be scannable in about 2 seconds.
- Empty state preserves layout height and names what will appear.

Good fit:
- Decision history, saved automations, skill libraries, task queues, project ledgers.

Reject when:
- The collection has no reliable outcome metric.
- Detail reading is more important than list scanning.

Recent refs:
- `UI Guidance/mockups/decision-doctor--v2-06-decisions-list-fun.html`

### 5. Pyramid Detail Page

Use when a detail view must replace a wall of text with a decision-first hierarchy.

Structure:
- Hero states the primary outcome in plain language.
- Three MECE supporting cards explain why.
- This-week/current-impact section makes near-term value concrete.
- Paired paths section shows viable alternatives without nudging.
- MCDA/math/provenance sit under disclosure.
- Keep no more than 5 major chunks visible at once.

Good fit:
- Decision details, recommendation details, audit explanations, strategy outputs.

Reject when:
- The user needs raw source review as the primary job.
- There is no actual decision, outcome, or rationale to explain.

Recent refs:
- `UI Guidance/mockups/decision-doctor--v2-07-detail-fun.html`

### 6. Glass Workspace

Use for data-rich professional tools where the user manages structured information and needs craft without losing density.

Structure:
- Sidebar or stable navigation when object count is high.
- Left-border accent as category/status signal.
- Source dot plus text metadata, not separate badge clutter.
- Card grid uses `repeat(auto-fill, minmax(300-320px, 1fr))` at desktop.
- Optional third detail pane at 280-320px for focused review.

Good fit:
- Developer tools, knowledge bases, dashboards, pipeline monitors.

Reject when:
- The surface is mobile-first, reading-heavy, or calm/clinical.
- Glass effects would reduce contrast or obscure content.

Recent refs:
- `UI Guidance/cross-platform-design-patterns.md`
- `UI Guidance/aurora-deep.md`
- `UI Guidance/aurora-glass.md`

### 7. Warm Craft Workbench

Use for reflective writing, document work, knowledge organization, or human-feeling tools where warmth helps trust and comprehension.

Structure:
- Warm neutral base with restrained amber/coral accents.
- Left accent bar connects cards, navigation, and section headers.
- Sectioned sidebar or grouped content blocks.
- Generous but structured spacing.
- Texture is enhancement only; content structure must work without it.

Good fit:
- Writing tools, personal knowledge systems, review surfaces, human-in-the-loop planning.

Reject when:
- The user needs dense operational monitoring.
- Warm palette could make risk, status, or urgency ambiguous.

Recent refs:
- `UI Guidance/warm-craft.md`
- `UI Guidance/cross-platform-design-patterns.md`

### 8. Data Narrative

Use when the UI presents research, trends, news, benchmarks, or evidence and needs to tell the user what matters before showing the raw data.

Structure:
- Decision-first title or subtitle above every chart.
- Dark atmospheric hero is allowed only when it introduces the domain; dense content should move to a readable light or neutral area.
- Bento/grid sections can organize evidence, but every chart needs a reason to exist.
- Source attribution is visible near the chart or section.

Good fit:
- Market research, briefing, analytics, trend reporting, intelligence products.

Reject when:
- The data is too sparse or unreliable.
- A sentence would communicate the answer better than a chart.

Recent refs:
- `UI Guidance/data-visualization-patterns.md`
- `UI Guidance/cross-platform-design-patterns.md`

### 9. Native Mobile Action System

Use for native or mobile-web surfaces where touch certainty, safe areas, and progressive disclosure matter more than desktop density.

Structure:
- One hero CTA per home screen.
- Touch targets: 44pt/px minimum; primary capture actions can be larger.
- Every mobile-web touchable has a visible resting container; do not rely on hover/cursor.
- Expandable cards beat sheets/modals when comparison and context retention matter.
- Design tokens cover colors, radius, elevation, and typography; raw numbers are exceptions.
- Haptics, button spring physics, and elevation communicate action feedback on native platforms.
- Liquid Glass belongs on navigation-layer controls only, availability-gated for iOS 26+, not stacked on content cards.

Good fit:
- Native iOS/macOS companion apps, mobile web tools, timer/voice/drill/session surfaces.

Reject when:
- The app is desktop-primary and comparison density is the core job.
- Touch affordance choices would add visual noise to a pointer-first surface.

Recent refs:
- `interface-built-right/mobile-ui/patterns/expandable-card-pattern.md`
- `interface-built-right/mobile-ui/patterns/design-token-architecture.md`
- `interface-built-right/mobile-ui/patterns/liquid-glass-ios26.md`
- `interface-built-right/mobile-ui/lessons/mobile-web-action-affordance.md`
- `interface-built-right/mobile-ui/lessons/home-screen-simplification.md`

### 10. AI Artifact Canvas

Use when the user is generating or editing a durable artifact, not just receiving a chat answer.

Structure:
- Split input/control lane from artifact canvas.
- Artifact gets stable identity, autosave, status, and version/undo affordance.
- Regeneration is scoped per section/block when possible.
- Loading states preserve layout and show phase, not generic spinners.
- Source/citation/provenance spine stays visible when claims matter.

Good fit:
- Document generation, research briefs, reports, code/spec generation, design drafts.

Reject when:
- Output is disposable or single-turn.
- The product has no artifact lifecycle.

Recent refs:
- UI-guidance memory family: AI-generation UX additions, source-grounded trust, semantic zoom.

## Cross-Cutting Rules

- Typography and text hierarchy come before surface treatment.
- Color tokens come before gradients, shadows, texture, or glass.
- Left-border accents are useful categorical structure, but not mandatory on every product.
- Status should not be a decorative pill by default; use text, weight, and placement unless a platform/system component requires a badge.
- Charts require a confidence gate: at least 3 comparable points, trustworthy source, and a pattern/comparison/trend that text alone would not communicate as well.
- Motion is final polish and must respect reduced-motion settings.
- Empty, loading, error, disabled, success, permission, and overflow states are part of the structure, not afterthoughts.

## Long-Term Memory Boundary

This file is the short-horizon runtime reference. Durable cross-project lessons, rating history, and changes over time live in `build-loop-memory`, especially decisions under `decisions/build-loop/`. Update build-loop-memory when a new structure is repeatedly used, explicitly selected/rejected, or materially changes build-loop's design-selection policy.
