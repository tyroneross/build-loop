<!-- PROVENANCE: op=search reference for build-loop:debugging-memory. Native core refreshed from @tyroneross/claude-code-debugger v1.9.0 at 74cc2cc96ce7c212a81d41b85143dc1fc9094bc3 on 2026-08-25. -->

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Native Debugging Memory Search

Build Loop owns this debugger. It does not require the standalone Coding Debugger package or MCP server. Search and store both use the project's structured `.claude/memory/` root.

## Invoke

Before investigating a bug, run:

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/build-loop-debugger.js" search "<symptom>" \
  --threshold 0.6 --workdir "$PWD"
```

The command returns JSON with `memory_root`, `debugger_core_version`, and a verdict:

- `KNOWN_FIX`: direct-apply only if the strict gate passes.
- `LIKELY_MATCH`: use the incident as a hypothesis and run the normal fix loop.
- `WEAK_SIGNAL`: consider the result, but investigate fresh.
- `NO_MATCH`: investigate fresh and store the verified result afterward.

## Strict direct-apply gate

All three checks must pass:

1. At least one recorded file exists at the same relative path.
2. Recorded dependency versions match the current project within minor version. Missing version evidence fails this check.
3. A second signal matches: error class, callsite, or a corroborating log entry.

React-hook, performance, and "increase a limit" fixes never direct-apply because they are context-sensitive.

## Retrieval depth

The initial search returns compact matches. Load a full incident only when needed:

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/build-loop-debugger.js" detail <INC_ID> --workdir "$PWD"
```

Announce the search and report whether it found a match. Store the verified outcome through the same native command described in `store.md`.

## Lifecycle

- Phase 1 Assess: search for relevant project incidents.
- Review-B failure: search the exact current error before changing code.
- Each Iterate attempt: search again if the symptom changes.
- Review-F: store every newly resolved, verified incident.

`.build-loop/issues/` remains the executable/open-issue lane. Do not write resolved debugger history there.
