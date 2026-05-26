<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Universal Communication And Design Principles

Canonical cross-medium communication and experience doctrine.

Use this file when an artifact must communicate clearly, guide a user through
an experience, or expose a changing process, whether the output is app UI,
writing, images, native screens, slides, Word documents, Google Docs, PDFs,
spreadsheets, reports, dashboards, charts, version-control flow, deployment
flow, sync process, automation, or design handoff.

Build Loop is the current runtime home for this file, but the principles are
not Build Loop-specific. Medium-specific skills still own their exact
primitives. This file owns the principles that should not change across media.

## How To Use

1. Start with the audience, task, decision, purpose, current state, target end
   state, route, and risk.
2. Apply the universal principles below.
3. Load the medium-specific adapter after that:
   - App UI: `build-loop:ui-design`, Calm Precision, UI input/output contract.
   - Writing: pyramid, storyline, voice, editing, and review skills.
   - Images/media: image brief, composition, accessibility, and provenance.
   - Decks: storyline, pyramid, deck-builder, presentation QA.
   - Documents: document preset, form factor selection, render QA.
   - Reports/spreadsheets: decision-first tables, charts, source discipline.
   - Operational workflows: version control, builds, deploys, sync, migration,
     automation, and any process where state changes over time.
4. Record any deliberate exception in the active contract, plan, or artifact
   notes. Exceptions should name the reason, not just the visual preference.

Core rule: medium changes primitives; principles stay stable.

## Universal Versus Specific Guidance

Use a three-layer split:

1. **Universal principles**: rules that apply to every communication,
   artifact, interface, or process. Examples: orient the user, show purpose and
   end state, preserve continuity, expose state changes, support recovery, avoid
   fake proof, and verify the real output.
2. **Routers**: classify the use case and narrow the option space before
   selecting a specific skill or reference. Examples: app archetype routers,
   web archetype routers, document archetypes, deck profiles, and operational
   workflow states.
3. **Domain skills or references**: provide the medium-specific primitive.
   Examples: a side drawer in UI, a transition phrase in writing, a slide action
   title in a deck, a Word style in a document, a chart type in data, or a commit
   SHA in version control.

The Calm Precision router is the model to follow: classify the archetype first,
apply defaults, route to domain files only when needed, ask only at high-impact
choice points, and flag choices made without asking. It does not make every
component option universal. It separates the stable decision process from the
specific UI primitive.

Conflict order should generally be:

1. Explicit user requirement.
2. Current project or artifact contract.
3. Router classification and archetype defaults.
4. Domain skill or reference.
5. Universal principles as the floor that should not be violated without a
   named exception.

## Universal Experience Model

Every communication or interface has the same underlying job:

1. **Orient**: make clear where the user or reader is, why this matters, and
   what frame they are operating in.
2. **Focus**: make the primary message, object, decision, state, or action
   obvious.
3. **Connect**: show how the current piece relates to the previous and next
   piece. Avoid abrupt jumps unless the break is intentional and labeled.
4. **Progress**: make movement visible: through a story, task, workflow,
   argument, form, deck, image, or system state.
5. **Recover**: when something fails, explain what happened, preserve context,
   and give a clear next step.
6. **Resolve**: end with a conclusion, action, saved state, handoff, source, or
   intentional stopping point.

This is why transitions matter across media. In writing, continuity may be a
transition phrase, topic sentence, repeated term, or paragraph bridge. In UI, it
may be a breadcrumb, progress stepper, preserved scroll position, side drawer,
bottom sheet, inline expansion, or motion that shows where an object came from.
In an image, it may be visual path, foreground/background staging, captioning,
or annotation. In version control or deployment, it may be branch state, current
diff, checkpoint, pending test, target commit, rollback path, or release status.
The primitive changes; the continuity requirement does not.

## Non-Negotiables

These apply to every artifact produced or reviewed through this system.

1. **Purpose before presentation**: name the user, job, context, time budget,
   decision, and output before choosing layout, style, or tool.
2. **Answer first**: the governing thought, recommendation, or primary state
   should be visible early. Background supports the answer; it should not bury
   it.
3. **One primary focus**: each viewport, slide, section, page, screen, table, or
   chart needs one main point. Competing primaries are a design failure.
4. **Wayfinding is mandatory**: the user or reader should know where they are,
   what changed, what came before, what comes next, and how to get back or
   continue.
5. **Process visibility**: if something can change rapidly, update in stages, or
   affect user trust, expose the purpose, current state, target end state,
   route, checkpoint, owner, and recovery path.
6. **MECE structure**: peer ideas should be the same kind of thing, not
   overlapping, and collectively sufficient for the parent claim.
7. **Reader question discipline**: every section, component, slide, or visual
   should answer the question raised by the level above it: why, how, or why do
   you say that.
8. **Continuity over abruptness**: transitions should preserve orientation.
   Writing needs connective tissue; UI needs navigational continuity; images
   need visual path; decks and docs need section logic and repeated landmarks;
   operational workflows need checkpoints and status deltas.
9. **Hierarchy before decoration**: use position, grouping, type scale, weight,
   contrast, spacing, and reading order before color effects, borders, shadows,
   backgrounds, or motion.
10. **Group, then separate**: use proximity, shared regions, alignment, and
   dividers to show relationships. Avoid isolated boxes for every item.
11. **Content over chrome**: visual furniture must earn its place. Use whitespace
   as structure, not as empty area to fill.
12. **Progressive disclosure**: expose what the user needs now. Hide secondary
   detail behind clear, shallow disclosure. Do not stack nested drawers, modals,
   appendices, or overloaded control panels without a strong reason.
13. **Action weight matches importance**: primary actions, asks, or conclusions
   get the strongest visual weight. Secondary actions stay visibly secondary.
14. **Semantic color only**: color should encode meaning, emphasis, category, or
   status. Decorative color is cut when it competes with comprehension.
15. **Functional integrity**: anything that looks interactive must work. Any
   data, metric, example, claim, or visual proof must be real, sourced, clearly
   labeled as illustrative, or omitted.
16. **Graceful degradation**: when the preferred path fails, the artifact should
   still be usable. Explain what happened, what remains safe, what the user can
   do next, and what context was preserved.
17. **Native primitives over visual fakes**: use the medium's semantic
   structures: UI components, PowerPoint bullets/placeholders, Word styles and
   numbering, table geometry, chart objects, alt text, headings, and source
   notes. Do not simulate them with text glyphs, manual spacing, screenshots, or
   decorative overlays when native structure exists.
18. **Accessibility is design quality**: contrast, readable type, focus order,
   keyboard path, touch targets, alt text, labels, semantic structure, and
   non-color cues are baseline requirements.
19. **State and recovery are part of the design**: loading, empty, error,
   permission, progress, saved, stale, and source-scope states need explicit
   treatment when they affect user decisions.
20. **Fit form to information**: choose prose, bullets, table, chart, diagram,
   checklist, form, callout, or slide rhythm based on the reading task. Do not
   force tables for prose, charts for single numbers, cards for everything, or
   dense paragraphs where a checklist is the real interface.
21. **Inherit before overriding**: preserve project tokens, component grammar,
   document presets, slide templates, and brand systems unless the task requires
   a named deviation.
22. **Verify the rendered artifact**: inspect the real output surface. For UI,
   use screenshots and interaction checks. For decks and docs, render pages or
   slides and inspect for overlap, clipping, drift, broken hierarchy, and
   unreadable density.

## Medium Adapters

Medium-specific skills should adapt the universal principles this way:

| Medium | Native continuity | Native recovery | Native proof |
|---|---|---|---|
| Writing | Transition phrases, topic sentences, repeated terms, parallel structure, SCQA, section headings | Clarify uncertainty, state assumptions, preserve reader context, show what can be concluded now | Claims, citations, examples, line of reasoning |
| Images/media | Visual path, framing, focal hierarchy, captions, annotations, before/after pairing | Alt text, fallback caption, provenance, clear label if illustrative | Source image, generation prompt, asset provenance, labels |
| App UI | Breadcrumbs, nav bars, preserved state, inline expansion, side drawers, bottom sheets, progress steppers, continuity motion | Loading, empty, error, stale, permission, offline, retry, saved-state, partial-result, and fallback states | Real data, working handlers, source labels, logs, screenshots, interaction tests |
| Decks | Claim spine, action titles, section dividers, agenda markers, repeated footer/source grammar | Mark missing inputs, use appendix/notes, preserve template rhythm, state caveats | Source footnotes, proof objects, rendered slide QA |
| Documents/reports | Heading ladder, lead paragraphs, captions, cross-references, repeated table headers, page furniture | Explain constraints, preserve edit trail, route unresolved items to notes/appendix | Citations, styles, numbering, table geometry, rendered page QA |
| Spreadsheets/data | Frozen headers, stable dimensions/measures, filters, tabs, summary-to-detail flow | Formula warnings, missing-data treatment, source flags, clear assumptions | Formulas, source lineage, audit checks, charts/tables tied to decisions |
| Operational workflows | Purpose, start state, target end state, current step, checkpoint, owner, status delta, next action | Rollback path, retry path, partial-completion note, blocked reason, safe stopping point | Git status, diff, commit SHA, test result, deployment target, migration log, sync record |

## Medium-Specific Direction

### App UI

Build around the UI input/output contract: data taxonomy, operations, component
mapping, states, validation/security ownership, and traceability. Calm Precision
is the default structural doctrine. Selected structures and style modes are
allowed only after product, workflow, data shape, platform, and risk are known.

UI continuity should keep the user oriented inside the same work surface where
possible: side drawers for contextual detail, bottom sheets for mobile tasks,
inline expansion for local detail, persistent navigation for route changes,
breadcrumbs for deep paths, and motion only when it explains origin,
destination, or state change. Avoid abrupt screen changes that make the user
reconstruct where they are.

UI degradation should fail softly: preserve input, show what happened, explain
whether the issue is data, permission, network, validation, or system failure,
offer retry or fallback, and make saved/unsaved state explicit.

### Writing

Writing should preserve the reader's place in the argument. Use transition
phrases, parallel sentence structure, callbacks to prior terms, and section
openers that tell the reader how the next point relates to the last one. Do not
stack facts without connective tissue.

When the evidence is incomplete, do not fake certainty. State the conclusion
that is supported, the assumption or missing input, and the next step needed to
resolve it.

### Images And Media

Images should guide the eye through a clear visual path. The main subject,
context, and intended interpretation should be recoverable without hidden
explanation. Captions, alt text, annotations, and provenance are part of the
artifact when they affect interpretation.

Generated or edited images should be labeled by role: inspiration, concept,
reference, illustration, or production asset. Do not let an illustrative image
look like factual proof.

### Decks

Storyline precedes rendering. The deck needs a governing thought, MECE key line,
action titles, and proof objects. At thumbnail size, the contact sheet should
show a coherent system and distinct slide rhythms. At readable size, every
slide should have a claim, evidence, and no filler. Native PowerPoint primitives
and template inheritance are preferred over one-off visual patches.

### Documents

Pick the document archetype before drafting: memo, brief, SOP, workflow, form,
proposal, manual, or report. Resolve a preset into exact page, type, paragraph,
list, table, callout, header, and footer tokens. Use form factors deliberately:
prose for explanation, steps for sequence, checklists for acceptance, tables for
shared fields, callouts for decisions or constraints. Render and inspect pages
before delivery.

### Reports, Spreadsheets, And Data Views

Data presentation is decision-first. Chart titles state the conclusion. Tables
need clear dimensions, measures, labels, units, dates, and source lineage. Use a
chart only when it beats text, and use a table only when row/column comparison
is the real task. Every number should be sourced, derived transparently, or
clearly labeled as illustrative.

### Operational Workflows

Operational workflows include version control, branching, commits, builds,
deployments, migrations, memory sync, plugin installs, automations, and any
process where state changes quickly or the user could lose track of the path.

Before or during the workflow, make these visible:

- Purpose: why this process is running.
- Start state: current branch, dirty files, current version, current artifact,
  current data state, or current deployment target.
- Target end state: commit created, tests passed, PR opened, deploy verified,
  memory updated, migration complete, or automation active.
- Route: the steps that will get from start to target.
- Current step: what is happening now.
- Checkpoints: tests, review gates, commits, rendered previews, backups,
  snapshots, or approval gates.
- Recovery: rollback, retry, pause point, safe stop, or what remains unchanged.

The user should never have to infer whether a process is still in progress,
safe to interrupt, partially complete, blocked, or finished.

## Graceful Degradation And Recovery

When something does not work as expected, the user experience should degrade
into clarity, not harshness.

Every failure or limitation should answer:

1. What happened?
2. Why does it matter to the user or reader?
3. What state was preserved?
4. What can they do now?
5. What happens next if they retry, continue, or stop?

Examples:

- Writing: "The source does not support that claim yet; the current supported
  conclusion is X, and Y remains an assumption."
- UI: "The data failed to refresh. The last saved version is still visible. Try
  again, switch source, or continue with cached data."
- Version control: "Commit failed because tests did not pass. No files were
  reverted. Current branch and staged files are unchanged; fix the failing test,
  unstage, or stop here."
- Deployment: "Preview deploy succeeded but production is blocked pending
  approval. The reviewed artifact is available at X; production has not changed."
- Deck: "Metric unavailable from public source; keep the claim qualitative or
  move the unsupported number to a research note."
- Document: "The render check failed, so structural edits are complete but
  visual layout is unverified."
- Image: "Generated concept only; do not treat text, labels, logos, or factual
  objects inside the image as verified."

Recovery should preserve context wherever possible: user input, scroll position,
draft text, selected filters, slide template, document style, current section,
source notes, and the user's sense of place in the story.

## Strict Versus Flexible

Strict:

- The artifact must have a named audience, job, and output.
- The governing thought or primary state must be clear.
- The user or reader must be oriented in the current state and next step.
- Rapidly changing workflows must show purpose, start state, target end state,
  route, checkpoint, and safe stop.
- Transitions must preserve continuity or intentionally mark a break.
- Peers must be same-kind and logically ordered.
- Claims, numbers, and data-bearing visuals need provenance.
- Interactive controls and generated outputs must be functional or clearly
  labeled as non-production.
- Degraded states must explain what happened and how to recover.
- Accessibility and visual QA are required before declaring the artifact done.
- Medium primitives must be semantic, not visual fakes.

Flexible:

- Visual style, mood, density, and surface treatment.
- Whether the structure is dashboard, narrative, wizard, ledger, canvas, memo,
  appendix, or slide sequence.
- The transition primitive: phrase, heading, animation, drawer, sheet,
  caption, breadcrumb, section divider, or data drilldown.
- The recovery primitive: inline message, note, fallback, retry, appendix,
  caveat, cached result, or alternative route.
- The process primitive: status line, checklist, branch summary, progress
  marker, run log, deploy URL, commit SHA, migration checkpoint, or sync record.
- Exact token palette, typography family, illustration style, and layout rhythm
  when a project has no existing design system.
- How much polish is justified by audience, artifact lifespan, and risk.
- Whether to use a recent design structure, project-local convention, template,
  or a new fit-for-purpose direction.

## Source Anchors

This file synthesizes rules from these local sources. Use them for deeper
medium-specific execution.

| Source | What it contributes |
|---|---|
| `/Users/tyroneross/.agents/skills/calm-precision/SKILL.md` | Cognitive predictability, grouping, hierarchy, disclosure, action weight, state/error rules, functional integrity |
| `/Users/tyroneross/ObsidianVault/raw/engineering/calm-precision-v1-1/0_Router.md` | Router pattern: classify archetype, apply defaults, route to domain files, ask on high-impact choices, flag decisions |
| `/Users/tyroneross/ObsidianVault/raw/engineering/calm-precision-v1-1/1_Navigation_Structure.md` | Task economy, step counting, one primary action, navigation continuity, transition semantics |
| `/Users/tyroneross/ObsidianVault/raw/engineering/calm-precision-v1-1/5_Motion_States_Identity.md` | Purposeful motion, loading states by wait time, empty/error-state context matching, voice calibration |
| `/Users/tyroneross/dev/git-folder/build-loop/skills/ui-design/references/ui-guidance-sources.md` | Build Loop design source map and runtime routing |
| `/Users/tyroneross/dev/git-folder/interface-built-right/references/web-design/0_router.md` | Web archetypes, density defaults, validation-focus risks by product surface |
| `/Users/tyroneross/dev/git-folder/interface-built-right/references/ios-design/0_router.md` | iOS archetype router and domain-reference split |
| `/Users/tyroneross/dev/git-folder/interface-built-right/.codex-plugin/skills/ui-ux-guidance/SKILL.md` | Compact IBR guidance order, target roles, imagegen gates, interaction states, validation contract |
| `/Users/tyroneross/dev/git-folder/UI Guidance/cross-platform-design-patterns.md` | Cross-platform foundational rules, mode selection, content-to-chrome, motion, status, responsive structure |
| `/Users/tyroneross/dev/git-folder/UI Guidance/data-visualization-patterns.md` | Decision-first data, chart-title discipline, figure-ground contrast, direct labeling, source attribution |
| `/Users/tyroneross/dev/git-folder/mockup-gallery/COMMON.md` | Mockup state model, scratch-first lifecycle, selection/implementation tracking, rating semantics |
| `/Users/tyroneross/dev/git-folder/mockup-gallery/skills/mockup-review/SKILL.md` | Scratch-first Codex flow, approved-target guardrails, imagegen assist boundaries |
| `/Users/tyroneross/ObsidianVault/outputs/drafts/2026-05-10-ui-preferences-mobile-first-web-apps-aggregate.md` | Durable mobile-first and desktop-expansion UI preferences |
| `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/d079a27d-83ff-4615-a11e-9eb6cad377f1/a2656a3a-bc97-4eae-9598-a705fad2e796/rpm/plugin_018devm9MHc4viTEy8PNz6yU/skills/deck-builder/SKILL.md` | Storyline-first deck workflow, template inheritance, native PowerPoint primitives, drift-check linter |
| `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/d079a27d-83ff-4615-a11e-9eb6cad377f1/a2656a3a-bc97-4eae-9598-a705fad2e796/rpm/plugin_01B9qBXwJFMZce4Tr79fyh6t/skills/calm-precision-pptx/SKILL.md` | Calm Precision for slides, action titles, one assertion per slide, semantic color, footnoted numeric claims |
| `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/a2656a3a-bc97-4eae-9598-a705fad2e796/d079a27d-83ff-4615-a11e-9eb6cad377f1/skills/storyline-builder/SKILL.md` | Audience brief, governing thought, SCQA, MECE key line, claim contextualization |
| `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/a2656a3a-bc97-4eae-9598-a705fad2e796/d079a27d-83ff-4615-a11e-9eb6cad377f1/skills/pyramid-principle-core/SKILL.md` | Pyramid grouping, vertical question/answer logic, horizontal deductive/inductive logic, SCQA |
| `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/a2656a3a-bc97-4eae-9598-a705fad2e796/d079a27d-83ff-4615-a11e-9eb6cad377f1/skills/pptx-design/SKILL.md` | Encoded deck design systems, density by purpose, type ladder, visual QA, factual audit |
| `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/a2656a3a-bc97-4eae-9598-a705fad2e796/d079a27d-83ff-4615-a11e-9eb6cad377f1/skills/docx/SKILL.md` | DOCX presets, form factor selection, real styles/numbering/tables, render-and-inspect workflow |
| `/Users/tyroneross/.codex/plugins/cache/openai-primary-runtime/documents/26.521.10419/skills/documents/SKILL.md` | Codex document rendering contract, preset selection, Word/Google Docs structural QA |
| `/Users/tyroneross/.codex/plugins/cache/openai-primary-runtime/presentations/26.521.10419/skills/presentations/SKILL.md` | Codex presentation workflow, claim spine, deck profiles, contact-sheet quality bar, source/asset provenance |
| `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/d079a27d-83ff-4615-a11e-9eb6cad377f1/a2656a3a-bc97-4eae-9598-a705fad2e796/rpm/plugin_01XXJmxLXPEhPMmnxmrgntNw/skills/design-system/SKILL.md` | Tokens, components, patterns, documentation, versioning, and migration discipline |
| `/Users/tyroneross/Library/Application Support/Claude/local-agent-mode-sessions/d079a27d-83ff-4615-a11e-9eb6cad377f1/a2656a3a-bc97-4eae-9598-a705fad2e796/rpm/plugin_01XXJmxLXPEhPMmnxmrgntNw/skills/accessibility-review/SKILL.md` | WCAG 2.1 AA baseline, contrast, keyboard, focus, touch targets, name/role/value |
