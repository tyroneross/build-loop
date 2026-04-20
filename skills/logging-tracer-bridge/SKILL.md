---
name: build-loop:logging-tracer-bridge
description: Generate stack-appropriate structured logging and optional OpenTelemetry tracing when a build lacks runtime visibility — surfaced in Phase 1 (if project has only print/console.log in production paths), triggered in Phase 5/6 when debugger-bridge cannot make progress due to silent failures. Delegates to claude-code-debugger's logging-tracer skill when available.
version: 0.1.0
user-invocable: false
---

# Logging-Tracer Bridge

Folds claude-code-debugger's `logging-tracer` skill into build-loop. Solves "tests failed but I can't tell why" — generates zero-dep structured logging, file-based JSONL for `read_logs` MCP consumption, or OTel tracing when the project already has it.

## When This Fires

### Phase 1 — Observability baseline check (passive, informational)

During ASSESS, after detecting the project's language/framework, run a quick observability scan:

```bash
# Web / Node
grep -rE "console\.(log|error|warn)" --include="*.ts" --include="*.js" --include="*.tsx" --include="*.jsx" src/ app/ pages/ 2>/dev/null | head -20

# Python
grep -rE "(print\(|pprint\()" --include="*.py" src/ 2>/dev/null | head -20

# Check for structured logging already present
grep -rE "(winston|pino|bunyan|structlog|loguru|logrus|zap|log/slog)" package.json pyproject.toml requirements.txt go.mod 2>/dev/null
```

Classify:
- **Well-instrumented**: structured logger detected → note in state.json, do nothing.
- **Print-only**: only `print()` / `console.log` in production paths → set `.build-loop/state.json.observability.level = "print-only"`.
- **Silent**: no logging at all in production paths → `.build-loop/state.json.observability.level = "silent"`.

Do **not** add logging in Phase 1. This is informational — Phase 5/6 may need it.

### Phase 5/6 — Reactive trigger (when debug-loop stalls)

When `build-loop:debugger-bridge` escalates to `debug-loop` and the debug-loop's `root-cause-investigator` agent returns with an `evidence_gap` verdict (i.e. "cannot determine cause because logs/traces are missing"), load this bridge.

Signals from debug-loop that trigger this bridge:
- `"Insufficient log evidence"` in the investigation output
- Empty `read_logs` results for the error window
- Error with no context: "throw new Error()" with no message, `assert` with no message, test failing without captured output
- Async failure with no stack trace rooted in application code

### Explicit trigger

User says: "add logging", "no logs", "silent failure", "need visibility", "can't see what's happening", "add tracing", "add OpenTelemetry".

## What It Does

### Delegate to upstream when available

If `availablePlugins.claudeCodeDebugger` is true, delegate to the debugger's logging-tracer:

```
Skill("claude-code-debugger:logging-tracer") with input {
  language: <detected>,
  framework: <detected>,
  tier: auto | 1 | 2 | 3,
  target_files: <from debug-loop evidence-gap or user hint>,
  integration: read_logs_mcp   # so logs are discoverable by debugger memory
}
```

The upstream skill handles tier selection and code generation. Bridge's job is to:
1. Pass the right context (what file, what symptom, what stack)
2. Record the intervention in `.build-loop/state.json.observability.interventions[]`
3. Re-run the failing Phase 5 criterion after the logging lands

### Fallback when upstream is absent

If `availablePlugins.claudeCodeDebugger` is false, synthesize inline. Logging tier defaults:

**Tier 1 — Zero-dependency structured JSON to stderr** (always safe):

Node/TS:
```typescript
function log(level: string, msg: string, meta: Record<string, unknown> = {}) {
  const entry = { ts: new Date().toISOString(), level, msg, ...meta };
  process.stderr.write(JSON.stringify(entry) + "\n");
}
```

Python:
```python
import json, sys, datetime
def log(level, msg, **meta):
    entry = {"ts": datetime.datetime.utcnow().isoformat() + "Z", "level": level, "msg": msg, **meta}
    print(json.dumps(entry), file=sys.stderr)
```

Go:
```go
import "log/slog"
// Use slog.Info(msg, "key", value) — structured by default in Go 1.21+
```

Rust:
```rust
// Use tracing crate if present; else eprintln! with serde_json
```

**Tier 2 — File-based JSONL** (discoverable by `read_logs` MCP):

Write to `.build-loop/logs/<component>.jsonl` with rotation at 10MB. File path goes in state.json so debugger `read_logs` can find it.

**Tier 3 — OpenTelemetry** (only if project already has OTel set up):

Add spans at the boundaries where the debug-loop identified evidence gaps. Do not introduce OTel as a new dependency — that is a build-loop decision requiring user approval. If OTel is not already installed, drop to Tier 1 or Tier 2.

### Ephemeral-by-default (mandatory)

Diagnostic instrumentation added by this bridge **must not land in the final diff unless the user explicitly approves it**. The adversarial review flagged that log/tracer patches that survive into the commit alter timing, IO, and snapshot behavior — masking rather than fixing the original failure. Two enforcement mechanisms, choose per invocation:

**Mechanism A — Runtime gate (preferred)**. Wrap every new log statement so it is inert unless `DEBUG_TRACE=1` is set:

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

**Mechanism B — Throwaway patch**. When the bridge cannot wrap the instrumentation in a runtime gate (e.g. language without env access, or instrumentation requires structural changes like adding request IDs), apply the change as a `git stash` patch BEFORE re-running:

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

The orchestrator tracks the stash entry in `.build-loop/state.json.observability.interventions[].stash_id`. Phase 8 verifies no stash entries remain with `build-loop:trace/` prefix at build completion; if any do, the orchestrator reverts them before writing the scorecard.

### Keep-in-diff approval (opt-in only)

To keep instrumentation in the final diff (e.g. the user wants ongoing observability), the orchestrator must invoke `AskUserQuestion`:

```
Question: "Keep the diagnostic logging added to <files> in the final diff?"
Options:
  - "Revert — instrumentation was diagnostic only" (default, recommended)
  - "Keep — convert to permanent observability (remove DEBUG_TRACE gate or unstash)"
  - "Keep with gate — leave DEBUG_TRACE wrapping in place"
```

Default answer on user absence: **revert**. No silent retention. If the user picks "keep", the bridge either removes the env-flag guard (Mechanism A) or applies the stash and drops the reference (Mechanism B).

### Code placement rules

- Insert at function entry/exit for functions the debug-loop flagged
- Never silently catch + log (`catch { log(...) }` without rethrow is an anti-pattern)
- Include the variable that was `undefined` / `null` / `nil` in the log entry — bare "error in X" is useless
- Add exactly ONE trace call per function added; no spam
- All calls must go through the `trace()` helper (Mechanism A) or live in a throwaway stash (Mechanism B) — no unguarded log/print/eprintln

### Re-validate after adding

After the logging change lands:
1. Re-run the failing Phase 5 criterion with `DEBUG_TRACE=1` (Mechanism A) or stash applied (Mechanism B)
2. If tests now fail WITH informative output → route to Phase 6 with the log evidence as fresh context
3. If tests still fail silently → this bridge did not solve the problem; escalate to user
4. **Always** revert instrumentation at Phase 8 unless the user approved keep-in-diff. The orchestrator verifies no `build-loop:trace/` stash entries remain.

## Model Tiering

| Step | Model |
|---|---|
| Phase 1 observability scan | inline, no model (grep only) |
| Code generation — Tier 1 | inline, template substitution (no model needed for zero-dep) |
| Code generation — Tier 2 | Sonnet (file path, rotation logic, JSONL schema need judgment) |
| Code generation — Tier 3 OTel | Sonnet (span placement requires reading existing instrumentation) |
| Placement decisions | Sonnet via debugger upstream |
| Phase 5 re-validation | inline via build-loop's standard grader |

## State Schema

Write observations to `.build-loop/state.json.observability`:

```json
{
  "level": "well-instrumented | print-only | silent",
  "detectedLibraries": ["winston", "pino"],
  "interventions": [
    {
      "date": "ISO",
      "phase": 5,
      "trigger": "evidence_gap",
      "tier": 2,
      "files_modified": ["src/auth/session.ts"],
      "resolved_phase_5_criterion": "tests pass",
      "outcome": "resolved | not_resolved"
    }
  ]
}
```

## What This Bridge Does NOT Do

- Does not add logging in Phase 1 without a triggering event (no surprise code changes)
- Does not introduce a new logging library as a dependency (Tier 3 only when already present)
- Does not add log statements everywhere — only at the debugger-identified evidence gap
- Does not replace structured logging that is already working

## Integration with Orchestrator

Orchestrator invokes via `Skill("build-loop:logging-tracer-bridge")` with either:
- `{phase: 1, action: "scan"}` for the passive observability check
- `{phase: 5, action: "repair", trigger: "<evidence_gap|user_request>", target_files: [...], symptom: "..."}` for reactive code generation

Results flow to `.build-loop/state.json.observability`. Phase 8 report includes any interventions made.
