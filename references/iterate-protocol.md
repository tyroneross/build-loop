<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 5 Iterate Protocol — orchestrator reference

Up to 5 iterations (classic mode) or 25 iterations (autonomous mode). Loaded on demand at Phase 5.

## End-of-run continuation gate

After the followup drain, a second iterate cycle drains `.build-loop/issues/` then `.build-loop/backlog/` — but **only** when `session_prefs.continue_from_queues == "always"` (checked via `scripts/context_bootstrap.py:should_continue_into_queues`). Unset / "ask" / "never" → no continuation.

## Re-validate hook for UI work (by `uiTarget.kind`)

- **web** → `ui-validator` against the affected route, or browser/screenshot tooling if a focused route can be resolved.
- **native macOS** (running `.app`, `.swift` files in macOS target) → built-in `skills/native-ax-driver/` (`python3 .../native_driver.py preflight|scan|action`). Cursor-free — uses `AXUIElementPerformAction`, no `CGEvent`.
- **iOS simulator** → install/launch the app, capture `xcrun simctl io booted screenshot`, and use `idb ui tap` per `reference_idb_sim_tap.md` when interaction is required.

## Backend short-circuit (Priority 21)

Read `state.json.architecture.backendHealth` (set during Phase 1 Assess by `backend_health.py`) at the start of each Iterate cycle. For each backend that's down, propagate the skip-flag to every memory call in this iterate cycle:

- `semantic.ok == false` → pass `skip_postgres=True` to `recall()` calls. The Postgres connection is bypassed entirely (no env-var check, no `import psycopg`, no `connect_timeout`), saving roughly 3 seconds per call across the memory-first gate's many lookups. The `reasons[]` envelope returns `skipped_postgres` (distinct from `db_unavailable: ...`) so the iterate brief can surface intentional skip vs genuine backend-down.
- `debugger.ok == false` → set `kind="runs"` or `kind="decisions"` on `recall()` calls instead of leaving `kind=None`, so the debugger MCP probe is skipped. Equivalent escape hatch in Phase 4 Review-B's debugging-memory verdict gate: skip the MCP `search` probe and fall through directly to the local-grep fallback at `skills/build-loop/fallbacks.md#bug-memory`.

Log the degradation in the iterate brief — one line per skipped backend. The graceful-degradation contract is preserved either way; this step only saves wall-clock time.

## Stuck-iteration escalation cascade (always on)

Every Iterate failure brief starts with a plain-language explanation before technical detail. The terminal cause must be a controllable system failure, not an actor-blame statement. "Agent forgot", "agent missed context", or "model overlooked it" is only acceptable when paired with the missing control that allowed it, such as an incomplete handoff, missing scope verifier, ambiguous owner, stale cache check, absent feedback path, or missing runtime smoke gate.

At the START of every Iterate attempt, run the cascade in order. Stop at the first rule that fires:

1. **Evidence-gap repair** (highest priority): if the previous attempt's gate flagged `evidence_gap: true` (silent failure, no log signal), invoke `Skill("build-loop:logging-tracer")` with intent `repair`, passing the failing criterion + target files identified by the prior `read_logs` empty result. The skill follows its ephemeral-by-default policy (Mechanism A: `DEBUG_TRACE=1` runtime gate, or Mechanism B: `git stash` throwaway). After logging lands:
   - Re-run the failed Review-B criterion with `DEBUG_TRACE=1 <test-command>` (Mechanism A) or with the stash applied (Mechanism B).
   - If output is now informative, proceed to Iterate with the log evidence as fresh context.
   - If still silent after instrumentation, escalate to user.
   - At Review-F, the orchestrator MUST verify no `build-loop:trace/<session-id>` stash entries remain and no unguarded trace calls landed unless the user explicitly approved keep-in-diff via `AskUserQuestion`.

2. **Memory-first re-check**: invoke `Skill("build-loop:debugging-memory")` again with the new symptom (the failure may have shifted shape after the prior fix attempt). Same verdict-handling rules as Review-B.

3. **Architecture impact pre-step (cross-layer failures)**: if the failing criterion's `files_touched` cross 2+ layers (per `.build-loop/architecture/file_map.json` lookup, falling back to `.navgator/architecture/file_map.json`), dispatch `Agent(subagent_type="build-loop:architecture-scout", prompt='task: iterate-subgraph, failing_files: [<files>]')` BEFORE any escalation. Scout returns `fix_scope_files` — the union of same-component + direct-downstream files that MUST be touched together.

4. **2 consecutive same-root-cause failures** → parallel multi-domain assessment via `Skill("build-loop:debugging-memory")` with `{op:"assess"}`. Fans out to relevant domain assessors (api / database / frontend / performance) in parallel. **Model override**: explicitly pass `model: sonnet` to each domain assessor to avoid 4 parallel Opus invocations. Only escalate individual assessors to Opus if their initial output flags `confidence: low` or `needs_judgment: true`.

5. **3 consecutive same-criterion failures** → causal-tree investigation via `Skill("build-loop:debug-loop")`. Do not attempt a 4th fix without it. The skill runs its own 7-phase cycle with up to 5 internal iterations. If still failing after 5 internal debug-loop iterations, hard-stop and escalate to user.

## Prioritized work list

Build the work list for this pass — Validate failures + queue entries, in this order:

1. Blocking Validate failures.
2. Blocker UX queue entries with `architecture_impact: false`.
3. Major UX queue entries with `architecture_impact: false`.
4. Optimization findings (Sub-step C).
5. UI coverage-gap queue entries (`dimension: test-coverage`) — repo-native additions, processed last.

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

## UI re-validate hook

After each implementer subagent reports back AND before re-entering Sub-step B, run the build-loop-owned UI re-validate path for affected route(s): `ui-validator` for web when a route is known, native AX driver for macOS, or simulator screenshot/interaction commands for iOS. If no renderable route can be resolved, record the gap in the iterate brief and fall back to the static design-rule scanner. IBR is not invoked unless the user explicitly requested it for this build.

Loop back to Review sub-step B (Validate). Sub-step A usually skipped on re-runs.

## Followup overflow

When iteration cap is reached and queue entries remain, write them to `.build-loop/followup/<topic>.md` for a subsequent `/build-loop:run` invocation. Plan content is already complete — the followup build skips Plan phase for these entries.

## Convergence rules

- Same failure 2x with same root cause → escalate to user (unless the stuck-iteration cascade above already escalated first).
- Fix A breaks criterion B → flag oscillation, ask user.
- 3+ simultaneous failures after a fix → systemic, stop and reassess.
- Hard stop at 5 iterations (classic mode) or 25 iterations (autonomous mode); proceed to final Review sub-step F/G with remaining ❓ Unfixed and queue overflow written to `.build-loop/followup/`.

## Phase 5 autonomous iterate loop (plan §14.3 — Phase A)

When `state.json.autonomous.enabled == true`, Phase 5 generalizes into a queue-drain loop. Entry conditions, body, and exits below; all backed by `scripts/budget_check.py` + `Agent(subagent_type="build-loop:alignment-checker", ...)`. The loop body executes after classic Phase 5 Iterate has handled the just-completed plan's own ❓ Unfixed items; then it picks up fresh queue items.

**Pre-entry — autonomous mode detection.** Read these in order; first hit wins:

1. `state.json.autonomous.enabled` (set by the skill body when `--autonomous=true` or default).
2. `--autonomous=false` on the original invocation forces `false`; loop is skipped entirely.
3. `state.json.execution.budget` MUST exist (the skill body writes it at start). Missing → log a warning and treat autonomous as disabled for this run.

**On every loop iteration entry — three short calls in order:**

1. **Budget check.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/budget_check.py --workdir "$PWD"`. Parse the envelope.
   - `action: continue` → proceed.
   - `action: checkin` → emit a `PushNotification` (`Build-loop progress @ N% — items closed: X, deferred: Y`), atomic-update `state.execution.budget.last_checkin_at = now()`, proceed (non-blocking).
   - `action: finalize_and_stop` → finish the **current chunk's commit only**, emit the final summary, exit autonomous loop. Do NOT start a new alignment-check or chunk. Plan §14.7 — no mid-commit hard cuts.
2. **Interrupt check.** Is `.build-loop/halt` present? If yes — same finalize behavior as `finalize_and_stop`. Reason: `user halt sentinel at .build-loop/halt`. (Phase A surface — `/build-loop:halt` command ships in Phase C.)
3. **Iterate cap.** Read `state.execution.iterate_attempt`. If `>= maxIterateAttemptsAutonomous` (config default 25) — finalize with reason `iterate cap reached`. Hard ceiling protects against runaway loops even when budget remains.

**Body — drain the queue:**

1. **Enumerate fresh items.** Glob `.build-loop/ux-queue/*.md` + `.build-loop/issues/*.md` + `.build-loop/backlog/*.md` + `.build-loop/proposals/*.md`. Exclude items previously routed in this run (track in `state.autonomousLoop.processed[]`). Backlog items (longer-lived deferred work) are treated identically to issues during draining — same alignment-checker routing, same per-item cap.
2. **For each item (sequential — alignment-check is per-item):**
   a. Dispatch `Agent(subagent_type="build-loop:alignment-checker", prompt=<brief>)` with `item_path`, `item_kind`, `workdir`, `current_task_id` (null when §15.2 working-state not yet shipped on this branch — graceful degradation per the agent's own contract), and the last 5 verdicts for consistency cross-checking.
   b. Parse the JSON verdict (the agent returns exactly one JSON object, no fence). Append to `state.runs[].alignment_verdicts[]` (one row per item, capped at 200 per run).
   c. **Route by verdict:**
      - **`aligned`** — schedule the item for Phase 2 → 3 → 4. Treat as a one-item plan: feed alignment-checker's `reason` + `matched_anchors` to plan-critic as part of the brief so plan-critic knows why this item earned alignment.
      - **`misaligned`** — `mv` the item to `.build-loop/followup/<basename>`. Append a markdown footer to the moved file: `\n\n---\n_Deferred by alignment-checker: <reason>. Violated: <comma-separated violated_non_goals>._\n`.
      - **`uncertain`** — emit `PushNotification` with item path + `uncertainty_evidence`. `TaskCreate` a follow-up task captioned `Review uncertain queue item: <basename>`. Do NOT block — continue loop with remaining items.
   d. **Per-item cap — Phase A logs only.** Plan §14.6 — per-item ≤ 3 same-verdict cap enforced in Phase C. Phase A logs a one-line warning when the same item gets the same verdict ≥ 3 times: `[autonomous] item <basename> received <verdict> for 3rd time — Phase C will force misaligned`.
   e. **Record item telemetry (fail-open).** Append one `item_iteration` row so `task_surface.py` and dry-run review show per-item attempt history with the tier that judged it: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/exec_state.py item-iteration --workdir "$PWD" --item-id <basename> --status <started|deferred|stopped> --validator alignment-checker --tier code` — status maps from the verdict (`aligned`→`started`, `misaligned`→`deferred`, `uncertain`→`stopped`); `--tier code` is the alignment-checker's tier and resolves the model via `model_overrides`. A non-zero exit is logged and skipped — telemetry never wedges the loop.
3. **Commit + advance.** When an `aligned` item finishes Phase 2 → 4, record completion telemetry: `exec_state.py item-iteration --workdir "$PWD" --item-id <basename> --status passed --validator independent-auditor --tier frontier` (the item's Review-A verdict tier — same `--tier frontier`→`fable` resolution the agent-ledger records, so the two lanes join on `item-id`). Then the standard Phase 3 commit step runs. Increment `state.execution.budget.commits_since_push`. **Push behavior in Phase A is unchanged from today** (manual) — `scripts/autonomous_push.py` ships in Phase B. The `budget_check.py` envelope's `should_push_now` field is informational only in Phase A; the orchestrator surfaces it in check-ins but does not push autonomously yet.

**Exit conditions (any one stops the loop):**

| Condition | Action |
|---|---|
| Queue empty + classic iterate complete | Normal exit → Phase 6 Learn (if enabled) → Review-G report |
| `budget_check.action == finalize_and_stop` | Finish current commit only, emit summary, exit |
| `.build-loop/halt` sentinel present | Same as `finalize_and_stop` |
| `iterate_attempt >= maxIterateAttemptsAutonomous` | Same as `finalize_and_stop`, reason `iterate cap` |
| Concurrent-modification trip via existing M4 collision detection | Existing safe-stop behavior |

**Report contribution.** At Review-G, the orchestrator writes a `budget_summary` to the run entry via `write_run_entry.py --budget-summary-json <tmp>`. Shape:

```json
{
  "mode": "default | long | custom",
  "budget_seconds": <int>,
  "used_seconds": <int>,
  "items_closed": <int>,
  "items_deferred": <int>,
  "commits": <int>,
  "pushes": <int>
}
```

Same mechanism as `--judge-decisions-json` (commit `c80cfc8`). Tracked under `state.runs[].budget_summary` for cross-run pattern mining by Phase 6 Learn.

**Resume on autonomous runs.** `scripts/resume_resolver.py.resolve()` returns `budget_resume.preserve_deadline: true` with the original `deadline_at`. The orchestrator MUST write that block back into `state.execution.budget` verbatim on resume — never recompute. A 2h budget that crashed at 1h59m gets only the remaining 1m on resume.
