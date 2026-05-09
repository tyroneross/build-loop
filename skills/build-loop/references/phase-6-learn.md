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
5. **Sample review sweep** — for artifacts in `.build-loop/skills/experimental/` from prior runs: if `.build-loop/config.json.autoPromote` is true AND effective (non-confounded) sample ≥ 8 AND target met → auto-promote to `active/`. Regressions and inconclusive-at-2N write proposals to `.build-loop/proposals/` for user confirmation — never auto-delete.
6. **Notify** — concise synthesis appended to Review sub-step G report, including removal command for each artifact moved or proposed.

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
