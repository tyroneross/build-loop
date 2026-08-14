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
- Outcome-bound executable work becomes `.build-loop/queue/` or a current-run
  issue/followup. Deferred work becomes a classed `.build-loop/backlog/items/`
  record and stays outside the queue until eligible promotion.
- Durable project work goes to `build-loop-memory/projects/<slug>/backlog.md`
  or milestones. Do not persist every subtask into memory.
- Completed/superseded tasks archive with rationale through the owning surface
  (followup/backlog archive, milestone, or decision), not silent deletion.

## Phase 1 Contract

Phase 1 may answer open-work questions by running:

```bash
python3 scripts/task_surface.py --workdir "$PWD" --json
```

If `open_count > 0`, surface executable work first, then separately label
planned pickup, gated initiatives, and workstream-relevant decisions. Do not
represent backlog records as queued tasks. Do not scan sibling project backlogs. Include proposals only when the
current task is specifically self-review, improvement triage, or proposal
cleanup.

## Cross-Agent Durable Lane: Operations Center

Everything above is repo-scoped and derived. Work that must outlive a session,
or be visible to a *different agent runtime*, belongs in Operations Center — the
single durable cross-agent ledger. OC reaches every runtime over MCP
(`oc mcp`, stdio), so Claude Code, Codex, and any other MCP-speaking host read
and write the same queue.

| Surface | Scope | Durability | Source of truth? |
|---|---|---|---|
| Operations Center | cross-repo, cross-agent, cross-session | durable (SQLite) | **yes** |
| `task_surface.py` view | one repo/branch | derived, recomputed | no — a view |
| Host `TaskCreate`/`TaskUpdate` | one session | dies with the session | **no — a mirror** |

The host task list is a *display* of what is already in OC or in the run's own
execution state. It is never the place work is recorded first. If an item exists
only in the host task list at the end of a session, it has been lost — that is
the "second source of truth" failure this policy exists to prevent.

MCP tools on the OC lane: `create_task`, `list_tasks`, `get_task`, `claim_task`,
`update_status`, `add_receipt`, `plan_tasks`, `update_ledger`, `agent_stats`.

## Priority: approve the list once, then delegate

Priority ordering belongs to the human. Agent autonomy operates *inside* an
ordering the human already accepted, not over it.

**The gate.** A proposed task list — new tasks, or a re-ranking of existing ones
— is presented to the user for approval before it becomes the working order.
This gate fires once per list, not once per task.

**After approval, agents operate independently.** Within the approved list an
agent sets working priority, claims, sequences, and executes without returning
for permission. Do not re-ask what the approved list already answers.

**Agents MAY re-prioritize** when one of these grounds holds, and only these:

| Ground | Meaning |
|---|---|
| `blocked` | The item cannot progress — missing credential, external dependency, upstream failure. |
| `contention` | Another agent is working it, or ownership is unclear and proceeding risks duplicate or conflicting work. |
| `dependency` | A lower-priority item is a prerequisite for, or materially improves, a higher-priority one. Raise the prerequisite. |

Every re-prioritization records the ground and the reasoning as a receipt via
`add_receipt`, so the ordering stays auditable and the human can see why their
approved order changed underneath them. A silent re-rank is a policy violation.

**Tasks discovered mid-flight** enter the queue at or below the default priority
and are flagged for the next approval pass. New work does not outrank approved
work until the human has seen it. Discovery is not authorization.
