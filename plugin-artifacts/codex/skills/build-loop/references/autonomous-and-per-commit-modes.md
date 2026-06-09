<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# Autonomous Mode + Per-Commit Mode (conditional detail)

Extracted from `SKILL.md` (WP-A, 2026-06-09): both modes are conditional — their
tables and contracts are load-bearing only when the mode is active, yet they
loaded in every session. The SKILL.md body now carries a one-line pointer to
each; the full detail lives here and loads on demand.

## Autonomous Mode (Queue-Drain Loop)

Autonomous mode generalizes Phase 5 Iterate into a self-replenishing worker that drains its own `ux-queue/` + `issues/` + `proposals/`, alignment-checks each item against the original intent, executes the aligned subset, and commits in batches until the queue is empty or the wall-clock budget elapses. Default since this mode shipped (`--autonomous=false` opts back to classic one-pass).

**End-of-run backlog/issues drain — SHIPPED DEFAULT 2026-06-04**: every run now auto-drains `.build-loop/issues/` then `.build-loop/backlog/` at end-of-thread without asking. Reversible per-repo via `.build-loop/config.json`:

```json
{ "sessionPrefs": { "continueFromQueues": "never" } }
```

`PRODUCTION`/`DECISION`-classified items still surface (not auto-executed). The continuation runs the same alignment-checker + scope-auditor + independent-auditor wiring as the in-run iterate loop. Stop conditions: iterate-cap (25 autonomous / 5 classic), budget exhausted, PRODUCTION encountered, 5 consecutive iterate failures, explicit user pause. Surfaced in the run report's `## Queue continuation` section.

### Flag surface

| Invocation | Effect |
|---|---|
| `/build-loop:run "goal text"` | default mode, 2h budget, autonomous=true |
| `/build-loop:run --long "goal text"` | long mode, 8h budget |
| `/build-loop:run --budget 4h "goal text"` | custom budget (overrides `--long`) |
| `/build-loop:run --budget 30m "goal text"` | accepts `30s`, `30m`, `4h`, or bare integer seconds |
| `/build-loop:run --autonomous=false "goal text"` | classic single-pass; queue items become `followup/` |
| `/build-loop:run "overnight refactor of auth ..."` | keyword `overnight` → long mode, 8h |

**Flag precedence (strict, top wins):**

1. `--budget <duration>` — explicit duration always wins; mode tagged `custom`.
2. `--long` — sets mode `long`, budget 8h.
3. Keyword detection in goal text — only when `--long` not explicitly set.
4. Default — mode `default`, budget 2h, autonomous=true.

`--autonomous=false` is orthogonal: it can combine with any budget flag but disables the queue-drain loop entirely. With autonomous off, `--budget` still tracks wall-clock but the orchestrator runs classic Phase 1–6 once and reports.

### Keyword fallback

Case-insensitive whole-word match against the goal text (or `intent.update_intent`). Detection runs ONLY when `--long` is not explicit on the command line. The flag always wins over keyword inference.

| Keyword | Example phrasings |
|---|---|
| `long` | "long refactor of …" |
| `long-running` | "long-running migration" |
| `overnight` | "overnight build" |
| `large-scale` | "large-scale rewrite" |
| `multi-day` | "multi-day backfill" |

Keyword list is configurable via `.build-loop/config.json.autonomy.keywordsLong[]`. The default list above is hard-coded in the orchestrator.

### Budget tracking

The orchestrator writes `state.execution.budget` at autonomous-mode start:

```json
{
  "mode": "default | long | custom",
  "started_at": "<iso8601 UTC>",
  "deadline_at": "<iso8601 UTC>",
  "last_checkin_at": "<iso8601 UTC> | null",
  "commits_since_push": 0,
  "checkin_interval_pct": 50
}
```

`scripts/budget_check.py` reads this block at every iterate-loop entry, every commit, and every phase boundary, returning a routing envelope (`continue | checkin | finalize_and_stop`). The script is informational — exit 0 always; sub-5ms compute.

**Resume contract**: when a budget block exists and the run resumes via `--resume <run_id>`, the orchestrator MUST reuse the original `deadline_at`. A 2h budget that crashed at 1h59m does NOT get a fresh 2h. `scripts/resume_resolver.py._resolve_budget_on_resume()` is the single source of truth for this rule and surfaces the preserved budget under `budget_resume.preserve_deadline: true`.

### Iteration caps

| Mode | Per-build cap | Per-item cap |
|---|---|---|
| Classic (autonomous=false) | 5 | n/a |
| Autonomous default | 25 | 3 same-verdict |
| Autonomous long | 25 | 3 same-verdict |

`maxIterateAttemptsAutonomous` is configurable in `.build-loop/config.json.autonomy.maxIterateAttemptsAutonomous`.

### Question timeout (autonomous auto-decide)

In autonomous / `--long` mode a question that would otherwise block on the human auto-resolves if unanswered within a window, so an unattended run never stalls. When the orchestrator surfaces such a question it states a **recommended default** + a deadline; `scripts/question_timeout.py` is consulted (e.g. on a `ScheduleWakeup` resume) and returns `answered | take_default | wait`. On `take_default` the orchestrator takes the recommended option, records it to `state.execution.autonomousDefaults[]` + `auto-decision-capture`, continues, and lists every auto-decided question in the end-of-run readback for override (prefer the reversible option when deciding).

**Never auto-resolves — waits indefinitely:** production push, destructive/irreversible delete, and anything the autonomy gate verdicts `confirm`/`block` (gates #1–#2 in `agents/build-orchestrator.md`). Only reversible / `user_impact: major` decisions (gate #3) and steering clarifications time out — the single production gate is preserved.

Config (`.build-loop/config.json.autonomy`): `questionTimeoutMinutes` (default 10), `onTimeout` (`decide_default` default | `wait`).

### Per-Phase A constraint

Phase A (current ship) wires queue drain + alignment-check + time budget. **Pushes stay manual** — `scripts/autonomous_push.py` and the K-commit batch-push policy ship in Phase B. The `should_push_now` field returned by `budget_check.py` is informational in Phase A; the orchestrator surfaces it in check-ins but does not push autonomously yet.

## Per-Commit Mode (Self-Recursive Builds)

Per-commit mode splits a multi-commit build into one independent orchestrator dispatch per commit, so each commit reviews and lands cleanly before the next one starts. It activates automatically when the working directory IS the runtime — that is, when the user is editing the build-loop plugin itself (or any plugin whose runtime symlink points back to the working tree). It can also be explicitly opted into or out of via skill arguments.

### Detection

Phase 1 Assess writes `selfRecursive.enabled: true|false` to `.build-loop/state.json` (commit 1 wired this via `scripts/detect_self_recursive.py`). The skill body MUST read this field BEFORE deciding which dispatch shape to use. If the field is absent, treat it as `false`.

### Mode Resolution

| Skill arg | `selfRecursive` | Resulting mode |
|---|---|---|
| `--per-commit` (explicit) | either | per-commit |
| `--no-per-commit` (explicit) | either | single-orchestrator |
| (none) | true | per-commit (default for self-recursive) |
| (none) | false | single-orchestrator (today's behavior) |

Passing both `--per-commit` and `--no-per-commit` is a user error — fail loud with a one-line message naming the conflict and stop before any dispatch.

### Dispatch Contract (Per-Commit Mode)

1. **Plan first, dispatch many.** The skill body invokes a single planning orchestrator (Phase 1 Assess + Phase 2 Plan only). Its return must include a per-commit work list at `.build-loop/per-commit-plan.json` with this exact JSON shape:

   ```json
   {
     "run_id": "run_<UTC>_<hash>",
     "commits": [
       {
         "id": "c1",
         "subject": "feat(scripts): add foo helper",
         "scope": "...",
         "files_planned": ["scripts/foo.py", "tests/test_foo.py"],
         "spec": "verbatim packet for the implementer orchestrator",
         "depends_on": []
       }
     ],
     "branch": "feat/...",
     "from_branch": "main"
   }
   ```

2. **Per-commit orchestrator dispatch.** For each commit in the plan (respecting `depends_on`), the skill body dispatches a fresh `Agent(subagent_type="build-loop:build-orchestrator", ...)` carrying ONLY that commit's packet plus a `PER_COMMIT_DISPATCH: { commit_id, run_id, prior_commit_hashes }` prompt prefix. Each dispatched orchestrator runs Phase 3 Execute + Phase 4 Review for ITS commit only, then commits and returns. The dispatched orchestrator's behavior on the prefix is documented in `agents/build-orchestrator.md` §0a.

3. **Aggregate.** The skill body collects each orchestrator's return envelope and writes a final report combining all commits' results. On partial failure (commit N fails), do NOT dispatch downstream commits; retain `.build-loop/per-commit-plan.json` so a subsequent `/build-loop:run --resume` invocation can pick up where it stopped. **Parent-dispatch contract (GAP-1):** the dispatcher (this skill body) HAS the Agent tool, so it is the parent that owes the audit. For every returned envelope whose `auditor_status` is `not-run:parent-must-dispatch` or `cross-vendor-deferred`, the dispatcher MUST — before declaring that commit/run review-complete — dispatch `Agent(subagent_type="build-loop:independent-auditor")` on that commit's diff range, append the verdict to `.build-loop/judge-decisions.json`, and re-run `write_run_entry --scope build` so the review-completeness gate passes. A nested per-commit orchestrator cannot audit itself; the audit is the dispatcher's responsibility, not an optional step.

   **Parent owes Phase 6 Learn + retrospective (E3).** A stop-early dispatch that never reaches Phase 4 Review-G can't run Phase 6 Learn or the post-push retro — so the dispatching parent owes them at close, under the same parent-dispatch contract as GAP-1 (not optional, name the owner at dispatch). Full contract: `agents/build-orchestrator.md` §Phase 4 A (E3 block).

### State.json schema

The per-commit dispatcher tracks its own progress under a `perCommit` block alongside the existing `execution` block:

```json
{
  "perCommit": {
    "enabled": true,
    "mode_source": "self_recursive_default|explicit_flag|opt_out",
    "plan_path": ".build-loop/per-commit-plan.json",
    "completed": [{"commit_id": "c1", "hash": "abc123", "completed_at": "..."}],
    "in_flight": "c2",
    "queued": ["c3"]
  }
}
```

M2's `execution.iterate_attempt` continues to track per-commit-orchestrator attempt counters (each dispatched orchestrator manages its own iterate counter) — do not duplicate iteration tracking inside `perCommit`.
