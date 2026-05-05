# Phase 5 Iterate Protocol — orchestrator reference

Up to 5 iterations. Loaded on demand at Phase 5.

## Stuck-iteration escalation cascade (always on)

At the START of every Iterate attempt, run the cascade in order. Stop at the first rule that fires:

1. **Evidence-gap repair** (highest priority): if the previous attempt's gate flagged `evidence_gap: true` (silent failure, no log signal), invoke `Skill("build-loop:logging-tracer")` with intent `repair`, passing the failing criterion + target files identified by the prior `read_logs` empty result. The skill follows its ephemeral-by-default policy (Mechanism A: `DEBUG_TRACE=1` runtime gate, or Mechanism B: `git stash` throwaway). After logging lands:
   - Re-run the failed Review-B criterion with `DEBUG_TRACE=1 <test-command>` (Mechanism A) or with the stash applied (Mechanism B).
   - If output is now informative, proceed to Iterate with the log evidence as fresh context.
   - If still silent after instrumentation, escalate to user.
   - At Review-F, the orchestrator MUST verify no `build-loop:trace/<session-id>` stash entries remain and no unguarded trace calls landed unless the user explicitly approved keep-in-diff via `AskUserQuestion`.

2. **Memory-first re-check**: invoke `Skill("build-loop:debugging-memory")` again with the new symptom (the failure may have shifted shape after the prior fix attempt). Same verdict-handling rules as Review-B.

3. **Architecture impact pre-step (cross-layer failures)**: if the failing criterion's `files_touched` cross 2+ layers (per `.build-loop/architecture/file_map.json` lookup, falling back to `.navgator/architecture/file_map.json`), dispatch `Agent(subagent_type="build-loop:architecture-scout", prompt='task: iterate-subgraph, failing_files: [<files>]')` BEFORE any escalation. Scout returns `fix_scope_files` — the union of same-component + direct-downstream files that MUST be touched together.

4. **2 consecutive same-root-cause failures** → parallel multi-domain assessment via `Skill("build-loop:debugging-assess")`. Fans out to relevant domain assessors (api / database / frontend / performance) in parallel. **Model override**: explicitly pass `model: sonnet` to each domain assessor to avoid 4 parallel Opus invocations. Only escalate individual assessors to Opus if their initial output flags `confidence: low` or `needs_judgment: true`.

5. **3 consecutive same-criterion failures** → causal-tree investigation via `Skill("build-loop:debugging-debug-loop")`. Do not attempt a 4th fix without it. The skill runs its own 7-phase cycle with up to 5 internal iterations. If still failing after 5 internal debug-loop iterations, hard-stop and escalate to user.

## Prioritized work list

Build the work list for this pass — Validate failures + queue entries, in this order:

1. Blocking Validate failures.
2. Blocker UX queue entries with `architecture_impact: false`.
3. Major UX queue entries with `architecture_impact: false`.
4. Optimization findings (Sub-step C).
5. IBR coverage-gap drafts (`dimension: test-coverage`) — additions, processed last.

Entries with `architecture_impact: true` are deferred to Review-F for explicit user confirmation, NOT included in this pass. Do NOT defer based on patch size — code is cheap, AI agents build fast. The only deferral signal is architecture impact.

## Partition for fan-out

Group entries by disjoint `files_touched` (no overlapping files). Dispatch mechanism depends on whether you can spawn subagents:

**Top-level mode**: dispatch up to 4 `implementer` subagents in parallel via `Agent(subagent_type="build-loop:implementer", ...)`. Hard cap from `~/.claude/CLAUDE.md` §Sub-Agents. Sequential groups process after the parallel batch.

**Subagent mode**: the `Agent` tool is unavailable to you. Do NOT halt — degrade gracefully to inline-implementer mode. Iterate the queue serially, applying each entry's `proposed_fix` yourself. Surface the degradation in your Phase 4 Sub-step F Report.

In either mode, each implementer dispatch (or inline pass) MUST include: (1) absolute `plan_path` to the queue entry's `.md`, (2) absolute `workdir` for the project root, (3) optional `additional_context` if this is a re-pass on an entry. Route the result by status:

- `fixed` → mark queue entry done (delete the .md).
- `partial` → keep entry, schedule re-pass next iteration.
- `scope_breach` → ask user before extending scope.
- `deferred_architecture` → move entry to Review-F deferred list.
- `evidence_stale` → re-run `ux_triage.py --clear` to regenerate the queue, then re-pass.
- `plan_malformed` → same as `evidence_stale`; log the malformed entry's id to `.build-loop/state.json.malformedPlans[]`.
- `needs_dependency` → ask user (same routing as `scope_breach`); never auto-add deps.
- `failed` → if attempts on this entry < 2, re-pass with the implementer's `notes` injected as `additional_context`; if attempts >= 2, escalate the implementer to Opus per `Skill("build-loop:model-tiering")` §Escalation Triggers and re-pass once more; if still `failed`, surface in Review-F as ❓ Unfixed.
- `concurrent_modification_detected` → abort the current parallel batch immediately, surface in Review-F.

For Validate failures (no queue entry), construct an inline plan in the same shape and treat it identically.

## IBR re-validate hook (UI work + IBR present)

After each implementer subagent reports back AND before re-entering Sub-step B, call `mcp__plugin_ibr_ibr__interact_and_verify` against the affected route(s) headlessly. For routes that fail this twice, optionally `ibr iterate <url> --headless --json` for a self-contained test-fix-rescan loop (internal iterations count against build-loop's 5-cap). No IBR viewer is opened.

Loop back to Review sub-step B (Validate). Sub-step A usually skipped on re-runs.

## Followup overflow

When iteration cap is reached and queue entries remain, write them to `.build-loop/followup/<topic>.md` for a subsequent `/build-loop:run` invocation. Plan content is already complete — the followup build skips Plan phase for these entries.

## Convergence rules

- Same failure 2x with same root cause → escalate to user (unless the stuck-iteration cascade above already escalated first).
- Fix A breaks criterion B → flag oscillation, ask user.
- 3+ simultaneous failures after a fix → systemic, stop and reassess.
- Hard stop at 5 iterations; proceed to final Review sub-step F with remaining ❓ Unfixed and queue overflow written to `.build-loop/followup/`.
