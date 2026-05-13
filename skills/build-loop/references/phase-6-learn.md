# Phase 6: Learn (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full Learn phase: pattern detection, experimental skill drafting, and sample review sweep.

## Phase 6: Learn — Cross-Build Pattern Detection (optional)

**Goal**: detect recurring patterns across recent runs, auto-draft experimental skills/agents to address them, surface them for keep/remove decisions. Closes the loop between "build N times" and "build N+1 is faster because we learned."

**Load the `build-loop:self-improve` skill for the full protocol.** (Skill keeps its existing name for backward compatibility; this phase was named "Self-Improvement Review" in v0.2.0 — renamed here to avoid collision with Phase 4 Review.)

Runs automatically after Review sub-step G on every build unless disabled. Also user-invokable via `/build-loop:self-improve` to run a scan without a build.

Quick flow:

1. **Detect** — dispatch `recurring-pattern-detector` (Haiku). Reads `.build-loop/state.json.runs[]`, returns JSON list of patterns crossing confidence threshold. Only emits `phase_failure` and `manual_intervention` types (real pain signals); `diagnostic_repeat` and `file_churn` were removed to prevent skill sprawl.
2. **Filter** — keep only `confidence: high` or `count >= 4`; manual interventions at lower threshold. Dedupe against existing active/experimental skill names. Cap 2 artifacts per scan.
3. **Draft** — for each kept pattern, dispatch `self-improvement-architect` (Sonnet). Writes to `.build-loop/skills/experimental/<name>/SKILL.md` with an A/B Experiment section including `run_id` and `co_applied_experimental_artifacts[]` schema.
4. **Signoff** — orchestrator (Opus 4.7) reviews each draft: APPROVE / REVISE (1 retry) / DISCARD.
5. **Sample review sweep** — for artifacts in `.build-loop/skills/experimental/` from prior runs: if `.build-loop/config.json.autoPromote` is true AND effective (non-confounded) sample ≥ 8 AND target met → eligible for promotion. **Promotion is no longer silent.** Each eligible candidate goes through (a) advisory review by `promotion-reviewer` (Opus, agent), (b) async user confirmation via PushNotification + TaskCreate fallback. The reviewer's variance verdict (approve / rethink / new_approach) becomes the body of the notification. Move from `experimental/` to `active/` happens only after the user confirms via `/build-loop:promote-experiment <name>`. Regressions and inconclusive-at-2N write proposals to `.build-loop/proposals/` for user confirmation — never auto-delete.

   **Promotion-reviewer dispatch protocol** (per advisory-judge design, plan §12 / `agents/promotion-reviewer.md`):
   - For each eligible candidate, dispatch `Agent(subagent_type="build-loop:promotion-reviewer", ...)` with brief fields: `artifact_path`, `experiment_log`, `sample_size`, `target_metric`, `triggering_run_id`, `recent_judge_decisions`.
   - Append the returned verdict object to the run's `judge_decisions[]` via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_run_entry.py --judge-decisions-json <verdict.json>` (combined with other Phase 4 verdicts if any).
   - Compose the user-facing notification body from the verdict's `variances[]` and `meta_guidance`. Fire `PushNotification` if available; fall back to `TaskCreate` with subject `"[BUILD-LOOP] Promotion candidate <name>: <verdict> — review needed"`.
   - Write a marker file at `.build-loop/proposals/<name>.pending.md` with the verdict + how-to-confirm so the user can resume context later (TTL 14 days; lapsed candidates auto-archive to `.build-loop/proposals/<name>.lapsed.md`).
   - **Do not move the artifact.** The user-invoked `/build-loop:promote-experiment <name>` command performs the move after reading the pending verdict.

6. **Notify** — concise synthesis appended to Review sub-step G report, including: (a) removal command for each artifact moved or proposed, (b) for each pending promotion: the reviewer verdict + confirmation command.

**Skip** when:
- `.build-loop/state.json.runs[]` has fewer than 3 entries
- Detector returns no patterns crossing threshold
- User has set `.build-loop/config.json.autoSelfImprove: false`

**User control**:
- Remove any artifact: `rm -rf .build-loop/skills/experimental/<name>/` or `active/<name>/`
- Block re-promotion of a name: add it to `.build-loop/skills/.demoted`
- Inspect tracking: `cat .build-loop/experiments/<name>.jsonl`
- Disable Learn entirely: `.build-loop/config.json` → `{"autoSelfImprove": false}`
- Auto-promote defaults to OFF — set `"autoPromote": true` to enable (requires effective sample ≥ 8)

**What this phase will NOT do**:
- Modify the build-loop plugin repo
- Promote artifacts cross-project without explicit `/build-loop:promote-experiment <name>`
- Run more than once per build
