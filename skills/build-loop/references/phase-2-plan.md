# Phase 2: Plan (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full Plan phase: task breakdown, dependency graph, mockup gate, and plan acceptance.

## Phase 2: Plan — Steps & Optimization

**Goal**: Break work into executable steps, then optimize the plan before execution.

0. **If no plan exists yet**: check whether `.build-loop/plan.md` is absent or empty. If so, invoke `Skill("build-loop:spec-writing")` to draft a build-loop-compatible plan markdown before proceeding. The spec-writing skill walks the 8-item completeness checklist (auth guard, external API contracts, rate-limit criterion, discoverability surfaces, server/client boundary, concurrency mechanism, observability events, input validation) and runs `check_checklist.py` + `plan-critic` on the output. It writes the plan to `docs/plans/<feature-slug>.md` and commits it before any implementation branches are cut. Only continue to step 1 once the spec-writing skill returns a plan path. Skip this step when a valid plan already exists and passed `plan-verify` on the previous run.

1. **Invoke `writing-plans` skill** for detailed task breakdown
2. **Identify parallel-safe tasks** vs sequential dependencies — build a dependency graph
3. **Map each task to intent**: state which user workflow, user-value rule, and north-star outcome it supports. Remove tasks that add complexity without clear user value.
4. **Partition tasks and files MECE**: Use one grouping dimension per level (domain, layer, workflow, bounded context, adapter, or test surface). Every changed file gets exactly one owner; every required behavior, state, migration, test, and user-facing surface gets an owner.
5. **Define subagent integration points**: Where do agents need to coordinate? Where must outputs be tested together? Record interface contracts and checkpoints for every boundary.
6. **Codex delegation gate**: If running in Codex, record whether the user explicitly authorized subagents/parallel delegation. If not, keep all execution local even when the graph contains parallel-safe groups.
7. **Research check**: For any external framework, API, or deployment target — verify current docs before coding
8. **Mockup-first gate for major UI work**: If the plan introduces a *new page/screen* or makes a *major redesign* (changes navigation graph, primary user flow, or replaces ≥40% of an existing screen), pause Plan and invoke `mockup-gallery:mockup-session-new` to draft black-and-white mockups before any UI is written. Wait for user feedback via `mockup-gallery:mockup-feedback`; carry the selected mockup into Execute as a reference. Skip for cosmetic tweaks, copy edits, or single-component swaps. This is the documented exception to build-loop's "actions/functions only, no UI surfaces" plugin-bridging policy — mockup drafting is itself the action.

**Optimization checklist** (review the plan for these before proceeding):
- Can more tasks run in parallel? Unnecessary sequential bottlenecks?
- Can subagent context be smaller? Shared reads that should be done once?
- Missing dependencies, interface mismatches, env assumptions?
- Changes that could conflict with each other (oscillation risk)?
- Define coordination checkpoints where subagents must sync
- UI/API/data choices that add options, mocks, or complexity without user value?
- MECE gaps or overlaps: unowned responsibilities, shared file ownership, or mixed grouping dimensions?
- Boundaries that are too tight, too broad, or missing a stable interface?
- If the plan chooses a simpler/integrated path over modularity, is there a documented `MODULARITY EXCEPTION`?

**Plan acceptance gate** — required before "Output: Plan file":

8. **Run `plan-verify`** (deterministic, grep-checkable rules):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_verify.py <plan.md> --repo "$PWD" --json
   ```
   - Exit 0 → proceed to step 8.
   - Exit 1 → revise the plan to address each BLOCKER, or document an explicit override in `.build-loop/state.json.planVerifyOverride[]` with rationale before proceeding.
   - Exit 2 → treat as verifier outage; log and proceed with `plan-critic` alone plus a state.json warning.
   - Full rule list and contract: `${CLAUDE_PLUGIN_ROOT}/skills/plan-verify/SKILL.md`.
9. **Dispatch `plan-critic` agent** (non-deterministic checks): pass the plan + the JSON from step 8 so the critic doesn't re-derive deterministic findings. Critic surfaces alternatives-considered, MECE scope, marker adequacy, headline drift. Severity capped at WARN — does not block.
10. **Dispatch `scope-auditor` agent** (NEW 2026-05-07 — Plan→Execute boundary): pass the plan + extracted commit table (with `modifies_api` per commit). The auditor is Opus + read-only; it traces every caller-site of every modified-API symbol via project-wide grep, classifies callers as in-scope / out-of-scope, and emits a `## Caller Audit (Scope Auditor)` JSON section appended to the plan. Verdict `scope_gap_found` requires plan revision (absorb missing callers into the right commit's owned-files) before Phase 3, OR explicit acceptance in `state.json.scopeGapAccepted[]` with rationale. Skip ONLY when the plan has zero `modifies_api` entries (doc-only commits). Prevents the fan-out scope-blindness defect class — see `agents/scope-auditor.md`.

**Output**: Plan file with dependency graph, integration points, optimization notes, plan-verify JSON, plan-critic findings, and scope-auditor caller audit.
