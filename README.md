# build-loop

A plugin for Claude Code that turns big code changes into a checked, repeatable workflow.

## What it is

Build-loop runs your code change through five phases: plan, execute, review, iterate, and (optional) learn. It splits the work into safe parallel chunks, runs a critic on the diff, runs your tests, traces every number on the page back to a real source, scans for fake data in production paths, and stops if what you shipped does not match what you said you would build. It picks the right model for each task: a strong model to plan and review, a faster model to write code, and a small model for pattern checks.

## Why use it

Big changes break things. You forget an edge case. You skip the test pass. The diff drifts from the plan. The implementer makes a quiet design call you never see. Build-loop catches all of that before the change ships:

- **One source of truth.** The plan lists every design decision up front. The implementer must say which decisions it made. A lint compares the claim to the actual code change. If the two do not match, the loop stops.
- **Speed where you can, depth where you must.** Mechanical work runs in parallel on a fast model. Work with five or more design decisions auto-routes to the strong model in one pass — the empirical point where the fast model loses cross-decision context.
- **Real evidence, not vibes.** Every pass or fail has a code-based grader. Every metric on a page traces back to its data source. Tests must run; output must render; placeholders are flagged.
- **Less rework.** A read-only critic runs before full validation, so cheap checks catch the obvious mistakes before you spend tokens on the long ones.
- **A way to actually improve a number.** When you want to make something faster or smaller, the optimize mode plans a small batch of test runs that vary two or more things at once, then tells you which one really moved the number — so you don't waste time turning one knob at a time and missing the interactions.

You get fewer regressions, a clean record of what changed and why, and a workflow you can trust on changes that touch many files at once.

## Get started

Install the plugin:

```
/plugin marketplace add tyroneross/build-loop
/plugin install build-loop@build-loop
```

Run a build:

```
/build-loop:run add user notification system with email and push
```

Skip the loop for small fixes (under about 20 lines, single file, no new endpoint). For everything else — features, refactors, migrations, schema changes, anything that crosses a file or system boundary — run it through the loop.

Debug a failing system:

```
/build-loop:debug tests pass locally but fail in CI
```

Detail on each phase, the model tier rules, the synthesis-decision lint, the architecture engine, and the debugger is below.

## Phases

| # | Phase | Purpose |
|---|-------|---------|
| 1 | **Assess** | Understand state (project type, architecture, tools, prior state) AND define goal + 3-5 scoring criteria with pass/fail conditions |
| 2 | **Plan** | Task breakdown with dependency order and parallel-safe groups |
| 3 | **Execute** | Build it — parallel subagents for independent work |
| 4 | **Review** | Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Report — six ordered sub-steps, single exit point; routes to Iterate on failure |
| 5 | **Iterate** | Fix Review failures, loop back to Review (max 5x) |
| 6 | **Learn** *(optional)* | Detect recurring patterns across runs, auto-draft experimental skills/agents with A/B tracking; auto-promote on metric wins when enabled |

## Installation

### From GitHub (recommended)

```
/plugin marketplace add tyroneross/build-loop
/plugin install build-loop@build-loop
```

build-loop bundles an MCP server (`build-loop-debugger`) that provides incident-memory tools (`search`, `store`, `outcome`, `read_logs`, `list`). It is wired automatically via `.mcp.json` and starts on first plugin load. The name is plugin-prefixed to coexist with the standalone `claude-code-debugger` plugin's `debugger` server (both can be installed; neither shadows the other).

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
/build-loop:run [goal description]
```

Examples:

```
/build-loop:run add user notification system with email and push
/build-loop:run refactor auth middleware to use JWT
/build-loop:run migrate database from SQLite to PostgreSQL
/build-loop:run --parallel add billing settings
```

Skip the loop for single-file edits, config changes, or fixes under ~20 lines.

### Debugging

```
/build-loop:debug <symptom>
```

Examples:

```
/build-loop:debug tests pass locally but fail in CI
/build-loop:debug login works once then breaks on refresh
/build-loop:debug API returns wrong data intermittently
```

Runs deep iterative root-cause investigation (causal-tree analysis, fix, verify, critique — up to 5 iterations). The build orchestrator also auto-invokes `Skill("build-loop:debug-loop")` on Review-B Validate failures and Iterate retries (attempts 2 and 3) — you don't have to call it manually during a build.

Quick incident-memory lookup: `/build-loop:debugger`. Multi-domain assessment: `/build-loop:assess`. Memory stats: `/build-loop:debugger-status`.

## Deployment Policy

Build-loop uses a repo-local policy before running push/deploy commands. If `.build-loop/config.json` is absent, the default is:

```json
{
  "deploymentPolicy": {
    "preview": "auto",
    "testflight": "auto",
    "production": "confirm",
    "unknown": "confirm"
  }
}
```

Meaning: preview deploys and TestFlight/App Store Connect upload/export flows can run automatically after review passes; production deploys, releases, publishes, protected-branch pushes, and unknown targets require explicit confirmation. Repos can override each target with `auto`, `confirm`, or `block`.

## Components

### Intent Capability Pack

Build-loop captures a north star before planning: app/repo purpose, primary users, core jobs, update intent, user value, and non-goals. It writes this to `.build-loop/intent.md` and passes a compact intent packet to every subagent.

Decision rule: prefer the simplest durable approach that creates user value. UI work should be intentional and polished in the basics: every button, option, nav item, chart, and message must have meaning and working behavior. Preview or prototype-looking surfaces must not use fake data in production/user decision paths.

### Modular Systems Pack

Build-loop defaults to modular, scalable, MECE structure: high cohesion, loose coupling, stable interfaces, and one clear owner per changed file. Plans and reports use pyramid structure: governing thought first, MECE support lines second, evidence/details third.

This is a decision rule, not architecture for its own sake. When a simpler or more integrated approach is better for the use case, the plan records `MODULARITY EXCEPTION: <reason>` and explains the user, performance, clarity, or delivery benefit.

### Agents

| Agent | Role | Model |
|-------|------|-------|
| **build-orchestrator** | Drives the 5-phase loop plus optional Learn, dispatches subagents | opus (overridable) |
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

## Native Architecture & Debugging Skills

Architecture and debugging are used on nearly every build, so build-loop ships its own copies of the canonical NavGator and claude-code-debugger skills under:

- `skills/architecture/{scan,impact,trace,rules,dead,review}/` — sourced from NavGator
- `skills/debugging/{memory,store,assess,debug-loop}/` — sourced from claude-code-debugger

Each native SKILL.md frontmatter carries `source:` (relative path from `~/dev/git-folder/`) and `source_hash:` (SHA-256 at copy time). The orchestrator calls them directly in Phase 1 Assess, Review-B Validate, Review-D Fact-Check, Review-F Report, and Phase 5 Iterate cross-layer pre-step.

Drift detection is a deliberate, opt-in pass:

```bash
python3 scripts/sync_skills.py
# or
Skill("build-loop:sync-skills")
```

The script recomputes each `source_hash` against the canonical upstream file and reports drift. Read-only — never auto-updates a SKILL.md. The legacy `skills/navgator-bridge/` and `skills/debugger-bridge/` deprecation stubs were removed in v0.10.0 — the orchestrator and downstream skills call the native skills directly.

## Architecture awareness

Build-loop owns native architecture awareness end-to-end. NavGator is now an optional escalation adapter, not a hard dependency.

**Native engine** (`src/build_loop/architecture/`)
Python-native scanner: `.gitignore`-aware walk, Python via `ast`, TS/JS via tree-sitter. Pure-function `compute_impact / trace_dataflow / check_rules / find_dead`. Output schema-parity with NavGator (component/connection JSON shapes verbatim) under `.build-loop/architecture/`.

```bash
uv run python -m build_loop.architecture {scan|impact|trace|rules|dead|connections|acp|acp-slice|llm-map|schema|diagram} \
    [--mode auto|native|navgator] [--json] [--incremental|--full]
```

**`architecture-scout` subagent** (`agents/architecture-scout.md`)
Sonnet, read-only, dispatched by the orchestrator at six phase points with one of five task types: `baseline`, `chunk-impact`, `review-rules`, `iterate-subgraph`, `learn-sync`. Decides native-vs-NavGator escalation per task. Returns ≤500-word JSON envelope; owns architecture-related side effects (violation capture, lessons sync).

**Architecture Context Pack (ACP)** (`scripts/build_acp.py`, `scripts/slice_acp.py`)
Compact JSON summary of current architecture state: top hotspots, recent violations, lessons-in-scope. Sliceable per file set with reverse-deps depth=1 + 4 KB cap. Embedded in subagent briefs at Phase 2 / 3 / 4 / 5.

**Aggressive freshness** (`hooks/session-start-architecture.sh`, `hooks/pre-edit-architecture.sh`)
SessionStart fires an incremental scan when manifest > 24 h old. PreToolUse Edit/Write triggers an async incremental scan when the touched file is parseable (extension allowlist: `.py .ts .tsx .js .jsx .mjs .cjs`); single-flight via `fcntl.flock`. Doc-only edits never fire scans.

**Capability registry + ≤8 shortlist** (`scripts/build_capability_registry.py`, `scripts/capability_shortlist.py`, `skills/capabilities/SKILL.md`)
116 capabilities indexed across 6 kinds (agent / skill / command / hook / mcp_tool / script) and 10 categories. Phase 1 invocation is **mandatory** — populates `state.json.activeCapabilities[<phase>]` with ≤8 relevant entries via plugin-surface collapse + trigger-aware demotion, keeping the orchestrator below the empirical tool-selection ceiling. Phase 2 / 3 dispatchers read the cache instead of re-scoring.

**Memory facade** (`scripts/memory_facade.py`)
Unified `recall(query, kind, project, limit, skip_postgres)` over four backends — `state.json.runs[]` · episodic `.episodic/decisions/` (legacy) and `~/dev/git-folder/build-loop-memory/decisions/<project>/` (canonical) · Postgres `semantic_facts` · debugger MCP `search`. Graceful degradation throughout; CLI mirrors the API including `--skip-postgres`.

**Backend health probe** (`scripts/backend_health.py`)
Phase 1 sub-step probes each memory backend with per-backend 5 s timeout. Output: `runs: OK N | decisions: OK <legacy> + <canonical> | semantic: ok|down | debugger: ok|down`. Envelope cached at `state.json.architecture.backendHealth`. Phase 5 Iterate consumes it to short-circuit Postgres lookups when down.

**Plan-verify rules** (`scripts/plan_verify.py`)
Now includes `schema-migration-full-chain` — flags any commit touching writer/storage/schema files without matching test fixture or reader-path co-change. Catches the recurring drift pattern where writer keys diverge from reader expectations.

**Decision capture loop**
Every architecture violation surfaced by `rules` becomes a deduplicated decision in the canonical episodic store via `scripts/capture_arch_violation.py`. Recurring violations (≥3× across runs) get promoted to project-local lessons by `scripts/promote_violation_to_lesson.py` and one-way-synced into Postgres `semantic_facts` for cross-project recall by `scripts/sync_navgator_lessons.py`.

## External Skill Dependencies

These skills enhance the loop when available but are not required:

| Skill | Used In | Without It |
|-------|---------|------------|
| `writing-plans` | Phase 2 (Plan) | Write plan directly with file paths and dependency order |
| `subagent-driven-development` | Phase 3 (Execute) | Dispatch parallel agents manually |
| `calm-precision` | Phase 3 (Execute, UI) | Use standard UI best practices |
| `verification-before-completion` | Phase 4 (Review sub-step B) | Run test/build/lint and confirm output manually |

## Cross-Tool Support

This repo includes `AGENTS.md` — the open-standard version of the build loop methodology. If you use Codex, Copilot, Cursor, Jules, or any other AI coding tool, that file provides the same 5-phase (+1 optional Learn) workflow without Claude-specific integration.

Codex-specific subagent behavior lives in `skills/build-loop/references/codex-subagents.md` and `skills/build-loop/templates/codex-worker-prompt.md`. These files are additive: Claude Code continues to use the existing `agents/*.md` runtime, while Codex maps Build Loop ownership packets to explorer/worker-style delegation only when the user explicitly authorizes subagents or parallel work.

## Project Data

Build loop stores runtime data in `.build-loop/` within consumer projects:

```
.build-loop/
├── goal.md          # Current build goal
├── intent.md        # North star, update intent, user value, non-goals
├── config.json      # Optional repo flags, including deploymentPolicy
├── state.json       # Iteration state, including compact intent/structure summaries
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

Codex adapter files:
- `skills/build-loop/references/codex-subagents.md`
- `skills/build-loop/templates/codex-worker-prompt.md`

Install the package from this package root using your current Codex plugin install flow. The Codex package is additive only: Claude-specific hooks, slash commands, and agent wiring remain unchanged for Claude Code.

To check whether an installed Codex cache is using the current source instructions:

```bash
python3 scripts/check_cache_sync.py --host codex --source .
```
