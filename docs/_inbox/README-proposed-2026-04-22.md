# build-loop

Orchestrated 5-phase development loop for Claude Code — plus an optional Learn phase — that brings structure, validation, and fact-checking to significant multi-file code changes. Opus plans and signs off, Sonnet executes and critiques, Haiku pattern-matches. Guardrails, not just guidelines: deploys are blocked until fact-check passes.

**Skip the loop for:** single-file edits, config changes, or fixes under ~20 lines.

## Phases

| # | Phase | Purpose | Output |
|---|-------|---------|--------|
| 1 | **Assess** | Understand state (project type, architecture, tools, prior state) and define goal + 3–5 scoring criteria with pass/fail conditions | State summary + `.build-loop/goal.md` |
| 2 | **Plan** | Task breakdown with dependency order, parallel-safe groups, checkpoints | Plan with dependency graph |
| 3 | **Execute** | Dispatch parallel subagents for independent file groups | Working implementation |
| 4 | **Review** | Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Report — six ordered sub-steps, single exit point | Scorecard + evidence; routes to Iterate on failure |
| 5 | **Iterate** | Fix Review failures, loop back to Review sub-step B (max 5x) | Updated scorecard |
| 6 | **Learn** *(optional)* | Detect recurring patterns across runs, auto-draft experimental skills/agents with A/B tracking, auto-promote on metric wins when enabled | Experimental artifacts + synthesis |

## Installation

### From GitHub

```
/plugin marketplace add tyroneross/build-loop
/plugin install build-loop@build-loop
```

### Local development

```bash
git clone https://github.com/tyroneross/build-loop.git
```

Add to `~/.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "build-loop": {
      "source": { "source": "directory", "path": "/path/to/build-loop" }
    }
  },
  "enabledPlugins": { "build-loop@build-loop": true }
}
```

## Usage

```
/build-loop [goal description]
```

Examples:

```
/build-loop add user notification system with email and push
/build-loop refactor auth middleware to use JWT
/build-loop migrate database from SQLite to PostgreSQL
```

Additional commands:

- `/build-loop:self-improve` — run Phase 6 Learn alone against recent runs
- `/optimize` — metric-driven optimization pass (Phase 4 sub-step C standalone)
- `/research` — research-only mode, no code changes
- `/promote-experiment` — promote an approved experimental skill/agent to active

## Components

### Agents

| Agent | Role | Model |
|-------|------|-------|
| **build-orchestrator** | Drives all phases, dispatches subagents | Opus 4.7 (overridable) |
| **sonnet-critic** | Adversarial read-only review of diffs | Sonnet 4.6 |
| **fact-checker** | Traces rendered metrics to data sources | inherit (session-driven) |
| **mock-scanner** | Scans production paths for placeholder data | Haiku 4.5 |
| **overfitting-reviewer** | Flags tests/fixes that only pass the grader | Sonnet 4.6 |
| **optimize-runner** | Runs atomic metric-driven improvements | Sonnet 4.6 |
| **recurring-pattern-detector** | Scans `runs[]` for repeated failures | Haiku 4.5 |
| **self-improvement-architect** | Drafts experimental skills from patterns | Sonnet 4.6 |

**Pin vs. inherit.** Pin where the tier is unambiguous (critic → Sonnet, mock-scanner → Haiku, orchestrator → Opus). Inherit where user intent should flow through (fact-checker). Override with `model:` at spawn time or edit frontmatter.

### Model Tiering

- **Opus 4.7** at boundaries — planning, final sign-off, novel architecture, ambiguity resolution, user-visible prose
- **Sonnet 4.6** inside — bounded code execution, adversarial critic, fact-checking, optimize runs
- **Haiku 4.5** for pattern-matching — mock scanning, recurring-pattern detection

Escalation triggers (mid-flow switch to Opus): two consecutive failures, ambiguous spec, cross-file architectural decision, critic `strong-checkpoint` finding, novel error, user-visible prose. See `skills/model-tiering/SKILL.md`.

The pattern amortizes Opus cost across many Sonnet subagents. Typical build: Opus plans once, 6–12 Sonnet implementer runs, one Sonnet critic per chunk, Opus final review — roughly 4× cheaper than single-pass Opus.

### Eval methodology

- Binary pass/fail only — no partial credit, no Likert scales
- Code-based graders first (tests, lint, type check, build) — fast and deterministic
- LLM-as-judge second — only for criteria code can't evaluate
- One evaluator per dimension — no multi-dimension "God Evaluator"

### Iteration rules

- Diagnose root cause before fixing
- Re-validate only failed criteria
- Three failures on same criterion with same cause → escalate to user
- Fixing one criterion breaks another → stop, reassess
- No improvement after two consecutive iterations → change strategy
- **Hard stop at five iterations**

### Guardrail hook

A `PostToolUse` hook on `Bash` blocks `git push`, `npm publish`, `vercel deploy`, and `gh release` when a build is active and the Review fact-check sub-step has not completed. Remove by deleting `.build-loop/state.json` if you need to bypass.

## Bundled skills

| Skill | Role |
|-------|------|
| `build-loop` | Main skill loaded by `/build-loop` |
| `model-tiering` | Opus/Sonnet/Haiku routing rules and escalation triggers |
| `self-improve` | Phase 6 Learn logic |
| `optimize` | Metric-driven optimization loop |
| `research` | Research-packet construction |
| `plugin-builder`, `mcp-builder`, `authentication` | Domain skills for common subtasks |
| `navgator-bridge`, `debugger-bridge`, `logging-tracer-bridge` | Cherry-pick adapters for companion tools with standalone fallbacks |
| `building-with-deepagents` | Subagent dispatch patterns |

## Optional companion skills (external)

These enhance the loop when available; phases degrade gracefully without them:

| Skill | Used in | Without it |
|-------|---------|------------|
| `writing-plans` | Phase 2 Plan | Plan directly with file paths + dependency order |
| `subagent-driven-development` | Phase 3 Execute | Dispatch parallel agents manually |
| `calm-precision` | Phase 3 Execute (UI) | Use standard UI defaults |
| `verification-before-completion` | Phase 4 Review sub-step B | Run test/build/lint manually |

## Project data

Build-loop stores runtime data in `.build-loop/` inside consumer projects (created on first use):

```
.build-loop/
├── goal.md                      # Current build goal
├── state.json                   # Iteration state + runs[] for Phase 6
├── feedback.md                  # Post-build lessons (one line per build)
├── evals/                       # Scorecard archives (YYYY-MM-DD-<topic>.md)
├── issues/                      # Discovered pre-existing issues
├── skills/experimental/         # Auto-drafted skills from Phase 6 Learn
├── agents/experimental/         # Auto-drafted agents from Phase 6 Learn
├── skills/active/               # Auto-promoted (opt-in, effective sample ≥ 8)
├── proposals/                   # Pending promotion/removal proposals
└── experiments/<name>.jsonl     # A/B tracking per experimental artifact
```

Add `.build-loop/` to your project's `.gitignore`.

## Cross-tool support

`AGENTS.md` is the open-standard version of this methodology for Codex, Copilot, Cursor, Jules, and other AI coding tools. A parallel `.codex-plugin/plugin.json` install surface ships alongside the Claude Code plugin — additive only, so Claude hooks, commands, and agent wiring are unchanged.

## License

[FSL-1.1-MIT](LICENSE) — Functional Source License with MIT future grant (becomes MIT after two years).
