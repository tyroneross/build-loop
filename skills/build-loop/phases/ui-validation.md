# Phase Guidance: UI Validation

How build-loop turns design rules into gates. Triggered when Phase 1 ASSESS detects a UI target (`uiTarget != null`).

## Why this exists

A retrospective on a real shipped build (SpeakSavvy build 50) found that mockup-parity validation was insufficient. Subagents reproduced mockups faithfully, including anti-patterns the project's own design rules forbade — colored status pills, ungated `repeatForever` animations, theme-token bypass, hardcoded font sizes in body copy.

Root cause: design rules in `CLAUDE.md` were loaded as session context but didn't enter individual subagent prompts. Knowledge that doesn't enter the prompt doesn't reach the code.

This phase guidance treats design rules as gates, not advisories.

## When triggered

Phase 1 ASSESS sets `uiTarget` to one of `web`, `mobile`, or `null` based on project signals (see SKILL.md §Sub-routers — `ios/`, `*.swift`, `*.xcodeproj` → mobile/apple; `app.json` (Expo) → mobile/react-native; else web).

When `uiTarget != null`, the four UI gates wire in automatically:

1. **Phase 1 (Assess)** — mockup pre-flight scan + required UI scoring criteria
2. **Phase 3 (Execute)** — verbatim subagent-prompt template injection on every UI dispatch
3. **Phase 4 sub-step B (Validate)** — design-rule scanner on changed files
4. **Phase 4 sub-step D (Fact-Check)** — Gate 5 design-rule scanner across full project

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

Add to the code-based grader pass:

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

## Phase 4 sub-step D (Fact-Check) — Gate 5

Runs alongside Gate 1 (Fact Checker), Gate 2 (Mock Data Scanner), Gate 3 (NavGator violations), Gate 4 (Plugin Cache Sync). Same scanner as Validate sub-step B, broader scope (full project, not just changed files). Surfaces any pre-existing must-fix violations newly observable due to scanner rule additions.

Pre-existing must-fix findings on first run for a project: log to `.build-loop/issues/` with break-what-if analysis (user decides scope, not auto-remediated). New-content findings are blocking.

## Acceptance for "production-ready UI on first pass"

A UI build is considered production-ready when:

- ✅ Build succeeds
- ✅ Tests pass (or pre-existing failures are documented)
- ✅ Mockup-parity verified (visual + element diff)
- ✅ Design-rule scanner exits 0 on changed files
- ✅ Mockup-vs-rule conflicts documented in subagent output
- ✅ IBR scan confirms rendered UI matches mockup intent
- ✅ Reduce Motion smoke test passes (manual or via simulator)

If all seven pass on the first build attempt, the gates worked as designed. If any fail, the gates need tightening — file a feedback note in `.build-loop/feedback.md`.

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
