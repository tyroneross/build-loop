# Halt-and-Ask Protocol (C5 Architectural-Decision Backstop)

_Linked from `agents/build-orchestrator.md` §Phase 3 Execute._

C3's `attestation_lint.py` and C4's `synthesis-critic` together cover most synthesis-class drift. **Architectural-class decisions** (where a phase lives, defensive contract shape, error-propagation policy, persistence boundary, hard-fail/retry counters, etc.) fall outside both — the lint has nothing to grep for, and the critic only fires on UI files. C5 catches those via a halt-and-ask backstop: implementers return `status: "blocked"` rather than guess, and the orchestrator dispatches a Thinking-tier resolver before re-dispatching the implementer.

This branch fires at envelope-receive time, **before** the commit step (`references/single-writer-commit-protocol.md`). If `status: "blocked"`, you do NOT enter the commit step at all on this iteration — there's nothing to commit yet.

**Trigger**: implementer envelope arrives with `status: "blocked"` AND `novel_decisions[]` non-empty.

## Procedure (per blocked envelope)

1. **Initialize / increment the per-chunk hard-fail counter.** Read `state.json.novelDecisionAttempts[<chunk_id>]` (default 0). If already at **3**, do NOT re-dispatch — surface the chunk as ❓ Unfixed in Review-G with the unresolved decisions logged to `state.json.novelDecisionUnresolved[]`, and proceed to the next chunk. Otherwise increment by 1 and continue. **N=3 chosen to mirror the existing "after 3 attempts surface as ❓ Unfixed" pattern documented in `skills/build-loop/references/phase-5-iterate.md` §"Fan-out" status routing** — keeps build-loop's escalation cadence consistent across phases.

2. **Validate the blocked envelope.** `status: "blocked"` requires `novel_decisions[]` non-empty (per `references/implementer-envelope-schema.md` parser rule 5). Empty `novel_decisions[]` with `status: "blocked"` is malformed — treat as `failed` and route to Iterate; do NOT enter the resolution loop.

3. **Reset working tree to the parent commit** before resolving. Implementers may have left partial edits on disk. Run `git stash push --keep-index --include-untracked -m "buildloop-c5-block-<chunk_id>-<attempt>"` to preserve the partial work for forensic review without contaminating the re-dispatch. `git status` must be clean after this step.

4. **For each entry in `novel_decisions[]`**, dispatch the configured Thinking-tier resolver:
   ```
   Agent({
     subagent_type: "build-loop:build-orchestrator",   // self-dispatch as resolver — Thinking-tier per frontmatter
     model: "<resolved via tier abstraction — see below>",
     prompt: <resolver brief: decision text, implementer's reasoning, plan excerpt, repo intent packet, ask-for-one-line-resolution-plus-rationale>
   })
   ```
   **Routing is `tier: thinking`, never a hardcoded model name.** Resolve the model identifier via the existing tier abstraction in this order: (a) `state.json.config.modelOverrides.thinking` if set (per `references/model-tier-mapping.md` §"Runtime override via .build-loop/config.json"); (b) the orchestrator's frontmatter `model:` value (currently `claude-opus-4-7` — the Thinking-tier default); (c) if neither resolves, log the missing-tier-mapping as a novel decision itself and surface to user. Do NOT inline a literal `claude-opus-4-7` — go through the tier lookup so multi-provider hosts (GPT-5 Thinking, Gemini 2.5 Pro) substitute cleanly.

   The resolver returns one JSON object per decision: `{"resolution": "<one-line directive>", "rationale": "<why>", "alternatives_rejected": ["<a>", "<b>"]}`.

5. **Persist resolutions.** Append each resolution to `state.json.novelDecisionResolutions[]` with shape:
   ```json
   {
     "chunk_id": "<from plan>",
     "attempt": <1|2|3>,
     "decision": "<verbatim from novel_decisions[]>",
     "implementer_reasoning": "<verbatim>",
     "resolution": "<from resolver>",
     "rationale": "<from resolver>",
     "resolved_by": "tier:thinking",
     "resolved_at": "<iso8601>"
   }
   ```
   This is durable — survives orchestrator restart and is read by Phase 6 Learn for pattern detection on architectural-decision drift across builds.

6. **Re-dispatch the implementer** with the **same brief** plus an appended `resolved_decisions:` block containing every resolution generated in step 4 for this chunk. Include both the prior attempts' resolutions and the latest — implementers don't need to remember context across re-dispatches if the brief carries it. The implementer applies the resolutions as if they had been part of the plan's `synthesis_dimensions` from the start, and attests against them in the next envelope's `synthesis_attestation`.

7. **Loop**. The next envelope can return:
   - `status: "completed"` / `"fixed"` / `"partial"` → proceed to the commit step (`references/single-writer-commit-protocol.md`), then continue to the next implementer in the batch.
   - `status: "blocked"` again with new `novel_decisions[]` → repeat from step 1. Counter increments. At N=3, surface as ❓ Unfixed.
   - Any other failure status → route per the standard commit step's failure handling (Iterate, etc.). The N=3 counter is specific to the halt-and-ask loop, not to general implementer failures.

## No new dependencies

This is a status-branch addition to the existing await-implementer dispatch, not a new runtime. The orchestrator already awaits implementer envelopes; `blocked` is just one more value to switch on. Do NOT introduce LangGraph, a state machine library, or any new event loop. The existing `Agent(...)` dispatch + envelope parsing is the substrate.

## State writes touched by this branch

- `state.json.novelDecisionAttempts[<chunk_id>]` — counter
- `state.json.novelDecisionResolutions[]` — durable resolution log
- `state.json.novelDecisionUnresolved[]` — entries that exhausted N=3

## Telemetry

Log one line per resolution in terminal output: `[C5 Resolver] chunk=<id> attempt=<n>/3 decision="<short>" → resolution="<short>"`. On hard-fail: `[C5 Resolver] ❌ chunk=<id> exhausted 3 attempts — routing to ❓ Unfixed`.

## Phase 3 UI spot-check (between chunks — RFC #30)

After the commit step closes for a chunk **and before the next chunk dispatches**, fire `ui-validator` whenever the just-closed chunk's `uiTouched` signal is true. Catches UI regressions inside the chunk that introduced them instead of letting them ride to end-of-Phase-4.

**`uiTouched` signal** (compute at chunk-close from the envelope's `files_changed`):

| Trigger | `uiTouched` |
|---|---|
| Any file under `(app\|components)/**/*.tsx` | `true` |
| `tailwind.config.{js,ts}` or theme/global-style files | `true` |
| Style helpers under `lib/(theme\|styles)/**` | `true` |
| Test files only (`tests/**`, `*.test.*`) | `false` |
| Schema / API route only (no UI files in the chunk) | `false` |

Cache the verdict on `state.json.execution.completed_chunks[<chunk_id>].uiTouched` so resume picks it up.

**Dispatch** (Sonnet tier; see `agents/ui-validator.md` for the agent contract):

```
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

**Routing on return**:

| envelope.status | Action |
|---|---|
| `pass` | Continue to next chunk dispatch. Persist envelope to `.build-loop/subagent-results/<run_id>/ui-spotcheck-<chunk_id>.json`. |
| `fail` | Treat `envelope.failing_assertion` as a rubric and route the chunk back to Iterate (same routing as Review-B failure path). Do NOT dispatch downstream chunks in the same batch — drain the queue first by serializing the next batch after the iterate fix. |
| `skipped` | Continue. Reasons: `(auth-gap)` (mark `⚠️ ui-spotcheck skipped — auth fixture missing` in Review-G), `(no-dev-server)` (mark `⚠️ untested ui — no dev server`), `(no-routes-implicated)` (silent skip — implementer touched no public render path). |

**Iteration budget**: UI-spot-check failures consume the global 5x Iterate cap. They do not get a separate budget.

**Skip when**: `uiTouched: false` (no UI files in chunk) OR `state.devServer.runtimeServer: false` (library-only project, no dev server to scan against) OR the project's `config.json` sets `uiSpotcheck.enabled: false`.

**Backward compat**: if `@tyroneross/ibr-core` is not installed in the project, `ui-validator` falls back to the existing `scripts/ibr_quickpass.py` shell-out path automatically (see `agents/ui-validator.md` §"Path selection"). The orchestrator's behavior is identical — only the underlying scan implementation differs. Track upstream lib availability via `tyroneross/interface-built-right#5`.
