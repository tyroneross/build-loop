# Scenario 4: UI build, IBR absent (exercises `fallbacks.md#web-ui`)

## Setup

- **Project**: Next.js app, no IBR installed, no claude-code-debugger, no NavGator
- **Goal**: "Add a settings-panel nav item with active-state indicator"
- **Files to touch**: `src/components/SettingsNav.tsx` (new), `src/styles/nav.module.css` (new)
- **Criteria**:
  1. Tests pass
  2. Lint/type check clean
  3. UI meets Calm Precision principles (a11y, touch targets, handlers)
  4. No mock data

## Pre-fallback behavior (before commit 76f9a26)

**Review sub-step B Validate**:
- `availablePlugins.ibr` is false
- Bridge: (no bridge existed — IBR path skipped silently)
- Criterion 3 (Calm Precision): orchestrator had `fallbacks.md#web-ui` available but no explicit instruction to paste it into the validation subagent. Default behavior: subagent does a best-effort review without structured guidance.
- Output: "Criterion 3 reviewed informally; recommend installing IBR for deep verification." No specific findings.
- Verdict: **soft pass** — nothing concrete flagged, but nothing verified either.

## Post-fallback behavior (after commit 76f9a26)

**Review sub-step B Validate**:
- `availablePlugins.ibr` is false AND build touched UI files (`*.tsx`, `*.module.css`)
- Orchestrator pastes `fallbacks.md#web-ui` into the validation subagent prompt
- Subagent runs the 10 grep checks against the diff:
  - Check 5 (icon-only buttons missing aria-label) matches: `<button><ChevronIcon /></button>` in SettingsNav.tsx:24
  - Check 6 (status as background pill) matches: `bg-blue-500 text-white` on the active-state indicator in nav.module.css — suspicious, could be a signal-to-noise violation
  - Check 3 (button missing onClick/submit) clean
  - Check 7 (hardcoded hex) clean
  - Remaining 6 checks clean
- Findings written to Review-F with paths + line numbers
- Verdict: **fail** on criterion 3 with 2 concrete findings → routes to Iterate
- Flag in report: `⚠️ static-analysis only — install IBR for computed-CSS verification`

## Concrete delta

| Aspect | Pre-fallback | Post-fallback |
|---|---|---|
| Criterion 3 result | Soft pass ("recommend install") | Fail with 2 specific file:line findings |
| Orchestrator action | None | Route to Iterate, fix aria-label + reconsider pill |
| User visibility | "IBR would have found issues" | "File X line Y is missing aria-label" |
| False positives | 0 (no findings emitted) | 1-2 possible (pill check is heuristic) |
| Time to catch | Post-deploy user bug report | Phase 4 Review, before merge |
| Install IBR? | Recommended | Still recommended for computed-CSS verification |

**Net**: fallback catches real bugs that would otherwise ship. False-positive tolerance is acceptable because findings are file:line-specific and the user can trivially dismiss.
