# build-loop

Orchestrated 8-phase development loop for Claude Code. Brings structure, validation, and fact-checking to significant multi-step code changes.

## Phases

| # | Phase | Purpose |
|---|-------|---------|
| 1 | **Assess** | Understand current state — project type, architecture, tools, prior state |
| 2 | **Define** | Concrete goal + 3-5 scoring criteria with pass/fail conditions |
| 3 | **Plan** | Task breakdown with dependency order and parallel-safe groups |
| 4 | **Execute** | Build it — parallel subagents for independent work |
| 5 | **Validate** | Eval against scoring criteria (code-based + LLM-as-judge) |
| 6 | **Iterate** | Fix failures, re-validate (5 iterations max) |
| 7 | **Fact Check** | Verify rendered data traces to real sources, scan for mock data |
| 8 | **Report** | Final scorecard: verified / unknown / unfixed |

## Installation

### From GitHub (recommended)

```
/plugin marketplace add tyroneross/build-loop
/plugin install build-loop@build-loop
```

### Manual (local development)

```bash
git clone https://github.com/tyroneross/build-loop.git
```

Add to `~/.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "build-loop": {
      "source": {
        "source": "directory",
        "path": "/path/to/build-loop"
      }
    }
  },
  "enabledPlugins": {
    "build-loop@build-loop": true
  }
}
```

## Usage

```
/build [goal description]
```

Examples:

```
/build add user notification system with email and push
/build refactor auth middleware to use JWT
/build migrate database from SQLite to PostgreSQL
```

Skip the loop for single-file edits, config changes, or fixes under ~20 lines.

## Components

### Agents

| Agent | Role | Model |
|-------|------|-------|
| **build-orchestrator** | Drives all 8 phases, dispatches subagents | opus (overridable) |
| **sonnet-critic** | Adversarial read-only review between execution and final validation | sonnet |
| **fact-checker** | Traces rendered metrics to data sources | inherit (sonnet recommended) |
| **mock-scanner** | Scans for placeholder/fake data in production code | haiku |

**Pin vs inherit philosophy**: pin when the task has a clear right tier (critic needs Sonnet, mock-scanner needs Haiku, orchestrator benefits from Opus judgment). Use `inherit` when user intent should flow through (fact-checker — recommended Sonnet, but respects main-session choice). Override an agent's pin by passing `model:` at spawn time or editing frontmatter.

### Model Tiering

Build-loop assigns models per task, not per phase, guided by the `model-tiering` skill:

- **Opus** at boundaries: planning, final review, novel architecture, ambiguity resolution, user-visible prose
- **Sonnet** inside: bounded code execution, adversarial critic, first-pass debugging, fact-checking
- **Haiku** for pattern-matching only (mock scanning)

Escalation triggers (mid-flow switch to Opus): 2 consecutive failures, ambiguous spec, cross-file architectural decision, critic `strong-checkpoint` finding, novel error, user-visible prose. See `skills/model-tiering/SKILL.md` and `agents/build-orchestrator.md §Escalation Triggers`.

The pattern amortizes Opus cost across many Sonnet subagents. Typical build: Opus plans once, 6 to 12 Sonnet implementer runs, 1 Sonnet critic per chunk, Opus final review. Estimated 4x cheaper than single-pass Opus end-to-end.

### Eval Methodology

- **Binary pass/fail only** — no partial credit, no Likert scales
- **Code-based graders first** — test pass/fail, lint, type check, build (fast, deterministic)
- **LLM-as-judge second** — for nuanced criteria code can't evaluate
- **One evaluator per dimension** — no multi-dimension "God Evaluator"

### Iteration Rules

- Diagnose root cause before fixing
- Re-validate only failed criteria
- 3 failures on same criterion with same cause → escalate to user
- Fixing one criterion breaks another → stop, reassess
- No improvement after 2 consecutive iterations → change strategy
- **Hard stop at 5 iterations**

## External Skill Dependencies

These skills enhance the loop when available but are not required:

| Skill | Used In | Without It |
|-------|---------|------------|
| `writing-plans` | Phase 3 | Write plan directly with file paths and dependency order |
| `subagent-driven-development` | Phase 4 | Dispatch parallel agents manually |
| `calm-precision` | Phase 4 (UI) | Use standard UI best practices |
| `verification-before-completion` | Phase 8 | Run test/build/lint and confirm output manually |

## Cross-Tool Support

This repo includes `AGENTS.md` — the open-standard version of the build loop methodology. If you use Codex, Copilot, Cursor, Jules, or any other AI coding tool, that file provides the same 8-phase workflow without Claude-specific integration.

## Project Data

Build loop stores runtime data in `.build-loop/` within consumer projects:

```
.build-loop/
├── goal.md          # Current build goal
├── state.json       # Iteration state
├── feedback.md      # Post-build lessons
├── evals/           # Scorecard archives
└── issues/          # Discovered issues
```

Add `.build-loop/` to your project's `.gitignore`.

## License

[FSL-1.1-MIT](LICENSE) — Functional Source License with MIT future license (becomes MIT after 2 years)

## Codex

This package now ships an additive Codex plugin surface alongside the existing Claude Code package. The Claude package remains authoritative for Claude behavior; the Codex package adds a parallel `.codex-plugin/plugin.json` install surface without changing the Claude runtime.

Package root for Codex installs:
- the repository root (`.`)

Primary Codex surface:
- skills from `./skills` when present
- MCP config from `(none)` when present

Install the package from this package root using your current Codex plugin install flow. The Codex package is additive only: Claude-specific hooks, slash commands, and agent wiring remain unchanged for Claude Code.

