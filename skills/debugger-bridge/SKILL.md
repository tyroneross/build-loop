---
name: build-loop:debugger-bridge
description: Memory-first debugger integration for Review-B Validate and Iterate. On criterion fail, calls claude-code-debugger's checkMemoryWithVerdict to route: apply known fix, adapt prior incident, or escalate.
version: 0.1.0
user-invocable: false
---

# Debugger Bridge

Folds claude-code-debugger into build-loop surgically. Instead of blind retries at Iterate, check institutional memory first. Most bugs recur; if the debugger has seen this class before, apply the known fix or adapt prior incident notes.

## Cherry-pick principle

**claude-code-debugger remains an independent plugin and repository.** This bridge does not embed or duplicate the debugger's memory, verdict classifier, or causal-tree logic — it only consumes the relevant MCP tools and skills:

- Calls MCP tools: `search`, `store`, `outcome`, `read_logs`, `list` — delegation only
- Invokes upstream skills: `claude-code-debugger:debugging-memory`, `:assess`, `:debug-loop` — delegation only
- Writes to `.build-loop/state.json.debuggerGates.*` — bridge's own namespace

What this bridge does NOT do:
- Reimplement verdict classification or memory search
- Cache debugger memory locally (always calls live MCP)
- Call `store` or `outcome` outside Review-F to avoid corrupting training data
- Duplicate the `assessment-orchestrator` or `debug-loop` internal phases

If the debugger plugin is absent, this bridge skips — consumer falls back to `fallbacks.md#debug` inline guidance (minimum viable; does NOT reimplement debugger memory).

**Use at:**
- Review-B — when any criterion fails with an error-like signal (exception, test failure, build error)
- Iterate — before each retry attempt, not just once

**Skip when:**
- `availablePlugins.claudeCodeDebugger` is false → use `fallbacks.md#debug` inline guidance
- Failure is expected and mapped (e.g., "tests must fail until implementation complete" in TDD flows)
- Iteration is due to user feedback, not a reproducible bug

## Pre-flight

Check installation:

```bash
# Via state.json set in Assess
jq -r '.availablePlugins.claudeCodeDebugger' .build-loop/state.json
```

If `true`, run the steps in this skill against the debugger's MCP tools and skills.

If `false`, **run the standalone fallback** instead of skipping silently. Build-loop carries degraded-but-useful bug-memory when the debugger isn't installed:

- **Load**: `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md` §`bug-memory` — executable token-extract + grep against `.build-loop/issues/`, `.build-loop/feedback.md`, and `.bookmark/`
- **Verdict shape**: `LOCAL_HIT_EXACT` / `LOCAL_HIT_PARTIAL` / `LOCAL_WEAK` / `LOCAL_NO_MATCH` — same four-state interface as the classifier verdict, but from file grep. No confidence score. No direct-apply path (all verdicts route to Iterate as adapted plan).
- **Storage**: after resolving a failure, write `.build-loop/issues/YYYY-MM-DD-<slug>.md` with `{symptom, root_cause, fix, files, tags}`. Future builds grep this file.
- **Flag in Review-F report**: `⚠️ debugger memory via local grep — install claude-code-debugger for classified cross-project memory + training feedback loop`

The fallback covers: this-project failure history, local pattern lookup, manual incident recording. It does NOT cover: cross-project memory, the verdict classifier, causal-tree investigation (`debug-loop`), parallel multi-domain assessment (`/assess`), or the `outcome` training signal — those require the debugger plugin.

Do not error, do not block the build.

## Assess — Context priming (optional, cheap)

At the start of Assess, pull recent project incident context so the orchestrator is aware of what's been failing lately:

```
mcp__plugin_claude_code_debugger__list({ filter: { project: "<current>" }, limit: 10 })
```

Summarize in one line for the orchestrator log: "Debugger memory: N recent incidents in this project, top categories: [db, frontend]." No action, just context. If memory is empty, skip silently.

## Review-B — Pre-Fail Memory Gate

Before marking a criterion as FAIL and routing to Iterate, query debugger memory.

### Steps

0. **Read logs before synthesizing** (if tests fail with no stderr/stdout capture): invoke `read_logs` MCP to pull any structured log entries from `.build-loop/logs/*.jsonl`, Sentry (if configured), or OTel endpoints (if `OTEL_EXPORTER_OTLP_ENDPOINT` set):

   ```
   mcp__plugin_claude_code_debugger__read_logs({
     source: "project",
     severity: "error",
     query: "<criterion keyword>",
     since: "<phase_5_start_timestamp>"
   })
   ```

   If log entries are returned, incorporate them into the symptom string below. If `read_logs` returns nothing but the test failed silently, flag `evidence_gap: true` in the gate record — Iterate escalation may need `logging-tracer-bridge` to restore visibility before debugger memory is useful.

1. **Synthesize a symptom string**. Take the failed criterion's evidence (test output, error message, type error) and compress to a single line < 200 chars. Preserve the error type, file, and key phrase.

   Good:
   ```
   Review-B FAIL: criterion "tests pass" — TypeError: Cannot read properties of undefined (reading 'middleware') at src/auth/session.ts:42
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

3. **Act on verdict — all verdicts treat memory as a hypothesis, not a patch**:

   The adversarial review flagged that compressing a failure to a single-line symptom and then applying a historical fix directly can overfit on superficially similar incidents (same error string, different root cause / version / layer). Direct-apply is now gated behind strict match requirements. By default, every verdict routes to Iterate as an adapted plan — never as a skip.

   | Verdict | Default action | Direct-apply? |
   |---|---|---|
   | `KNOWN_FIX` (>80% confidence, exact symptom match) | Load the top incident's detail. Adapt the fix to current context. Route to Iterate with the adapted fix as the plan. | Only if **all three** secondary signals match (see below) |
   | `LIKELY_MATCH` (60-80%, multiple similar) | Load the top incident's detail. Adapt the fix to current context. Route to Iterate as the plan. | No |
   | `WEAK_SIGNAL` (30-60%, loosely related) | Note the similar incident in the Iterate plan as a reference, but investigate normally. | No |
   | `NO_MATCH` (<30%) | Fall through to standard Iterate behavior. Record the failure for future memory (Review-F store). | No |

   **Direct-apply gate for `KNOWN_FIX`** — all three must hold or the verdict falls back to "adapted plan in Iterate":

   1. **File match**: at least one of the incident's `files[]` exists at the same path in the current project (string match on suffix is acceptable — e.g. `src/auth/session.ts` matches even if relative vs absolute).
   2. **Version match**: if the incident records a framework/library version (e.g. `next@14`, `prisma@5.8`), the current project's equivalent version must be within the same major (and same minor for libraries with pre-1.0 semver). If no version metadata on the incident, this check defaults to "fail" — no direct-apply.
   3. **Second validation signal**: a non-symptom-string match must also agree. At least one of:
      - An exact stack-frame match (same function name + same file) between current failure and incident
      - A matching error class/type hierarchy (not just the message text)
      - A matching log entry from `read_logs` earlier in this gate

   If any of the three fails, downgrade to adapted-plan routing. Record the downgrade in the gate log with `direct_apply_blocked_by: "version_mismatch" | "no_file_overlap" | "no_secondary_signal"`.

   **Why this is strict**: a bad direct-apply mutates the codebase on a lossy match and then Review-F stores the (wrong) outcome back to memory, reinforcing the false association. The cost of occasionally skipping a legitimate direct-apply is small; the cost of one overfit mutation compounding across sessions is large.

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

## Iterate — Stuck-Iteration Escalation

After Iterate attempt N fails and before attempt N+1, escalate based on failure count and diversity.

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

4. Aggregate the assessors' ranked findings. Use the top action as the Iterate attempt N+1 plan.

### Causal-tree investigation

When the bug is not multi-layer but deep (same root-cause symptom keeps reappearing despite targeted fixes):

1. Invoke `debug-loop` skill:

   ```
   Skill("claude-code-debugger:debug-loop") with input {
     symptom,
     reproductionSteps: <from Review-B evidence>,
     previousAttempts: <Iterate diffs so far>
   }
   ```

2. `debug-loop` runs 7 internal phases (investigate → hypothesize → fix → verify → score → critique → report), up to 5 iterations, with `root-cause-investigator` and `fix-critique` agents.

3. When it returns, the fix (if any) is already applied. Build-loop's Iterate validates against its original criteria. If it passes, proceed. If it fails after 5 internal debug-loop iterations, hard-stop and escalate to user.

4. Result is stored in debugger memory automatically via its own `store` tool.

## Review-F — Store for Future Memory + Outcome Feedback

When a build completes (pass or fail), close the feedback loop to debugger memory in two steps:

### Step A — Store resolved incidents (write new knowledge)

For each Review-B/Iterate failure resolved during this run, store the incident:

```
mcp__plugin_claude_code_debugger__store({
  symptom: "<original failure string>",
  root_cause: "<what was wrong>",
  fix: "<diff or description>",
  tags: ["build-loop", "<project>", "<layer>"],
  files: ["<paths touched>"]
})
```

### Step B — Report outcomes on applied memory (train verdict classification)

For each Review-B gate entry in `.build-loop/state.json.debuggerGates` where a prior `KNOWN_FIX` or `LIKELY_MATCH` was applied, report back whether the suggested fix actually worked:

```
mcp__plugin_claude_code_debugger__outcome({
  incident_id: "<from the gate record>",
  result: "worked" | "failed" | "modified",
  notes: "<one line>"
})
```

- `worked`: applied as-is, resolved Review-B criterion on first attempt
- `modified`: applied the suggested approach but had to adapt substantially (Iterate attempt count > 1 on that criterion)
- `failed`: applied but criterion still failed; eventually resolved via different fix or not at all

This is the training signal that makes the verdict classifier better over time. Skipping this step means the debugger's verdicts never improve from your builds. **Always call `outcome` for applied gates, even on build failures** — "worked" vs "failed" is meaningful in both outcomes.

This is what makes the memory-first gate useful on the next run. Do not skip storage.

## Model Tiering

| Step | Model |
|---|---|
| `search` call (verdict lookup) | inline, no model (MCP tool does the work) |
| Apply KNOWN_FIX | inline, orchestrator (Opus 4.7) for signoff on non-trivial fixes |
| LIKELY_MATCH adaptation | Sonnet |
| Parallel assessors | Sonnet (override debugger default `inherit` to prevent 4× Opus) |
| Causal-tree (`debug-loop`) | Sonnet by default; internal escalation to Opus on strong-checkpoint |
| `store` call (Review-F) | inline, no model |

## What This Skill Does NOT Do

- Does not replace Review-B Validate or Iterate — it augments them
- Does not write to debugger memory automatically at Review-B — store only at Review-F when resolution is known
- Does not override build-loop's 5-iteration hard stop
- Does not invoke the debugger's `logging-tracer` skill directly (that's `logging-tracer-bridge`'s job)
- Does not block a build when the debugger plugin is absent — routes to `fallbacks.md#bug-memory` for a standalone local-grep lookup instead of skipping silently

## Integration with Orchestrator

Build-orchestrator dispatches this skill at:
- Review-B, immediately after any criterion marked FAIL (before routing to Iterate)
- Iterate, at the start of each attempt (not just attempt 1)
- Review-F, once per build, to store resolved incidents

Orchestrator reads `.build-loop/state.json.debuggerGates.*` for dashboard visibility and Phase 6 Learn signals.
