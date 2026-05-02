# Build Loop Plugin

Orchestrated 5-phase development loop (+1 optional) for significant multi-step code changes.

**Phases**: Assess → Plan → Execute → Review → Iterate (5x max). Optional: Learn (cross-build pattern detection).

Review has internal sub-steps: Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Report.

## Principles

- Self-sufficient: works without any specific tool installed. Bridges to NavGator, IBR, etc. all have **standalone fallbacks** in `skills/build-loop/fallbacks.md` — degraded-but-useful behavior when upstream plugins are absent, not skip-silently. (As of 0.6.0 the debugger is bundled internally; see KNOWN-ISSUES "Plugin merge — 2026-05-02".)
- North star first: every build captures app/repo purpose, update intent, user value, and non-goals, then passes that intent to each subagent.
- Beauty in the basics: core flows, real data, clear hierarchy, working controls, useful states, and accurate information matter more than extra surface area.
- Modular by default, not by dogma: prefer high cohesion, loose coupling, stable interfaces, scalable boundaries, and MECE file/agent ownership unless a documented exception better serves the use case.
- Tools loaded on demand, not pre-loaded
- Guidelines for the creation process, guardrails for user-facing output
- No false data, no mock data in production, no unverified claims
- Diagnose before fixing, converge or escalate
- Learn from recurring patterns — auto-draft experimental skills with A/B comparison, user keeps or removes
- Cherry-pick from companion tools, don't embed — except when integration density justifies a merge. The companion repos (NavGator, IBR) stay independent; bridges only consume their relevant outputs/skills. claude-code-debugger was merged inline in 0.6.0 because the debugger is invoked from inside the build loop on every Review-B / Iterate failure (multiple times per build) — keeping it external created loose coupling without a benefit. Other companions remain separate.

## Claude Code Integration

- `/build-loop:run [goal]` — triggers the build-loop skill which orchestrates all 5 phases (the bare `/build-loop` form is deprecated due to a namesake collision with the skill of the same qualified name; see `KNOWN-ISSUES.md`)
- `/build-loop:debug <symptom>` — deep iterative root-cause investigation via the bundled `debug-loop` skill (also auto-invoked by the orchestrator on Review-B failures and Iterate attempts 2 and 3)
- `/build-loop:debugger`, `/build-loop:debugger-detail`, `/build-loop:debugger-scan`, `/build-loop:debugger-status`, `/build-loop:assess` — bundled debugger surface
- `/build-loop:self-improve` — run Phase 6 Learn alone against recent runs without a new build
- Build orchestrator agent (Opus 4.7) coordinates phase execution and spawns parallel subagents
- Fact-checker and mock-scanner agents run in parallel during Review sub-step D
- Recurring-pattern-detector (Haiku) + self-improvement-architect (Sonnet) run during Phase 6 Learn
- External skills used when available: `writing-plans`, `subagent-driven-development`, `calm-precision`, `verification-before-completion`, `plugin-dev:skill-development`, `navgator` — phases degrade gracefully without them

## Model Tiering

| Role | Model | Why |
|---|---|---|
| Orchestrator / plan / final signoff | Opus 4.7 | Wrong spec is catastrophic |
| Implementer, sonnet-critic, optimize-runner, overfitting-reviewer, self-improvement-architect | Sonnet 4.6 | Bounded, recoverable, ~4× cheaper |
| Mock-scanner, recurring-pattern-detector | Haiku 4.5 | Pattern matching only |
| Fact-checker | inherit | Session-driven |

## Project Data

Runtime data stored in `.build-loop/` within consumer projects (created on first use):
- `goal.md` — current build goal
- `intent.md` — north star, update intent, user value, and non-goals
- `config.json` — optional repo flags, including deploymentPolicy
- `state.json` — iteration state, phase progress, compact intent/structure summaries, **`runs[]`** for self-improvement scanning
- `feedback.md` — post-build lessons
- `evals/` — scorecard archives
- `issues/` — discovered issues
- `skills/experimental/` — auto-drafted skills from Phase 6 Learn (remove with `rm -rf`)
- `agents/experimental/` — auto-drafted agents from Phase 6 Learn
- `skills/active/` — auto-promoted skills (opt-in; requires `autoPromote: true` + effective sample ≥ 8)
- `proposals/` — pending promotion/removal proposals awaiting user confirmation
- `experiments/<name>.jsonl` — A/B tracking log per experimental artifact
- `experiments/discarded.jsonl` — Opus-rejected drafts with reasons

## Cross-Tool Support

This repo includes `AGENTS.md` — the open-standard version of the build loop methodology. Non-Claude tools (Codex, Copilot, Cursor, etc.) can use that file directly for the same workflow without Claude-specific integration.

## Plugin Development

- Plugin manifest: `.claude-plugin/plugin.json`
- Test changes by installing locally: add repo path to `~/.claude/settings.json` under `projects.plugins`
- Runtime data goes in `.build-loop/` in consumer projects, not in the plugin repo
