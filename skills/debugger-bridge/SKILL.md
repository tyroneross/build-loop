---
name: build-loop:debugger-bridge
description: DEPRECATED — superseded by native debugging skills under skills/debugging/ and the bundled debugger MCP server. This stub redirects callers to the new fully-qualified skill names. Will be removed after one release cycle.
version: 0.4.0-deprecated
user-invocable: false
deprecated: true
superseded_by:
  - build-loop:debugging-memory          # legacy in-tree (kept for backward compat)
  - build-loop:debugging-memory-search   # native sourced version with provenance
  - build-loop:debugging-store
  - build-loop:debugging-assess
  - build-loop:debugging-debug-loop
---

# Debugger Bridge — Deprecated

This bridge has been replaced by build-loop-native debugging skills under `skills/debugging/`. The debugger MCP server is bundled with build-loop (since 0.6.0) and the native skills carry `source:` + `source_hash:` provenance pointing at the canonical claude-code-debugger repo.

## Migration map

| Bridge use | Native replacement |
|------------|--------------------|
| Pre-flight + memory search | `build-loop:debugging-memory` (legacy in-tree) — runtime path used by Review-B gate. Or `build-loop:debugging-memory-search` (native sourced) for explicit provenance |
| Multi-domain assessment escalation | `build-loop:debugging-assess` |
| Stuck-iteration deep debug | `build-loop:debugging-debug-loop` |
| Incident storage on Review-F | `build-loop:debugging-store` |

## Behavior of this stub

If invoked, this skill emits one line and exits:

```
build-loop:debugger-bridge is deprecated. Use build-loop:debugging-{memory,memory-search,store,assess,debug-loop}.
```

It does not perform any reads, writes, or MCP calls. The orchestrator (`agents/build-orchestrator.md`) has been migrated to call the native skills directly.

## Cross-instance escalation (the original bridge purpose)

The bridge's original niche was escalating from bundled debugger memory to a separate `claude-code-debugger` plugin install for cross-project memory pooling. That escalation is now handled inside `build-loop:debugging-memory` itself — the skill checks for an external `claude-code-debugger` plugin and invokes its MCP tools when present.

## Removal timeline

This stub remains for one build-loop release cycle to give external callers time to migrate. Remove after that.

*Native equivalents under `skills/debugging/` were authored on 2026-05-03 alongside this deprecation.*
