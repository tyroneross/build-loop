---
name: logging-tracer
description: Use when the user asks to "add logging", "add tracing", "improve observability", "OpenTelemetry", "structured logging", or reports silent failures or no runtime visibility. Generates stack-appropriate logging with optional OTel.
version: 1.0.0
user-invocable: false
---

# Logging & Tracing Code Generation

Generate structured logging and tracing code tailored to the project's stack. Follow a tiered approach: start with zero dependencies, escalate only when the user needs distributed tracing.

## Stack Detection

Before generating code, detect the project's stack and existing logging:

1. Check for `package.json` (Node.js/TypeScript), `requirements.txt`/`pyproject.toml` (Python), `go.mod` (Go), `Cargo.toml` (Rust), `Gemfile` (Ruby)
2. Check for existing logging frameworks:
   - Node.js: winston, pino, bunyan, console
   - Python: logging, loguru, structlog
   - Go: zap, zerolog, logrus, slog
3. Check for existing tracing: OpenTelemetry SDK, Sentry SDK, Datadog agent
4. Detect the application type: API server, CLI tool, web app, worker/queue processor

If existing logging exists, extend it rather than replacing it. If uncertain about the stack, ask the user before generating code.

## Tiered Code Generation

### Tier 1: Zero-Dependency Structured Logging (Default)

Generate a single logger module using only built-in language features. Output structured JSON to stderr (not stdout, which may be used for data or protocols).

**Key requirements:**
- Log levels: debug, info, warn, error
- Configurable minimum level via environment variable
- Structured JSON output with: timestamp, level, message, and arbitrary context fields
- Operation name for every log entry
- Duration tracking for async operations

Refer to `references/stack-templates.md` for full implementation templates per language.

### Tier 2: File-Based Logging

When the user needs persistent logs or the debugger's `read_logs` tool should discover them:

- Write logs to `logs/app.jsonl` in the project root (JSONL format, append-only)
- Use standard field names: `ts` (Unix ms), `level`, `msg`, `op` (operation name)
- Add log rotation at 10MB with 2 rotated files maximum
- These locations are auto-discoverable by the debugger's `read_logs` MCP tool

### Tier 3: OpenTelemetry + Free Backends

When the user explicitly requests distributed tracing or mentions OTel/Jaeger/SigNoz:

- Install the OTel SDK for their language
- Create a tracing initialization module with:
  - Graceful degradation when no collector is running
  - Hot-reload safety (prevent duplicate initialization)
  - Smart sampling: 100% in development, 10% in production (100% for errors/slow operations)
- Wrap key operations in spans
- Recommend free backends: Jaeger (local), SigNoz (self-hosted), or Grafana Tempo

## Where to Add Logging

Guide the user on strategic placement. Log at these points:

1. **Function entry/exit** for key operations (API handlers, service methods, data pipelines)
2. **External calls** — every HTTP request, database query, cache operation, file I/O
3. **Error handlers** — always log error name, message, stack, and the operation that failed
4. **State transitions** — authentication changes, workflow steps, queue processing stages
5. **Decision points** — when code takes a branch based on runtime data (cache hit/miss, feature flag, fallback)

Avoid logging:
- Every iteration of a loop (use summary: "processed 150 items in 230ms")
- Sensitive data (passwords, tokens, PII) — redact before logging
- Redundant information already captured by the framework (e.g., Express request logging middleware)

## Ephemeral-by-default (mandatory for diagnostic instrumentation)

When this skill is invoked **reactively** to repair an evidence gap during a debugging session — not proactively at the user's standing request for permanent observability — the instrumentation **must not land in the final diff unless the user explicitly approves it**. Log/tracer patches that survive into commits alter timing, IO, and snapshot behavior — masking rather than fixing the original failure.

Two enforcement mechanisms; choose per invocation. Default to Mechanism A.

### Mechanism A — Runtime gate (preferred)

Wrap every new diagnostic log statement so it is inert unless `DEBUG_TRACE=1` is set:

```typescript
const __trace = process.env.DEBUG_TRACE === "1";
function trace(msg: string, meta: Record<string, unknown> = {}) {
  if (!__trace) return;
  const entry = { ts: new Date().toISOString(), level: "trace", msg, ...meta };
  process.stderr.write(JSON.stringify(entry) + "\n");
}
```

```python
import os, json, sys, datetime
_TRACE = os.environ.get("DEBUG_TRACE") == "1"
def trace(msg, **meta):
    if not _TRACE: return
    entry = {"ts": datetime.datetime.utcnow().isoformat() + "Z", "level": "trace", "msg": msg, **meta}
    print(json.dumps(entry), file=sys.stderr)
```

Re-run the failing criterion with `DEBUG_TRACE=1 <test-command>`. Output flows to stderr for `read_logs` MCP to capture. Production paths never execute trace code in normal builds.

### Mechanism B — Throwaway patch

When the change cannot be wrapped in a runtime gate (e.g. language without env access at the call site, or the instrumentation requires structural changes like adding request IDs or new fields to types), apply the change as a `git stash` patch BEFORE re-running:

```bash
# After the code changes land
git stash push -u -m "build-loop:trace/<session-id>"
# Stash applied in-place
git stash show stash@{0}
# Re-run the failing criterion
<test-command>
# Diagnostics captured in .build-loop/logs/
# Revert after the capture completes
git stash drop stash@{0}
```

The orchestrator tracks the stash entry in `.build-loop/state.json.observability.interventions[].stash_id`. At Review-F the orchestrator MUST verify no stash entries remain with `build-loop:trace/` prefix; if any do, revert them before writing the scorecard.

### Keep-in-diff approval (opt-in only)

To keep instrumentation in the final diff (e.g. the user wants ongoing observability), the caller must invoke `AskUserQuestion`:

```
Question: "Keep the diagnostic logging added to <files> in the final diff?"
Options:
  - "Revert — instrumentation was diagnostic only" (default, recommended)
  - "Keep — convert to permanent observability (remove DEBUG_TRACE gate or unstash)"
  - "Keep with gate — leave DEBUG_TRACE wrapping in place"
```

Default answer on user absence: **revert**. No silent retention. If the user picks "keep", remove the env-flag guard (Mechanism A) or apply the stash and drop the reference (Mechanism B).

## Code placement rules (diagnostic instrumentation)

When adding instrumentation reactively to repair an evidence gap:

- Insert at function entry/exit for functions the investigation flagged — not the whole codebase
- Never silently catch + log (`catch { log(...) }` without rethrow is an anti-pattern that turns errors into lost signal)
- Include the variable that was `undefined` / `null` / `nil` in the log entry — bare "error in X" is useless
- Add exactly ONE trace call per function added; no spam
- All calls go through the `trace()` helper (Mechanism A) or live in a throwaway stash (Mechanism B) — no unguarded log/print/eprintln statements added to the codebase

## Re-validate after adding

After the instrumentation lands:

1. Re-run the failing criterion with `DEBUG_TRACE=1` (Mechanism A) or stash applied (Mechanism B)
2. If tests now fail WITH informative output → return the log evidence to the caller as fresh context for the next fix attempt
3. If tests still fail silently → instrumentation did not solve the visibility problem; escalate to user
4. **Always revert** at session end unless the user explicitly approved keep-in-diff via the prompt above. The orchestrator (or caller) verifies no `build-loop:trace/` stash entries remain and no unguarded trace calls landed.

## Log Analysis Guidance

When the user has logs but needs help interpreting them, follow this diagnostic sequence:

1. **Start with errors** — filter for error/fatal level, read newest first
2. **Check timing** — look for operations that took >2s or showed sudden duration spikes
3. **Look for patterns** — repeated errors, cascading failures, periodic spikes
4. **Correlate timestamps** — align logs across services/components for the same time window
5. **Diagnose missing logs** — if expected log entries are absent, the code path wasn't reached or the logger isn't configured

Refer to `references/log-analysis.md` for common error signatures and diagnostic checklists.

## Integration with Debugger

Generated logging code integrates with the debugger's `read_logs` MCP tool when:

- Logs are written to discoverable locations (`logs/`, `*.log`, `*.jsonl` in project root)
- JSONL format uses standard fields (`ts`, `level`, `msg`)
- Error entries include structured error objects (`error.name`, `error.message`, `error.stack`)

After adding logging, tell the user they can read logs using:
```
Use the debugger read_logs tool with source "project" to view these logs.
```

## Output Format

When generating logging code:

1. Generate the logger module first (single file)
2. Show 2-3 examples of how to use it in existing code
3. Mention the environment variable for log level configuration
4. If Tier 2+, note the log file location and rotation behavior
