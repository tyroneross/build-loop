---
name: build-loop:debugger-bridge
description: Optional escalation from build-loop's bundled debugger skills to the standalone claude-code-debugger plugin for extended capabilities — cross-project memory, additional assessor coverage, coordination across build-loop instances. Called BY target skills when bundled capability is insufficient, NOT by the orchestrator.
version: 0.3.0
user-invocable: false
---

# Debugger Bridge — Extended Capability Escalation

As of build-loop 0.7.1 this bridge's role is **escalation, not primary coordination**. The orchestrator owns when-to-fire (Phase 1 Assess context priming, Phase 4-B Review memory gate, Phase 5 Iterate stuck-escalation) and routes those phases to the bundled internal skills (`build-loop:debugging-memory`, `build-loop:debug-loop`, `build-loop:logging-tracer`).

This bridge is the **secondary hop**: when a bundled skill decides bundled-only capability is not enough, it can invoke this bridge to delegate to the standalone `claude-code-debugger` plugin (if installed). The standalone plugin acts as a supporting agent / tool — broader memory, additional MCP capabilities, cross-instance coordination.

## When this bridge is invoked

By target skills, not the orchestrator. Typical calling sites:

- `build-loop:debugging-memory` §"Extended capability" — when project-local memory misses but cross-project memory might have a hit, or when the bundled MCP lacks a capability the standalone provides
- `build-loop:debug-loop` §"If stuck" — when bundled assessors aren't enough and the standalone plugin ships additional ones
- `build-loop:logging-tracer` §"Extended capability" — when extended observability tooling beyond bundled is required

The orchestrator MUST NOT call this bridge directly. Orchestrator → target skill → (optional) this bridge → standalone plugin.

## Pre-flight (always run first)

```
if (!state.availablePlugins.claudeCodeDebugger) {
  return { delegated: false, reason: "standalone claude-code-debugger plugin not installed" }
}
```

If false, the calling target skill continues with bundled-only capability — no error, no log spam.

## Delegations available

| Capability needed | Standalone Skill / MCP call |
|---|---|
| Cross-project incident memory (broader than current project) | `Skill("claude-code-debugger:debugging-memory")` with `scope: "global"` |
| Additional domain assessor coverage | `Skill("claude-code-debugger:assess")` if standalone ships assessors not in bundle |
| Cross-build-instance coordination | standalone-only MCP tools surfaced under `mcp__plugin_claude_code_debugger__*` |

The bridge passes through caller-supplied symptom + context, returns enriched data (cross-project verdicts, additional assessor findings) to the calling target skill. The target skill decides how to fold the extended result into its own decision.

## What this bridge does NOT do

- Reimplement bundled capability — that lives in the build-loop internal skills (`debugging-memory`, `debug-loop`, `logging-tracer`)
- Replace the orchestrator's when-to-fire policy — that lives in `agents/build-orchestrator.md`
- Mutate `.claude-code-debugger/` paths — they are intentionally shared between bundled and standalone for backward-compat
- Hard-fail when standalone is absent — pre-flight returns gracefully

## State

Optional bridge invocations are logged to `.build-loop/state.json.debuggerGates.escalations[]`:

```json
{ "ts": "ISO", "calledBy": "debug-loop", "reason": "stuck-iteration-3", "delegated": true|false, "result": "..." }
```

The orchestrator surfaces these in Review-F as informational, not blocking.

## Cross-references

- `agents/build-orchestrator.md` — when-to-fire policy (Phase 1, 4-B, 5)
- `skills/debugging-memory/SKILL.md` — primary memory skill
- `skills/debug-loop/SKILL.md` — primary deep-debug skill
- `skills/logging-tracer/SKILL.md` — primary logging skill
- `skills/build-loop/fallbacks.md` §`bug-memory` — local-grep fallback when neither bundled nor standalone is reachable

## History

- v0.2.0 — coordination layer (orchestrator called this bridge as primary entry point)
- v0.7.0 — dissolved into target skills + orchestrator (transient, in-flight architecture)
- v0.7.1 — restored as **extended-capability escalation hop** with the architecture documented above
