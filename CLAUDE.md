# Build Loop Plugin

Orchestrated 5-phase development loop (+1 optional) for significant multi-step code changes.

**Phases**: Assess → Plan → Execute → Review → Iterate (5x max). Optional: Learn (cross-build pattern detection).

Review has internal sub-steps: Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Report.

## Principles

- Self-sufficient: works without any specific tool installed. Bridges to NavGator, claude-code-debugger, IBR, etc. all have **standalone fallbacks** in `skills/build-loop/fallbacks.md` — degraded-but-useful behavior when upstream plugins are absent, not skip-silently.
- Tools loaded on demand, not pre-loaded
- Guidelines for the creation process, guardrails for user-facing output
- No false data, no mock data in production, no unverified claims
- Diagnose before fixing, converge or escalate
- Learn from recurring patterns — auto-draft experimental skills with A/B comparison, user keeps or removes
- Cherry-pick from companion tools, don't embed. The companion repos (NavGator, claude-code-debugger, IBR) stay independent; bridges only consume their relevant outputs/skills. When companions are absent, fallbacks.md carries the **knowledge of what to look for** even if the deep execution isn't available.

## Claude Code Integration

- `/build-loop [goal]` — triggers the build-loop skill which orchestrates all 5 phases
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
- `state.json` — iteration state, phase progress, **`runs[]`** for self-improvement scanning
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
