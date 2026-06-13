<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 6: Learn (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full Learn phase: pattern detection, experimental skill drafting, and sample review sweep.

## Phase 6: Learn — Cross-Build Pattern Detection (mandatory; always runs and always reports)

**Goal**: detect recurring patterns across recent runs, auto-draft experimental skills/agents to address them, surface them for keep/remove decisions. Closes the loop between "build N times" and "build N+1 is faster because we learned."

**Load the `build-loop:self-improve` skill for the full protocol.** (Skill keeps its existing name for backward compatibility; this phase was named "Self-Improvement Review" in v0.2.0 — renamed here to avoid collision with Phase 4 Review.)

**Mandatory contract (v0.30.0+).** Every Phase 6 always does three things: (a) dispatches the Haiku detector (cheap), (b) runs `consolidate_memory.py` + `procedural_governance.py --mode detect-patterns` (already unconditional), and (c) emits a `## Learn` outcome line in the Review-G report — even when nothing crosses threshold. Net marginal cost over the prior gated path is one cheap Haiku state-scan per run. The expensive arm (Sonnet draft + Opus signoff) stays conditional on `runs[] >= 3` AND a pattern crossing threshold AND not-deferred. Also user-invokable via `/build-loop:self-improve` to run a scan without a build.

Quick flow:

1. **Detect** — dispatch `recurring-pattern-detector` (Haiku). Reads **two signal sources**:
   - **Signal 1**: `.build-loop/state.json.runs[]` — emits `phase_failure`, `manual_intervention`, and `security_finding` patterns (real pain signals; `diagnostic_repeat` and `file_churn` were removed to prevent skill sprawl). **`runs[]` is written by the orchestrator's Review-G, so an INLINE run (skill-as-methodology, no orchestrator dispatch) records nothing and is invisible to Learn.** Any run-close path that did not go through Review-G — inline runs, the memory closeout — MUST record the run with `python3 scripts/append_run.py --workdir "$PWD" --run-id <id> --goal "..." --outcome <done|partial|blocked> [--manual-intervention "<phase>:<note>"] [--phase "<id>:<status>"]` (append-only, idempotent on `run_id`). Without it, inline work never accrues toward the `runs[] >= 3` threshold and recurring inline pain (e.g. the user re-prompting for a skipped step) never becomes a `manual_intervention` pattern.
   - **Signal 2**: `.build-loop/proposals/enforce-from-retro/*.md` (the post-push retrospective's enforce-candidates) — emits `enforce_recurrence` patterns when the same normalized candidate signature appears across ≥ 2 distinct run-ids. The orchestrator may cite `python3 scripts/enforce_retro_signals.py --workdir "$PWD" --json` as pre-computed input to the agent. This delivers "anything prompted/needed repeatedly → enforce" **across** sessions, not just within one.
2. **Filter** — keep only `confidence: high` or `count >= 4`; manual interventions at lower threshold. Dedupe against existing active/experimental skill names. Cap 2 artifacts per scan.
3. **Draft** — for each kept pattern, dispatch `self-improvement-architect` (Sonnet). Writes to `.build-loop/skills/experimental/<name>/SKILL.md` with an A/B Experiment section including `run_id` and `co_applied_experimental_artifacts[]` schema.
4. **Signoff** — orchestrator (Opus 4.7) reviews each draft: APPROVE / REVISE (1 retry) / DISCARD.
5. **Sample review sweep** — for artifacts in `.build-loop/skills/experimental/` from prior runs: if `.build-loop/config.json.autoPromote` is true AND effective (non-confounded) sample ≥ 8 AND target met → eligible for promotion. **Promotion is no longer silent.** Each eligible candidate goes through (a) advisory review by `promotion-reviewer` (Opus, agent), (b) async user confirmation via PushNotification + TaskCreate fallback. The reviewer's variance verdict (approve / rethink / new_approach) becomes the body of the notification. Move from `experimental/` to `active/` happens only after the user confirms via `/build-loop:promote-experiment <name>`. Regressions and inconclusive-at-2N write proposals to `.build-loop/proposals/` for user confirmation — never auto-delete.

   **Promotion-reviewer dispatch protocol** (per advisory-judge design, plan §12 / `agents/promotion-reviewer.md`):
   - For each eligible candidate, dispatch `Agent(subagent_type="build-loop:promotion-reviewer", ...)` with brief fields: `artifact_path`, `experiment_log`, `sample_size`, `target_metric`, `triggering_run_id`, `recent_judge_decisions`.
   - Append the returned verdict object to the run's `judge_decisions[]` via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_run_entry/__main__.py --judge-decisions-json <verdict.json>` (combined with other Phase 4 verdicts if any).
   - Compose the user-facing notification body from the verdict's `variances[]` and `meta_guidance`. Fire `PushNotification` if available; fall back to `TaskCreate` with subject `"[BUILD-LOOP] Promotion candidate <name>: <verdict> — review needed"`.
   - Write a marker file at `.build-loop/proposals/<name>.pending.md` with the verdict + how-to-confirm so the user can resume context later (TTL 14 days; lapsed candidates auto-archive to `.build-loop/proposals/<name>.lapsed.md`).
   - **Do not move the artifact.** The user-invoked `/build-loop:promote-experiment <name>` command performs the move after reading the pending verdict.

6. **Notify** — concise synthesis appended to Review sub-step G report, including: (a) removal command for each artifact moved or proposed, (b) for each pending promotion: the reviewer verdict + confirmation command.

**Always-run + report gating (v0.30.0)**

Phase 6 has NO "skip entirely" condition. Three outcome states cover every run:

| State | Trigger | What runs | Review-G `## Learn` line |
|---|---|---|---|
| **Accruing** | `runs[] < 3` | Detector + consolidation only (no Sonnet draft) | `Learn: accruing (N/3 runs)` |
| **Deferred** | debug-only (`closeout: false` in dispatch envelope) OR budget-exhausted (`budget_check` envelope `action == "finalize_and_stop"` at Phase 6 entry) | Detector + consolidation; write `.build-loop/proposals/learn-deferred-<run-id>.md` marker with `{reason, runs_count, budget_action}`; skip Sonnet draft + Opus signoff | `Learn: deferred — <reason>` |
| **Full** | `runs[] >= 3` AND detector returned a pattern AND not deferred | Detector + consolidation + Sonnet draft + Opus signoff + sample sweep | `Learn: <N> patterns drafted` (or `Learn: 0 patterns above threshold (N runs scanned)` when detector returned nothing) |

**Deprecated escape hatch (migration no-op).** `.build-loop/config.json.autoSelfImprove: false` is no longer honored. It is read for migration safety: when present and `false`, the orchestrator appends a one-line `state.json.warnings[]` entry (`"autoSelfImprove: false is deprecated; ignored (migration no-op)"`) and proceeds as if the key were absent. Old user configs do not error. Remove the key at your convenience.

**User control (unchanged safety boundary)**:
- Remove any artifact: `rm -rf .build-loop/skills/experimental/<name>/` or `active/<name>/`
- Block re-promotion of a name: add it to `.build-loop/skills/.demoted`
- Inspect tracking: `cat .build-loop/experiments/<name>.jsonl`
- Promotion to `active/` STILL requires explicit `/build-loop:promote-experiment <name>` (decision-3 safety boundary preserved — auto-promote of unreviewed drafts never happens).
- Auto-promote defaults to OFF — set `"autoPromote": true` to enable (requires effective sample ≥ 8).

- Consumer default — learned drafts route to `~/.build-loop-extensions/pending/` via `scripts/extensions_route.py --name <ext-slug> --file <draft>`; they do not load until `scripts/extensions_approve.py` moves them into `plugin/`. (Maintainer routing: P2.)

**What this phase will NOT do**:
- Modify the build-loop plugin repo
- Promote artifacts cross-project without explicit `/build-loop:promote-experiment <name>`
- Run more than once per build
