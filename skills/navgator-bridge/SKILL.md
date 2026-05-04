---
name: build-loop:navgator-bridge
description: DEPRECATED — superseded by native architecture skills under skills/architecture/. This stub redirects callers to the new fully-qualified skill names. Will be removed after one release cycle.
version: 0.2.0-deprecated
user-invocable: false
deprecated: true
superseded_by:
  - build-loop:architecture-scan
  - build-loop:architecture-impact
  - build-loop:architecture-trace
  - build-loop:architecture-rules
  - build-loop:architecture-dead
  - build-loop:architecture-review
---

# NavGator Bridge — Deprecated

This bridge has been replaced by build-loop-native architecture skills under `skills/architecture/`. Each new skill carries `source:` + `source_hash:` provenance pointing at the canonical NavGator repo and is drift-checked by `build-loop:sync-skills`.

## Migration map

| Bridge use | Native replacement |
|------------|--------------------|
| Assess blast-radius read | `build-loop:architecture-scan` then `build-loop:architecture-impact` |
| Review-D violation check | `build-loop:architecture-rules` |
| Review-F orphan scan | `build-loop:architecture-dead` |
| LLM use-case map | `mcp__plugin_navgator__llm_map` (called directly, no skill) |
| Cross-layer integrity review | `build-loop:architecture-review` |

## Behavior of this stub

If invoked, this skill emits one line and exits:

```
build-loop:navgator-bridge is deprecated. Use build-loop:architecture-{scan,impact,trace,rules,dead,review}.
```

It does not perform any reads or writes. The orchestrator (`agents/build-orchestrator.md`) and `skills/build-loop/SKILL.md` have been migrated to call the native skills directly.

## Why deprecated

- Bridge pattern drifted from upstream NavGator; native skills are version-tracked with explicit source hashes
- Reduces indirection — orchestrator now invokes the work directly without a wrapper
- Drift detection (`build-loop:sync-skills`) only operates on native sourced skills, not bridges

## Removal timeline

This stub remains for one build-loop release cycle to give external callers time to migrate. Remove after that.

*Native equivalents under `skills/architecture/` were authored on 2026-05-03 alongside this deprecation.*
