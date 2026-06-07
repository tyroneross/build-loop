<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Task Capture Policy

Build-loop does not add a new durable task ledger by default. The active task
view is derived from existing surfaces through:

```bash
python3 scripts/task_surface.py --workdir "$PWD" --json
```

This answers "what is still open for this repo/branch?" without creating
another freeform cross-repo tracker.

## Current Surfaces

| Surface | Lifecycle | Owner |
|---|---|---|
| Plan `T-N` IDs | Planned work inside a specific build | Phase 2 plan + `plan_verify.py` |
| `.build-loop/state.json.execution` | Active run queue/in-flight/completed chunks | Orchestrator |
| Implementer working state | Current task/file/status while a worker is active | Implementer |
| Cost ledger `task_id` | Dispatch/return correlation and cost analysis | Orchestrator |
| Rally task heartbeat | Long-running task liveness and still-on-task health | Active terminal |
| `.build-loop/ux-queue/` | Review-discovered UX/test-coverage work for Phase 5 | Review-D/Iterate |
| `.build-loop/issues/` | Repo-local open issues detected during runs | Review/Learn |
| `.build-loop/followup/` | Deferred current-run items that should drain later | Report/queue drain |
| `.build-loop/backlog/` | Repo-local backlog items | Queue continuation |
| `.build-loop/proposals/` | Candidate self-review/improvement ideas, opt-in only | Learn/self-review |
| `build-loop-memory/projects/<slug>/backlog.md` | Durable project backlog | Memory writer / human backlog |
| TaskCreate/TaskUpdate list | Host-visible user-facing mirror | Orchestrator/session |

## Decision

Use `scripts/task_surface.py` as the canonical active view. It reads the
current repo's execution state, local queues, and project-scoped memory backlog,
then emits a priority-sorted JSON list with
`decision: "derived-active-view-no-new-ledger"`. It is read-only and writes no
ledger. Proposals are excluded by default because they are candidates, not open
tasks; pass `--include-proposals` for self-review sweeps.

Do not add `.build-loop/tasks.jsonl` until there is evidence that the derived
view cannot answer a real Phase 1 or coordination question. The failure mode to
avoid is a second source of truth where tasks close in one place and remain open
elsewhere.

## Promotion Rules

- Transient checklist items stay in the host task list and current run state.
- Deferred work becomes `.build-loop/followup/` or `.build-loop/backlog/` using
  the existing queue rules.
- Durable project work goes to `build-loop-memory/projects/<slug>/backlog.md`
  or milestones. Do not persist every subtask into memory.
- Completed/superseded tasks archive with rationale through the owning surface
  (followup/backlog archive, milestone, or decision), not silent deletion.

## Phase 1 Contract

Phase 1 may answer open-work questions by running:

```bash
python3 scripts/task_surface.py --workdir "$PWD" --json
```

If `open_count > 0`, surface the top active items by priority:
in-flight/queued chunks, UX queue, issues, followups, repo backlog, memory
backlog. Do not scan sibling project backlogs. Include proposals only when the
current task is specifically self-review, improvement triage, or proposal
cleanup.
