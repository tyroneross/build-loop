# Build Loop Plugin

Orchestrated 8-phase development loop for significant multi-step code changes.

**Phases**: Assess → Define → Plan → Execute → Validate → Iterate (5x max) → Fact Check → Report

## Principles

- Self-sufficient: works without any specific tool installed
- Tools loaded on demand, not pre-loaded
- Guidelines for the creation process, guardrails for user-facing output
- No false data, no mock data in production, no unverified claims
- Diagnose before fixing, converge or escalate

## Claude Code Integration

- `/build [goal]` — triggers the build-loop skill which orchestrates all 8 phases
- Build orchestrator agent coordinates phase execution and spawns parallel subagents
- Fact-checker and mock-scanner agents run in parallel during Phase 7
- External skills used when available: `writing-plans`, `subagent-driven-development`, `calm-precision`, `verification-before-completion` — phases degrade gracefully without them

## Project Data

Runtime data stored in `.build-loop/` within consumer projects (created on first use):
- `goal.md` — current build goal
- `state.json` — iteration state, phase progress
- `feedback.md` — post-build lessons
- `evals/` — scorecard archives
- `issues/` — discovered issues

## Cross-Tool Support

This repo includes `AGENTS.md` — the open-standard version of the build loop methodology. Non-Claude tools (Codex, Copilot, Cursor, etc.) can use that file directly for the same workflow without Claude-specific integration.

## Plugin Development

- Plugin manifest: `.claude-plugin/plugin.json`
- Test changes by installing locally: add repo path to `~/.claude/settings.json` under `projects.plugins`
- Runtime data goes in `.build-loop/` in consumer projects, not in the plugin repo
