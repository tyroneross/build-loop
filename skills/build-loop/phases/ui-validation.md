<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase Guidance: UI Validation

How build-loop turns design rules into gates. Triggered when Phase 1 ASSESS detects a UI target (`uiTarget != null`).

## Why this exists

A retrospective on a real shipped build (example app build 50) found that mockup-parity validation was insufficient. Subagents reproduced mockups faithfully, including anti-patterns the project's own design rules forbade — colored status pills, ungated `repeatForever` animations, theme-token bypass, hardcoded font sizes in body copy.

Root cause: design rules in `CLAUDE.md` were loaded as session context but didn't enter individual subagent prompts. Knowledge that doesn't enter the prompt doesn't reach the code.

This phase guidance treats design rules and UI input/output contracts as gates, not advisories.

## When triggered

Phase 1 ASSESS sets `uiTarget` to one of `web`, `mobile`, or `null` based on project signals (see SKILL.md §Sub-routers — `ios/`, `*.swift`, `*.xcodeproj` → mobile/apple; `app.json` (Expo) → mobile/react-native; else web).

When `uiTarget != null`, the UI gates wire in automatically:

1. **Phase 1 (Assess)** — mockup pre-flight scan + required UI scoring criteria
2. **Phase 2 (Plan)** — UI input/output contract section from `references/ui-io-contract.md`, then mockup-gallery hook for major UI work (new page or ≥40% redesign): draft B&W mockups via `mockup-gallery:mockup-session-new` before any UI is written. The exception to build-loop's "actions/functions only, no plugin UI" policy — mockup drafting is itself the action.
3. **Phase 3 (Execute)** — verbatim subagent-prompt template injection on every UI dispatch, including the plan's UI input/output contract
4. **Phase 4 sub-step B (Validate)** — build-loop-owned `ui-validator` first, then design-rule scanner on changed files, contract coverage, code graders, and visual evidence capture
5. **Phase 4 sub-step D (Fact-Check)** — Gate 5 design-rule scanner across full project; Gate 5a UI input/output contract scan; Gate 7 UX triage scanner (interactability, performance, data-accuracy, usability) writing queue entries to `.build-loop/ux-queue/`
6. **Phase 5 (Iterate)** — drains the UX queue alongside Validate failures; parallel fan-out (≤4) for independent fixes; runs the build-loop UI re-validate hook before returning to Review-B

## UX scan dimensions (Gate 7)

Static portion runs via `ux_triage.py`. Agent-driven portion runs alongside via `performance-assessor` and `fact-checker` for the dimensions the static scanner can't fully cover.

| Dimension | Static checks | Agent augmentation |
|---|---|---|
| Interactability | Buttons without handlers, anchors without href/onClick, icon buttons missing aria-label, empty SwiftUI Button closures | `ui-validator` + runtime smoke when a route can be rendered; static grep remains the fallback |
| Performance | N+1 fetch in forEach/map, unbounded useEffect, full-lib lodash imports | `performance-assessor` agent: profile or simulate the full app, return findings outside static scope |
| Data accuracy beyond current scope | Hardcoded percent/dollar/year literals in JSX, "as of <date>" strings | `fact-checker` agent: walk full rendered surface, not just changed files |
| Usability heuristics | Status badges using background color, lists without empty/error branch | LLM judge sub-prompt for hierarchy/empty-state/status-clarity (confidence ≥ medium only) |

Each `blocker` or `major` finding becomes a queue entry from `templates/ux-fix-plan.md`. Minor findings → Review-F report only. `architecture_impact: true` entries pause for user confirmation in Review-F before Iterate dequeues.

## Build-loop designer and validator ordering

When `uiTarget != null`, build-loop owns the design route:

1. Phase 2 loads `build-loop:ui-design`, then dispatches `design-contract-specialist` with `trigger_point: phase2-design-direction` for non-trivial UI work. It reads the UI input/output contract, `references/recent-design-structures.md`, `skills/ui-design/references/ui-guidance-sources.md`, product/workflow needs, project tokens, mockups, screenshots, and local design artifacts, then chooses a fit-for-purpose direction and writes `.build-loop/app-contract/ui.md`. Recent and existing design patterns are inputs, not mandates.
2. Phase 3 implementers receive the UI contract plus `templates/ui-subagent-prompt.md`.
3. Phase 4-B dispatches `ui-validator` first, then runs scanners and code graders.

IBR is not an automatic route. If the user explicitly asks for IBR, the bridge can be invoked manually, but build-loop's default UI path stays inside build-loop-owned agents and artifacts.

## Skills to load by platform

The orchestrator must load these skills before dispatching subagents:

| Platform | Always | + Platform skills |
|---|---|---|
| SwiftUI / iOS / macOS / watchOS | `build-loop:ui-design` + `calm-precision` + `design-contract-specialist` | `apple-dev` when Apple-platform implementation/deploy details matter |
| React / Next.js / web | `build-loop:ui-design` + `calm-precision` + `design-contract-specialist` | `frontend-design` only when explicitly requested |
| Vue / Svelte | `build-loop:ui-design` + `calm-precision` + `design-contract-specialist` | `frontend-design` only when explicitly requested |
| Native iOS guidance | `build-loop:ui-design` + `calm-precision` + `design-contract-specialist` | `apple-dev` for native platform conventions |

Subagents themselves are also instructed to load these skills via the `templates/ui-subagent-prompt.md` preamble.

## Phase 1 (Assess) — Mockup pre-flight

If the project has `mockups/` or `.mockup-gallery/` and the goal references selected mockups:

```
node "${CLAUDE_PLUGIN_ROOT}/skills/build-loop/scanners/audit-design-rules.mjs" \
  --root="<mockups_dir>" \
  --platform=html \
  --json
```

Log output to `.build-loop/issues/mockup-rule-conflicts.md`. Don't block.

Why: mockups can be drawn with anti-patterns. If a mockup has a colored status pill and a rule forbids it, the rule wins. Surfacing the conflict upfront means subagents know to deviate from the mockup at those exact points, with documented justification.

## Phase 1 (Assess) — Required UI criteria

When `uiTarget != null`, append these to the standard scoring criteria (do not ask the user to opt in):

| # | Criterion | Grader | Pass condition |
|---|---|---|---|
| UI-1 | Design-rule compliance | code: `audit-design-rules.mjs` on changed files | exit 0 (must-fix=0) |
| UI-2 | Reduce Motion compliance | code: scanner `animation-without-reducemotion` rule | zero must-fix |
| UI-3 | Theme token usage | code: scanner rules `uicolor-rgb-outside-theme`, `literal-corner-radius`, `hex-color-outside-theme` | zero must-fix |
| UI-4 | Accessibility labels on icon-only graphics | code: scanner `sf-symbol-without-label` rule (or web equivalent) | zero must-fix on changed files |
| UI-5 | Input/output contract coverage | document/read check: `## UI Input/Output Contract` + Review-B trace | every changed UI surface has inputs, outputs, data taxonomy, operation/domain verb, component mapping, states, modality fallback, validation/security, and traceability |

These scope to changed files only — pre-existing violations elsewhere are logged to `.build-loop/issues/` and tracked separately, not blocking the current build.

## Phase 2 (Plan) — UI input/output contract

Before mockups or implementation, the plan must include `## UI Input/Output Contract` using `references/ui-io-contract.md`. The contract is the binding source for component choice. A good row names the surface, every user-provided value, every system-returned value, data taxonomy, operation/domain verb, exact component mapping, all states, modalities and fallbacks, validation/security layers, and schema/API/design-system traceability.

Common failures:
- A chart is planned without naming chart type, data schema, axis labels, and table fallback.
- An AI response is planned without declaring markdown vs JSON vs generated UI, streaming vs complete rendering, abort/retry behavior, and sanitization.
- A rich text or Markdown field is planned as a plain textarea without storage/rendering/sanitization rules.
- A domain operation such as approve, publish, refund, or reorder is collapsed into generic "update" language.
- Delete/update actions lack permission behavior for hidden, disabled, or 403 states.

## Phase 3 (Execute) — Subagent prompt template

Every UI subagent prompt (those touching `Views/`, `*.swift`, `*.tsx`, etc.) MUST be prepended with the verbatim contents of `templates/ui-subagent-prompt.md`.

Pseudocode:
```js
const uiPreamble = readFile('templates/ui-subagent-prompt.md')
const fullPrompt = uiPreamble + '\n\n---\n\n' + taskSpecificContract
dispatchSubagent({ prompt: fullPrompt, ... })
```

The template covers: skill loading mandate, UI input/output contract application, mockup-vs-rule conflict policy (rule wins), anti-pattern checklist, required env hooks, self-verification requirement.

## Phase 4 sub-step B (Validate)

Two graders, both required when `uiTarget != null`.

### Static scanner

```bash
node "${CLAUDE_PLUGIN_ROOT}/skills/build-loop/scanners/audit-design-rules.mjs" \
  --root="<project>" \
  --platform="<auto-detected>" \
  --json
```

Exit code routing:
- 0 → pass (clean)
- 1 → pass with warnings (log, continue)
- 2 → fail (route to Phase 5 Iterate; fix must-fix items before continuing)

### UI input/output contract

Read the plan's `## UI Input/Output Contract` section and compare it to the changed UI files. Pass only when every changed surface maps:

- User inputs and system outputs.
- Structural type, content format, and persistence intent.
- CRUD operation and domain verb.
- Exact input and output component choice.
- Default, populated, focused, disabled, loading, success, error, empty, and streaming states as applicable.
- Text/voice/file/vision/chart/map/AI modality and fallback.
- Presentation, application, and domain validation plus sanitization.
- Auth/authz display behavior for gated actions.
- Data schema/API/design-system traceability.

Missing coverage is a Validate failure unless the diff is copy-only and the contract explicitly says no data surface changed.

### Visual validation (lesson from build 53–55 + session-findings 2026-06-04)

The static scanner caught zero issues with a semicircle gauge that rendered upside-down with an invisible track stroke and stray floating tick marks. The bug was only visible by rendering the screen and looking at it. **Static rules cannot catch rendering bugs.** Visual validation is a separate, non-negotiable gate.

**The BL-1 enforced gate** (`scanners/require-visual-evidence.mjs`) replaces the previous prose requirement with an exit-code check at chunk-close (Phase 3, step 7b) and at Phase 4-B Validate. Symbol/string evidence (`nm`, `strings`, `git grep`, "identifier present", "compiles cleanly") is rejected automatically when `uiTarget != null` and a UI file changed. The required artifacts are a screenshot path (anchored to the running app's pid), an AX-tree dump, or a scan/SSIM result. See `references/phase-3-execute.md` §7b for the wiring.

Per platform:

| Platform | Tool |
|---|---|
| iOS / macOS / watchOS | Build → install on booted simulator → launch → `xcrun simctl io booted screenshot <path>` or built-in native AX driver for macOS |
| Web (Next/Vite/Vue) | Start dev server → browser/screenshot artifact via available host browser tooling |
| Fallback | Static scanner + saved screenshot gap note in Review-G |

Failure modes the visual gate is checking for:
- Geometry rendering correctly (arcs, charts, gauges, custom paths)
- Stroke / track visibility against the actual background palette
- Last row of any scrollable surface clears floating tab bars / safe areas
- No text wrapping unexpectedly (long dim names, timestamps, labels)
- No overlapping elements
- Mockup parity in element placement

If returning-user states need data to render meaningfully (Home dashboards, Profile stats, History rows), use the **DebugSeeder pattern** below to seed test data before scanning.

### DebugSeeder pattern (testability for stateful screens)

UI states reachable only after onboarding / N user actions are unverifiable in a fresh sim install. Add a debug-only seeder gated by `#if DEBUG` and a launch arg, so any subagent can render any state in seconds.

SwiftUI/SwiftData example (from real shipped app):

```swift
// Services/DebugSeeder.swift
#if DEBUG
import Foundation
import SwiftData

enum DebugSeeder {
    static func seedIfEmpty(context: ModelContext) {
        let existing = (try? context.fetch(FetchDescriptor<Session>())) ?? []
        guard existing.isEmpty else { return }
        // Insert representative test data spanning a meaningful range:
        // multiple drill types, dim scores, dates across 7+ days so
        // weekly aggregations and trend deltas have something to compute.
        try? context.save()
    }
}
#endif
```

```swift
// AppEntryPoint.swift init()
#if DEBUG
if CommandLine.arguments.contains("-SeedDebugSessions") {
    DebugSeeder.seedIfEmpty(context: container.mainContext)
}
#endif
```

Launch with seed:
```
xcrun simctl launch booted <bundle-id> -SeedDebugSessions YES -SelectedTab 1
```

For multi-tab apps, also add a `-SelectedTab N` launch arg so any tab opens directly. Both compile out of release builds.

The seeder pays for itself the first time it catches a visual bug.

## Phase 4 sub-step D (Fact-Check) — Gate 5

Runs alongside Gate 1 (Fact Checker), Gate 2 (Mock Data Scanner), Gate 3 (NavGator violations), Gate 4 (Plugin Cache Sync). Same scanner as Validate sub-step B, broader scope (full project, not just changed files). Surfaces any pre-existing must-fix violations newly observable due to scanner rule additions.

Pre-existing must-fix findings on first run for a project: log to `.build-loop/issues/` with break-what-if analysis (user decides scope, not auto-remediated). New-content findings are blocking.

## Acceptance for "production-ready UI on first pass"

A UI build is considered production-ready when ALL of these pass on the first build attempt:

- ✅ Build succeeds
- ✅ Tests pass (or pre-existing failures are documented)
- ✅ Design-rule scanner exits 0 on changed files
- ✅ UI input/output contract covers every changed surface
- ✅ Mockup-vs-rule conflicts documented in subagent output
- ✅ **Visual validation: every changed screen rendered and inspected against mockup**
  - Geometry correct (arcs, charts, custom paths)
  - No clipping behind floating bars / safe areas
  - No unexpected text wrapping or element overlaps
  - Track strokes visible against actual background palette
- ✅ Reduce Motion smoke test passes (manual or via simulator)

If all six pass on the first build attempt, the gates worked as designed. If any fail, the gates need tightening — file a feedback note in `.build-loop/feedback.md`.

Real bugs that bypassed the gates and were only caught visually (build-loop hardening came from these):
- **Build 53–55 (example app)**: Semicircle gauge rendered upside-down because `Path.addArc clockwise:true` traces the bottom half in SwiftUI's flipped y-axis. Track stroke used `Theme.surfacePrimary` (#1A1F26), invisible against `Theme.background` (#0F1419). Tick marks correctly placed but appeared "stray" because no arc was visible. Static scanner couldn't see this.
- **Build 53–56 (example app)**: Detailed mode's 7th dim row, Profile's version row, Practice's bottom drill all clipped behind iOS 26 floating tab bar because `safeAreaPadding` didn't push scroll content. Required explicit `Color.clear.frame(height: 100)` spacer.
- **Build 56 (example app)**: "Engagement" dim name wrapped to 2 lines in History session chips because no `lineLimit` + `minimumScaleFactor` constraint, AND right column timestamps wrapped because no `fixedSize` width policy.

These shipped to TestFlight before being caught — gates are now tightened to prevent recurrence.

## Tuning the rule packs

The scanner's regex patterns are hand-maintained in `scanners/audit-design-rules.mjs`. Add new patterns when:
- A real shipped violation slips through and is surfaced in audit
- A new platform/framework needs a pack
- A rule pack has false positives degrading agent productivity (relax pattern, add `pathExclude`)

Each pattern entry: `{ id, severity, description, pattern, contextRequired?, contextWindow?, fileMustContain?, invertFileCheck?, pathExclude? }`. See the scanner source for full schema.

## Out of scope for this phase guidance

- Cross-build visual regression testing
- Runtime accessibility audits beyond the build-loop validator's route scan
- Performance gates (sub-step B standard graders cover those)
- Cross-browser pixel diff (separate concern)
