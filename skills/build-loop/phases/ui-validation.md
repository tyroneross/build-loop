# Phase Guidance: UI Validation

How build-loop turns design rules into gates. Triggered when Phase 1 ASSESS detects a UI target (`uiTarget != null`).

## Why this exists

A retrospective on a real shipped build (SpeakSavvy build 50) found that mockup-parity validation was insufficient. Subagents reproduced mockups faithfully, including anti-patterns the project's own design rules forbade — colored status pills, ungated `repeatForever` animations, theme-token bypass, hardcoded font sizes in body copy.

Root cause: design rules in `CLAUDE.md` were loaded as session context but didn't enter individual subagent prompts. Knowledge that doesn't enter the prompt doesn't reach the code.

This phase guidance treats design rules as gates, not advisories.

## When triggered

Phase 1 ASSESS sets `uiTarget` to one of `web`, `mobile`, or `null` based on project signals (see SKILL.md §Sub-routers — `ios/`, `*.swift`, `*.xcodeproj` → mobile/apple; `app.json` (Expo) → mobile/react-native; else web).

When `uiTarget != null`, the UI gates wire in automatically:

1. **Phase 1 (Assess)** — mockup pre-flight scan + required UI scoring criteria
2. **Phase 2 (Plan)** — mockup-gallery hook for major UI work (new page or ≥40% redesign): draft B&W mockups via `mockup-gallery:mockup-session-new` before any UI is written. The exception to build-loop's "actions/functions only, no plugin UI" policy — mockup drafting is itself the action.
3. **Phase 3 (Execute)** — verbatim subagent-prompt template injection on every UI dispatch
4. **Phase 4 sub-step B (Validate)** — IBR-first quick pass via `ibr_quickpass.py` (runs project's existing `.ibr-test.json` suite); falls back to design-rule scanner on changed files when IBR unavailable
5. **Phase 4 sub-step D (Fact-Check)** — Gate 5 design-rule scanner across full project; Gate 7 UX triage scanner (interactability, performance, data-accuracy, usability) writing queue entries to `.build-loop/ux-queue/`; Gate 8 IBR coverage-gap detector drafting new tests for uncovered surfaces
6. **Phase 5 (Iterate)** — drains the UX queue alongside Validate failures; parallel fan-out (≤4) for independent fixes; IBR `interact_and_verify` after each UI fix before re-validating

## UX scan dimensions (Gate 7)

Static portion runs via `ux_triage.py`. Agent-driven portion runs alongside via `performance-assessor` and `fact-checker` for the dimensions the static scanner can't fully cover.

| Dimension | Static checks | Agent augmentation |
|---|---|---|
| Interactability | Buttons without handlers, anchors without href/onClick, icon buttons missing aria-label, empty SwiftUI Button closures | IBR `interact_and_verify` when present (replaces grep for tappability) |
| Performance | N+1 fetch in forEach/map, unbounded useEffect, full-lib lodash imports | `performance-assessor` agent: profile or simulate the full app, return findings outside static scope |
| Data accuracy beyond current scope | Hardcoded percent/dollar/year literals in JSX, "as of <date>" strings | `fact-checker` agent: walk full rendered surface, not just changed files |
| Usability heuristics | Status badges using background color, lists without empty/error branch | LLM judge sub-prompt for hierarchy/empty-state/status-clarity (confidence ≥ medium only) |

Each `blocker` or `major` finding becomes a queue entry from `templates/ux-fix-plan.md`. Minor findings → Review-F report only. `architecture_impact: true` entries pause for user confirmation in Review-F before Iterate dequeues.

## IBR-first ordering (Sub-step B)

When IBR is available and `uiTarget != null`, Sub-step B runs the project's existing `.ibr-test.json` suite via `scripts/ibr_quickpass.py` BEFORE any other validator. Rationale: a passing test suite the project already maintains is the strongest possible signal — much stronger than a grep finding. If the suite passes, sub-step B is green-lit. If any test fails, the failing test's assertion becomes the Iterate fix target directly. Only headless/programmatic IBR surfaces are used; the IBR viewer is never opened by build-loop. Full protocol: `Skill("build-loop:ibr-bridge")`.

## Skills to load by platform

The orchestrator must load these skills before dispatching subagents:

| Platform | Always | + Platform skills |
|---|---|---|
| SwiftUI / iOS / macOS / watchOS | `calm-precision` | `ibr:ios-design`, `ibr:apple-platform`, `ibr:macos-ui` (macOS only) |
| React / Next.js / web | `calm-precision` | `frontend-design`, `ibr:mobile-web-ui` |
| Vue / Svelte | `calm-precision` | `frontend-design` |
| Native iOS guidance | `calm-precision` | `ibr:ios-design-router` (auto-classifies app archetype) |

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

These scope to changed files only — pre-existing violations elsewhere are logged to `.build-loop/issues/` and tracked separately, not blocking the current build.

## Phase 3 (Execute) — Subagent prompt template

Every UI subagent prompt (those touching `Views/`, `*.swift`, `*.tsx`, etc.) MUST be prepended with the verbatim contents of `templates/ui-subagent-prompt.md`.

Pseudocode:
```js
const uiPreamble = readFile('templates/ui-subagent-prompt.md')
const fullPrompt = uiPreamble + '\n\n---\n\n' + taskSpecificContract
dispatchSubagent({ prompt: fullPrompt, ... })
```

The template covers: skill loading mandate, mockup-vs-rule conflict policy (rule wins), 8-item anti-pattern checklist, required env hooks, self-verification requirement.

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

### Visual validation (lesson from build 53–55)

The static scanner caught zero issues with a semicircle gauge that rendered upside-down with an invisible track stroke and stray floating tick marks. The bug was only visible by rendering the screen and looking at it. **Static rules cannot catch rendering bugs.** Visual validation is a separate, non-negotiable gate.

Per platform:

| Platform | Tool |
|---|---|
| iOS / macOS / watchOS | Build → install on booted simulator → launch → `mcp__plugin_ibr_ibr__native_scan` |
| Web (Next/Vite/Vue) | Start dev server → `mcp__plugin_ibr_ibr__scan` against URL |
| Fallback | `xcrun simctl io booted screenshot <path>` (iOS), Playwright headless (web) |

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
- ✅ Mockup-vs-rule conflicts documented in subagent output
- ✅ **Visual validation: every changed screen rendered and inspected against mockup**
  - Geometry correct (arcs, charts, custom paths)
  - No clipping behind floating bars / safe areas
  - No unexpected text wrapping or element overlaps
  - Track strokes visible against actual background palette
- ✅ Reduce Motion smoke test passes (manual or via simulator)

If all six pass on the first build attempt, the gates worked as designed. If any fail, the gates need tightening — file a feedback note in `.build-loop/feedback.md`.

Real bugs that bypassed the gates and were only caught visually (build-loop hardening came from these):
- **Build 53–55 (SpeakSavvy)**: Semicircle gauge rendered upside-down because `Path.addArc clockwise:true` traces the bottom half in SwiftUI's flipped y-axis. Track stroke used `Theme.surfacePrimary` (#1A1F26), invisible against `Theme.background` (#0F1419). Tick marks correctly placed but appeared "stray" because no arc was visible. Static scanner couldn't see this.
- **Build 53–56 (SpeakSavvy)**: Detailed mode's 7th dim row, Profile's version row, Practice's bottom drill all clipped behind iOS 26 floating tab bar because `safeAreaPadding` didn't push scroll content. Required explicit `Color.clear.frame(height: 100)` spacer.
- **Build 56 (SpeakSavvy)**: "Engagement" dim name wrapped to 2 lines in History session chips because no `lineLimit` + `minimumScaleFactor` constraint, AND right column timestamps wrapped because no `fixedSize` width policy.

These shipped to TestFlight before being caught — gates are now tightened to prevent recurrence.

## Tuning the rule packs

The scanner's regex patterns are hand-maintained in `scanners/audit-design-rules.mjs`. Add new patterns when:
- A real shipped violation slips through and is surfaced in audit
- A new platform/framework needs a pack
- A rule pack has false positives degrading agent productivity (relax pattern, add `pathExclude`)

Each pattern entry: `{ id, severity, description, pattern, contextRequired?, contextWindow?, fileMustContain?, invertFileCheck?, pathExclude? }`. See the scanner source for full schema.

## Out of scope for this phase guidance

- Visual regression testing (use IBR `compare` / `native_compare`)
- Runtime accessibility audits (use IBR `design-validation`)
- Performance gates (sub-step B standard graders cover those)
- Cross-browser pixel diff (separate concern)
