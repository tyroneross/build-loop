# Codex Subagent Adapter

Use this adapter when Build Loop runs inside Codex. It translates Build Loop's host-neutral "parallel subagents" concept into Codex-native delegation without changing Claude Code's `agents/*.md` runtime.

## Core Rule

Codex may use subagents only when the user explicitly authorizes delegation or parallel agent work. Examples: "use subagents", "parallelize this", "delegate", "spin up workers", or `/build-loop --parallel ...`.

If authorization is absent, keep the work local in the lead Codex session. Still write the same MECE plan and ownership packets, but do not spawn workers.

## Role Mapping

| Build Loop need | Codex role | Use when |
|---|---|---|
| Codebase question | `explorer` | The answer can be read-only, bounded, and returned as facts with file paths. |
| Implementation slice | `worker` | The write set is disjoint from other workers and the interface contract is clear. |
| Critic/review | lead session, or `explorer` if authorized | The review is read-only and does not block the immediate next local step. |
| Final integration | lead session | The lead must own merge, validation, and final judgment. |

## Permission Gate

Before spawning any Codex subagent:

1. Confirm user authorization is explicit in the current request or command flags.
2. Confirm the task is not on the immediate critical path.
3. Confirm the subtask has a bounded write set or read-only question.
4. Confirm the prompt includes a MECE ownership packet.

If any condition fails, do the work locally.

## When Not To Delegate

- Ambiguous product decisions.
- Final integration or final report.
- Destructive git operations.
- Push/deploy confirmation.
- A task whose result is required before the lead can make the next local move.
- Files already owned by another active worker.
- Cross-file architecture decisions that were not settled in the plan.

## Prompt Packet Required

Every Codex worker prompt must include:

- `task`: one concrete outcome.
- `owns`: exact files or directories the worker may edit.
- `does_not_own`: files, directories, or responsibilities the worker must avoid.
- `context`: condensed facts from Phase 1 and Phase 2.
- `interface_contract`: functions, routes, schemas, props, CLI flags, or docs the worker must preserve or expose.
- `integration_checkpoint`: what the lead will verify before merging the worker result.
- `validation`: commands the worker should run if feasible.
- `return_format`: changed files, summary, validation, unresolved risks, integration notes.

Tell every worker: "You are not alone in the codebase. Do not revert edits made by others; adapt around them and report conflicts."

## Context Policy

Prefer explicit prompt packets over full context forks. Use full context only when the worker genuinely needs the thread history to avoid a wrong implementation.

Shared reads should happen once in the lead session, then be condensed into worker prompts. This keeps workers focused and reduces contradictory interpretations.

## Parallel Pattern

1. Lead creates the plan and identifies parallel-safe groups.
2. Lead spawns only independent sidecar work.
3. Lead continues non-overlapping local work while workers run.
4. Lead waits only when blocked on a worker result.
5. Lead reviews changed files and integrates deliberately.
6. Lead runs final validation locally.

## Return Format

Workers should finish with:

```text
Changed files:
- <path>: <what changed>

Validation:
- <command or "not run">: <result or reason>

Integration notes:
- <contract, migration, or ordering notes>

Unresolved risks:
- <risk or "none known">
```

