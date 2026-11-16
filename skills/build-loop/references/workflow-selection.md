# Select the smallest sufficient workflow

Read this shared Claude/Codex selector before the full Build Loop skill. Select
by dependencies, uncertainty, risk, and available checks. File count and line
count alone do not select a workflow. The host's intent owner stays authoritative;
these are execution structures within that owner.

## Preserve intent and authority

- Honor an execution structure selected by the user or upstream dispatcher;
  selection may escalate it, never downgrade a structure selected upstream.
  Questions and explicit read-only requests take precedence over resuming actions.
  Before selecting fresh
  work, inspect relevant unfinished run state in `.build-loop/state.json`, owed
  verification, and run-created branches/worktrees. Continue aligned unfinished
  work under its original contract; unrelated historical debt does not select the
  route for a new task.
- Honor explicit mode flags before selecting: `--parallel`, `--no-regrets`,
  `--long`, `--budget`, `--autonomous=false`, and per-commit overrides retain their
  existing meanings. Load the canonical skill's applicable mode protocol to
  resolve them; do not silently discard a flag on a lighter path. Explicit full
  loop requests and active `selfRecursive.enabled` or per-commit dispatch use the
  canonical protocol. Reading a mode protocol alone does not require six phases
  when that mode permits bounded work; if the mode protocol is silent, use adaptive.
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
  In Build Loop's own repository, the current AGENTS.md/CLAUDE.md mandates
  intentionally keep significant multi-step changes on adaptive orchestration.
  Fixed chains are available in consuming repositories without that mandate;
  this selector does not amend or override those repository instructions.
- Existing risk classification and model-tier pins apply on every path, including
  database/migration, auth/secrets, production, and destructive operations. Load
  their applicable contracts before acting; when the effect or required checks
  are uncertain, use adaptive orchestration.

## Select the execution structure

| Structure | Use when | Execution |
|---|---|---|
| **Direct** | One bounded operation has understood effects and a checkable result. | Inspect context, act, verify, report evidence. No full skill load unless another requirement needs it. |
| **Fixed chain** | Dependent steps and boundaries are known before execution, including predictable multi-file changes. | State goal and acceptance checks, give a short ordered plan, execute with relevant intermediate checks, verify the integrated result and required reviews, report outcome and limits. Keep execution in the lead session by default. |
| **Adaptive orchestration** | Investigation changes the decomposition, workstreams need dynamic integration, or risk/uncertainty requires the full protocol. | Load the canonical skill; run Assess, Plan, Execute, Review, Iterate, Learn. Choose each next action from observed tool/test feedback within the accepted goal and budget. |

The fixed-chain review floor is relevant executable acceptance checks plus one
scoped independent review of the changed files and integrated result. Reuse an
already-required independent review; do not add a duplicate round. If that review
is unavailable, report it owed and the run partial. Apply the existing 2h default
budget unless the user sets another. A failed correction with no new evidence
ends blind retries: reassess and escalate to the adaptive protocol if further
investigation is needed. Budget exhaustion ends the run with remaining work stated.
After any failed check, reassess before retrying. Follow the canonical skill's
"Question-led inspections and investigations" search-and-batch rule; only rerun
after the evidence supports a correction. Read only that section when needed.
A local correction does not by itself require restarting the phase ledger.

For direct and fixed-chain work, do not load six-phase references, create a phase
ledger, drain unrelated work, or start cross-run mining solely for the workflow.
Existing explicit obligations still apply. Finish when the requested outcome and
required checks are met. Record structure and reason in the existing plan/run
record. Direct work may use the short readback when no record exists; fixed
chains retain the run record described below.

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
pass. Before any dispatch, load the repository-root
`references/model-tier-mapping.md` section "Model preferences and bounded workers";
use its existing tier pins and capacity/permission checks.

**Evaluator–optimizer** adds iteration when a test, grader, or scoped reviewer can
identify an actionable gap against acceptance criteria. Correct the gap and rerun
affected checks. Stop on acceptance, budget exhaustion, or existing non-convergence
limits. Repeated unchanged feedback requires reassessing the cause. Additional
review rounds need a named uncertainty; prose approval alone proves no behavior.

## Memory across occasional runs

Reuse the `scripts/memory_facade/` recall API, `scripts/context_snapshot.py`, and existing task
progress, resolved from the plugin root. Full store details live in repository-root
`references/memory-systems.md`. For bounded
work, retrieve goal-relevant decisions or incidents when prior context matters.
For adaptive work, use the canonical context bootstrap. Do not load full histories
or require a memory backend for an unrelated small task.

Keep user instructions separate from observations. Save pointers to facts that
can be looked up again and refresh mutable source state before relying on it.
Task progress records the goal, completed work with evidence, next action, owed
verification, and pending approvals. Save progress through the existing context
mechanism before handoff or interruption. Memory cannot grant authority, satisfy
a review, or prove an external effect.

Progress belongs in the run/context record, not automatically in host memory.
For a fixed chain, retain structure, reason, checks, outcome, and remaining work
in `.build-loop/state.json` `runs[]` through
`python3 -m scripts.write_run_entry` from the plugin root (entrypoint:
`scripts/write_run_entry/__main__.py`). Its required arguments are `--workdir`,
`--goal`, and `--outcome`; use existing `--scope build` and review-evidence options
for code changes. Put structure, reason, budget start time, and excluded historical
debt in the existing free-form object accepted by `--harness-json <file>`, and
check commands in `--diagnostic-commands`. This preserves the existing run schema
and outcomes consumed by Learn; do not invent top-level fields or a new run type.
This lets later assessment include these runs without
creating a second phase ledger. Do not promote the record into curated memory.

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
