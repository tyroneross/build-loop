<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Halt-and-Ask Protocol (mode-aware decision handler)

_Linked from `agents/build-orchestrator.md` §Phase 3 Execute._

C3's `attestation_lint.py` and C4's `synthesis-critic` cover most synthesis-class drift. **Architectural-class decisions** (where a phase lives, defensive contract shape, error-propagation policy, persistence boundary, hard-fail/retry counters, etc.) fall outside both — the lint has nothing to grep for, and the critic only fires on UI files. This protocol catches those via mode-aware routing: implementers return `status: "blocked"` with `novel_decisions[]` populated, and the orchestrator either auto-picks (long-mode) or surfaces the trade-off table (normal-mode) — escalating to the operator only when the implementer signals low confidence.

This branch fires at envelope-receive time, **before** the commit step (`references/single-writer-commit-protocol.md`). If `status: "blocked"`, you do NOT enter the commit step at all on this iteration — there's nothing to commit yet.

**Trigger**: implementer envelope arrives with `status: "blocked"` AND `novel_decisions[]` non-empty (or any envelope with `novel_decisions[]` non-empty, when the do/branch/surface policy is active).

## Mode resolution

Before processing the decision, the orchestrator resolves the current run's mode from `state.execution.budget`:

| Mode | Trigger | Effect |
|---|---|---|
| **long** | `--long` flag, `--budget >= 4h`, or `overnight` keyword in goal | Auto-pick `recommended_default`; log to `autonomousDefaults[]`; continue |
| **normal** | Default (2h budget, no long-mode trigger) | Surface trade-off table to operator; wait |
| **forced-escalate** | `novel_decisions[i].confidence == "low"` | Surface trade-off table even in long-mode |

The mode resolution is deterministic; the implementer's `confidence` field is the only thing that can override long-mode → normal-mode behavior.

## Procedure (per blocked envelope)

1. **Validate the envelope schema.** Every `novel_decisions[i]` MUST have `decision_id`, `options` (non-empty), `recommended_default` (matching one of `options[].id`), `confidence` (`high|med|low`), and per-option `user_impact`/`performance`/`speed`/`cost` (non-empty, not `"n/a"`). Schema violations route to Iterate with the implementer asked to fill in the trade-off fields; do NOT enter the resolution loop with a malformed envelope.

2. **Classify via `scripts/classify_action.py`** with the envelope passed in. The classifier returns `DECISION` plus a `decision_state` of `pickable`, `low_confidence`, or `malformed`. Branch on state:
   - `pickable` → proceed to step 3 (mode-aware routing)
   - `low_confidence` → force-escalate (step 4) regardless of mode
   - `malformed` → route to Iterate (step 1 caught most cases; this is the belt-and-braces)

3. **Mode-aware routing for `pickable` decisions.**

   **Long-mode (`state.execution.budget.mode in {long, custom, overnight}`):**
   - For each `novel_decisions[i]`, take `recommended_default` as `chosen`.
   - Append to `state.json.runs[].autonomousDefaults[]` via `scripts/log_decision.py --kind autonomous_default`.
   - Emit terminal log: `[auto-pick] decision=<id> chose=<chosen> confidence=<level> rationale="<one-line>"`.
   - Re-dispatch the implementer with `resolved_decisions:` containing each pick (same shape as the legacy path).
   - **Do not** dispatch a Thinking-tier resolver. The implementer already articulated the trade-offs; auto-pick honors that.

   **Normal-mode:**
   - Surface the trade-off table to the operator via `AskUserQuestion` with one row per option showing `user_impact`, `performance`, `speed`, `cost`.
   - The operator's pick is logged to `autonomousDefaults[].escalated: true` so the audit trail captures human-in-the-loop decisions identically.
   - Re-dispatch with the operator's pick as the resolution.

4. **Force-escalate for `low_confidence`.** Even in long-mode, surface the trade-off table to the operator. The implementer is signaling "I cannot pick well" — that's a real ask, not a procedural pause. Logged with `escalated: true` and `reason: "low_confidence"`.

5. **Hard-fail counter (N=3) — preserved for backwards compatibility.** If the same `decision_id` returns as `novel_decisions[]` three times in a row (auto-pick or operator-pick didn't resolve it), surface as `❓ Unfixed` in Review-G with the unresolved decisions logged to `state.json.novelDecisionUnresolved[]`. The counter is per-chunk per-decision; reaching it indicates either the implementer's recommended_default is genuinely wrong or the plan needs revision.

6. **Persist resolutions.** Continue to write `state.json.novelDecisionResolutions[]` for forensic compatibility:
   ```json
   {
     "chunk_id": "<from plan>",
     "decision_id": "<from envelope>",
     "attempt": <1|2|3>,
     "decision": "<verbatim from novel_decisions[]>",
     "implementer_reasoning": "<verbatim>",
     "resolution": "<chosen option id + summary>",
     "rationale": "<from implementer.recommended_default reasoning OR operator pick>",
     "resolved_by": "auto_pick | operator | tier:thinking_fallback",
     "resolved_at": "<iso8601>"
   }
   ```

7. **Re-dispatch the implementer** with the **same brief** plus an appended `resolved_decisions:` block containing every resolution generated for this chunk. The implementer applies the resolutions as if they had been part of the plan's `synthesis_dimensions` from the start, and attests against them in the next envelope's `synthesis_attestation`.

8. **Loop**. The next envelope can return:
   - `status: "completed"` / `"fixed"` / `"partial"` → proceed to the commit step (`references/single-writer-commit-protocol.md`).
   - `status: "blocked"` again with **new** `novel_decisions[]` → repeat from step 1. Per-decision counter increments. At N=3, surface as ❓ Unfixed.
   - Any other failure status → route per the standard commit step's failure handling.

## Legacy Thinking-tier resolver fallback

The pre-mode-aware version of this protocol dispatched a Thinking-tier resolver via `Agent(subagent_type: "build-loop:build-orchestrator", ...)` for every novel decision. That path remains as a **fallback** when:
- The implementer cannot articulate a `recommended_default` (malformed envelope, attempt 1 only; orchestrator re-dispatches asking for the missing field).
- An operator escalation in normal-mode is interrupted before the operator responds (resume contract).

When the fallback fires, `resolved_by: "tier:thinking_fallback"` and the resolver returns the same `{"resolution", "rationale", "alternatives_rejected"}` shape as before. Model routing is unchanged from the legacy doc (see "Routing is `tier: thinking`" below).

## No new dependencies

This is a status-branch addition to the existing await-implementer dispatch, not a new runtime. The orchestrator already awaits implementer envelopes; `blocked` is just one more value to switch on. Do NOT introduce LangGraph, a state machine library, or any new event loop. The existing `Agent(...)` dispatch + envelope parsing is the substrate.

## State writes touched by this branch

- `state.json.runs[].autonomousDefaults[]` — per-decision auto-picks with full trade-off context (NEW; written by `scripts/log_decision.py`)
- `state.json.novelDecisionAttempts[<chunk_id>:<decision_id>]` — per-decision counter (≤3 attempts before ❓ Unfixed)
- `state.json.novelDecisionResolutions[]` — durable resolution log (preserved for backward compat)
- `state.json.novelDecisionUnresolved[]` — entries that exhausted N=3

## Telemetry

Per auto-pick: `[auto-pick] decision=<id> chose=<chosen> confidence=<level> mode=<long|normal> rationale="<short>"`.
Per operator escalation: `[escalate] decision=<id> reason=<low_confidence|normal_mode> options=<count>`.
Per re-dispatch: `[redispatch] chunk=<id> decision=<id> attempt=<n>/3 resolution="<short>"`.
On hard-fail: `[hard-fail] ❌ chunk=<id> decision=<id> exhausted 3 attempts — routing to ❓ Unfixed`.

## Phase 3 UI spot-check (between chunks)

Extracted to `references/ui-spotcheck-protocol.md` for MECE separation — that file covers the `uiTouched` signal, dispatch shape, routing on return, iteration budget, skip conditions, and render-path fallback. UI spot-check and the C5 halt-and-ask branch share Phase 3 timing but no machinery — UI spot-check fires after successful commits on `uiTouched: true`, while halt-and-ask fires before commit on `status: blocked`.
