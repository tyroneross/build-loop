<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- GENERATED FILE — DO NOT EDIT BY HAND. -->
<!-- Source of truth: skills/**/SKILL.md frontmatter. -->
<!-- Regenerate: python3 scripts/skill_index.py --workdir . --apply -->
<!-- Verify in sync: python3 scripts/skill_index.py --workdir . --check -->

# Skill Index — build-loop

Routing table for any coding agent — Claude Code, Codex, Cursor, or
anything else that reads plain markdown. Use it to pick the skill that
owns a request, then read that skill's `SKILL.md` for the procedure.

This file is generated from the skills' own frontmatter. Editing it by
hand is pointless: the next `--apply` overwrites the change, and
`--check` fails the build until it matches. To change a row, edit the
skill's `SKILL.md`.

Column meanings:

- **Skill** — the canonical skill id, linked to its source file.
- **When to use** — the skill's own `description`, trimmed to 160 characters. Read the linked file for the full trigger list.
- **Invocation** — how an agent reaches it. `internal` skills are not loaded directly by a user; the plugin entrypoint routes to them.
- **Exposure** — `hidden` is `user-invocable: false`. `public` is `user-invocable: true` plus a non-empty `public-justification:` in the same frontmatter. `public-undeclared` is exposed without a stated reason: the harness resolves visibility as `userInvocable ?? true`, so a skill with no `user-invocable` field is public by default, not hidden.

**50 skills** · 0 public · 0 public-undeclared · 50 hidden

| Skill | When to use | Invocation | Exposure |
| --- | --- | --- | --- |
| [`build-loop:agent-rally-point`](../skills/agent-rally-point/SKILL.md) | Use when coordinating build-loop with peer coding agents, checking Rally Point presence/inbox state, posting handoffs or feedback, validating the embedded… | internal — routed to by `build-loop` | hidden |
| [`build-loop:agent-rally-watcher`](../skills/agent-rally-watcher/SKILL.md) | Use when listening for Rally Point changes, wiring coordination watchers, debugging watch-loop behavior, or changing the future agent-rally-watcher spin-out… | internal — routed to by `build-loop` | hidden |
| [`build-loop:api-registry-bridge`](../skills/api-registry-bridge/SKILL.md) | Use when Phase 1 Assess or Phase 5 Iterate detects a new API dependency, API config fails, or the user asks to "register this API" or "check the API… | internal — routed to by `build-loop` | hidden |
| [`build-loop:architecture-dead`](../skills/architecture/dead/SKILL.md) | Use when the user asks to "find dead code", "scan for orphaned components", or "clean up unused files", or during Phase 4 Review before a release. Scans for… | internal — routed to by `build-loop` | hidden |
| [`build-loop:architecture-impact`](../skills/architecture/impact/SKILL.md) | Use when Phase 1 Assess evaluates top-risk components, Phase 5 Iterate precedes a cross-layer fix, or the user asks "what does changing X break". Blast-radius… | internal — routed to by `build-loop` | hidden |
| [`build-loop:architecture-review`](../skills/architecture/review/SKILL.md) | Use when Phase 4 Review covers a build that crosses 2+ layers, or the user asks for an "architectural review" or "full integrity check". Covers system flow… | internal — routed to by `build-loop` | hidden |
| [`build-loop:architecture-rules`](../skills/architecture/rules/SKILL.md) | Use when Phase 4 Review checks architectural integrity, the user asks to "check for violations" or "find circular deps", or before a release. Detects orphans… | internal — routed to by `build-loop` | hidden |
| [`build-loop:architecture-scan`](../skills/architecture/scan/SKILL.md) | Use when Phase 1 Assess detects stale architecture state, the user asks for an "architecture scan", or before blast-radius analysis. Refreshes Build Loop's… | internal — routed to by `build-loop` | hidden |
| [`build-loop:architecture-trace`](../skills/architecture/trace/SKILL.md) | Use when the user asks to "trace data flow", "follow this request end-to-end", or "show how X reaches Y". Follows a component's connections from input to… | internal — routed to by `build-loop` | hidden |
| [`build-loop:attribution-standard`](../skills/attribution-standard/SKILL.md) | Apply the canonical four-layer Apache-2.0 attribution model (NOTICE, per-file SPDX, REUSE.toml, canary markers) to a repo. Triggers on 'stamp attribution'… | internal — routed to by `build-loop` | hidden |
| [`build-loop:authentication`](../skills/authentication/SKILL.md) | Use when wiring auth to a new project, debugging login errors (redirect_uri_mismatch, invalid_grant, session callback, refresh_token), or adding social/magic… | internal — routed to by `build-loop` | hidden |
| [`build-loop:auto-decision-capture`](../skills/auto-decision-capture/SKILL.md) | Project-scoped skill for proactive in-session decision capture. Provides Claude the signal taxonomy, confidence ladder, overwrite rules, and the three… | internal — routed to by `build-loop` | hidden |
| [`build-loop:auto-finding-capture`](../skills/auto-finding-capture/SKILL.md) | Project-scoped skill documenting build-loop's DEFAULT-ON auto-capture of clearly-identified findings/issues into the backlog, regardless of which terminal or… | internal — routed to by `build-loop` | hidden |
| [`build-loop:build-loop`](../skills/build-loop/SKILL.md) | Orchestrated build loop for multi-step code work. TRIGGER on verb language ('build', 'implement', 'create', 'add', 'ship', 'wire up', 'integrate', 'refactor'… | internal — routed to by `build-loop` | hidden |
| [`build-loop:building-with-deepagents`](../skills/building-with-deepagents/SKILL.md) | Use when building or refactoring an agent that imports OSS `deepagents` (`from deepagents import create_deep_agent`). Covers SubAgent API, middleware, tool… | internal — routed to by `build-loop` | hidden |
| [`build-loop:capabilities`](../skills/capabilities/SKILL.md) | Invoked by Phase 1 Assess to populate `state.json.activeCapabilities[<phase>]` with ≤8 relevant entries via plugin-surface collapse + trigger-aware demotion… | internal — routed to by `build-loop` | hidden |
| [`build-loop:color-engine`](../skills/color-engine/SKILL.md) | Generate accessible color systems and solve contrast, instead of guessing hex values. Use when picking or fixing colors for any UI, chart, diagram, doc, or… | internal — routed to by `build-loop` | hidden |
| [`build-loop:cost-rca`](../skills/cost-rca/SKILL.md) | Use for structured cost-impact root-cause analysis — quantify what a context/caching/model change did to token spend and dollars. Reads MEASURED tokens from… | internal — routed to by `build-loop` | hidden |
| [`build-loop:data-plane-worktrees`](../skills/data-plane-worktrees/SKILL.md) | Use when a Build Loop run or Git worktree touches mutable non-Git state: SQLite or file-backed databases, PostgreSQL databases/schemas and migrations… | internal — routed to by `build-loop` | hidden |
| [`build-loop:debug-loop`](../skills/debug-loop/SKILL.md) | Use when a fix didn't hold, `/build-loop:debug` is invoked, the user asks for root cause analysis, memory lookup returns LIKELY_MATCH/WEAK_SIGNAL/NO_MATCH, or… | internal — routed to by `build-loop` | hidden |
| [`build-loop:debugging-memory`](../skills/debugging-memory/SKILL.md) | Use when the user asks to "debug this", "fix this bug", "investigate error", "diagnose", "root cause", or reports a crash/exception/failure. Memory-first… | internal — routed to by `build-loop` | hidden |
| [`build-loop:defenseclaw-bridge`](../skills/defenseclaw-bridge/SKILL.md) | Use when the user is working on the defenseclaw project and build-loop's Phase 1 detects defenseclaw-specific files (CLAUDE.md indicates the bridge target)… | internal — routed to by `build-loop` | hidden |
| [`build-loop:drain-proposals`](../skills/drain-proposals/SKILL.md) | Walk the cross-repo proposal backlog interactively: scan every registered repo's .build-loop/proposals/ (incl. enforce-from-retro/ and self-review) plus… | internal — routed to by `build-loop` | hidden |
| [`build-loop:focused-loop-builder`](../skills/focused-loop-builder/SKILL.md) | Use when the user asks to "create a custom build loop", "build a loop spec", "make a focused loop", "generate a workflow loop", "adapt a framework into a… | internal — routed to by `build-loop` | hidden |
| [`build-loop:handoff`](../skills/handoff/SKILL.md) | Compose a complete, durable build-loop handoff document from the current run state, and optionally launch a fresh session with it injected. Use when crossing… | internal — routed to by `build-loop` | hidden |
| [`build-loop:ibr-bridge`](../skills/ibr-bridge/SKILL.md) | Routing bridge to the IBR plugin for UI visual verification. Build-loop prefers IBR `scan` / `scan_macos` when the IBR plugin is installed; otherwise falls… | internal — routed to by `build-loop` | hidden |
| [`build-loop:knowledge`](../skills/knowledge/SKILL.md) | Canonical build-loop-memory framework. Use when the user asks to "record a decision", "log an ADR", "write an MADR", "capture this choice", "regenerate the… | internal — routed to by `build-loop` | hidden |
| [`build-loop:logging-tracer`](../skills/logging-tracer/SKILL.md) | Use when the user asks to "add logging", "add tracing", "improve observability", "OpenTelemetry", "structured logging", or reports silent failures or no… | internal — routed to by `build-loop` | hidden |
| [`build-loop:mcp-builder`](../skills/mcp-builder/SKILL.md) | Use when building, packaging, or debugging an MCP server, adding MCP tools to a plugin, or working on .mcp.json, transport, or bundling. Pair with… | internal — routed to by `build-loop` | hidden |
| [`build-loop:model-bakeoff`](../skills/model-bakeoff/SKILL.md) | Use to run a controlled multi-model bake-off — have N models (e.g. Opus 4.8, Sonnet 5.0, GPT-5.5) each independently diagnose→plan→execute the SAME bounded… | internal — routed to by `build-loop` | hidden |
| [`build-loop:model-tiering`](../skills/model-tiering/SKILL.md) | Use when choosing a model tier or segment for a subagent, deciding a role descriptor (segment + tier) in frontmatter, or escalating mid-flow. Covers the… | internal — routed to by `build-loop` | hidden |
| [`build-loop:native-ax-driver`](../skills/native-ax-driver/SKILL.md) | Use when the build needs to automate a macOS .app without touching the hardware cursor, or the user asks to "click through the app" or "test the UI… | internal — routed to by `build-loop` | hidden |
| [`build-loop:optimize`](../skills/optimize/SKILL.md) | Use when the user says "optimize this", "optimization", "make X faster", "reduce <metric>", or "speed up my app". Runs a Design of Experiments test matrix… | internal — routed to by `build-loop` | hidden |
| [`build-loop:plan-verify`](../skills/plan-verify/SKILL.md) | Use when build-loop Phase 2 wraps plan drafting, the user runs `/build-loop:verify-plan`, asks to "verify the plan" or "lint the plan", or any plan markdown… | internal — routed to by `build-loop` | hidden |
| [`build-loop:plugin-builder`](../skills/plugin-builder/SKILL.md) | Use when the user asks to create, build, scaffold, convert, or migrate a Claude Code plugin, or needs guidance on plugin.json, directory layout, hooks, MCP… | internal — routed to by `build-loop` | hidden |
| [`build-loop:plugin-tests`](../skills/plugin-tests/SKILL.md) | Static-analysis test harness for Claude Code plugins. Triggers on "test plugin", "validate plugin", "check skill resolution", "run plugin tests", "lint… | internal — routed to by `build-loop` | hidden |
| [`build-loop:prd-bridge`](../skills/prd-bridge/SKILL.md) | Use when Phase 1 Assess runs, the user mentions a PRD, or asks to "ground the build in product strategy". Surfaces always-true principles and Navigation Map… | internal — routed to by `build-loop` | hidden |
| [`build-loop:recursive-retrospective`](../skills/recursive-retrospective/SKILL.md) | Run a recursive-learning retrospective on an app/agent/plugin/build-loop project — analyze build history, behavior, and current state to extract reusable… | internal — routed to by `build-loop` | hidden |
| [`build-loop:repo-closeout`](../skills/repo-closeout/SKILL.md) | Compatibility alias for Repository Maintenance. Use when an existing prompt or workflow invokes repo-closeout; route all repository structure, artifact… | internal — routed to by `build-loop` | hidden |
| [`build-loop:repo-maintenance`](../skills/repo-maintenance/SKILL.md) | Audit and evolve repository structure safely across the full maintenance lifecycle: repository topology and scope, application and build-system profiles… | internal — routed to by `build-loop` | hidden |
| [`build-loop:research`](../skills/research/SKILL.md) | Use when the user asks to "research", "investigate", "evaluate options", or "find out about" a topic. Generate a repo-grounded research packet before deciding… | internal — routed to by `build-loop` | hidden |
| [`build-loop:root-cause-analysis`](../skills/root-cause-analysis/SKILL.md) | Blameless root-cause analysis that produces durable system levers, not blame or one-off patches. Use AFTER a failure/regression/wrong-output/near-miss when… | internal — routed to by `build-loop` | hidden |
| [`build-loop:runtime-parity-verification`](../skills/runtime-parity-verification/SKILL.md) | Use in Phase 4/5 (Validate/Iterate) for ANY change to a user-visible flow — web, macOS, iOS, agent, or CLI/TUI — before claiming "done". Verifies the RUNNING… | internal — routed to by `build-loop` | hidden |
| [`build-loop:security-methodology`](../skills/security-methodology/SKILL.md) | Use when a build crosses a security boundary (auth, authz, secrets handling, network exposure, persistence of sensitive data) or when Phase 1 Assess flags… | internal — routed to by `build-loop` | hidden |
| [`build-loop:security-scan`](../skills/security-scan/SKILL.md) | Run before any push OR any deployment, during Phase 2 planning, or whenever an agent wants a security pass. Executes a deterministic, model-independent OWASP… | internal — routed to by `build-loop` | hidden |
| [`build-loop:self-improve`](../skills/self-improve/SKILL.md) | Use when Phase 6 Learn fires automatically after Report, the user runs `/build-loop:self-improve`, or asks to "scan recent runs" or "improve build-loop"… | internal — routed to by `build-loop` | hidden |
| [`build-loop:spec-writing`](../skills/spec-writing/SKILL.md) | Write a build-loop-compatible plan/spec. Walks the completeness checklist before drafting; runs plan-critic on output. Triggers when build-loop Phase 2 starts… | internal — routed to by `build-loop` | hidden |
| [`build-loop:sync-skills`](../skills/sync-skills/SKILL.md) | Use when the user asks to "check skill drift", "sync skills", or "update architecture skills", or when Phase 1 Assess detects stale source_hash values. Walks… | internal — routed to by `build-loop` | hidden |
| [`build-loop:telemetry`](../skills/telemetry/SKILL.md) | Use when adding observability/telemetry to an app, instrumenting traces/metrics/logs, wiring LLM/agent tracing, choosing a monitoring vendor, or auditing an… | internal — routed to by `build-loop` | hidden |
| [`build-loop:ui-design`](../skills/ui-design/SKILL.md) | Use when build-loop needs UI design direction, visual style selection, UI guidance inventory, a .build-loop/app-contract/ui.md design contract, or non-trivial… | internal — routed to by `build-loop` | hidden |
