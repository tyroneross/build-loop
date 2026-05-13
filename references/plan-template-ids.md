# Plan task IDs — T-N convention

Per plan §15.2 of `~/.claude/plans/assess-build-loop-how-logical-quilt.md`.

## Why task IDs

Build-loop plans describe work as a series of tasks (chunks, commits, steps). Without stable IDs, downstream agents have no anchor:

- **Implementers** can't write `current_task_id` to `.build-loop/working-state/current.json`
- **commit-auditor** can't cite `spec_ref: plan:T-N` in its variance verdicts
- **alignment-checker** can't reference plan tasks in `matched_anchors`
- **Self-improvement-architect** can't mine task-duration patterns across runs

The T-N convention is a lightweight, deterministic anchor: every task in a plan gets a stable identifier `T-1`, `T-2`, ..., `T-N`.

## Convention

### Format

- Prefix: literal `T-`
- Suffix: integer ≥ 1
- Examples: `T-1`, `T-2`, `T-42`

### Where IDs appear

In a plan, IDs appear in **two places**, both required when the convention is in use:

1. **Task table** at the top of the plan or a dedicated `## Tasks` / `## Chunks` section:

```markdown
## Tasks

| ID | Title | Files | Estimated |
|---|---|---|---|
| T-1 | Pin assessors to Sonnet | agents/*.md (5) | XS |
| T-2 | Add judge_decisions schema | scripts/write_run_entry.py | S |
| T-3 | Constitution + Phase 1 load | agents/build-orchestrator.md | M |
```

2. **Inline at each task's detail heading** further down in the plan:

```markdown
### T-1: Pin assessors to Sonnet

Change `model: inherit` → `model: sonnet` in five agent files...

### T-2: Add judge_decisions schema

Extend `scripts/write_run_entry.py` with a new optional flag...
```

### Validation rules (enforced by `scripts/plan_verify.py rule_task_id_convention`)

The rule fires only when at least one `T-\d+` appears in the plan (opt-in convention):

- **Uniqueness** — every `T-N` value appears once in task-defining contexts (table rows + detail headings)
- **Sequential** — IDs start at `T-1` and increase by 1 with no gaps (`T-1, T-2, T-3` ✅; `T-1, T-3` triggers WARN)
- **Table + detail symmetry** — every ID in the task table has a matching detail heading and vice versa

Severity: WARN. Plans can still be approved with task-ID issues; the orchestrator and judges degrade gracefully. The WARN exists so plan authors notice the audit trail will be incomplete.

### Cross-chunk task IDs (multi-chunk plans)

For plans dispatching multiple parallel chunks (Mode A fan-out, per `agents/build-orchestrator.md`), task IDs are global to the plan, not per-chunk:

```markdown
## Tasks

| ID | Chunk | Title |
|---|---|---|
| T-1 | c1 | implement rate-limit middleware |
| T-2 | c1 | wire into route handler |
| T-3 | c2 | add tests |
| T-4 | c3 | update API docs |
```

This lets the orchestrator pass `task_ids_in_scope: ["T-1", "T-2"]` to the c1 implementer brief.

### What the IDs unlock

| Surface | Use |
|---|---|
| `.build-loop/working-state/current.json` | Implementers write `current_task_id`, `next_task_id` |
| `.build-loop/working-state/log.jsonl` | Append-only history of task transitions |
| `judge_decisions[].variances[].spec_ref` | Cite as `plan:T-N` |
| `judge_decisions[].policy_refs` | Mix `rubric:rN`, `constitution:C-X/...`, `plan:T-N`, `memory:<slug>` |
| `alignment-checker` `matched_anchors` | Cite task IDs anchoring an aligned finding |
| Phase 6 Learn | Mine task durations + file-touch sequences from `working-state/log.jsonl` |

### Migration for existing plans

Plans without T-N IDs continue to work — the convention is opt-in. Phase 6 Learn pattern detection will surface plans that would benefit most (high judge_decisions churn, frequent alignment misses) so authors can retrofit one plan at a time.

The `commit-auditor` and `alignment-checker` agents read working-state if present but fall back gracefully when `current_task_id` is empty (cite by file path + line range instead of task ID).
