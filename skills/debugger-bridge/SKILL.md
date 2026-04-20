---
name: build-loop:debugger-bridge
description: Memory-first debugger integration for Phase 5 (VALIDATE) and Phase 6 (ITERATE). When a criterion fails or iteration gets stuck, call claude-code-debugger's `checkMemoryWithVerdict()` before re-planning. Verdict decides: apply known fix, adapt prior incident, parallel multi-domain assessment, or escalate to causal-tree investigation.
version: 0.1.0
user-invocable: false
---

# Debugger Bridge

Folds claude-code-debugger into build-loop surgically. Instead of blind retries at Phase 6, check institutional memory first. Most bugs recur; if the debugger has seen this class before, apply the known fix or adapt prior incident notes.

**Use at:**
- Phase 5 VALIDATE — when any criterion fails with an error-like signal (exception, test failure, build error)
- Phase 6 ITERATE — before each retry attempt, not just once

**Skip when:**
- `availablePlugins.claudeCodeDebugger` is false → use `fallbacks.md#debug` inline guidance
- Failure is expected and mapped (e.g., "tests must fail until implementation complete" in TDD flows)
- Iteration is due to user feedback, not a reproducible bug

## Pre-flight

Check installation:

```bash
# Via state.json set in Phase 1
jq -r '.availablePlugins.claudeCodeDebugger' .build-loop/state.json
```

If `true`, proceed. If `false`, emit:

```
Debugger memory: claude-code-debugger not installed. Using inline debug fallback.
```

## Phase 5 — Pre-Fail Memory Gate

Before marking a criterion as FAIL and routing to Phase 6, query debugger memory.

### Steps

1. **Synthesize a symptom string**. Take the failed criterion's evidence (test output, error message, type error) and compress to a single line < 200 chars. Preserve the error type, file, and key phrase.

   Good:
   ```
   Phase 5 FAIL: criterion "tests pass" — TypeError: Cannot read properties of undefined (reading 'middleware') at src/auth/session.ts:42
   ```

   Bad (too broad):
   ```
   Tests failed
   ```

2. **Call memory search** via the `search` MCP tool:

   ```
   mcp__plugin_claude_code_debugger__search({
     symptom: "<synthesized string>",
     token_budget: 2500
   })
   ```

   Or via Skill invocation:
   ```
   Skill("claude-code-debugger:debugging-memory") with input { symptom, budget: 2500 }
   ```

3. **Act on verdict**:

   | Verdict | Action |
   |---|---|
   | `KNOWN_FIX` (>80% confidence, exact match) | Apply the documented fix directly. Proceed to Phase 6 validation. Do NOT re-plan. |
   | `LIKELY_MATCH` (60-80%, multiple similar) | Load the top incident's detail. Adapt the fix to current context. Route to Phase 6 with the adapted fix as the plan. |
   | `WEAK_SIGNAL` (30-60%, loosely related) | Note the similar incident in the Phase 6 plan as a reference, but investigate normally. |
   | `NO_MATCH` (<30%) | Fall through to standard Phase 6 behavior. Record the failure for future memory (Phase 8 store). |

4. **Record the verdict** in `.build-loop/state.json.debuggerGates.phase5`:

   ```json
   {
     "timestamp": "ISO-8601",
     "criterion": "tests pass",
     "verdict": "LIKELY_MATCH",
     "confidence": 0.72,
     "incidentId": "INC_FRONTEND_20260403_112345_abc1",
     "appliedFix": "boolean — did the orchestrator apply the suggested fix without re-planning"
   }
   ```

## Phase 6 — Stuck-Iteration Escalation

After Phase 6 attempt N fails and before attempt N+1, escalate based on failure count and diversity.

### Escalation rules

**After 2 consecutive failed fixes** on the same criterion:

1. Check if the failures have the same root cause. If yes → **parallel multi-domain assessment** (below).
2. If the root causes are diverging → **causal-tree investigation** (below).

**After 3 consecutive failed fixes**:

Automatic escalation to causal-tree investigation. Do not attempt a 4th fix without it.

### Parallel multi-domain assessment

When the failure symptom touches multiple layers (search queries are slow AND results look wrong → database + frontend):

1. Dispatch `/assess <symptom>` command from claude-code-debugger:

   ```
   Skill("claude-code-debugger:assess") with input { symptom, context: current_attempt_diff }
   ```

2. The debugger's `assessment-orchestrator` fans out to relevant domain assessors (api / database / frontend / performance) in parallel.

3. **Model override**: when invoking from build-orchestrator (Opus 4.7), explicitly pass `model: sonnet` to each domain assessor via the subagent dispatch to avoid 4 parallel Opus invocations. Only escalate individual assessors to Opus if their initial output flags `confidence: low` or `needs_judgment: true`.

4. Aggregate the assessors' ranked findings. Use the top action as the Phase 6 attempt N+1 plan.

### Causal-tree investigation

When the bug is not multi-layer but deep (same root-cause symptom keeps reappearing despite targeted fixes):

1. Invoke `debug-loop` skill:

   ```
   Skill("claude-code-debugger:debug-loop") with input {
     symptom,
     reproductionSteps: <from Phase 5 evidence>,
     previousAttempts: <Phase 6 diffs so far>
   }
   ```

2. `debug-loop` runs 7 internal phases (investigate → hypothesize → fix → verify → score → critique → report), up to 5 iterations, with `root-cause-investigator` and `fix-critique` agents.

3. When it returns, the fix (if any) is already applied. Build-loop's Phase 6 validates against its original criteria. If it passes, proceed. If it fails after 5 internal debug-loop iterations, hard-stop and escalate to user.

4. Result is stored in debugger memory automatically via its own `store` tool.

## Phase 8 — Store for Future Memory

When a build completes (pass or fail), if any Phase 5/6 failure was resolved during this run, store the incident:

```
mcp__plugin_claude_code_debugger__store({
  symptom: "<original failure string>",
  root_cause: "<what was wrong>",
  fix: "<diff or description>",
  tags: ["build-loop", "<project>", "<layer>"],
  files: ["<paths touched>"]
})
```

This is what makes the memory-first gate useful on the next run. Do not skip storage.

## Model Tiering

| Step | Model |
|---|---|
| `search` call (verdict lookup) | inline, no model (MCP tool does the work) |
| Apply KNOWN_FIX | inline, orchestrator (Opus 4.7) for signoff on non-trivial fixes |
| LIKELY_MATCH adaptation | Sonnet |
| Parallel assessors | Sonnet (override debugger default `inherit` to prevent 4× Opus) |
| Causal-tree (`debug-loop`) | Sonnet by default; internal escalation to Opus on strong-checkpoint |
| `store` call (Phase 8) | inline, no model |

## What This Skill Does NOT Do

- Does not replace Phase 5 VALIDATE or Phase 6 ITERATE — it augments them
- Does not write to debugger memory automatically at Phase 5 — store only at Phase 8 when resolution is known
- Does not override build-loop's 5-iteration hard stop
- Does not invoke the debugger's `logging-tracer` skill (out of scope; add when needed separately)

## Integration with Orchestrator

Build-orchestrator dispatches this skill at:
- Phase 5, immediately after any criterion marked FAIL (before routing to Phase 6)
- Phase 6, at the start of each attempt (not just attempt 1)
- Phase 8, once per build, to store resolved incidents

Orchestrator reads `.build-loop/state.json.debuggerGates.*` for dashboard visibility and Phase 9 self-improvement signals.
