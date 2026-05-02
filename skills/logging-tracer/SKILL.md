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
