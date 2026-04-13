---
name: build-loop
description: Use when making significant multi-step code changes requiring planning, parallel execution, and validation. Not for single-file edits or quick fixes.
---

# Build Loop — Orchestrated Development

An 8-phase development loop: assess current state, define goals with scoring criteria, plan and optimize execution, build with parallel agents, validate against internal evals, iterate on failures, fact-check output, and report results.

## Scope Check

Before starting the loop, assess whether the task warrants it. If the task is a single file edit, a config change, or a fix under ~20 lines — skip the loop and just do it. The loop is for multi-step work where planning and validation add value.

## External Skill Dependencies

Build-loop does NOT reinvent patterns that specialized skills already encode. When the task matches a specialty, load the skill and delegate — don't write plugin manifests, hook JSON, or SKILL.md files from scratch. If a skill is unavailable, degrade gracefully per the fallback column.

### Core loop skills

| Skill | Used In | Fallback |
|-------|---------|----------|
| `writing-plans` | Phase 3 (Plan) | Write a structured plan directly: goal, tasks with exact file paths, dependency order, test commands |
| `subagent-driven-development` | Phase 4 (Execute) | Dispatch parallel agents manually using the Agent tool for independent file groups |
| `calm-precision` | Phase 4 (Execute, UI work) | Follow standard UI best practices: 44px touch targets, 4.5:1 contrast, 8pt grid, content >= 70% of chrome |
| `verification-before-completion` | Phase 8 (Report) | Run all test/build/lint commands and confirm output before claiming completion |
| `simplify` (slash: `/simplify`) | Phase 8 (after Report) | Self-review the diff: remove scaffolding, inline single-use helpers, delete dead branches |

### Plugin / hook / skill / agent work — **mandatory**

If Phase 1 ASSESS detects that the task touches any of the following, Phase 3 PLAN must map each task to the authoritative skill below and Phase 4 EXECUTE must load that skill (or include its guidance verbatim in subagent prompts — subagents don't inherit parent skills). **Do not infer plugin formats from memory or by reading another plugin's config.**

| Task surface | Skill (authoritative) | Fallback |
|---|---|---|
| `.claude-plugin/plugin.json` (manifest, paths, component registration) | `plugin-dev:plugin-structure` | Read `RossLabs-AI-Toolkit/LESSONS-LEARNED.md` for the `plugin.json` paths-must-start-with-`./` lesson |
| `hooks/hooks.json` or any hook script | `plugin-dev:hook-development` + run `plugin-dev/scripts/hook-linter.sh` before commit | Command hooks default; silent-exit pattern; NO prompt hooks on PostToolUse/Stop/SessionStart unless always-visible output is the goal |
| Slash commands (`commands/*.md`, frontmatter, `allowed-tools`, `$ARGUMENTS`) | `plugin-dev:command-development` | — |
| Subagents (`agents/*.md`, frontmatter, `description:`, tools list) | `plugin-dev:agent-development` + `RossLabs-AI-Toolkit/agents/` for prior examples | — |
| MCP servers (`.mcp.json`, server config, wrapper-vs-unwrapped) | `plugin-dev:mcp-integration` | Dedicated `.mcp.json` files should NOT wrap with `mcpServers` key (Method 1) |
| `~/.claude/settings.json` enabledPlugins / extraKnownMarketplaces | `plugin-dev:plugin-settings` | — |
| **Creating a new skill** (SKILL.md frontmatter, progressive disclosure, auto-activation) | `plugin-dev:skill-development` + `skill-builder` (personal skill) | Follow official skill format; keep SKILL.md ≤200 lines; progressive disclosure via references/ |
| Creating a new plugin end-to-end | `plugin-builder` (personal skill) → delegates into the plugin-dev skills above | — |
| Architecture scan / impact / trace before editing | `gator:*` commands (if installed) + `RossLabs-AI-Toolkit/skills/architecture-scan` | Read component → edit → re-read downstream |
| Debugging / root-cause investigation | `claude-code-debugger:*` + `RossLabs-AI-Toolkit/skills/debugging-memory` | Standard: reproduce → isolate → hypothesis → test |
| Design validation, UI audit | `ibr:*` commands + `RossLabs-AI-Toolkit/skills/design-validation` | Manual screenshot + review checklist from `calm-precision` |
| Recovering from compaction / context loss | `bookmark:*` + `RossLabs-AI-Toolkit/skills/context-continuity` | Re-read last plan file in `.build-loop/` |

### External knowledge — check before coding

| Source | When | How |
|---|---|---|
| `/cookbook` (Claude Cookbook — 66 recipes, weekly-diff tracked) | When task involves Claude API patterns: tool calling, PTC, code execution, Agent SDK, RAG, extended thinking, structured output, batch, prompt caching, context compaction | Invoke `/cookbook` or `/cookbook search <term>`; read `~/.claude/projects/-Users-tyroneross/memory/reference_claude_cookbook.md` for full index |
| `RossLabs-AI-Toolkit/LESSONS-LEARNED.md` | Before any plugin work | Read top-to-bottom during Phase 1 ASSESS if the task touches plugin components |
| `context7` MCP | When task uses a library/framework | Call `query-docs` / `resolve-library-id` for current syntax — do NOT code external APIs from training data |
| `research` skill | When factual claims, pricing, version numbers needed | Use tiered research — T1 official docs → T4 forum posts; 2-source minimum for disputed facts |

## Efficiency

- No extraneous code. Every line serves the goal
- Terminal output: current phase, key decisions (one line each), status changes, failures. No restated instructions, no verbose reasoning, no "I will now proceed to..."
- Subagent context: minimum needed per job. Shared reads done once, passed as condensed summaries
- Tools: load on demand as each phase needs them. Do not pre-load tools or skills before they're relevant

## Tool Selection

Use the best available tool for each need. If a preferred tool is unavailable, improvise — never block on a missing dependency. The skill is self-sufficient; external tools make it faster but their absence does not stop the loop.

## Phase 1: ASSESS — Understand Current State

**Goal**: Know what exists before changing anything. Scope assessment to files and directories relevant to the stated goal. On large codebases, limit to 2-3 focused exploration passes.

1. **Detect project type**: web app, API, library, mobile, CLI, monorepo, **Claude Code plugin**, etc. A plugin is detected by the presence of `.claude-plugin/plugin.json`, `hooks/hooks.json`, `skills/*/SKILL.md`, `commands/*.md`, `agents/*.md`, or `.mcp.json` in the working directory. If detected, mark the build as "plugin work" in state.json and plan to load the `plugin-dev:*` skills in the table above before any manifest/hook/skill/agent/MCP/command edits.
2. **Detect available tools**: Check for test runners (`package.json` scripts, `pytest.ini`, etc.), linters, deploy targets
3. **Map architecture** using best available approach:
   - NavGator if available → Explore agents → file reading
4. **Capture UI state** (if web/mobile):
   - IBR scan if available → screenshots → manual review
5. **Check prior state**: Read `.build-loop/issues/` and `.build-loop/feedback.md` if they exist. Surface relevant items
6. **Research gate**: If project uses external frameworks/APIs/deploy targets, check current official docs (Context7 → research skill → WebSearch) before building assumptions
7. **Recovery check**: If `.build-loop/state.json` exists, check for interrupted prior build. Offer to resume from last completed phase instead of restarting

**Output**: Structured state summary. Brief.

## Phase 2: DEFINE — Goal, Scoring, Evaluation Criteria

**Goal**: Define the target and how to measure success — before writing any code.

1. **State the goal** in concrete, measurable terms
2. **Suggest 3-5 scoring criteria** from: functionality, code quality, UX, performance, security, accessibility, test coverage — select what's relevant to the project and goal. Show for confirmation
3. **Design eval graders per criterion** using the grading hierarchy:

**Prefer code-based graders** (fast, deterministic, cheap):
- Test suite pass/fail, lint/type check, build succeeds, schema validation, accessibility audit

**Use LLM-as-judge graders** when code can't check the criterion:
- Binary pass/fail only — no Likert scales
- One evaluator per dimension — no multi-dimension God Evaluator
- Judge reasons in thinking tags, outputs only pass/fail
- Use Claude (the running instance) as judge

Each criterion gets: `description | grading method | pass condition | evidence required`

Load `eval-guide.md` in this skill directory for judge prompt template and scorecard format if needed.

4. **Write goal file**: Save to `.build-loop/goal.md` in the project directory

## Phase 3: PLAN — Steps & Optimization

**Goal**: Break work into executable steps, then optimize the plan before execution.

1. **Invoke `writing-plans` skill** for detailed task breakdown
2. **Identify parallel-safe tasks** vs sequential dependencies — build a dependency graph
3. **Define subagent integration points**: Where do agents need to coordinate? Where must outputs be tested together?
4. **Research check**: For any external framework, API, or deployment target — verify current docs before coding

**Optimization checklist** (review the plan for these before proceeding):
- Can more tasks run in parallel? Unnecessary sequential bottlenecks?
- Can subagent context be smaller? Shared reads that should be done once?
- Missing dependencies, interface mismatches, env assumptions?
- Changes that could conflict with each other (oscillation risk)?
- Define coordination checkpoints where subagents must sync

**Output**: Plan file with dependency graph, integration points, and optimization notes.

## Phase 4: EXECUTE — Build With Agents

**Goal**: Implement the plan using parallel subagents where possible.

1. **Use `subagent-driven-development`** — dispatch subagents per task
2. **Parallel agents** where dependency graph allows
3. **Each agent gets**: minimal context + clear integration contract + relevant doc context for external APIs
4. **UI work**: Load `calm-precision` skill and follow it
5. **Surface pre-existing issues**: Don't silently ignore problems discovered during implementation. Log to `.build-loop/issues/` with context
6. **Coordination checkpoints**: At defined sync points, verify agent outputs align before continuing

## Phase 5: VALIDATE — Eval Against Scoring Criteria

**Goal**: Test every criterion from Phase 2 with evidence.

**Code-based graders first** (fast, deterministic):
```
test suite       → pass/fail
lint / type check → pass/fail
build            → pass/fail
accessibility    → threshold pass/fail (if web)
schema validation → pass/fail
custom assertions → pass/fail
```

**LLM-as-judge graders second** (for nuanced criteria):
- Each criterion → its own focused judge prompt
- Binary pass/fail output only
- No multi-dimension scoring in a single prompt

**Evidence collection**:
- Every pass/fail must have evidence: command output, screenshot, or judge reasoning
- Use `verification-before-completion` for evidence-based claims
- No criterion marked "pass" without proof

**Output**: Scorecard with pass/fail per criterion + evidence

## Phase 6: ITERATE — Fix & Retry (up to 5x)

**Goal**: Fix failures systematically, not blindly.

If any criterion fails:
1. **Diagnose root cause** — don't just retry
2. **Create targeted fix plan** for failed criteria only
3. **Execute fix** (subagents if parallel-safe)
4. **Re-validate ONLY failed criteria** — re-run their specific graders
5. **Track**: iteration count, what failed, what was attempted, what changed

**Convergence detection**:
- Same criterion fails 2x with same root cause → escalate to user
- Fix A breaks criterion B (oscillation) → flag and ask user
- 3+ criteria fail simultaneously after a fix → systemic issue, stop and reassess

**Hard stop at 5 iterations**. Report remaining failures in Phase 8.

Log each iteration to `.build-loop/state.json`.

## Phase 7: FACT CHECK & MOCK SCAN

**Goal**: Nothing false, fabricated, or placeholder reaches the user.

Two gates. Run in parallel for speed. Load `phases/fact-check.md` in this skill directory for detailed guidance.

**Gate A — Fact Checker**: Trace every rendered %, $, score, count, or assessment to its data source. Flag "always", "never", "100%", "guaranteed" — replace with accurate language unless genuinely absolute. Verify scoring logic produces displayed values. Every rendered metric needs a traceable path: source → transformation → display.

**Gate B — Mock Data Scanner**: Lightweight scan of production code paths for residual mock/placeholder data — hardcoded fake data, placeholder text, faker/random in display paths, stubs replacing real implementations. Exclude test files and dev-only code.

Blocking issues → route back to Phase 6 (Iterate). Warnings → include in report.

## Phase 8: REPORT — Present Results

**Goal**: Clear, honest summary with certainty markers.

- **Scorecard**: Final pass/fail per criterion with evidence
- **✅ Known**: Verified working features (with proof)
- **⚠️ Unknown**: Untested or uncertain areas
- **❓ Unfixed**: Issues remaining after iteration limit
- **Discovered issues**: Pre-existing problems from `.build-loop/issues/` — user decides: fix now, defer, or dismiss
- **Fact check results**: Any unverifiable claims or mock data warnings

Write scorecard to `.build-loop/evals/YYYY-MM-DD-<topic>-scorecard.md`.

## Phase 8.5: SIMPLIFY — Trim The Diff

**Goal**: remove incidental complexity added during iteration without changing behavior.

Run `/simplify` (or load the `simplify` skill directly) against the changed files. Focus:
- Inline single-use helpers that were extracted "just in case"
- Delete dead branches, commented-out code, and unused imports
- Collapse try/except that catches a thing that can't happen
- Remove validation for invariants that the type system or upstream already guarantees
- Reduce abstractions that have exactly one call site

Preserve: public API surface, test coverage, observability (logging/tracing), documented behavior. If a simplification would break evidence collection or monitoring, keep it.

For **plugin work specifically**: also re-run `plugin-dev/scripts/hook-linter.sh` against any touched `hooks.json`, and `grep` the manifest for `../` or bare paths (per `RossLabs-AI-Toolkit/LESSONS-LEARNED.md` 2026-04-05). Silent manifest failures are worse than loud ones.

## Feedback — After Every Build

Append one line to `.build-loop/feedback.md` only if something surprising happened: a plan deviation, a tool that produced wrong results, a skill gap, an eval blind spot. Format: `YYYY-MM-DD | what happened | what to do differently`. No entry needed if the build went as expected.

On future `/build` runs, check this file and adjust proactively.

## Process Flow

```
ASSESS → DEFINE → PLAN → EXECUTE → VALIDATE
                                       ↓
                                  All pass? ──yes──→ FACT CHECK ──pass──→ REPORT → SIMPLIFY → FEEDBACK
                                       ↓                  ↓
                                      no            blocking issues
                                       ↓                  ↓
                                  ITERATE ←──────────────┘
                                 (up to 5x)
```
