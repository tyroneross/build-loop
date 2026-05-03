---
name: build-loop:ibr-bridge
description: Run the project's existing IBR test suite during Review Sub-step B as a quick pass, surface coverage gaps in Sub-step D, and re-validate via interact_and_verify after each Iterate. Functional surfaces only — never invokes IBR's viewer or dashboard UI.
version: 0.1.0
user-invocable: false
---

# IBR Bridge

Lets build-loop consume IBR's full functional toolkit (declarative test runner,
interactability flows, baseline/compare, design-token validation, scan, native
simulator testing) without absorbing IBR's UI surfaces. IBR remains an
independent product; this bridge is one-way: build-loop calls IBR.

**Use:**
- Sub-step B Validate — run existing `.ibr-test.json` suite first (quick pass)
- Sub-step D — coverage-gap detection + draft new test scripts for uncovered surfaces
- Phase 5 Iterate — re-validate each fix via `interact_and_verify` before re-entering Validate
- Sub-step F Report — embed IBR test results and screenshot evidence

## Cherry-pick principle

**Actions and functions only. No UI elements.** This bridge composes IBR's
headless/programmatic capabilities. It must NOT invoke any IBR command or MCP
tool that opens a viewer, dashboard, or persistent browser session intended for
human inspection. Specifically:

| Allowed (functions/actions) | Forbidden (UI surfaces) |
|---|---|
| `ibr test --headless --json` | `ibr serve` (viewer web UI) |
| `ibr scan`, `ibr audit`, `ibr check`, `ibr start` | `/ibr:ui` (validation dashboard) |
| `ibr generate-test`, `ibr test-form/login/search/interact` | `ibr session:start` (interactive browser) |
| `mcp__plugin_ibr_ibr__scan`, `native_scan`, `compare`, `interact_and_verify` | `mcp__plugin_ibr_ibr__list_sessions` (session inventory UI hook) |
| `mcp__plugin_ibr_ibr__plan_test`, `references`, `validate_tokens`, `design_system` | Any tool that returns a URL the user is expected to open |
| `mcp__plugin_ibr_ibr__flow_form/login/search`, `flow_form` (programmatic) | Any IBR command that holds a browser open after returning |

If the user wants the IBR viewer, they invoke IBR directly (`/ibr:ui`,
`/ibr:scan` standalone). The bridge never auto-opens it.

## Pre-flight

Before any Sub-step B/D/F use:

```bash
command -v ibr >/dev/null 2>&1 && echo HAVE_IBR_CLI || echo NO_IBR_CLI
```

Plus check MCP availability via the orchestrator's `availablePlugins.ibr` flag.

- **HAVE_IBR_CLI or MCP available** → run protocols below
- **Neither available** → fall through to `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md#web-ui` (static scanner + grep matrix). Flag in Review-F: `⚠️ UI validation via static fallback — install IBR for declarative test runner + interactability + baseline diff`.

## Quick-pass protocol (Sub-step B)

When `uiTarget != null` and IBR is available, run **before** any other validator:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ibr_quickpass.py" --workdir "$PWD" --scope changed
```

Interpret the JSON:

| Status | Action |
|---|---|
| `ran`, `pass == ran` | Quick pass green. Proceed to focused `scan`/`native_scan` of changed surface, then Sub-step D. |
| `ran`, `fail` non-empty | Route each failed test to Iterate as a fix target — the test's assertion is the rubric. Skip generic critic re-run. |
| `no_tests` | Suite is empty. Coverage-gap protocol below generates initial drafts. Sub-step B falls through to static scanner. |
| `ibr_unavailable` | CLI gone mid-build. Fall through to `fallbacks.md#web-ui`. |

The quickpass writes `.build-loop/ibr-quickpass.json` for downstream phases to read.

After the suite runs, augment with a focused visual scan of the changed surface:

```bash
ibr scan <route-of-changed-surface> --output-dir .ibr/quickpass-augment --headless
ibr check <session-id> --headless   # diff against baseline
```

These are programmatic — `--headless` and explicit output-dir keep the viewer out of the loop.

## Coverage-gap protocol (Sub-step D)

Read `.build-loop/ibr-quickpass.json.untested_surfaces`. For each surface:

1. Use `mcp__plugin_ibr_ibr__plan_test` (or `ibr generate-test <route> --output .ibr-tests/_draft/<id>.ibr-test.json --headless`) to author a draft test based on the page's actual structure.
2. Write a one-paragraph summary entry to `.build-loop/ux-queue/<id>.md` from the `ux-fix-plan.md` template, with `dimension: test-coverage` and `severity: major`.
3. Drafts go to `.ibr-tests/_draft/` — the user accepts by moving the file out of `_draft/`, rejects by deleting it. The bridge **never auto-promotes**.

Coverage-gap entries enter the same Phase 5 queue as the four UX dimensions but
are processed last (priority 5: lowest), since they're additions rather than
fixes to broken behavior.

## Iterate hook (Phase 5)

When an Iterate iteration touches files matching UI extensions (`.tsx, .jsx, .vue, .svelte, .swift`) and IBR is available, the orchestrator calls `interact_and_verify` against the affected route immediately after the implementer subagent reports back, **before** re-entering Sub-step B Validate. Cheaper than a full Validate cycle and catches "fix introduced a new visual or interaction regression" early.

For stubborn UI bugs (same route fails twice), optionally invoke `ibr iterate <url> --headless --json` for a self-contained test-fix-rescan loop. Capped to respect build-loop's 5-iteration ceiling — IBR's internal iterations count against build-loop's budget.

## Sub-step D supplementary checks

Beyond coverage gaps, also run when IBR is available:

```bash
mcp__plugin_ibr_ibr__validate_tokens   # off-token colors/spacings introduced this build
mcp__plugin_ibr_ibr__design_system     # design-system drift since last baseline
```

Findings flow into the standard UX queue with `dimension: usability` and severity per the `validate_tokens` exit code.

## Sub-step F evidence

Embed in the scorecard:
- IBR test pass/fail counts (from `.build-loop/ibr-quickpass.json`)
- Screenshot paths from `.ibr/quickpass-augment/` and `.ibr/test-results/`
- Coverage-gap summary: N drafts written, M accepted by user (file no longer in `_draft/`)

Do not embed dashboard URLs or "open in viewer" links. The scorecard is a read-only artifact.

## Memory boundaries

- IBR's own data lives in `.ibr/` — never write there from this bridge.
- The bridge writes only `.build-loop/ibr-quickpass.json` (summary) and `.build-loop/ux-queue/*.md` (coverage-gap entries).
- Test drafts live in `.ibr-tests/_draft/` (project-level, not under `.ibr/` or `.build-loop/`) so the user can promote them by `mv` without involving either tool's runtime data.
- Per `~/.claude/CLAUDE.md` Project Configuration: plugin data stays under `.<toolname>/`. IBR data → `.ibr/`. build-loop data → `.build-loop/`. Test drafts → `.ibr-tests/` (project owns this directory).

## Fallback

When IBR is absent, defer entirely to `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md#web-ui`. The fallback's grep matrix and `audit-design-rules.mjs` cover the static portion of interactability and usability dimensions; performance and data-accuracy degrade to agent-driven analysis only. Flag the degradation in Review-F.

Do not error, do not block the build. The fallback is the worst case; IBR is better.
