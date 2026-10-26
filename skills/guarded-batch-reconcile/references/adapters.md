<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Project adapters and Build Loop integration

Load only the matching adapter. These examples do not authorize mutations or
replace local procedures.

## Plain Git

Resolve repository identity, common Git directory and target explicitly. Useful
read-only inputs: `git worktree list --porcelain`, per-worktree
`git status --porcelain=v1 --untracked-files=all`, exact ref resolution,
`git merge-base --is-ancestor`, `git cherry <target> <candidate>` and patch diffs.
Inspect relevant ignored state separately before cleanup. Resolve coordination
state through its documented resolver, not an assumed worktree-relative path.

Use `git merge-tree` only when the installed Git supports the required invocation;
inspect conflicts and exit status. Verify the actual combined result in isolated
state. Follow the repo's merge/rebase/PR policy and existing serialization. Never
force-update a checked-out target to imitate a queue. If safe conditional target
advancement is unavailable, return a verified candidate for normal maintainer
landing. Git-only projects need neither Rally nor a task database; missing worker
receipts require running the necessary checks to establish readiness.

## Easy Terminal (optional)

Read current repository agent instructions, build/landing, coordination and
worktree-preservation procedures. Confirm options against the installed/source
interface before using these mappings:

| Portable step | Easy Terminal interface |
|---|---|
| Readiness | `et merge submit --branch <branch> --sha <sha> --tests "<attributed exact results>"`, with dependencies/build evidence as applicable |
| Planning | `et merge plan --batch <chosen-size>` |
| Ownership preview | `tools/merge_owner_check.py active-check ... --no-ask --json` |
| Guarded landing | `et merge next --batch <chosen-size>` |
| Reconciliation | queue entries, exact target identity and landing/gate receipts |

`--no-ask` previews risk without posting messages. Preview `asked`/`fyi` entries
need not be delivered messages; inspect posting evidence. The landing guard
rechecks ownership and can hold work or post messages under coordination policy.
Interpret own-work exemptions, stale claims and unknown liveness using the actual
guard. Full gates and affected verification have different coverage; follow the
current rules. App installation/restart is separate from integration.

## Hosted PR or merge queue

Use approved provider tools to read PR heads, reviews, check runs and queue state.
Bind checks to the head or synthetic merge revision they actually tested. Prefer
the platform queue/protection rules. Green head checks do not prove the combined
merge passed. Use supported queue retry/failure isolation rather than constructing
a second queue. Report submission, queue admission, landing and deployment separately.

## Non-Git project or artifacts

Map revisions to digests/version IDs, the target to the canonical artifact,
ownership to the writer mechanism, recovery to verified versioned snapshots,
and verification to artifact-specific checks. Batch only when the system supports
isolated combined validation and safe publication. Do not concatenate files as
a merge substitute or invent Git ancestry. When isolation or conditional
publication is missing, deliver inventory/proposed artifacts and name the gap.

## Build Loop ownership

This is an internal Repository Maintenance helper. The public Build Loop
entrypoint routes maintenance to `skills/repo-maintenance/SKILL.md`, which loads
this skill for parallel-work reconciliation; direct reconciliation and merge
planning references also link here. The generated skill index marks it hidden.

Reuse the parent's inventory, comparison and ref-goal ledgers, merge-risk scorer,
existing planner, canonical verification and strict closeout. Return exact
candidate/base/result identities, attributed checks and explicit holds. Loading
this helper does not prove the parent orchestrator or its independent review ran.

Build Loop retains orchestration, run recording, independent review and closeout
obligations. This helper does not schedule recurring work, authorize cross-project
messages, or grant push/install/deletion authority. Source integration, plugin
cache synchronization and host reload are distinct delivery steps.
