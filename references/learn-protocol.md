<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 6 Learn Protocol — orchestrator reference

Mandatory cross-build pattern detection (v0.30.0+). Phase 6 **always runs and always emits a `## Learn` outcome line** in the Review-G report. The expensive arm (Sonnet draft + Opus signoff) stays conditional on `runs[] >= 3` AND a pattern crossing threshold AND not-deferred. See §"Gating outcomes" below for the three Review-G outcome states (accruing / deferred / full). The prior `autoSelfImprove: false` opt-out is deprecated to a migration no-op — old configs do not error.

## Gating outcomes (decide once at Phase 6 entry)

| State | Trigger | What runs | Review-G line |
|---|---|---|---|
| **Accruing** | `runs[] < 3` | Detector (cheap) + consolidation only — skip Sonnet draft | `Learn: accruing (N/3 runs)` |
| **Deferred** | debug-only (`closeout: false` in dispatch envelope) OR budget-exhausted (`budget_check.py` envelope `action == "finalize_and_stop"` at Phase 6 entry) | Detector + consolidation; write `.build-loop/proposals/learn-deferred-<run-id>.md` marker with `{reason, runs_count, budget_action}`; skip Sonnet draft + Opus signoff so Learn never blows the budget ceiling | `Learn: deferred — <reason>` |
| **Full** | `runs[] >= 3` AND detector returned a pattern AND not deferred | All steps below 4–9 fire | `Learn: <N> patterns drafted` (or `Learn: 0 patterns above threshold (N runs scanned)` when detector returned nothing) |

Deprecated `autoSelfImprove: false` is read for migration safety only: when present and `false`, log a one-line `state.json.warnings[]` entry (`"autoSelfImprove: false is deprecated; ignored (migration no-op)"`) and proceed as if the key were absent. Decision-3 of the design: promotion to `active/` still requires explicit `/build-loop:promote-experiment` — that safety boundary is unchanged.

## Steps

1. Load `Skill("build-loop:self-improve")` for the full protocol.
2. Dispatch `recurring-pattern-detector` (Haiku) — reads **three signal sources**: (a) `.build-loop/state.json.runs[]` (emits `phase_failure`, `manual_intervention`, `security_finding`), (b) `.build-loop/proposals/enforce-from-retro/*.md` written by the post-push retrospective (emits `enforce_recurrence` when the same normalized candidate signature appears across ≥ 2 distinct run-ids; cite `python3 scripts/enforce_retro_signals.py --workdir "$PWD" --json`), and (c) `.build-loop/learning-objects.json` — the `recursive-retrospective` skill's encoding-target-classified findings — via `python3 scripts/learning_to_draft.py --in .build-loop/learning-objects.json`. Source (c) routes `encoding_target: skill|agent` objects marked `encode: yes` straight to step 4 as `retrospective_pattern` proposals (the capture step's `encode` decision is the filter — NOT recurrence-gated, so a single high-confidence finding drafts), and emits a routable Prevention-Pattern `enforcement_spec` for `eval`/`gate`/`preflight`/`approval` objects (gap #3: no auto-producer yet, but the spec — condition → required behavior → lever → actuator → verifying artifact — is ready for a human or a future enforced-check producer to build, never silently dropped). No-op when the file is absent. In parallel, dispatch `Agent(subagent_type="build-loop:architecture-scout", prompt='task: learn-sync')` — promotes new lessons (Chunk 8) and syncs NavGator lessons into Postgres (Chunk 7); scout no-ops gracefully when those scripts are not yet present.
3. Filter to `confidence: "high"` or `count >= 4` (or type `manual_intervention` with count >= 2, or type `enforce_recurrence` with count >= 2 distinct run-ids, or type `retrospective_pattern` which the capture step already gated via `encode: yes` — passes without recurrence); dedupe against existing active/experimental skill names; cap 2 artifacts per scan.
4. For each kept pattern, dispatch `self-improvement-architect` (Sonnet) — drafts experimental artifact to `.build-loop/skills/experimental/<name>/SKILL.md`.
5. **Opus 4.7 signoff (you)** — read each drafted artifact, verdict: APPROVE / REVISE (1 retry max) / DISCARD. Log discard reason to `.build-loop/experiments/discarded.jsonl`.
6. For APPROVED artifacts: write baseline entry to `.build-loop/experiments/<name>.jsonl` with metric, target, sample size (default 8 non-confounded runs).
7. **Sample review sweep** — for each artifact in `.build-loop/skills/experimental/`, compute the **effective sample** (count of applied rows where `confounded: false`). Then:
   - **Auto-promote requires all of**: `autoPromote: true` in `.build-loop/config.json`, effective sample >= 8, delta meets target, no regressions in the non-confounded set. When all hold: `git mv` to `.build-loop/skills/active/<name>/`, update frontmatter, log `{event: "auto_promote", ...}`.
   - **Regressions do NOT auto-remove**. Instead: write `.build-loop/proposals/<name>-remove.md` with evidence and ask the user via `AskUserQuestion` in the next Learn run before any file deletion.
   - **Inconclusive at 2N** (flat after extended sample): write `.build-loop/proposals/<name>-inconclusive.md`; same user-confirmed removal gate as regressions.
   - **Effective sample < 8**: record evidence but take no action, even if `autoPromote: true`.
   - **Flat at N (effective)**: extend `sample_size_target` to 2N; log `{event: "extend_sample", ...}`.
   - Honor `.build-loop/skills/.demoted` (do not re-promote names listed there).
   - If `autoPromote` is false (default): every row above becomes "write proposal, no file moves or deletes."
8. Append concise synthesis to the Review sub-step F report — include any auto-promotes, proposals written, and extend-sample logs. If `autoPromote: false`, state this clearly so the user knows proposals accumulated.
9. **Memory consolidation** (runs unconditionally after step 8 when candidates exist). Two scripts in this order:
   - `python3 scripts/consolidate_memory.py --workdir "$PWD"` — reads the canonical semantic-candidate location resolved by the memory helpers, embeds each, dedups against `agent_memory.<schema>.semantic_facts` per cosine ladder. No-op when no candidates exist.
   - `python3 scripts/procedural_governance.py --workdir "$PWD" --mode detect-patterns` — clusters `state.json.runs[].root_cause` and writes procedural candidates through the canonical memory helpers for any cluster ≥3 incidents.

   Surface counts in the Phase 6 summary line: `consolidated: N inserted / M merged / K conflicts; procedural candidates: J added`. Do NOT auto-draft procedures — `--mode auto-draft` is gated until ≥5 hand-authored procedures exist, and is left for explicit user invocation.

## Constraints

Never write outside `.build-loop/` and the canonical `build-loop-memory/` helper paths. Cross-project promotion (into the plugin repo) stays behind `/build-loop:promote-experiment <name>` — user-invoked only.
