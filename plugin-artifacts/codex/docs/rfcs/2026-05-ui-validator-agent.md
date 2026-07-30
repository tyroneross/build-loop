# RFC — `build-loop:ui-validator` agent + Phase 3 spot-check trigger

| Field | Value |
|---|---|
| Status | Draft |
| Authors | tyroneross |
| Created | 2026-05-12 |
| Cross-ref | tyroneross/interface-built-right#5 — RFC: export engine + analyzers as `@tyroneross/ibr-core` |

## Summary

Replace the `ibr_quickpass.py` shell-out with a dedicated `ui-validator` agent that imports `@tyroneross/ibr-core` directly. Wire it into two trigger points:

1. **Phase 3 chunk-close** — after a UI-touching chunk's implementers return, before the orchestrator dispatches the next chunk.
2. **Phase 4 Review sub-step B** — current location, but with the library path replacing the shell-out.

Functional failures (console errors, layout collisions, touch-target regressions, hydration timeouts) at either point route directly to Iterate with the failing assertion as the rubric — the same pattern `ibr_quickpass.py` uses today. Visual-only failures (SSIM under threshold without a functional signal) route to the Review-B backlog as a warning rather than to Iterate, since visual changes are usually intentional. See "Failure routing rules" below for the full table.

## Motivation

Three current weaknesses:

1. **Late feedback.** UI regressions surface only at end of Phase 4. A regression from chunk 2 of a 6-chunk build is detected after chunk 6 commits, leading to longer Iterate cycles and noisier blame surfaces.
2. **Auth gap.** `npx ibr scan` against `/app/*` redirects to `/sign-in` because IBR's `loadAuthState` doesn't apply (bug at `dist/index.mjs:5949`). Build-loop's UI audit is effectively public-routes-only today.
3. **Scan cost.** Each shell-out re-launches Chrome (~5 s overhead). Phase 4's 6–10 route scans add ~50 s of overhead per build.

## Agent shape

```yaml
name: ui-validator
model: sonnet
description: |
  Run deterministic UI scans against the dev server using @tyroneross/ibr-core.
  Used during Phase 3 chunk-close and Phase 4 Review-B.
tools:
  # Direct library import — @tyroneross/ibr-core is loaded as a Node module
  # by the agent's runtime. No MCP server involved. The Bash/Read/Grep/Glob
  # tools below are the only Claude Code tools the agent needs.
  - Bash               # spawn the IBR-core driver process; shell-out fallback when ibr-core missing
  - Read               # changed-file inspection
  - Grep               # route discovery + selector audits
  - Glob               # changed-files pattern matching
inputs:
  - changedFiles: string[]
  - baseUrl: string
  - priorBaselineDir: string
  - signInForm: { url, email, password } | null
outputs:
  - status: "pass" | "fail" | "skipped"
  - consoleErrors: ConsoleError[]
  - layoutCollisions: LayoutCollision[]
  - touchTargetViolations: TouchTargetViolation[]
  - hydrationStable: boolean
  - visualRegression: { route: string; ssim: number }[]
  - failingAssertion: string | null   # routed to Iterate
```

## Trigger placement

### Phase 3 — between chunk dispatches

In `references/halt-and-ask-protocol.md` §"Phase 3 UI spot-check (between chunks)", after the "chunk-close" step:

> After all parallel implementers in a chunk return, BEFORE the next chunk dispatches, compute `uiTouched` for the just-closed chunk from the envelope's `files_changed` (true when any file matches `(app|components)/**/*.tsx`, `tailwind.config.{js,ts}`, theme/global-style files, or style helpers under `lib/(theme|styles)/**` — see the §"`uiTouched` signal" table for full coverage). Cache the verdict on `state.json.execution.completed_chunks[<chunk_id>].uiTouched`. If true, dispatch `ui-validator`. On `pass`, continue. On `fail`, route the failing assertion to Iterate (same as Review-B failure routing). `uiTouched` is the chunk-level boolean; the broader `uiSignal` enum (major / touched / none) below is the Phase 1 input used to pre-compute eligibility and is consulted alongside it.

### Phase 4 — Review sub-step B replacement

Replace the `ibr_quickpass.py` shell-out with a `ui-validator` dispatch. Pass `--scope changed` equivalent via the agent's `changedFiles` input. Same failure-routing rules; same green-light condition.

## Library consumer wiring

`ui-validator` imports `@tyroneross/ibr-core` directly (no shell-out):

```ts
import { EngineDriver, scan, login } from "@tyroneross/ibr-core";

const driver = new EngineDriver();
await driver.launch({ headless: true });

// Build-loop owns the auth flow — fixes the IBR loadAuthState bug.
if (signInForm) await login(driver, signInForm);

const results = await Promise.all(
  changedRoutes.map((route) => scan(driver, baseUrl + route)),
);

await driver.close();
return aggregate(results);
```

One launch per chunk-close, N routes scanned against it, deterministic verdict.

## "Major UI change" detection signal

Phase 1 Assess writes `state.json.uiSignal` per chunk:

| Signal | Trigger |
|---|---|
| `uiSignal.major` | Chunk owns ≥3 .tsx files OR modifies `tailwind.config.ts` OR adds a route under `app/` |
| `uiSignal.touched` | Chunk owns ≥1 .tsx file |
| `uiSignal.none` | No .tsx, no styles |

Phase 3 spot-check fires on `major` and `touched`; `none` skips the scan entirely.

## Failure routing rules (sub-step B addition)

| Signal | Routing |
|---|---|
| New console error vs prior baseline | → Iterate, assertion = "no new console errors on $route" |
| New layout collision | → Iterate, assertion = "no overlapping interactive elements on $route" |
| Touch-target regression (drop below 24×24) | → Iterate, assertion = "all touch targets ≥ 24×24" |
| Hydration timeout | → Iterate, assertion = "AX tree stabilizes within 3 s" |
| Visual SSIM < threshold | → Review-B backlog (warn, not block — visual changes are often intentional) |

## Migration plan

1. (this PR) RFC + agent contract + trigger spec + Phase 1 `uiSignal` computation step in the Phase 1 Assess detail (`references/phase-gate-checklist.md` §Phase 1) — read changed-set + `git log -n 5 --name-only`, derive `uiSignal: major | touched | none`, persist to `state.json.uiSignal`.
2. Wait for `@tyroneross/ibr-core@0.1.0` (cross-ref PR on the IBR repo)
3. Author the agent definition at `agents/ui-validator.md`
4. Wire the Phase 3 trigger in `references/halt-and-ask-protocol.md` §"Phase 3 UI spot-check (between chunks)"
5. Replace the Phase 4 sub-step B shell-out in `references/phase-gate-checklist.md` §"Sub-step B — Validate"
6. Migration test — re-run the most recent UI-heavy build-loop run and confirm equivalent findings at lower wall-clock

## Backward compatibility

While `@tyroneross/ibr-core` is unpublished, the `ibr_quickpass.py` shell-out remains the fallback. Both paths produce the same `ScanResult` JSON shape, so the rest of Phase 4 doesn't care which path was used. The replacement is gated on `core` availability in `state.json.availablePlugins`.

## Resolved questions

- **Iteration budget**: `ui-validator` failures share the global 5x classic / 25 autonomous Iterate cap. No separate budget. Rationale: this RFC adds validation *points*, not new rework — a UI regression that takes 3 iterations to fix should consume 3 of the existing budget, not start a fresh count. Agreed; documented in `agents/ui-validator.md` §"Failure routing assertions".
- **Trigger granularity**: per-chunk-close. Per-implementer-commit considered and rejected — implementers in a single chunk can land sequentially via the single-writer commit protocol, and scanning between each one inflates the budget linearly with chunk size. Per-chunk-close picks up regressions before the next chunk dispatches, which is the right blame-isolation point.

## Open questions

- Should the agent persist a `ui-baseline/` dir per project for visual-regression checks across builds, or only intra-build? Today's IBR flow is intra-build only; cross-build baselines need a storage decision (repo-tracked vs `.build-loop/ui-baselines/<run_id>/` retention policy).

## Out of scope

- Authoring `agents/ui-validator.md` itself. This PR is RFC only.
- Migration of `ibr_quickpass.py` callers. Follow-on PR.
- Auth-state bug fix in the IBR CLI — owned by the IBR-side RFC.

## Cross-reference

IBR's library-export RFC: tyroneross/interface-built-right#5. This PR depends on that side shipping `@tyroneross/ibr-core@0.1.0` before any implementation can land.
