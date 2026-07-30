<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Design — Post-push Retrospective + Deferred-to-Backlog Auto-Iterate

**Date:** 2026-06-04
**Repo:** build-loop (v0.28.0)
**Status:** approved (design); implementation routes through `/build-loop:run`

## Goal

Two additions to build-loop, both firing automatically at the end of a run:

1. **Post-push retrospective** — after a build-loop run pushes, a background agent reads the
   session thread (up to the push) and the run state, and writes a structured lessons-learned
   retrospective.
2. **Deferred → backlog → auto-iterate** — when work is descoped during a run, triage it for
   product impact; product-impacting deferrals are assessed (causal-tree) and written to the
   backlog, and the end-of-run backlog drain is made automatic.

## Non-goals

- No retrospective for pushes made **outside** a build-loop run (single-trigger choice). A
  `PostToolUse(git push) + async:true` hook is the documented future extension; not built now.
- No marketplace/plugin **deploy** as part of this work. Commit locally only; deploy happens at a
  user-chosen restart boundary (deploying the in-use plugin GCs its cache version and kills live
  agents — `feedback_deploy_at_restart_boundary`).
- No refactor of build-loop's existing `nohup`-style Stop hooks to native `async:true` (valid but
  separate; opt-in).

## Discovery — what already exists (reuse, do not duplicate)

- **Descope capture** — orchestrator already writes descoped work to `.build-loop/followup/` +
  mirrors to the task list the moment it is deferred, and auto-drains `followup/` at end of run.
- **Backlog** — `templates/backlog-item.md` (repo-segmented), active dir `.build-loop/backlog/`,
  durable copy `build-loop-memory/projects/<slug>/backlog.md`. End-of-run drain into
  `issues/` then `backlog/` exists but is **gated** on `session_prefs.continue_from_queues=="always"`
  via `should_continue_into_queues()` in `scripts/context_bootstrap.py`.
- **Transcript scanners** — `scripts/scan_transcript_for_decisions.py` and `scan_corrections`
  (run on the **Stop** hook, backgrounded). `agents/transcript-pattern-miner.md` locates session
  JSONLs. `scan_corrections` already detects repeated user corrections.
- **Root cause** — `agents/root-cause-investigator.md` builds a **causal tree** (not linear
  5-Whys) and is bound to "name the missing system control, never blame the agent."
- **Phase 6 Learn** — cross-run pattern detection → experimental skills/agents.
- **Branch hygiene** — `scripts/collapse_run.py` + `createdRefs[]` ledger collapse a run's
  worktrees/branches back to `main` at Phase D Closeout.

## Stop-hook decision (why no hook for the retrospective)

Verified against current docs (code.claude.com/docs/en/hooks): a Stop hook is non-gating unless it
returns `exit 2` / `{"decision":"block"}` (which gates by *forcing continuation*, never by freezing).
Silent/parallel patterns: native `async:true`, or detach + return `{}`. **But** build-loop's own
history (`5c2a030`) established that **subagent Stop hooks do not fire reliably**, so it switched to
explicit in-flow dispatch. Therefore the retrospective uses **explicit in-flow dispatch by the
orchestrator after the closing push, run as a non-gating background job** — no hook.

## Feature 1 — Post-push retrospective

### Agent: `retrospective-synthesizer` (new, Sonnet)
Thread-judgment work (not regex), so Sonnet, not Haiku.

- **Tools:** Read, Bash, Grep, Glob.
- **Trigger:** `build-orchestrator`, immediately after the Phase 4 Report closing push, dispatches
  it non-gating (does not block run close).
- **Inputs:** session transcript up to the push (located via the transcript-pattern-miner locator),
  `.build-loop/state.json`, `intent.md`, `plan.md`, captured decisions, and the run's commit range.
- **Reuse:** `scan_corrections` output for "prompted ≥2×"; `root-cause-investigator` (or its causal-
  tree method inline) for the *Issues* section.

### Output sections (exactly as requested)
1. Lessons learned
2. Key takeaways
3. Recommendations
4. What could be done better
5. What went well
6. What went well by accident — split **planned-and-earned** vs **lucky/unplanned-good**
7. What should be enforced
8. User prompts this thread — and anything **prompted ≥2×** (flagged)
9. Issues — each traced to root cause via causal tree (system control, not actor blame)

**Enforce-loop:** anything prompted ≥2× or any section-7 enforce item → auto-drafted as an
enforce-candidate routed to the feedback/Phase-6 Learn lane (systems-not-discipline rule). Drafts
are candidates, never silently promoted.

### Where it writes (hierarchy)
- Active: `.build-loop/retrospectives/<YYYY-MM-DD>/<run-id>.md` (full) +
  `<run-id>.summary.md` (≤5 lines, surfaced inline to the user).
- Durable promotion: cross-project lessons → `build-loop-memory/projects/<slug>/retrospectives/`;
  enforce-items → `build-loop-memory/lessons/`.
- Template: `templates/retrospective.md` (new).

## Feature 2 — Deferred → backlog → auto-iterate

### Capture-time product-impact triage
When work is descoped/"do later" (the existing followup-capture point in the orchestrator), run a
lightweight triage: *is this product-impacting?* If yes:
- Assess via `root-cause-investigator` (causal tree): **why it matters** + the smallest fix.
- Write a backlog item using `templates/backlog-item.md` with two added fields:
  `product_impacting: true` and `impact: <one-line user-facing consequence>`.
Non-product-impacting deferrals stay in `followup/` as today.

### Drain — flip global default to "always" (user choice)
Change the shipped default so the end-of-run backlog drain + iterate fires automatically:
- `should_continue_into_queues()` / the `continue_from_queues` config default flips so an unset
  preference behaves as `"always"` (was: only `"always"` continued).
- Drain runs after / in parallel with the retrospective where file-ownership is non-conflicting.
- `PRODUCTION`/`DECISION`-classified items still surface (not auto-executed) — unchanged.

**Blast radius (⚠️):** every build-loop run now auto-iterates its backlog at end-of-thread.
Reversible per-repo via `continue_from_queues:"never"`. Surfaced in the run report's
`## Queue continuation` section.

## Folder hierarchy (parent principle — trackable)

```
.build-loop/
├── retrospectives/<YYYY-MM-DD>/<run-id>.md        # full retro
│                              /<run-id>.summary.md  # inline summary
├── backlog/<repo>/<id>-<slug>.md                   # existing, repo-segmented
└── followup/                                        # existing, in-run descopes
build-loop-memory/
├── projects/<slug>/retrospectives/                 # promoted durable lessons
├── projects/<slug>/backlog.md                      # existing durable copy
└── lessons/                                         # cross-project enforce-items
```

## Modular layout (folder per capability)

```
scripts/retrospective/
├── locate.py        # find the session transcript (reuse transcript-pattern-miner locator)
├── sections.py      # assemble the 9 sections from transcript + state + scan_corrections
├── synthesize.py    # entry point; writes active + summary; emits enforce-candidates
└── write.py         # active + durable promotion writers (atomic)
scripts/backlog/
├── triage.py        # product-impact yes/no + impact line
└── assess.py        # causal-tree assessment wrapper → backlog item
```

## Files to create / modify

**Create:** `agents/retrospective-synthesizer.md`, `templates/retrospective.md`,
`scripts/retrospective/{locate,sections,synthesize,write}.py`,
`scripts/backlog/{triage,assess}.py`, tests under `tests/`.

**Modify:** `agents/build-orchestrator.md` (post-push retro dispatch step; capture-time product-
impact triage; auto-drain wording), `scripts/context_bootstrap.py`
(`should_continue_into_queues` default), `templates/backlog-item.md` (add `product_impacting`,
`impact`), `skills/build-loop/SKILL.md` (Phase 4 Report → retrospective; queue-continuation default
note), `.build-loop/config.json` template default if applicable. Version bump handled by
Phase 4G auto-bump.

## Acceptance criteria (verifiable)

1. A build-loop run that pushes writes `.build-loop/retrospectives/<date>/<run>.md` containing all
   9 named sections, plus a `<run>.summary.md` surfaced inline. ✅ by inspecting output of a real run.
2. The retrospective dispatch is **non-gating**: run close is not delayed waiting on it (background).
   ✅ by code review of the dispatch + a timing check.
3. An item prompted ≥2× in the thread appears in section 8 AND produces an enforce-candidate file.
   ✅ by a fixture transcript test.
4. A descoped, product-impacting item lands in `.build-loop/backlog/<repo>/` with
   `product_impacting: true` + `impact:` + a causal-tree assessment. ✅ by unit test + a real run.
5. With no `continue_from_queues` set, the end-of-run backlog drain runs automatically (default flip).
   ✅ by unit test on `should_continue_into_queues` + integration.
6. `plan-verify` + `plan-critic` pass on the Phase 2 plan; `independent-auditor` runs at Review-A.
7. All new scripts have tests; `tests/` suite green under `env -u` (no rigged PYTHONPATH).

## Branch hygiene / merge-back (documented before fan-out)

- One run-worktree under `.build-loop/worktrees/run-<id>` (`bl/` branch prefix), collapsed to `main`
  as the single final decision via `collapse_run.py`. No per-approach branch sprawl.
- `createdRefs[]` ledger + human-readable `MERGE-BACK.md` written before any subagent/branch launch.
- Commit locally; **no marketplace deploy** mid-session — deploy at a restart boundary (final step,
  surfaced to user).

## Risks / rollback

- **Default-flip blast radius** — mitigated: reversible via `continue_from_queues:"never"`; surfaced
  in report.
- **Retrospective token cost** — Sonnet over a long thread; mitigate by bounding transcript window to
  the current run's turns and summarizing.
- **Self-modification recursion** — build-loop editing build-loop is safe (orchestrator loads from
  installed cache, not the worktree); only deploy is dangerous and is deferred.
