# Codex Review: Build Loop Use And Issues, 2026-06-18 To 2026-07-02

## Bottom Line

Build Loop's weakest current boundary is closure, not intent. The repo has strong
doctrine for autonomy, model tiering, Rally coordination, Review-E simplification,
and recursive learning, but the last two weeks show that several contracts are
still advisory unless a deterministic consumer records and enforces them.

The top update is to make run/judge/learn closure executable: a build that touched
code should not finish as review-complete until the run ledger, independent judge
status, learning objects, Rally handoffs, and evidence citations are reconciled.

## Evidence Reviewed

- Codex local rollouts for this repo, 2026-06-18 through 2026-07-02: 6 sessions.
- Claude Code local project JSONL for this repo in the same window: 5 active
  transcript files plus subagent metadata.
- Rally room/change logs in the window: 2255 entries.
- Git history in the window: 107 non-merge commits.
- `.build-loop/state.json`, `.build-loop/judge-decisions.json`,
  `.build-loop/learning-objects.json`, `.build-loop/proposals/`,
  model taxonomy/resolver files, and build-loop-memory recall hits.
- Live Rally state at review time, including open handoffs and file claims.

## Findings And Updates

1. Run and judge closure is the highest-impact gap.

   Evidence: `.build-loop/state.json` has 8 historical runs but no in-window run
   entries, while git/Rally show heavy activity. `.build-loop/judge-decisions.json`
   has one current decision and `auditor_status: not-run:parent-must-dispatch`.

   Update: add a closeout reconciler that compares commits, Rally artifacts,
   judge-decisions, and `state.json.runs[]` since the last run record. A final
   Review-G pass should fail or downgrade to `partial` when code changed and any
   of these are unresolved: missing run entry, `parent_must_dispatch`, unresolved
   owned handoff, or learning-object output not consumed.

2. Codex parity needs managed-session state, not just room visibility.

   Evidence: Rally repeatedly records `duplicate-active-squad-id: codex` and
   `unmanaged-agent: codex`; Codex direct inbox items stayed unread across runs.
   This matches prior memory that Rally room presence does not prove delivery.

   Update: Codex startup should use a unique session id and either adopt/register
   the running surface or explicitly mark itself `polling-only`. The closeout gate
   should require explicit ACK/dispose for direct handoffs, not just a room read.

3. Rally evidence boundary is known but still needs enforcement at report time.

   Evidence: the docs now state Rally is coordination metadata, and a recent user
   correction called out Codex treating Rally as proof. The risk is behavioral,
   not missing prose.

   Update: add a report lint: any final claim sourced from Rally must be labeled
   `peer-authored coordination` or backed by git/tests/manifests/GitHub/package
   registry evidence. Rally may discover work; it must not verify truth.

4. Agent and skill activation is under-measured.

   Evidence: Claude used several subagents, but many were `general-purpose` even
   when role-specific agents exist. Codex sessions used shell tooling only
   (`exec_command`, `write_stdin`, `update_plan`) and no worker/subagent path.

   Update: Phase 1 should record expected capability surfaces for the task
   (`skills_expected`, `agents_expected`, `scripts_expected`) and Review-G should
   record actual invocations. Misses should become `activation_miss` learning
   signals, especially when a task failed or required user correction.

5. Model tiering doctrine exists, but actual use is expensive and coarse.

   Evidence: Codex used `gpt-5.5` with `xhigh` effort for all observed sessions.
   Claude parent traffic heavily used Fable, with Opus and Sonnet subagents. Local
   docs already say to use the lowest tier that produces a verifiable output.

   Update: make the default order executable: deterministic script first, Pattern
   or Code tier for extraction/classification, Thinking tier for hard synthesis,
   Frontier tier for plan selection, independent verdicts, and high-stakes
   architecture calls. Add a per-run model-cost summary by task class so Fable
   and future GPT high-cost tiers are reserved for work they uniquely improve.

6. Simplification should optimize cognitive load without quality loss.

   Evidence: Review-E already exists, and `complexity_detector.py` already finds
   hotspots. The backlog/proposals show churn and user-correction clusters; raw
   line count alone would mislead because some repeated code can be simpler than
   a brittle abstraction.

   Update: score simplification candidates on equal-or-better behavior,
   readability, debuggability, testability, performance, observability, and
   future flexibility. Prefer deletion only when those axes do not regress. Rank
   candidates by convergence of signals: high churn + user correction + failing
   tests + large/complex file + duplicated path.

7. `/retro` is the correct user-facing route for deep recursive learning.

   Evidence: the current Rally next item asks for `commands/retro.md` plus the
   `recursive-retrospective` user-invocable flip. SessionEnd already points humans
   at `/retro`, but the command is absent and the skill is currently not
   user-invocable.

   Update: implement the command in the active owner lane. Acceptance should be:
   command frontmatter valid, `/retro` loads `build-loop:recursive-retrospective`,
   `skills/recursive-retrospective/SKILL.md` has `user-invocable: true`, and the
   surface-policy tests/docs include `recursive-retrospective` as an intentional
   Claude public entrypoint while Codex remains single-entry.

8. Common failure classes are recurring enough to deserve closure tests.

   Evidence across memory and logs: dormant records with no consumer, docs
   claiming runtime behavior without tests, stale generated artifacts, Python env
   drift, worktree/session coordination drift, empty retrospectives, and
   parent-dispatch obligations that persist after the child run reports.

   Update: treat each as a closure-test family. The fix is complete only when the
   real signal would have prevented, detected, or contained the exact failure, not
   when a new note or optional detector exists.

## Coordination Notes

- Codex posted a Rally handoff to Claude Code with these findings and the
  intended no-regret fixes.
- Codex did not edit `commands/retro.md` or
  `skills/build-loop/references/phase-4-review.md` because Rally reported
  conflicting Claude ownership for those paths.
- If Claude lands the `/retro` command, Codex agrees it is safe as long as Codex's
  slim plugin artifact remains single-entry and the public-surface tests reflect
  the host-specific distinction.

## Recommended Implementation Order

1. Land `/retro` in the active owner lane and update policy/tests.
2. Add run/judge/learn closeout reconciliation and make `parent_must_dispatch`
   impossible to finalize as `pass`.
3. Add report lint for Rally-as-evidence misuse.
4. Add capability activation telemetry.
5. Add model-cost/task-class telemetry and wire the existing model-bakeoff skill
   into Phase 6 Learn for sampling, not every run.
6. Upgrade Review-E with the quality-preserving simplicity rubric above.
