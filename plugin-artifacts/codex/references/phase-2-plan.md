<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 2: Plan (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full Plan phase: task breakdown, dependency graph, mockup gate, and plan acceptance.

## Phase 2: Plan — Steps & Optimization

**Goal**: Break work into executable steps, then optimize the plan before execution.

0. **Consume the Phase 1 spec-router record (author selection)**: READ `state.json.intent.spec_router` (written by Phase 1 Assess step 11 per `references/capability-routing.md` §"Spec/Plan author router (intent-driven, ordered)"). Do NOT independently re-decide which author skill to call — **branch on `action` first**, then on `skill`:
   - `action: "noop"` → **terminal**: author NOTHING from the router. Skip the author/writing-plans invocation entirely and proceed to step 1's optimization work without drafting a spec. Do not fall through to `writing-plans`.
   - `action: "recommend"` → surface-only: name the recommended `skill` in the report so the lead knows what to run if it chooses, but do NOT auto-invoke it. Then proceed. (Distinct from `call`, which auto-invokes, and `noop`, which skips silently.)
   - `action: "call"`, `skill: "build-loop:spec-writing"` → invoke `Skill("build-loop:spec-writing")` to draft the plan (the `no-plan` case: `.build-loop/plan.md` absent/empty).
   - `action: "call"`, `skill: "build-loop:writing-plans"` → the plan exists and is valid; go straight to step 1 (`writing-plans` turns it into the task/dependency graph). Skip spec-writing. `writing-plans` is the external superpowers skill, not vendored here; if absent, write a structured plan inline (see `references/capability-routing.md` §"Core loop skills/assets" fallback).
   - `action: "call"`, `skill: "prd-builder"` → greenfield PRD authoring (only when `run_active == false`); outside an active run this row rarely reaches Phase 2.
   - **Fallback** (record absent — older state, or a Codex lead that skipped step 11): apply the router's own logic inline. Compute `plan_status` exactly as the signal is defined — `no-plan` when `.build-loop/plan.md` is absent/empty OR the last `plan-verify` result failed; `plan-valid` otherwise. If `plan_status == no-plan`, invoke `Skill("build-loop:spec-writing")`; otherwise skip to step 1.

   When spec-writing is invoked it walks the completeness checklist (auth guard, external API contracts, rate-limit criterion, discoverability surfaces, server/client boundary, concurrency mechanism, observability events, input validation, UI input/output contract when UI is in scope, routing-risk fields, dispatch/env-var fields, capability gap map, single-shot build guardrails, and read-before-edit map), runs `check_checklist.py` + `plan-critic`, writes the plan to `docs/plans/<feature-slug>.md`, and commits it before any implementation branches are cut. Only continue to step 1 once a plan path exists.

1. **Invoke `writing-plans` skill** for detailed task breakdown
2. **Identify parallel-safe tasks** vs sequential dependencies — build a dependency graph
   - If the graph has 2+ independent / parallel-safe chunks, write `parallel_batch:` naming the chunks that will dispatch together.
   - If the graph appears parallelizable but execution must serialize, write `parallel_skipped_reason:` with the specific dependency, tool limit, or coordination constraint.
3. **Map each task to intent**: state which user workflow, user-value rule, and north-star outcome it supports. Remove tasks that add complexity without clear user value.
3a. **Approach Lenses section**: For non-trivial architecture, workflow, dependency, UI/product, or long-lived interface decisions, add `## Approach Lenses` before the task list. Use the Phase 1 `.build-loop/state.json.approachLenses` summary and include:
   - **Clean-sheet best approach**: the use-case-first answer if no prior implementation debt or historical decisions constrained the design.
   - **Current-constraints approach**: the best practical answer given the repo's existing code, dependencies, tools, debt, migration risk, and delivery horizon.
   - **Bridge/backcast**: the smallest credible migration path from current state toward the clean-sheet target.
   - **Recommendation**: what to execute now and why. If choosing the constrained path, name the constraint that justifies not taking the clean-sheet path now.

   Skip only for narrow single-file fixes, pure config changes, or decisions where the two answers are identical; in that case write `Approach Lenses: n/a - <reason>`.
3b. **Depends-on (reads-from) section**: For any plan that ships code, add a `## Depends-on (reads-from)` section listing every data path, contract, or invariant the new/changed code reads. Each entry takes the form `- \`<path-or-contract>\` — verified` or `- \`<path-or-contract>\` — unverified`. Mark `verified` when you can confirm something in the repo writes that path or holds that invariant (grep, schema inspection, or test fixture confirms it); mark `unverified` when no writer exists or you cannot confirm. Any `unverified` entry is a BLOCKING unknown that must be resolved — either add the missing writer to the plan, remove the read, or add `override: reads-from-dependency` with rationale. This section is exempt for doc-only and config-only plans that name no source-code paths. Enforced by `plan-verify` rule `reads-from-dependency`.
3c. **Activation Map section**: For any plan that proposes a new *event-driven or call-site-dependent* component — a stop/SessionStart/PreToolUse/PostToolUse hook, a cron/launchd job, a watcher, a git hook (pre-commit/post-commit), a webhook, or a gate that fires on a host event — add an `## Activation Map` section. This converts build-loop's recurring failure class (machinery built, activation path never verified — a dormant WARN gated on a dict that int()'d to 0, state_finalize reading the wrong phase key, repo-level codex hooks that never fired, run-identity reuse silently skipping records) into a structural plan requirement. Each entry takes the form `- <component> — trigger: <event-or-call-site> — verified-live: yes|pending`. The `trigger:` must name the *concrete* host event or call site (e.g. `PostToolUse:Bash matcher in hooks/hooks.json`, `SessionStart hook`, `pre-commit hook in .pre-commit-config.yaml`), not an aspiration ("runs at review time"). Mark `verified-live: yes` only when you have confirmed the trigger actually fires (a live run exercised it, or a test asserts the host event reaches the handler); mark `verified-live: pending` otherwise. Any `pending` entry must map to a verification task before Report — the plan does not close while a component's activation is unconfirmed. This section is exempt for plans that propose no new event-driven machinery (doc-only, refactor-only, pure inline-logic changes); a `## Activation Map` is not required there. Add `override: activation-map-exempt` with rationale only when the section genuinely does not apply. Enforced by `plan-verify` rule `activation-map-required` (BLOCKER: missing section on a dormant-risk plan, or any entry that names a `trigger:` without a `verified-live:` key).
3d. **Capability Gap Map section**: For any non-trivial implementation plan, add `## Capability Gap Map` before the F/Q criteria. Map each changed capability or workflow to: current source of truth, target behavior, gap, build action, owned files/contracts, and validation. This is the default home for "current vs target" gap closure; avoid a separate gap-closure plan unless the user asks for one or the map is too large for the main plan.
3e. **Single-Shot Build Guardrails section**: For any non-trivial implementation plan, add `## Single-Shot Build Guardrails`. Each row names a guardrail, the failure mode it prevents, and the evidence/test that proves compliance. Guardrails must be enforceable: cite a target test, ADR, source file, acceptance criterion, or command. Generic cautions without evidence do not count.
3f. **Read-Before-Edit Map section**: For any non-trivial implementation plan, add `## Read-Before-Edit Map`. Each work item must name the files, tests, contracts, docs, or search commands the implementer reads first; why those reads matter; and which files are edited after. This keeps execution grounded in current repo state and reduces build-from-memory drift.

4. **Partition tasks and files MECE**: Use one grouping dimension per level (domain, layer, workflow, bounded context, adapter, or test surface). Every changed file gets exactly one owner; every required behavior, state, migration, test, and user-facing surface gets an owner.
5. **Define subagent integration points**: Where do agents need to coordinate? Where must outputs be tested together? Record interface contracts and checkpoints for every boundary.
6. **Codex delegation gate**: If running in Codex, record whether the user explicitly authorized subagents/parallel delegation. If not, keep all execution local even when the graph contains parallel-safe groups.
7. **Research Context gate**: read `.build-loop/state.json.researchGate`. If
   `research_required: true`, add `## Research Context` to the plan with the
   returned `depth`, `packet_path`, source policy, and
   `blocks_final_claims` value. If `packet_path` is non-null, state whether
   the packet already exists, will be created before Execute, or is unavailable
   with rationale. For current/external/API claims, verify current docs before
   coding and do not carry uncited claims into the final report.
8. **UI input/output contract gate**: If `uiTarget != null`, load `references/ui-io-contract.md` and add a `## UI Input/Output Contract` section to the plan before mockups or implementation. The section must cover every affected screen/component and name: user inputs, system outputs, data taxonomy, CRUD/domain operation, component mapping, state matrix, modality fallback, validation/security, and traceability. If a planned UI component has no named input/output, remove it or mark it decorative with rationale; decorative controls are usually a scope error.
8a. **Calm Precision core-consideration gate**: If `uiTarget != null`, the design direction must treat Calm Precision as a core decision gate before selecting structure, style mode, motion, or interaction behavior. The resulting `.build-loop/app-contract/ui.md` must include `## Calm Precision Core Considerations` with relevant principles, perceptual foundations, implementation effects, and explicit exceptions.
8b. **Recent design structures gate**: If `uiTarget != null`, load `references/recent-design-structures.md` before dispatching `design-contract-specialist`. The specialist, not the planner, selects the structure. The plan should pass the file path and any relevant mockup/screenshot/design artifacts; it should not force a named structure unless the user explicitly requested one.
9. **Mockup-first gate for major UI work**: If the plan introduces a *new page/screen* or makes a *major redesign* (changes navigation graph, primary user flow, or replaces ≥40% of an existing screen), pause Plan and invoke `mockup-gallery:mockup-session-new` to draft black-and-white mockups before any UI is written. Wait for user feedback via `mockup-gallery:mockup-feedback`; carry the selected mockup into Execute as a reference. Skip for cosmetic tweaks, copy edits, or single-component swaps. This is the documented exception to build-loop's "actions/functions only, no UI surfaces" plugin-bridging policy — mockup drafting is itself the action.

**Optimization checklist** (review the plan for these before proceeding):
- Can more tasks run in parallel? Unnecessary sequential bottlenecks?
- Can subagent context be smaller? Shared reads that should be done once?
- Missing dependencies, interface mismatches, env assumptions?
- Changes that could conflict with each other (oscillation risk)?
- Is the recommendation accidentally anchored to current tech debt when a cleaner use-case-first answer exists?
- If the plan chooses the current-constraints approach, is the bridge/backcast explicit enough to prevent the compromise from becoming permanent architecture by default?
- Define coordination checkpoints where subagents must sync
- UI/API/data choices that add options, mocks, or complexity without user value?
- UI plans missing input/output coverage, state coverage, modality fallbacks, validation/security layers, or schema/API traceability?
- Missing capability gap rows: any target behavior without a current source of truth, named gap, owner, and validation?
- Missing single-shot guardrails: known failure modes without enforceable evidence/tests?
- Missing read-before-edit entries: any implementation chunk whose required current-state reads are implicit?
- MECE gaps or overlaps: unowned responsibilities, shared file ownership, or mixed grouping dimensions?
- Boundaries that are too tight, too broad, or missing a stable interface?
- If the plan chooses a simpler/integrated path over modularity, is there a documented `MODULARITY EXCEPTION`?

**Plan acceptance gate** — required before "Output: Plan file":

**Readback discipline**: build-loop runs `plan-verify` and `plan-critic` automatically and prefixes every plan presentation with a one-line gaps-readback. The user should never have to ask "anything missing?" — the answer is always shown first.

Readback format (one line, mandatory, before the plan body):
- `✓ Plan gaps-checked (plan-verify + plan-critic): none` — when both passes are clean.
- `⚠ Plan gaps: <N> — <comma-separated list of findings>` — when findings exist, with each item marked `resolved` or `surfaced` (resolved = fixed in this plan revision; surfaced = carried as open for user awareness).

8. **Run `plan-verify`** (deterministic, grep-checkable rules; now includes `no-stop-language` rule):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_verify.py <plan.md> --repo "$PWD" --json
   ```
   - Exit 0 → proceed to step 9.
   - Exit 1 → revise the plan to address each BLOCKER, or document an explicit override in `.build-loop/state.json.planVerifyOverride[]` with rationale before proceeding.
   - Exit 2 → treat as verifier outage; log and proceed with `plan-critic` alone plus a state.json warning.
   - `parallel-decision-record` is a BLOCKER: plans that name independent / parallel-safe multi-chunk work must include `parallel_batch:` or `parallel_skipped_reason:`.
   - Full rule list and contract: `${CLAUDE_PLUGIN_ROOT}/skills/plan-verify/SKILL.md`.
9. **Dispatch `plan-critic` agent** (non-deterministic checks): pass the plan + the JSON from step 8 so the critic doesn't re-derive deterministic findings. Critic surfaces alternatives-considered, MECE scope, marker adequacy, headline drift. The critic's emitted severity caps at WARN. **Gating is stakes-conditional (decided by the orchestrator, not the critic):** on high-stakes plans (`synthesisDensity > 5`, `triggers.riskSurfaceChange`, `stakes >= medium`, or `dispatch_tier: frontier`) those WARNs are **blocking** — Phase 2 does not finish until each is revised or explicitly overridden; otherwise they are **advisory** (today's behavior). The gate advances on objective signals only, never self-reported confidence. See `references/advisor-dispatch-ladder.md`.
10. **Emit gaps-readback** using the combined output of steps 8–9. Populate the one-line readback prefix before presenting the plan. Both passes must complete before the plan is shown to the user — never present a plan without the readback line.
11. **Dispatch `scope-auditor` agent** (Plan→Execute boundary): pass the plan + extracted commit table (with `modifies_api` per commit). The auditor is Opus + read-only; it traces every caller-site of every modified-API symbol via project-wide grep, classifies callers as in-scope / out-of-scope, and emits a `## Caller Audit (Scope Auditor)` JSON section appended to the plan. Verdict `scope_gap_found` requires plan revision (absorb missing callers into the right commit's owned-files) before Phase 3, OR explicit acceptance in `state.json.scopeGapAccepted[]` with rationale. Skip ONLY when the plan has zero `modifies_api` entries (doc-only commits). Prevents the fan-out scope-blindness defect class — see `agents/scope-auditor.md`.

**Output**: Plan file with dependency graph, integration points, optimization notes, plan-verify JSON, plan-critic findings, gaps-readback line, and scope-auditor caller audit.
