---
name: guarded-batch-reconcile
description: "Use when reconciling parallel branches, worktrees or completed task outputs, draining a merge queue, or closing a multi-agent work wave. Establish readiness and integrate authorized work through the project's supported merge path; preserve assessment-only requests as read-only."
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Guarded Batch Reconciliation

Converge completed work without losing unfinished work or mistaking build success
for functional acceptance. This is a portable decision procedure, not a merge
queue implementation. Repository Maintenance loads it for parallel-work
reconciliation. Keep the parent's comparison record, goal ledger, readiness
scorer, review, run recording and strict closeout gates; do not create parallel
stores or replace those gates. The state dimensions below supplement the existing
record's evidence; they do not change its schema.

## Discover scope and capabilities

State the target and intended outcome before mutations. Preserve assessment-only
requests as read-only. Creating this skill does not authorize merging the project
in which it was created. Do not expand a scoped drain into repository-wide cleanup.

Read project instructions and the relevant integration/coordination procedures.
Discover rather than assume:

- repository identity, target branch/artifact, exact base revision and dirty state;
- candidate sources: branches/worktrees, PRs, task receipts and review records;
- ownership signals and supported contact/acknowledgment mechanisms;
- required checks, protected-ref rules, merge queue and concurrency controls;
- preservation/cleanup rules, including ignored local notes and artifacts.

Record each capability as available, unavailable or unknown. Use approved
connectors or documented local interfaces; do not guess database paths or schemas.
Missing retrieval is a coverage gap. Never invent identities, credentials,
approvals, locks or tool equivalents. Read the applicable section of
[adapters.md](references/adapters.md) for Git, Easy Terminal, hosted queues,
non-Git projects and Build Loop integration.

## Inventory independent state dimensions

Pin candidate revisions. Inventory registered worktrees and explicitly relevant
project paths, including detached worktrees, dirty/untracked state and relevant
stashes without applying them. Do not scan unrelated personal directories.
Cross-check completed task/review receipts when available: finished work can be
absent from a queue because its worker could not submit it.

Track these independent fields per candidate:

| Field | Values and distinction |
|---|---|
| Committed work | ancestor, patch-equivalent, unique, mixed, unknown |
| Disposition | integrate, represented, superseded with explicit evidence, needs decision, unfinished, unknown |
| Working state | clean, tracked edits, untracked work, relevant ignored state, unreadable |
| Ownership | own/released, other active owner, dormant evidence, unknown |
| Readiness | revision-bound evidence sufficient, stale, missing, failing |
| Dependencies | exact prerequisites/contracts, or not established |

An ancestor HEAD may have unique dirty WIP. A mixed branch may contain superseded
and useful commits; review/port the useful scope rather than accepting or rejecting
the whole branch. Leave unfinished and decision-bearing work intact unless handling
it is authorized.

Ancestry proves reachability; patch IDs support patch equivalence. Commit subjects,
task status, age and symbol searches are leads, not proof that current target
behavior represents the work. Compare patches, current implementation and relevant
tests; historically included work may have been reverted. Mark unknown rather
than guessing integration or supersession.

## Establish readiness and ownership

Record each candidate's SHA/digest, source and target, commands/check IDs, tested
revision, configuration, outcomes, evidence locations and evidence author.
Separate worker-reported results from checks run now. Bind merge-result checks
to the current base and resulting revision. Refresh affected evidence if inputs move.

Inspect textual conflicts and semantic contracts. Disjoint files, non-overlapping
hunks and clean merge simulations do not establish behavioral independence.
Check shared APIs, schemas, generated outputs, configuration, ordering and runtime
assumptions. Follow declared prerequisites.

Use the project's ownership policy. Absent/expired presence is not owner release;
an FYI is not acknowledgment; caller attestation is not author approval.
Unreadable evidence remains unknown and follows the policy's uncertainty handling.
Recheck ownership at landing through the supported guard. Contact peers only when
the user or an explicitly invoked coordination workflow authorizes the channel;
otherwise record the hold and continue independent candidates.

## Preserve, plan and integrate

Before ref changes, retain recoverable base/candidate identities under the project
policy. Recovery tags must have unique run IDs; never overwrite an existing
recovery ref. Tags preserve commits, not dirty, untracked or ignored work.
Do not touch another owner's WIP merely to snapshot it. Before authorized cleanup,
preserve those files and verify recovery under the project's procedure.
Integration and deletion remain separate actions.

Submit through the supported readiness queue with exact revisions, dependencies
and attributed evidence. Do not impersonate a worker. Missing attestation is a
submission blocker to resolve through an approved path, not a reason to bypass guards.

Choose the smallest practical verification unit:

- Batch independent ready work when the integration system supports it.
- Sequence shared-file work and contract dependencies as the adapter requires.
- Isolate high-risk coupled work when that improves diagnosis or coverage.
- Choose batch size from coupling, gate cost and observed failure history. Four
  was one successful Easy Terminal run, not a universal default or measured optimum.

Use isolated integration state, not a dirty shared target. Respect the existing
lock/queue and revalidate base/candidate identities at landing. A Git fallback
must safely serialize concurrent target writers and conditionally advance the
tested target; otherwise prepare a candidate and report the landing hold.
Do not install new guard infrastructure or disable hooks to complete a drain.

Run required checks on the exact combined result. Separate static checks, unit
tests, full assembly, runtime behavior, installation and release evidence.
Verify changed workflows as project policy requires. A batch-tip pass does not
prove each intermediate merge commit passes independently.

On failure, preserve logs and distinguish code, interaction, infrastructure and
unknown failures. Use supported bisection only where dependencies permit it;
a failing combination need not have a single culpable branch. Admission or
credential failures do not establish candidate faults. Continue independent
authorized work; do not repeatedly retry unchanged blockers.

Land only through the permitted project path. Push, deploy, install, cleanup
and unrelated backlog pickup retain their own scope and authority requirements.

## Reconcile results

Compare every intended candidate with actual landing receipts and target state.
An empty queue or Git integrity check alone does not prove completion. Report:

- base/final target identities, integrated candidates and revision-bound checks;
- represented/superseded work with evidence, held items and concrete reasons;
- retained dirty work, branches/worktrees and recovery locations;
- coverage gaps and whether runtime verification, install, push or release occurred.

Report measured timing only for observed runs; label hypothetical costs and
speedups. Do not claim zero lost work without preservation/reconciliation evidence.
Return a partial result when acceptance requirements remain unresolved.
