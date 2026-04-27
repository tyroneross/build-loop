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

- **Tools on demand.** Detect what's available, use what's needed. Don't assume any tool exists.
- **North star first.** Understand the app/repo purpose, primary users, core workflows, and update intent before planning. Every subtask should explain how it contributes to that purpose.
- **Beauty in the basics.** Core flows, real data, clear hierarchy, useful states, working controls, and accurate information matter more than extra surface area.
- **Modular by default, not by dogma.** Prefer high cohesion, loose coupling, stable interfaces, and scalable boundaries unless a simpler or integrated approach better serves the use case. Document `MODULARITY EXCEPTION: <reason>` when taking that path.
- **MECE work ownership.** Partition files, agents, and task groups so ownership is mutually exclusive and collectively exhaustive: no overlapping file owners, no unowned responsibilities, and one clear grouping dimension per level.
- **Guidelines for creation, guardrails for output.** Be flexible during building. Be strict about what reaches users.
- **No false data.** No mock data in production. No hardcoded metrics pretending to be real. No unverified claims.
- **Diagnose before fixing.** Root-cause analysis before code changes. Many errors sharing a pattern = one system problem.
- **Converge or escalate.** If iteration isn't improving scores, stop and surface the blocker. Don't burn cycles.

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
- If web/mobile UI: capture current visual state for before/after comparison

**Define goal + criteria:**
- State the goal in one concrete sentence — what will be true when this succeeds?
- Design 3-5 scoring criteria. Each criterion must have:
  - A clear pass condition
  - A grading method: code-based (preferred) or LLM-as-judge (for nuance)
- Write goal to `.build-loop/goal.md`

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

### Phase 3: Execute

- Dispatch parallel work for independent file groups
- Each worker gets minimal context + integration contract (what interfaces to implement) + an intent packet explaining how the subtask fits the north star + a MECE ownership packet defining owned files, non-owned files, interface contracts, and integration checkpoints
- For UI work: follow established design system or sensible defaults (44px touch targets, 4.5:1 contrast). Every visible element must have meaning, working behavior, and a clear user purpose.
- Surface pre-existing issues separately from new work. If an issue impacts users and is local to the current build, plan and fix it automatically; if too large/risky, log user impact and defer.
- Checkpoint after major integration points

### Phase 4: Review

Six ordered sub-steps; intermediate failures route to Iterate, final pass writes Report artifacts.

**Sub-step A — Critic (adversarial read-only)**: dispatch a read-only reviewer against the diff. Catch scope drift, missed edge cases, rubric violations before spending tokens on full validation. Strong-checkpoint findings route back to Execute (no iteration burn); guidance findings are logged.

**Sub-step B — Validate**: code-based graders first (test, lint, type, build), LLM-as-judge for nuanced criteria. Every pass/fail has evidence. Scorecard format:

| # | Criterion | Method | Result | Evidence |
|---|-----------|--------|--------|----------|
| 1 | Tests pass | code | PASS | exit 0, 47/47 |
| 2 | No lint errors | code | FAIL | 3 errors in auth.ts |

**Sub-step C — Optimize (opt-in)**: runs only when a mechanical metric exists and the user hasn't disabled it. 3-5 iterations polish. Uses autoresearch pattern: constrained scope + metric + atomic changes + commit-or-revert.

**Sub-step D — Fact-Check & Mock Scan**: three gates in parallel.

- *Fact Check*: trace every rendered metric (%, $, score, count) to source. Flag "always", "never", "100%", "guaranteed" — replace unless genuinely absolute.
- *Mock Data Scan*: production paths only. Detect lorem ipsum, faker, hardcoded fake values, `Math.random()` in display, placeholder text. Classify blocking (renders to user) vs warning.
- *Architectural Violations* (if available): `navgator rules --json`. Blocking: circular-dependency, layer-violation, database-isolation, frontend-direct-db. Warning: hotspot, high-fan-out, orphan.

Blocking issues (any gate) route to Iterate. Warnings land in Report.

**Sub-step E — Simplify**: trim the diff — inline single-use helpers, delete dead branches, remove validation for upstream-guaranteed invariants. Preserve public API, tests, observability, and modular boundaries that protect user value, scalability, accuracy, security, testability, or stable interfaces. If an integrated simplification is better, document `MODULARITY EXCEPTION`.

**Sub-step F — Report** (only on final Review pass):
- **Scorecard** with final pass/fail per criterion + evidence
- **Verified** (working with evidence), **Unknown** (untested), **Unfixed** (post-cap)
- **Discovered issues**: pre-existing problems from assessment
- **Fact check results**: warnings from sub-step D

Write scorecard to `.build-loop/evals/YYYY-MM-DD-<topic>-scorecard.md`. Append run entry to `.build-loop/state.json.runs[]` with `run_id`, phase statuses, files touched, diagnostic commands, manual interventions, active experimental artifacts.

Before any push/deploy, classify the exact command with `scripts/deployment_policy.py` when available. Follow the returned action: `auto` may run after Review passes; `confirm` requires explicit user confirmation in chat; `block` must not run. Defaults allow preview deploys and Xcode/App Store Connect/TestFlight upload/export flows, while production deploys, releases, publishes, protected-branch pushes, and unknown targets require confirmation.

### Phase 5: Iterate

For each failed criterion flagged by Review:
1. Diagnose root cause (not just symptoms)
2. Create targeted fix plan
3. Execute fix
4. Loop back to Review sub-step B (Validate). Sub-step A usually skipped unless the fix touched new files.

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
├── evals/               # Scorecard archives
│   └── YYYY-MM-DD-*.md
└── issues/              # Discovered issues
```

This directory is created on first use. Add `.build-loop/` to your project's `.gitignore`.

## Post-Build

After every build, if something surprising happened, append one line to `.build-loop/feedback.md`:

```
YYYY-MM-DD | what happened | what to do differently
```

These entries are loaded during Phase 1 (Assess) of future builds to prevent repeating mistakes.
