<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 6 Learn Protocol — orchestrator reference

Optional cross-build pattern detection. Runs after Review sub-step F on every build unless `.build-loop/config.json.autoSelfImprove` is false or `runs[]` has fewer than 3 entries.

## Steps

1. Load `Skill("build-loop:self-improve")` for the full protocol.
2. Dispatch `recurring-pattern-detector` (Haiku) — reads `.build-loop/state.json.runs[]`, returns patterns JSON (only `phase_failure` and `manual_intervention` types). In parallel, dispatch `Agent(subagent_type="build-loop:architecture-scout", prompt='task: learn-sync')` — promotes new lessons (Chunk 8) and syncs NavGator lessons into Postgres (Chunk 7); scout no-ops gracefully when those scripts are not yet present.
3. Filter to `confidence: "high"` or `count >= 4` (or type `manual_intervention` with count >= 2); dedupe against existing active/experimental skill names; cap 2 artifacts per scan.
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
9. **Episodic memory consolidation** (runs unconditionally after step 8 when `.episodic/` is present in the repo). Two scripts in this order:
   - `python3 scripts/consolidate_memory.py --workdir "$PWD"` — reads `.semantic/_candidates.jsonl`, embeds each, dedups against `agent_memory.<schema>.semantic_facts` per cosine ladder. No-op when `_candidates.jsonl` is missing.
   - `python3 scripts/procedural_governance.py --workdir "$PWD" --mode detect-patterns` — clusters `state.json.runs[].root_cause` and writes `.procedural/_candidates.jsonl` for any cluster ≥3 incidents.

   Surface counts in the Phase 6 summary line: `consolidated: N inserted / M merged / K conflicts; procedural candidates: J added`. Do NOT auto-draft procedures — `--mode auto-draft` is gated until ≥5 hand-authored procedures exist, and is left for explicit user invocation.

## Constraints

Never write outside `.build-loop/` and `.episodic/` / `.semantic/` / `.procedural/`. Cross-project promotion (into the plugin repo) stays behind `/build-loop:promote-experiment <name>` — user-invoked only.
