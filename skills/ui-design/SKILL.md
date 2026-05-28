---
name: ui-design
description: Use when build-loop needs UI design direction, visual style selection, UI guidance inventory, a .build-loop/app-contract/ui.md design contract, or non-trivial web/mobile/native UI planning. Build-loop-owned design route for design-contract-specialist; selects from project tokens, recent structures, UI Guidance, IBR artifacts, Mockup Gallery, and research based on product/workflow/data/platform fit.
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# UI Design

Build-loop-owned guidance for choosing UI direction. This skill is the design selection layer; `design-contract-specialist` is the agent that writes the resulting contract.

For broader communication and information-design artifacts such as writing, images, decks, documents, reports, spreadsheets, PDFs, or UI-generated exports, start from `references/universal-design-principles.md`, then load the medium-specific skill.

## Runtime Contract

Use this skill when `uiTarget != null` and the work is not copy-only:

1. Load the UI input/output contract from the plan.
2. Load `references/universal-design-principles.md` when the surface presents information, contains charts/tables/reports, creates document/deck/image-like outputs, or needs continuity, wayfinding, process visibility, or graceful degradation.
3. Apply Calm Precision as the structural foundation: hierarchy, grouping, disclosure, action weight, touch targets, motion restraint, copy clarity, and functional integrity.
4. Load project-local visual evidence: current screens, screenshots, tokens, components, selected mockups, and existing `.build-loop/app-contract/ui.md`.
5. Select only the guidance sources needed for the surface from `references/ui-guidance-sources.md`.
6. Choose the design direction from product fit: user job, workflow frequency, data shape, information density, platform, accessibility risk, and error cost.
7. Have `design-contract-specialist` write the decision to `.build-loop/app-contract/ui.md`.

## Design Layers

Think in layers, not competing design systems:

1. **Product contract** — the UI input/output contract, user job, platform, data shape, and risk.
2. **Universal communication and experience design** — answer-first structure, one primary focus, continuity, wayfinding, process visibility, graceful degradation, MECE grouping, hierarchy, source integrity, native primitives, accessibility, and visual QA across UI, writing, images, decks, docs, operational workflows, and data artifacts.
3. **Calm Precision foundation** — the baseline rules for cognitive predictability and implementation discipline.
4. **Project surface** — existing tokens, components, brand, screenshots, and selected mockups.
5. **Structure or style mode** — recent design structures such as Conversational Command Surface, Bento Operating Dashboard, Pipeline Wizard, Outcome Ledger, Pyramid Detail, Glass Workspace, Warm Craft, Data Narrative, native mobile, or AI Artifact Canvas.
6. **Validation evidence** — `ui-validator`, design-rule scanner, browser/simulator screenshots, and contract traceability.

Calm Precision is not just one optional theme. It stays active underneath every selected mode. A Glass, Warm Craft, Aurora, Data Narrative, or native mobile direction can change surface treatment, density, and mood, but it must not override Calm Precision's hierarchy, accessibility, motion, interaction, and real-data rules unless the app contract records an explicit exception.

## Source Priority

Resolve conflicts in this order:

1. Explicit user requirement for this build.
2. Current product/workflow/data/platform need.
3. Existing project tokens, components, and current UI conventions.
4. `.build-loop/app-contract/ui.md` and the plan's UI input/output contract.
5. Universal information-design principles from `references/universal-design-principles.md`.
6. Calm Precision structural rules.
7. Build-loop references: `skills/build-loop/references/ui-io-contract.md`, `skills/build-loop/references/recent-design-structures.md`, and `skills/build-loop/templates/ui-subagent-prompt.md`.
8. Local guidance sources from `references/ui-guidance-sources.md`.
9. Research or vault material, summarized into a concrete decision before implementers receive it.

## Required Output

The design decision must be written into `.build-loop/app-contract/ui.md` before implementation for non-trivial UI work. Include:

- `selected_structure` and why it fits.
- At least one rejected structure and why it was rejected.
- Source refs used, with absolute paths when outside the repo.
- Density, hierarchy, surface model, typography roles, token source, action hierarchy, visual non-goals, and validation implications.

Implementers should read the app contract and UI input/output contract, not the whole guidance corpus.

## Guardrails

- Do not route to IBR unless the user explicitly asks for IBR, Interface Built Right, or an IBR-specific artifact.
- Do not force recent structures. They are options, not requirements.
- Do not treat Calm Precision as a surface style that excludes other modes. Use it as the shared baseline under the selected mode.
- Do not load broad vault/research folders into implementation prompts. Select one to three relevant sources and synthesize them.
- Do not introduce mock data, fake affordances, arbitrary palettes, or decorative visual complexity that does not serve the workflow.
- If a major UI build lacks enough visual evidence, ask the orchestrator for mockup/screenshot/design-tool artifacts; keep the final design decision in `.build-loop/app-contract/ui.md`.
- When gathering evidence from owned/reference apps, route token-extraction to source-of-truth reads and interaction-texture (motion, haptics, render gradients, transition states) to live IBR capture — see `references/evidence-capture-policy.md`.
