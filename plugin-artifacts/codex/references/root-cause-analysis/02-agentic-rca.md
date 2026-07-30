# Agentic Coding RCA — extension of 01-rca.md (deltas only, do not restate)

> Use when an AI coding agent writes/edits/reviews/tests/ships code and the result is wrong, incomplete, unsafe, unverified, brittle, or misaligned. This is Prompt 1 PLUS the agentic deltas below — inherit 01-rca.md's evidence labels (incl. verify-before-FACT), creation/escape paths, action-strength hierarchy, lever+actuator, regression artifact, density + level binding. Do not re-derive those here.

## Core split
Separate **immediate code repair** (fix the repo now) from **durable agent-system fix** (change the prompt / context injector / planning loop / tool wrapper / sandbox / verification gate / eval / hook / workflow / review). A code patch is NOT sufficient if the agent system would repeat the failure.

## Delta 1 — First Attribution Gate (run BEFORE blaming the agent)
Do not assign "agent failure" until task, context, loop, tool, codebase, verification, and release controls are checked.
Table: Domain | Evidence | Confidence | Primary/Secondary/Not implicated. Rows: Task/spec · Context · Agent loop · Tool/platform · Codebase · Verification · Review/release. → state Primary domain + Secondary contributors.

## Delta 2 — Agentic failure modes (hypotheses, not conclusions)
Context blindness · Planning drift · Tool misuse (ignored exit code/stderr) · Verification gap (claimed done, weak proxy check) · State corruption (sandbox≠real repo) · Spec ambiguity (literal ask, wrong outcome) · Cascading micro-errors · Architecture violation · Security/control miss · Observability gap (can't reconstruct why it acted).

## Delta 3 — Evidence pack (agentic-specific, present? yes/no)
Original request · full agent prompt · injected context · repo map version · dependency graph · architecture rules · plan artifact · replan events · tool-call trace (cmd/args/stdout/stderr/exit) · intermediate diffs · self-checks · lint/type/test/security results · CI result · final diff · acceptance criteria · reproduction. **No RCA without a trace** — if the trace is absent, that itself is an observability root cause.

## Delta 4 — Agentic layer taxonomy (extends §6)
Task/spec · Context assembly · Architecture memory · Planning/control loop · Tool execution · Workspace state · Generated code · Verification · CI/release control · Checkpoint/rollback · Observability · Human review · Agent platform (hook/wrapper/policy-as-code/permission/sandbox).

## Delta 5 — Lever+Actuator map (agentic examples)
Context blindness → repo-map/dep-graph injector · mandatory pre-task assembly. Spec ambiguity → prompt template w/ acceptance criteria · prompt-linter blocks incomplete task. Planning drift → persistent plan artifact · plan-validator before each edit phase. Tool misuse → wrapper w/ schema+exit-code enforcement · wrapper blocks failing commands. Verification gap → mandatory repro+lint/type/test/security gate · agent cannot mark done until it passes. State corruption → clean sandbox+snapshot · reset before task / checkpoint after each edit group. Architecture violation → rules file · hook checks changed files. Security miss → scan+policy-as-code · block on unsafe pattern. No rollback → versioned snapshots · auto-revert on failed gate. Observability gap → structured trace · emitter writes every step.

## Delta 6 — Regression artifact (required for meaningful RCA)
Prefer a committed test when code-observable; else golden eval / prompt regression test / trace replay / fixture repo / tool-call simulation / policy-as-code test / sandbox-reset test / review-checklist update. Must show: old fails · fixed passes · location · how it runs in future.

## Delta 7 — Escalation triggers
>3 replans without progress · context injection fails · dep graph un-buildable · gates conflict · required tests missing & unsafe to generate · architectural root cause · broad-refactor fix · security/privacy/data risk · same root cause >2× in a sprint · agent can't produce a trace · agent proposes unrelated large diffs · agent can't explain why a change is safe.

## Delta 8 — Prevention pattern (state the reusable rule)
> When [condition], the agent must [required behavior], enforced by [gate/hook/eval/tool], verified by [artifact].

## Output (L2): use 01-rca.md §11 plus the Attribution Gate (after Bottom line) and "Immediate repair vs Durable loop fix" (split §8). Honor verify-before-FACT and the density governor.
