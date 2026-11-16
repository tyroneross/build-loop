# Select the smallest sufficient workflow

Read this shared Claude/Codex selector before the full Build Loop skill. Select
by dependencies, uncertainty, risk, and available checks. File count and line
count alone do not select a workflow. The host's intent owner stays authoritative;
these are execution structures within that owner.

## Preserve intent and authority

- Questions and read-only assessments end with an answer. Explicit research then
  implementation retains both steps.
- Resume existing work with its scope, remaining budget, and owed verification.
  Load the canonical skill's resume protocol; never reclassify incomplete work
  as direct to clear obligations.
- Debugging, research, optimization, RCA, retrospective, plan/spec, PRD, and
  repository maintenance use the canonical skill's intent table and owning
  protocols. This selector does not replace those specialists.
- Required repository checks, credentials, approvals, independent review,
  isolation, recovery, and closeout requirements apply to every path. A lighter
  path changes orchestration overhead, not acceptance. If repository instructions
  require six phases for the task, select adaptive orchestration.

## Select the execution structure

| Structure | Use when | Execution |
|---|---|---|
| **Direct** | One bounded operation has understood effects and a checkable result. | Inspect context, act, verify, report evidence. No full skill load unless another requirement needs it. |
| **Fixed chain** | Dependent steps and boundaries are known before execution, including predictable multi-file changes. | State goal and acceptance checks, give a short ordered plan, execute with relevant intermediate checks, verify the integrated result and required reviews, report outcome and limits. Keep execution in the lead session by default. |
| **Adaptive orchestration** | Investigation changes the decomposition, workstreams need dynamic integration, or risk/uncertainty requires the full protocol. | Load the canonical skill; run Assess, Plan, Execute, Review, Iterate, Learn. Choose each next action from observed tool/test feedback within the accepted goal and budget. |

For direct and fixed-chain work, do not load six-phase references, create a phase
ledger, drain unrelated work, or start cross-run mining solely for the workflow.
Existing explicit obligations still apply. Finish when the requested outcome and
required checks are met. Record structure and reason in the existing plan/run
record, or the short readback when no record exists.

Inspect uncertainty with a bounded read or probe before selecting. Escalate when
unknown dependencies, conflicting ownership, or failed checks invalidate the plan.
Preserve completed evidence and remaining budget; do not restart or repeat passed
checks without a relevant change. A local defect with a known correction can stay
in the fixed chain.

## Add patterns only when useful

**Parallelization** adds independent tasks with disjoint ownership, or independent
review when required, to either a chain or adaptive run. Honor host delegation
authorization and capacity. Sequential execution remains valid when delegation is
unavailable; a required independent review stays owed, never becomes a self-review
pass.

**Evaluator–optimizer** adds iteration when a test, grader, or scoped reviewer can
identify an actionable gap against acceptance criteria. Correct the gap and rerun
affected checks. Stop on acceptance, budget exhaustion, or existing non-convergence
limits. Repeated unchanged feedback requires reassessing the cause. Additional
review rounds need a named uncertainty; prose approval alone proves no behavior.

## Memory across occasional runs

Reuse the memory facade, working context, and existing task progress. For bounded
work, retrieve goal-relevant decisions or incidents when prior context matters.
For adaptive work, use the canonical context bootstrap. Do not load full histories
or require a memory backend for an unrelated small task.

Keep user instructions separate from observations. Save pointers to facts that
can be looked up again and refresh mutable source state before relying on it.
Task progress records the goal, completed work with evidence, next action, owed
verification, and pending approvals. Save progress through the existing context
mechanism before handoff or interruption. Memory cannot grant authority, satisfy
a review, or prove an external effect.

Curated lessons need evidence, scope, freshness, and supersession information.
Use existing authorized writers and memory policy; preserve any host requirement
for an explicit request to save memory. Keep run artifacts separate from curated
memory. Learn/daydream curates across runs at its existing cadence or on request,
not on every bounded task.

Event inboxes, timers, and outboxes are optional infrastructure for authorized
unattended work. Interactive routing does not install schedulers or start daemons.
Resuming a saved task supports continuity without always-on execution. If
unattended operation enters scope, assess existing host event delivery,
deduplication, worker ownership, permissions, and delivery receipts first.

## Evidence boundary

Adapted from [Anthropic's workflow patterns](https://www.anthropic.com/research/building-effective-agents)
and [cross-run primitives](https://claude.com/resources/articles/building-always-on-agents).
This is a testable policy, not a demonstrated performance improvement. Compare
old/new routing on matched task snapshots, model/effort, host, delegation,
permissions, budgets, and memory conditions. Measure external acceptance,
completed required reviews, time, tokens, correction turns, and recovery
separately. Include failures/timeouts and a fresh representative task before
claiming general effectiveness.
