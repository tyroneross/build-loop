# Build Loop

Orchestrated 5-phase development loop (+1 optional) for significant multi-step code changes. Use this methodology when changes span multiple files, require planning, and benefit from structured validation.

**Skip this loop for:** single-file edits, config changes, quick fixes under ~20 lines.

## Phases

| # | Phase | Purpose | Output |
|---|-------|---------|--------|
| 1 | **Assess** | Understand state (project type, architecture, tools, prior state) AND define goal + 3-5 scoring criteria with pass/fail conditions | State summary + `.build-loop/goal.md` |
| 2 | **Plan** | Break work into tasks with dependency order, identify parallel-safe groups | Plan with dependency graph |
| 3 | **Execute** | Build it — dispatch parallel work for independent file groups | Working implementation |
| 4 | **Review** | Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Report — six ordered sub-steps, single exit point | Scorecard + evidence; routes to Iterate on failure |
| 5 | **Iterate** | Fix Review failures, loop back to Review (max 5x) | Updated scorecard |
| 6 | **Learn** (optional) | Detect recurring patterns across runs, auto-draft experimental skills/agents with A/B tracking; auto-promote on metric wins when enabled | Experimental artifacts + synthesis |

## Core Principles

- **Tools on demand.** Detect what's available, use what's needed. Don't assume any tool exists. The debugger (skills + MCP) is bundled inside build-loop as of 0.6.0; `/build-loop:debug <symptom>` is always available, and the orchestrator auto-invokes `Skill("build-loop:debug-loop")` on Review-B Validate failures and Iterate attempts 2 and 3.
- **North star first.** Understand the app/repo purpose, primary users, core workflows, and update intent before planning. Every subtask should explain how it contributes to that purpose.
- **Beauty in the basics.** Core flows, real data, clear hierarchy, useful states, working controls, and accurate information matter more than extra surface area.
- **Modular by default, not by dogma.** Prefer high cohesion, loose coupling, stable interfaces, and scalable boundaries unless a simpler or integrated approach better serves the use case. Document `MODULARITY EXCEPTION: <reason>` when taking that path.
- **MECE work ownership.** Partition files, agents, and task groups so ownership is mutually exclusive and collectively exhaustive: no overlapping file owners, no unowned responsibilities, and one clear grouping dimension per level.
- **Guidelines for creation, guardrails for output.** Be flexible during building. Be strict about what reaches users.
- **No false data.** No mock data in production. No hardcoded metrics pretending to be real. No unverified claims.
- **Name every UI input and output.** For UI work, every affected surface must have an input/output contract before component choices are locked: data taxonomy, CRUD/domain operation, component mapping, states, modality fallback, validation/security, and traceability.
- **Diagnose before fixing.** Root-cause analysis before code changes. Many errors sharing a pattern = one system problem.
- **Converge or escalate.** If iteration isn't improving scores, stop and surface the blocker. Don't burn cycles.
- **Keep going until done.** Once the user accepts the plan, every phase is authorized scope. Do not ask the user to confirm each phase. Issues found mid-build route to Iterate. Status updates are fine; permission requests are not. The only valid stops are: a destructive action not in the plan, a missing credential, externally-blocked work, an explicit hand-off point in the plan, a genuine scope branch the plan does not resolve, or 8 hours wall-clock without a Review pass / 5 consecutive Iterate failures on the same criterion.

## Phase Details

### Phase 1: Assess

Combines situational awareness with goal definition so Plan has everything it needs.

**Understand state:**
- Detect project type and tooling (language, framework, test runner, linter, build system)
- Read deployment policy from `.build-loop/config.json.deploymentPolicy` when present. Default: `preview: auto`, `testflight: auto`, `production: confirm`, `unknown: confirm`.
- Capture app/repo north star and update intent in `.build-loop/intent.md`: purpose, primary users, core jobs, user value, and non-goals.
- Capture modular structure in `.build-loop/state.json.structure`: current module boundaries, stable interfaces, coupling risks, likely MECE work partitions, and any justified modularity exception.
- Map relevant architecture (only what the goal touches)
- Check for prior state (`.build-loop/state.json` from interrupted builds)
- If goal involves external frameworks or APIs: research current docs before planning
- If web/mobile UI: capture current visual state for before/after comparison, then load `skills/build-loop/references/ui-io-contract.md` and inventory the affected user inputs and system outputs before planning
- **Supply-chain dependency cooldown**: if a JS project (`package.json`), run `scripts/inject_dependency_cooldown.py --workdir <repo>` to idempotently write the 7-day publish-age config using each PM's native key: npm ≥ 11.10.0 → `.npmrc` `min-release-age` (DAYS); pnpm → `pnpm-workspace.yaml` `minimumReleaseAge` (MINUTES) + `.npmrc` `minimum-release-age` for 10.x; yarn ≥ 4.10 → `.yarnrc.yml` `npmMinimalAgeGate` (numeric MINUTES). npm has no native exclude (npm/cli#8994), so on npm the user-authored allowlist (`.build-loop/config.json` → `dependencyCooldown.allowlist`, default `["@tyroneross/*"]`) is enforced by the PreToolUse hook (`scripts/hooks/pre_bash_dependency_cooldown.sh`), which stays engaged even with native config; pnpm/yarn carry the exclude natively so the hook stands down once enforced. `--check` verifies the PM actually recognizes the key (no false `enforced:true`). Constitution rule: `C-SUPPLY/dependency_cooldown`. Older npm (< 11.10.0) falls back to the hook's `--before=<7d ago>` date-pin. pip/cargo not covered in v1.

**Multi-session presence registration + collision check (cross-host: Claude Code, Codex, Gemini CLI, others):**

Multiple build-loop sessions can run concurrently against the same project across terminals and coding hosts. To prevent two sessions from racing to commit to the same files, every session participates in a shared registry at `~/.build-loop/sessions/` and a memory-write log at `~/.build-loop/memory/INDEX.jsonl`. The scripts are host-neutral JSON CLIs invoked from any host:

1. **Surface SAFE-STOP sentinels first.** Before any other Phase 1 work, list `<workdir>/.build-loop/SAFE-STOP-collision-*.md`. If any exist, surface to the user and require explicit deletion before proceeding — they indicate a prior session detected a CRITICAL collision here and stopped.
2. **Register this session** (immediately after `run_id` is known):
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_registry.py register \
     --run-id "$RUN_ID" --host codex --workdir "$PWD" \
     --pid $$ --phase assess
   ```
   `--host` values: `claude_code | codex | gemini | other`. The script writes `~/.build-loop/sessions/<run_id>.json` with workdir, host, pid, phase, files_owned, started_at, last_heartbeat_at.
3. **Check for peer collisions**:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_registry.py check \
     --run-id "$RUN_ID" --workdir "$PWD" --phase assess --json
   ```
   Exit codes: 0=LOW, 1=MEDIUM, 2=HIGH, 3=CRITICAL.
4. **Headless tier routing** (Codex / cron — no AskUserQuestion available):
   - LOW (different workdir) → log + proceed.
   - MEDIUM (same workdir, different phases) → log + proceed.
   - HIGH (same workdir + both in execute/iterate) → log + enter `high_frequency_mode` (heartbeat every 30s vs default 5min) + proceed; recheck collision before each Phase 3 chunk dispatch.
   - CRITICAL (same workdir + overlapping `files_owned`) → call `session_registry.write_safe_stop_sentinel(workdir, peer_run_id, reason)` and exit non-zero. The first sentinel wins; the surviving peer continues.
5. **Heartbeat refresh** at every M2 trigger point (dispatch_chunk, return_chunk, phase_transition, iterate_attempt). Append `--phase $CURRENT_PHASE` whenever phase changes; in Phase 3, also `--files-owned "$FILES_OWNED_CSV"`.
6. **Unregister on clean completion**:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_registry.py unregister --run-id "$RUN_ID"
   ```
   Moves presence file to `sessions/dead/`. Stale-sweep (default 5 min) handles forgotten unregisters.
7. **Memory writes** — use `scripts/memory_writer.py write` instead of writing memory files directly. The writer adds provenance frontmatter (source_repo, source_workdir, source_run_id, source_host, cross_repo_validated, applied_in_repos, created_at, last_updated_at), then atomically appends a row to `INDEX.jsonl` for sibling discovery:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py write \
     --file "<rel-path>" --name "<slug>" --description "<one-line>" \
     --type feedback --run-id "$RUN_ID" --workdir "$PWD" --host codex \
     --body-file /tmp/memory-body.md
   ```
8. **Memory reads (cross-session discovery)** — between phases, tail the index for new peer learnings:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_index.py tail \
     --since "$LAST_INDEX_CHECK_TS" --exclude-run-id "$RUN_ID" --json
   ```
   For each returned row, read the memory file. Tag with `[CROSS-REPO — requires scrutiny]` when `source_workdir` ≠ current `$PWD` AND `source_repo` ≠ this repo's git remote. Tag with `[VALIDATED — applied in N repos]` when `cross_repo_validated: true` AND `len(applied_in_repos) >= 2`.
9. **Memory mark-applied** — when a cross-repo memory is successfully applied here, record the application:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py mark-applied \
     --file "<rel-path>" --applying-repo "$THIS_REPO_REMOTE" \
     --applying-workdir "$PWD" --applying-run-id "$RUN_ID"
   ```
   Flips `cross_repo_validated: true` once a different repo confirms the lesson.
10. **One-time migration** — on the first build after installing this version, run:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py migrate \
     --run-id "$RUN_ID" --workdir "$PWD" --host codex
   ```
   Idempotent backfill of provenance frontmatter onto pre-existing memory files. Safe to re-run.

All three scripts are stdlib-only Python 3.11+ with atomic writes (`tmpfile + os.replace`) and `fcntl.flock` on append. They work identically in Claude Code and Codex; the only difference is which `--host` value the session passes.


**Define goal + criteria:**
- State the goal in one concrete sentence — what will be true when this succeeds?
- Design 3-5 scoring criteria. Each criterion must have:
  - A clear pass condition
  - A grading method: code-based (preferred) or LLM-as-judge (for nuance)
- Write goal to `.build-loop/goal.md`

**Synthesis-density routing (4-priority resolution):** if a plan exists at this point, count its `synthesis_dimensions:` entries (see Phase 2) and resolve the implementer tier in this priority order:

1. **Explicit override** — if the host config sets a `thinking-tier` override OR the plan/chunk frontmatter declares `tier: thinking` → route to thinking-tier.
2. **Auto-escalate on density** — `count > 5` (6 or more entries) → `tier: thinking` (synthesis-dense at the commit level; fan-out loses cross-dimension coherence here).
3. **Default — fan-out for speed** — count 0–5 → fan out to code-tier implementers; backstops in Phase 4.5 catch the residual recall gap.
4. **Per-chunk override** — individual chunks may declare `tier: thinking` even when the plan-level decision was fan-out (mixed-density plans).

Write the verdict to `.build-loop/state.json.synthesisDensity` as `{count, escalated, reason}`. Routing target is `tier: thinking` (provider-agnostic), never a hardcoded model name.

The `> 5` threshold matches the empirical inflection point measured in the synthesis-decision A/B experiment (2026-05-07): below that, code-tier implementer recall is poor but non-zero and the Phase 4.5 backstops materially help; at 6+ dimensions, depth dominates.

**Eval methodology:**
- Binary pass/fail only. No Likert scales, no partial credit.
- One evaluator per dimension. No multi-dimension "God Evaluator."
- Code-based graders first (test pass/fail, lint clean, build succeeds, type check passes).
- LLM-as-judge only for criteria code can't evaluate (UX quality, naming clarity, etc.).

### Phase 2: Plan

- Break work into tasks with exact file paths
- Identify dependency order — what must complete before what?
- Flag parallel-safe groups: files that don't import each other can be written simultaneously
- Partition files and agents MECE: every changed file has exactly one owner, every required responsibility has an owner, and each group declares `owns`, `does not own`, `interface contract`, and `integration checkpoint`
- Define checkpoints where work should be verified before continuing
- Optimize: remove unnecessary steps, combine related changes, eliminate redundant work
- **UI input/output contract gate**: if `uiTarget != null`, add a `## UI Input/Output Contract` section before mockups or implementation. It must name every changed surface's inputs, outputs, data taxonomy, operation/domain verb, component mapping, state matrix, modality fallback, validation/security layer, and schema/API/design-system traceability.
- **Enumerate synthesis dimensions** for any commit that involves design judgment (UI placement, copy tone, CTA tier, schema shape, dispatch contracts, etc.). Add a `synthesis_dimensions:` block to the plan listing each named decision with a concrete claimed value:
  ```yaml
  synthesis_dimensions:
    placement_NewsBanner: "after `<NewsCard>` in app/components/Feed.tsx"
    cta_tier_save_button: "primary"
    copy_tone_settings: "second person, calm-precision, no exclamation marks"
    empty_state_feed: "icon + one-line explanation + primary CTA"
  ```
  Vague values (`"appropriate"`, `"as needed"`, `"sensible"`) fail the deterministic plan-verify rule — every entry must name a specific choice or write `n/a` with a reason. The block is the contract the implementer attests to applying; Phase 4.5 lints diff-vs-claim.
- **Mockup-first gate for major UI work**: if the plan introduces a new page/screen or makes a major redesign (changes navigation graph, primary user flow, or replaces ≥40% of an existing screen), pause and use a mockup-drafting tool to produce black-and-white mockups before any UI is written. Wait for user feedback; carry the selected mockup into Execute as a reference. Skip for cosmetic tweaks, copy edits, or single-component swaps. This is the documented exception to the "actions/functions only, no plugin UI surfaces" bridging policy — mockup drafting IS the action.
- **Pay-it-forward architectural gate**: when a chunk touches a typed protocol / interface boundary / schema / multi-surface-capable behavior, the plan MUST include a `Path A vs Path B` comparison per `skills/build-loop/references/pay-it-forward-arch.md`. Default recommendation is **Path B** (typed-contract extension). Gates that justify Path A: time-budget >2×, missing dep/infra, missing design decision, or empty foreclosed-future-capability list. Named-future-capability list must cite the roadmap / PRD / `intent.md` — flexibility-for-its-own-sake (plugin systems, abstract factories with no current second consumer) is the explicit anti-pattern. Skip when the chunk fires none of the signals. Path A/B section template:
  ```markdown
  ### Path A vs Path B — <chunk name>
  **Path A (minimum-viable):** <what / where / time / foreclosed>
  **Path B (typed-contract extension):** <what / where / time delta / NAMED capabilities unlocked>
  **Gates check:** time-budget? missing dep? missing decision? empty foreclosed-future-list?
  **Recommendation:** Path B (default) / Path A (because <named gate>)
  ```

**Plan acceptance gate** — required before Phase 3 begins:

1. **`plan-verify` (deterministic, Python stdlib)** — run grep-checkable rules over the plan:
   ```bash
   python3 <build-loop>/scripts/plan_verify.py <plan-file> --repo "$PWD" --json
   ```
   Catches: deletes/orphans contradicted by repo grep, internal numeric drift, route changes without evidence, package-state contradictions, missing markers, scope-split breadth.
   - Exit 0 → continue to step 2.
   - Exit 1 → revise the plan to clear each BLOCKER (or document an explicit override with rationale).
   - Exit 2 → verifier error; log and continue with step 2 only.
2. **`plan-critic` (non-deterministic)** — invoke the equivalent reviewer in your tool of choice with the plan + the JSON from step 1. Looks for: less-invasive alternatives considered, MECE quality of phase splits, marker adequacy across long passages, headline drift across sections. Findings cap at WARN — surface but do not auto-block.

Wire all three surfaces (`skills/build-loop/SKILL.md`, `agents/build-orchestrator.md`, this file) together when the gate evolves — phase-asymmetric updates have caused silent skips before.

### Phase 3: Execute

- Dispatch parallel work for independent file groups
- Each worker gets minimal context + integration contract (what interfaces to implement) + an intent packet explaining how the subtask fits the north star + a MECE ownership packet defining owned files, non-owned files, interface contracts, and integration checkpoints
- If the host supports typed subagents, map read-only codebase questions to explorer-style agents and disjoint implementation slices to worker-style agents. If the host requires explicit user authorization for subagents, identify parallel-safe groups but execute locally unless the user asked for delegation, parallelization, workers, or a `--parallel` mode.
- Do not delegate ambiguous product decisions, final integration, destructive git operations, push/deploy confirmation, or tasks whose result blocks the immediate next lead-session step.
- For UI work: follow established design system or sensible defaults (44px touch targets, 4.5:1 contrast). Every visible element must have meaning, working behavior, a clear user purpose, and a matching entry in the UI input/output contract.
- Surface pre-existing issues separately from new work. If an issue impacts users and is local to the current build, plan and fix it automatically; if too large/risky, log user impact and defer.
- Checkpoint after major integration points

**Implementer return contract (envelope):** every implementer subagent returns a structured envelope, not freeform prose. Required fields:

```json
{
  "status": "completed" | "blocked" | "failed",
  "files_modified": ["path/to/file.ts", "..."],
  "synthesis_attestation": {
    "<dimension_name>": "applied" | "deviated" | "n/a",
    "<dimension_name>": {"status": "applied", "claim": "<concrete value>"}
  },
  "novel_decisions": [
    {"decision": "<one line>", "reasoning": "<why and what alternative>"}
  ],
  "commit_sha": "<sha or null>"
}
```

The `synthesis_attestation` map MUST have one entry per dimension named in the plan's `synthesis_dimensions` block. `novel_decisions` is the recall-test field — synthesis-class decisions the implementer faced that weren't enumerated in the plan. Be honest; silent decisions defeat the purpose.

**Halt-and-ask backstop (`status: blocked`):** if an implementer encounters a synthesis-class decision NOT in the plan's `synthesis_dimensions` block, it returns `status: blocked` with the decision in `novel_decisions[]` instead of committing. The orchestrator routes each blocked decision to a thinking-tier resolver, stores resolutions in `state.json.novelDecisionResolutions[]`, and re-dispatches the implementer with resolutions appended to the brief. Hard-fail counter: 3 attempts. No new dependency required — this is a status-branch addition, not a state-machine framework.

### Phase 4: Review

Six ordered sub-steps; intermediate failures route to Iterate, final pass writes Report artifacts.

**Sub-step A — Critic (adversarial read-only)**: dispatch a read-only reviewer against the diff. Catch scope drift, missed edge cases, rubric violations before spending tokens on full validation. Strong-checkpoint findings route back to Execute (no iteration burn); guidance findings are logged.

**Sub-step A.5 — Synthesis-decision backstops (post-implementer-commit, runs before B):**

Two checkpoints fire automatically after every implementer commit on plans that declared a `synthesis_dimensions` block:

- **Phase 4.5a — `attestation_lint`** (deterministic): compares the implementer's `synthesis_attestation` envelope against the actual git diff for verifiable dimensions (placement, cta_tier, visual_weight). Catches silent drift between claim and code. Exit 1 escalates to user; exit 2 logs warning and proceeds; exit 0 silent. Subjective dimensions (copy_tone, empty_state) return `unverifiable` and route to 4.5b. Reference implementation: `scripts/attestation_lint.py` (Python stdlib, accepts strict α-style and permissive β-style claim shapes; `--strict-mode` reverts to α-only).
- **Phase 4.5b — `synthesis-critic`** (subjective, read-only critic): a code-tier critic agent reviews the diff against the plan's subjective synthesis dimensions (copy_tone, empty_state). Severity capped at WARN — never blocks. Output: `{verdict: "pass" | "flag", flagged: [{dimension, claimed, observed, reasoning}], notes}`. Skips when the diff touches no UI files (`*.tsx`/`*.jsx`/`*.vue`/`*.svelte`).

Both backstops are first-class on the code-tier (fan-out) implementer path where they catch some of the recall gap, and defense-in-depth on the thinking-tier path where they rarely fire.

**Sub-step B — Validate**: when an IBR-style declarative test runner is installed and the build touches UI, run the project's existing `.ibr-test.json` suite first as a quick pass (`scripts/ibr_quickpass.py --workdir . --scope changed`). A passing existing suite is the strongest possible signal — failing tests route directly to Iterate with the assertion as the rubric. Then check the UI input/output contract for changed surfaces, code-based graders (test, lint, type, build), and LLM-as-judge for nuanced criteria. Every pass/fail has evidence. Use only headless/programmatic surfaces — never auto-open a viewer/dashboard. Scorecard format:

| # | Criterion | Method | Result | Evidence |
|---|-----------|--------|--------|----------|
| 1 | Tests pass | code | PASS | exit 0, 47/47 |
| 2 | No lint errors | code | FAIL | 3 errors in auth.ts |

**Sub-step C — Optimize (opt-in)**: runs only when a mechanical metric exists and the user hasn't disabled it. 3-5 iterations polish. Uses autoresearch pattern: constrained scope + metric + atomic changes + commit-or-revert.

**Sub-step D — Fact-Check, Mock Scan, UX Triage, Coverage**: gates run in parallel.

- *Fact Check*: trace every rendered metric (%, $, score, count) to source. For UI work, walk the full rendered surface, not just changed files. Flag "always", "never", "100%", "guaranteed" — replace unless genuinely absolute.
- *UI Input/Output Contract Scan*: for UI work, trace every changed user input and system output to the plan contract. New gaps in component mapping, states, modality fallback, validation/security, or source traceability are blocking.
- *Mock Data Scan*: production paths only. Detect lorem ipsum, faker, hardcoded fake values, `Math.random()` in display, placeholder text. Classify blocking (renders to user) vs warning.
- *Architectural Violations* (if available): `navgator rules --json`. Blocking: circular-dependency, layer-violation, database-isolation, frontend-direct-db. Warning: hotspot, high-fan-out, orphan.
- *Plugin Cache Sync* (plugin work): resync the local cache when diverged. Defer version bumps until the feature batch is declared complete (see Version Advisor).
- *Version Advisor* (plugin work): `scripts/version_advisor.py` reads plugin manifest and last-bump SHA, counts commits since via Conventional Commits to propose semver. Default state is `hold` — a one-line note in Report. State `suggest` only when the user creates `.build-loop/release-pending.md`. Never auto-bumps; never blocks.
- *UX Triage* (UI work): `scripts/ux_triage.py` static-scans interactability, performance, data-accuracy, and usability across the full project. Each blocker/major finding becomes a queue entry in `.build-loop/ux-queue/<id>.md` with a complete fix plan. Agent-driven augmentation (performance, fact-check on broader surface) merges into the same queue.
- *Coverage Gap* (UI work + IBR available): for each surface in `.build-loop/ibr-quickpass.json.untested_surfaces`, generate a draft `.ibr-test.json` to `.ibr-tests/_draft/`. Drafts never auto-promote; user accepts by `mv` out of `_draft/`.

Blocking gates route to Iterate. Queue entries flow into Phase 5's prioritized work list. Warnings land in Report.

**Sub-step E — Simplify**: trim the diff — inline single-use helpers, delete dead branches, remove validation for upstream-guaranteed invariants. Preserve public API, tests, observability, and modular boundaries that protect user value, scalability, accuracy, security, testability, or stable interfaces. If an integrated simplification is better, document `MODULARITY EXCEPTION`.

**Sub-step F — Report** (only on final Review pass):
- **Scorecard** with final pass/fail per criterion + evidence
- **Verified** (working with evidence), **Unknown** (untested), **Unfixed** (post-cap)
- **Discovered issues**: pre-existing problems from assessment
- **Fact check results**: warnings from sub-step D

Write scorecard to `.build-loop/evals/YYYY-MM-DD-<topic>-scorecard.md`. Append run entry to `.build-loop/state.json.runs[]` with `run_id`, phase statuses, files touched, diagnostic commands, manual interventions, active experimental artifacts.

Before any push/deploy, classify the exact command with `scripts/deployment_policy.py` when available. Follow the returned action: `auto` may run after Review passes; `confirm` requires explicit user confirmation in chat; `block` must not run. Defaults allow preview deploys and Xcode/App Store Connect/TestFlight upload/export flows, while production deploys, releases, publishes, protected-branch pushes, and unknown targets require confirmation.

### Phase 5: Iterate

Build a prioritized work list per pass: (1) blocking Validate failures, (2) blocker UX queue entries with `architecture_impact: false`, (3) major UX queue entries with `architecture_impact: false`, (4) optimization findings, (5) IBR coverage-gap drafts. Entries with `architecture_impact: true` are deferred to Report for explicit user confirmation, NOT picked up here. Do not defer based on patch size — the only deferral signal is architecture impact.

Partition the list by disjoint `files_touched` and dispatch up to 4 parallel implementer subagents per pass (the standard cap). Sequential groups process after the parallel batch.

When the build touches UI files and an IBR-style runner is installed, after each implementer reports back AND before re-entering Sub-step B, run `interact_and_verify` against the affected route headlessly. Catches new visual/interaction regressions cheaply.

For each fix:
1. Diagnose root cause (not just symptoms)
2. Use the queue entry's `proposed_fix` plan as the prompt (or, for Validate failures, create a targeted fix plan)
3. Execute fix
4. Loop back to Review sub-step B (Validate). Sub-step A usually skipped unless the fix touched new files.

**Followup overflow**: when iteration cap is reached and queue entries remain, write them to `.build-loop/followup/<topic>.md` for a subsequent build invocation. The followup build skips its own Plan phase for these entries (plans are already complete).

**Convergence rules:**
- If a criterion fails 3 times with the same root cause: escalate to user
- If fixing one criterion breaks another: stop, reassess approach
- If score doesn't improve after 2 consecutive iterations: change strategy, don't repeat
- **Hard stop at 5 iterations.** Proceed to Review sub-step F with remaining ❓ Unfixed.

Log iteration state to `.build-loop/state.json`.

### Phase 6: Learn (optional)

Runs after Review sub-step F on every build unless disabled or `runs[]` has fewer than 3 entries.

- **Detect**: pattern detector scans `state.json.runs[]` for recurring `phase_failure` + `manual_intervention` signals.
- **Draft**: for each kept pattern, architect agent writes experimental SKILL.md with A/B Experiment section (sample target 8 non-confounded runs).
- **Signoff**: Opus reviews each draft; APPROVE / REVISE (1 retry) / DISCARD.
- **Sample sweep**: for existing experimental artifacts with sample complete, auto-promote to `active/` (only when `autoPromote: true` config is set AND effective non-confounded sample ≥ 8 AND non-regression). Regressions and inconclusive results write proposals, never auto-delete.

User controls: `rm -rf .build-loop/skills/experimental/<name>/`, `.build-loop/skills/.demoted` blocklist, `autoSelfImprove: false` disables the phase entirely.

## Project Data

Build loop stores state in `.build-loop/` within the project directory:

```
.build-loop/
├── goal.md              # Current build goal
├── intent.md            # North star, update intent, user value, non-goals
├── config.json          # Optional repo flags, including deploymentPolicy
├── state.json           # Iteration state, phase progress, structure summary
├── feedback.md          # Post-build lessons (one line per build)
├── release-pending.md   # User-created marker: "feature batch complete, advise version bump"
├── ibr-quickpass.json   # Summary from scripts/ibr_quickpass.py (UI work + IBR present)
├── ux-queue/            # UX-impacting findings with full fix plans (drained by Iterate)
│   └── <id>.md
├── followup/            # Overflow when iteration cap hit; input to subsequent build
│   └── <topic>.md
├── evals/               # Scorecard archives
│   └── YYYY-MM-DD-*.md
└── issues/              # Discovered issues
```

Project-level (not under `.build-loop/`):
```
.ibr-tests/_draft/       # IBR test drafts from Coverage Gap; user mv to accept, rm to reject
```

This directory is created on first use. Add `.build-loop/` to your project's `.gitignore`.

## Post-Build

After every build, if something surprising happened, append one line to `.build-loop/feedback.md`:

```
YYYY-MM-DD | what happened | what to do differently
```

These entries are loaded during Phase 1 (Assess) of future builds to prevent repeating mistakes.
