<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# UI Spot-Check Protocol (Phase 3 chunk-close)

_Linked from `agents/build-orchestrator.md` §Phase 3 Execute and §"Phase 3 UI spot-check (between chunks)"._

After the commit step closes for a chunk **and before the next chunk dispatches**, fire `ui-validator` whenever the just-closed chunk's `uiTouched` signal is true. Catches UI regressions inside the chunk that introduced them instead of letting them ride to end-of-Phase-4.

This is the procedural complement to `agents/ui-validator.md` (the agent contract).

## `uiTouched` signal

Compute at chunk-close from the envelope's `files_changed`:

| Trigger | `uiTouched` |
|---|---|
| Any file under `(app\|components)/**/*.tsx` | `true` |
| `tailwind.config.{js,ts}` or theme/global-style files | `true` |
| Style helpers under `lib/(theme\|styles)/**` | `true` |
| Test files only (`tests/**`, `*.test.*`) | `false` |
| Schema / API route only (no UI files in the chunk) | `false` |

Cache the verdict on `state.json.execution.completed_chunks[<chunk_id>].uiTouched` so resume picks it up.

## Dispatch

Sonnet tier; see `agents/ui-validator.md` for the agent contract:

```python
Agent(
  subagent_type="build-loop:ui-validator",
  prompt=brief({
    triggerPoint: "phase3-chunk-close",
    changedFiles: envelope.files_changed,
    baseUrl: state.devServer.baseUrl,           # captured by detect_runtime_server
    priorBaselineDir: ".build-loop/ui-baselines/" + run_id + "/",
    signInForm: state.devServer.signInForm,     # null if no auth fixture
  })
)
```

Cost ledger (M3) applies — emit `--agent ui-validator` rows at dispatch and return.

## Routing on return

| envelope.status | Action |
|---|---|
| `pass` | Continue to next chunk dispatch. Persist envelope to `.build-loop/subagent-results/<run_id>/ui-spotcheck-<chunk_id>.json`. |
| `fail` | Treat `envelope.failing_assertion` as a rubric and route the chunk back to Iterate (same routing as Review-B failure path). Do NOT dispatch downstream chunks in the same batch — drain the queue first by serializing the next batch after the iterate fix. |
| `skipped` | Continue. `skip_reason` distinguishes: `auth-gap` (mark `⚠️ ui-spotcheck skipped — auth fixture missing` in Review-G), `no-dev-server` (mark `⚠️ untested ui — no dev server`), `no-routes-implicated` (silent skip — implementer touched no public render path). |

## Iteration budget

UI-spot-check failures consume the global 5x classic (or 25 autonomous) Iterate cap. They do not get a separate budget.

## Skip conditions

Skip the dispatch entirely when:

- `uiTouched: false` (no UI files in chunk)
- `state.devServer.runtimeServer: false` (library-only project, no dev server to scan against)

## Render-path fallback

If no browser/simulator/native-AX render path is available, `ui-validator` returns `skipped` with the reason and the orchestrator records the evidence gap. Review-D then falls back to `audit-design-rules.mjs` plus the static checks in `skills/build-loop/fallbacks.md#web-ui`. IBR is not an automatic fallback; run `build-loop:ibr-bridge` only when the user explicitly asks for IBR / Interface Built Right / `.ibr-test.json`.
