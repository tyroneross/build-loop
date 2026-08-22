<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# IBR UI verification policy

This file is the single source of truth for when Build Loop invokes Interface Built Right (IBR).

## Automatic trigger

When Build Loop **updates, compares, or audits a renderable UI design**, invoke headless/programmatic IBR as the primary visual verifier when IBR is installed. Renderable UI designs include application routes, pages, components, HTML mockups, native screens, and approved visual references.

| Operation | Required IBR evidence |
|---|---|
| Update | Capture the relevant before state when available, then compare or scan the rendered result after the change. |
| Compare | Capture both candidates or use `match`/`compare`; report intentional differences separately from style or behavior drift. |
| Audit | Run the narrowest `scan`, flow, or platform scan that checks layout, accessibility, handlers, semantic state, and console health. |

IBR verifies the design. Build Loop still owns design direction through the UI input/output contract, Calm Precision, project tokens, and `.build-loop/app-contract/ui.md`.

## Applicability

- A UI plan with no renderable route, mockup, screenshot, or running native surface records `IBR: not applicable yet` and makes IBR a required validation step once a render exists.
- If IBR is not installed or cannot reach the surface, record the exact blocker and use `ui-validator`, `native-ax-driver`, simulator screenshots/interactions, browser screenshots, or the static design-rule scanner as the platform-appropriate fallback.
- A symbol, string, build, lint, or type check never substitutes for rendered visual/accessibility evidence.

## Boundary

Build Loop may invoke IBR headlessly without separate user confirmation for the automatic trigger above. Interactive viewers, persistent browser sessions, dashboards, and authoring an `.ibr-test.json` suite remain explicit actions unless the active plan already authorizes them.

Use the narrowest operation that proves the claim. Do not open a viewer merely because an IBR scan ran.

## Result handling

- Read the issue list behind any `ISSUES` verdict.
- Fix blocker and major functional/accessibility findings before completion.
- Treat visual diffs as evidence to classify, not automatic failures.
- Re-run the same check after a fix and retain the before/after artifact references in the Build Loop verification record.
