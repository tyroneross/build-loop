# Tool collaboration and workflow efficiency

For Rust test relocation, crate splits or shared test infrastructure, load
[Rust test migrations](rust-test-migrations.md). Separate source inventory,
compiler discovery, semantic review and executed results; preserve visibility
and equivalent behavioral coverage.

Use primary responsibility to route work, not to prohibit useful overlap.

| Tool | Primary responsibility | Useful overlap |
| --- | --- | --- |
| Architect | Trace end-to-end workflows, architecture, dependencies and state ownership; identify candidate redundant workflows/tasks | Inspect implementation evidence and propose acceptance checks; remain read-only on target repositories |
| Code Audit | Establish code mechanisms, affected callers, invariants and counterexamples | Trace workflows and make proportionate authorized repairs within its existing scope |
| Build Loop | Coordinate implementation, verification, integration and closeout under one plan and budget | Perform architecture analysis and code review itself when adequate |
| Rally, when present | Carry assignments, scoped claims, handoffs and receipts | Expose overlapping work for investigation; presence or a queued message does not prove receipt or correctness |

Do not launch all tools by default. A focused repair may stay with Code Audit; a
multi-step coordinated change belongs with Build Loop. The active coordinator
assigns one current owner per deliverable within existing authority. Independent
review is a separate deliverable and may overlap analysis. Do not cancel, merge or
reassign tasks merely because titles or file paths overlap. Preserve each agent's
claims and the existing edit/merge checks. A tool role or handoff grants no new
execution authority. Continue with available capabilities if an optional plugin is
missing and disclose the resulting coverage gap.

## Reuse evidence and pass the unresolved question

Keep one plan and budget; reuse stable finding IDs and existing source captures,
graphs and results when revision, inputs, environment and coverage still apply.
Recheck changed dependencies, stale evidence and uncovered behavior. Independent
reviews may reuse inputs without treating a prior conclusion as independent proof.
Avoid rescanning unchanged inputs or repeating broad audits at every transition.

A handoff carries objective, authorized scope, repository/revision and inputs,
finding/evidence references, preserved guarantees, unresolved question, acceptance
predicates and next owner. The recipient checks applicability and continues the
existing task. Handoff acceptance does not transfer file claims automatically.

## Find waste without discarding protection

Map request to accepted outcome: useful work, required protection, waiting and
rework. Identify the constrained resource before increasing concurrency. Compare
eliminate, combine, rearrange and simplify against ordering, authority, durability,
isolation and recovery. Similar names and static graph edges cannot establish
runtime duplication. Compare task deliverables, inputs, revisions and acceptance
obligations. Separate candidate, source-supported and observed findings.

For a cache identify key, invalidation, permissions, ownership changes, restart and
fallback. For batches preserve per-item identity, authority, ordering and partial
or unknown outcomes. Test conflicting same-file edits/merges and stale checks when
applicable; lookup then write alone is not atomic protection. Start verification
with the highest-risk applicable failure, uncertainty or dependency, then exercise
the ordinary path. Compare end-to-end latency, errors, resources and operator effort
on equivalent workloads before claiming improved speed. Retain useful independent
reviews and checks at distinct trust boundaries.
