# Build Loop

Orchestrated 8-phase development loop for significant multi-step code changes. Use this methodology when changes span multiple files, require planning, and benefit from structured validation.

**Skip this loop for:** single-file edits, config changes, quick fixes under ~20 lines.

## Phases

| # | Phase | Purpose | Output |
|---|-------|---------|--------|
| 1 | **Assess** | Understand current state — project type, architecture, available tools, prior state | Assessment summary |
| 2 | **Define** | State the goal in concrete terms, design 3-5 scoring criteria with pass/fail conditions | Goal file + scoring criteria |
| 3 | **Plan** | Break work into tasks with dependency order, identify parallel-safe groups | Plan with dependency graph |
| 4 | **Execute** | Build it — dispatch parallel work for independent file groups | Working implementation |
| 5 | **Validate** | Evaluate against scoring criteria from Phase 2 | Scorecard with pass/fail + evidence |
| 6 | **Iterate** | Fix failures, re-validate only failed criteria (5 iterations max) | Updated scorecard |
| 7 | **Fact Check** | Verify all rendered data traces to real sources, scan for mock/placeholder data | Verification report |
| 8 | **Report** | Present final scorecard with verified/unknown/unfixed categories | Final report |

## Core Principles

- **Tools on demand.** Detect what's available, use what's needed. Don't assume any tool exists.
- **Guidelines for creation, guardrails for output.** Be flexible during building. Be strict about what reaches users.
- **No false data.** No mock data in production. No hardcoded metrics pretending to be real. No unverified claims.
- **Diagnose before fixing.** Root-cause analysis before code changes. Many errors sharing a pattern = one system problem.
- **Converge or escalate.** If iteration isn't improving scores, stop and surface the blocker. Don't burn cycles.

## Phase Details

### Phase 1: Assess

- Detect project type and tooling (language, framework, test runner, linter, build system)
- Map relevant architecture (only what the goal touches)
- Check for prior state (`.build-loop/state.json` from interrupted builds)
- If goal involves external frameworks or APIs: research current docs before planning
- If web/mobile UI: capture current visual state for before/after comparison

### Phase 2: Define

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

### Phase 3: Plan

- Break work into tasks with exact file paths
- Identify dependency order — what must complete before what?
- Flag parallel-safe groups: files that don't import each other can be written simultaneously
- Define checkpoints where work should be verified before continuing
- Optimize: remove unnecessary steps, combine related changes, eliminate redundant work

### Phase 4: Execute

- Dispatch parallel work for independent file groups
- Each worker gets minimal context + integration contract (what interfaces to implement)
- For UI work: follow established design system or sensible defaults (44px touch targets, 4.5:1 contrast)
- Surface pre-existing issues separately from new work
- Checkpoint after major integration points

### Phase 5: Validate

Run every scoring criterion from Phase 2:

1. Code-based graders first (fast, deterministic):
   - Test suite: run and check exit code
   - Linter: run and check exit code
   - Type checker: run and check exit code
   - Build: run and check exit code

2. LLM-as-judge graders second (for nuanced criteria):
   - Present criterion, pass condition, and evidence
   - Judge reasons internally, outputs only PASS or FAIL

3. Collect evidence for every result — no pass/fail without proof.

**Scorecard format:**

| # | Criterion | Method | Result | Evidence |
|---|-----------|--------|--------|----------|
| 1 | Tests pass | code | PASS | exit 0, 47/47 |
| 2 | No lint errors | code | FAIL | 3 errors in auth.ts |

### Phase 6: Iterate

For each failed criterion:
1. Diagnose root cause (not just symptoms)
2. Create targeted fix plan
3. Execute fix
4. Re-validate ONLY failed criteria

**Convergence rules:**
- If a criterion fails 3 times with the same root cause: escalate to user
- If fixing one criterion breaks another: stop, reassess approach
- If score doesn't improve after 2 consecutive iterations: change strategy, don't repeat
- **Hard stop at 5 iterations.** Report current state and let the user decide.

Log iteration state to `.build-loop/state.json`.

### Phase 7: Fact Check & Mock Scan

Two verification gates, run in parallel:

**Gate A — Fact Check:**
- Trace every rendered metric (%, $, score, count) to its data source
- Flag unverifiable claims in code, comments, or output
- Catch extreme language: "always", "never", "100%", "guaranteed" — replace with qualified language unless genuinely absolute
- Verify scoring logic produces displayed values (no hardcoded display values without backing computation)

**Gate B — Mock Data Scan:**
- Scan production code paths (exclude test files, fixtures, dev-only code)
- Detect: lorem ipsum, faker usage, hardcoded fake names/emails/prices, placeholder text in rendered output, `Math.random()` generating user-facing values, stubs replacing real implementations
- Classify: blocking (renders to user) vs warning (internal only)

**Resolution:**
- Blocking issues route back to Phase 6
- Warnings included in Phase 8 report

### Phase 8: Report

Present final results:

- **Scorecard** with final pass/fail per criterion
- **Verified** (confirmed working with evidence)
- **Unknown** (untested or uncertain areas)
- **Unfixed** (remaining issues after iteration cap)
- **Discovered issues** (pre-existing problems found during assessment)
- **Fact check results** (any warnings from Phase 7)

Write scorecard to `.build-loop/evals/YYYY-MM-DD-<topic>-scorecard.md`.

## Project Data

Build loop stores state in `.build-loop/` within the project directory:

```
.build-loop/
├── goal.md              # Current build goal
├── state.json           # Iteration state, phase progress
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

These entries are loaded during Phase 1 of future builds to prevent repeating mistakes.
