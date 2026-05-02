---
name: build-loop:logging-tracer-bridge
description: Optional escalation from build-loop's bundled logging-tracer skill to the standalone claude-code-debugger plugin for extended observability tooling — additional tracer backends, cross-build log correlation, advanced placement intelligence. Called BY logging-tracer when bundled tier-selection / codegen is insufficient, NOT by the orchestrator.
version: 0.3.0
user-invocable: false
---

# Logging-Tracer Bridge — Extended Capability Escalation

As of build-loop 0.7.1 this bridge's role is **escalation, not primary coordination**. The orchestrator owns when-to-fire (Phase 1 Assess observability scan, Phase 5 Iterate reactive trigger on `evidence_gap`) and routes those phases to the bundled internal `build-loop:logging-tracer` skill, which owns tier selection, stack detection, codegen, the ephemeral-by-default policy (Mechanisms A and B), and code-placement rules.

This bridge is the **secondary hop**: when the bundled logging-tracer decides it needs more than build-loop ships natively, it can invoke this bridge to delegate to the standalone `claude-code-debugger` plugin (if installed) for extended observability tooling.

## When this bridge is invoked

By `build-loop:logging-tracer`, not the orchestrator. Calling sites:

- `build-loop:logging-tracer` §"Extended capability" — when the project requires a tracer backend or placement intelligence beyond what build-loop ships (e.g., a downstream MCP-discoverable log sink that lives in the standalone plugin only)

The orchestrator MUST NOT call this bridge directly. Orchestrator → `logging-tracer` skill → (optional) this bridge → standalone plugin.

## Pre-flight (always run first)

```
if (!state.availablePlugins.claudeCodeDebugger) {
  return { delegated: false, reason: "standalone claude-code-debugger plugin not installed" }
}
```

If false, the calling target skill continues with bundled-only capability.

## Delegations available

| Capability needed | Standalone Skill / MCP call |
|---|---|
| Extended tracer backends not in bundle | `Skill("claude-code-debugger:logging-tracer")` with `tier: <upstream-only>` |
| Cross-build log correlation (e.g., correlation IDs across multiple build-loop runs) | standalone-only MCP tools |
| Advanced placement intelligence (e.g., function-call graph aware insertion) | standalone-only assessor skills |

The bridge passes through caller-supplied symptom + target-files + tier-hint, returns enriched data to `logging-tracer`. The target skill decides how to fold the extended result into its own codegen.

## What this bridge does NOT do

- Reimplement tier selection, stack detection, ephemeral mechanisms, or code placement — those live in `build-loop:logging-tracer`
- Replace the orchestrator's when-to-fire policy (Phase 1 Assess scan, Phase 5 Iterate evidence_gap trigger) — that lives in `agents/build-orchestrator.md`
- Introduce new logging dependencies without explicit user approval — that constraint stays in `logging-tracer`
- Mutate `.claude-code-debugger/` paths — backward-compat preserved
- Hard-fail when standalone is absent — pre-flight returns gracefully

## State

Optional bridge invocations are logged to `.build-loop/state.json.observability.escalations[]`:

```json
{ "ts": "ISO", "calledBy": "logging-tracer", "reason": "tier_3_otel_required", "delegated": true|false }
```

## Cross-references

- `agents/build-orchestrator.md` — when-to-fire policy (Phase 1 Assess scan, Phase 5 Iterate)
- `skills/logging-tracer/SKILL.md` — primary tier-selection / codegen / ephemeral-by-default skill
- `skills/build-loop/fallbacks.md` §`debug` — local-only fallback when neither bundled nor standalone is reachable

## History

- v0.2.0 — coordination layer (orchestrator called this bridge as primary entry point for observability)
- v0.7.0 — dissolved into `logging-tracer` skill + orchestrator (transient, in-flight architecture)
- v0.7.1 — restored as **extended-capability escalation hop** with the architecture documented above
