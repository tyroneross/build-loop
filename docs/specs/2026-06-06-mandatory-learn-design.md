<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Design — Mandatory Phase 6 Learn + retro→Learn wiring

**Date:** 2026-06-06
**Repo:** build-loop (from v0.29.3 → v0.30.0)
**Status:** approved (design); implementation routes through build-loop

## Goal

Make Phase 6 Learn **always run and always report** (no silent skips), degrade gracefully below
the cross-run floor, keep narrow escape hatches, and wire the per-run retrospective's
enforce-candidates into Learn's cross-run detector — turning two disconnected mechanisms into a
two-tier learning system (retro = intra-run, Learn = cross-run).

## Current state (what exists)

Phase 6 Learn runs after Review-G **unless** any of three skips fire:
`autoSelfImprove: false`, `runs[] < 3`, or detector finds no patterns. Flow:
`recurring-pattern-detector` (Haiku, reads `state.json.runs[]`) → filter → `self-improvement-architect`
(Sonnet) drafts experimental skill → Opus signoff → promotion requires explicit
`/build-loop:promote-experiment`. `consolidate_memory.py` already runs unconditionally. The retro
(`scripts/retrospective/`) writes enforce-candidates to `.build-loop/proposals/enforce-from-retro/`
but the detector does **not** read them — the two are disconnected.

## Decisions (approved)

1. **Below 3 runs (graceful degradation):** always run consolidation + record signals; report
   `Learn: accruing (N/3 runs)`. Do NOT force a single-run reflection (redundant with the retro).
2. **Narrow escape hatches (only these two skip the expensive arm):**
   - debug-only runs (`closeout: false` / debug mode) — not learnable builds.
   - hard budget-exhausted autonomous runs — run cheap consolidation + write a `learn-deferred`
     marker; skip the Sonnet draft / Opus signoff so Learn never blows the budget ceiling.
   The general `autoSelfImprove: false` opt-out is **removed** (deprecated; honored only as a
   migration no-op). Promotion to `active/` still requires explicit user confirmation — unchanged
   (that safety boundary stays).
3. **Retro → Learn wiring:** `recurring-pattern-detector` gains a second signal source — it scans
   `.build-loop/proposals/enforce-from-retro/` across runs; an enforce-candidate (normalized) that
   recurs ≥ threshold becomes a high-priority pattern to draft/enforce. This delivers
   "anything prompted/needed repeatedly → enforce" **across** sessions, not just within one.

## What "mandatory" changes (engine unchanged)

Only the gate. Every run now: runs the Haiku detector (state.json.runs[] + enforce-from-retro/),
runs consolidation, and emits a Learn outcome line in the Review-G report — even when the outcome
is "nothing crossed threshold." The expensive arm (Sonnet draft + Opus signoff) stays conditional
on `runs[] ≥ 3` AND a pattern crossing threshold AND not-budget-exhausted. Net marginal cost of
"mandatory" ≈ one cheap Haiku state-scan per run.

## Files to modify

- `skills/build-loop/references/phase-6-learn.md` — skip-conditions → mandatory + graceful accrue
  + escape hatches + retro wiring; "(optional)" → "(mandatory)".
- `references/learn-protocol.md` — same, full protocol.
- `agents/build-orchestrator.md` §"Phase 6: Learn" (~line 409) — gating change; always-report.
- `skills/build-loop/SKILL.md` — Phase 6 summary "(optional)" → "(mandatory, always runs)".
- `agents/recurring-pattern-detector.md` (+ any backing script) — add `enforce-from-retro/` as a
  signal source; emit cross-run enforce-recurrence patterns.
- `CLAUDE.md` / `AGENTS.md` — "optional Learn" → "mandatory Learn" wording.
- `.build-loop/config.json` template / config docs — deprecate `autoSelfImprove` (migration no-op).
- Tests under `tests/` and colocated `test_*.py` for any changed/added script.
- Version bump 0.29.3 → **0.30.0** (new default behavior — minor).

## Acceptance criteria (verifiable)

1. A run with `runs[] >= 3` and a crossing pattern: Learn runs end-to-end and the Review-G report
   carries a `## Learn` outcome. ✅ by a run + report inspection.
2. A run with `runs[] < 3`: Learn does NOT silently skip — report shows `Learn: accruing (N/3)`;
   consolidation still ran. ✅ by unit/integration test.
3. `autoSelfImprove: false` no longer disables Learn (honored as migration no-op). ✅ by test.
4. Debug-only (`closeout:false`) and budget-exhausted runs skip only the expensive arm and write
   the documented marker; consolidation still runs. ✅ by test.
5. An enforce-candidate recurring ≥ threshold across runs' `enforce-from-retro/` surfaces as a
   detector pattern. ✅ by a fixture test (multiple runs' candidate files).
6. Promotion to `active/` still requires explicit `/build-loop:promote-experiment`. ✅ unchanged.
7. plan-verify + plan-critic pass; independent-auditor PASS at Review-A; tests green under `env -u`.

## Branch hygiene / merge-back

One run-worktree → collapse to `main` via `collapse_run.py`; `createdRefs[]` + the ledger updated
before fan-out. Commit to **local main only — no push, no marketplace deploy** without explicit
user OK (deploy is a restart-boundary decision). Surface the deploy as the final step.

## Risks / rollback

- **Per-run cost creep** — mitigated: only the cheap detector+consolidation become unconditional;
  expensive arm stays pattern-gated + budget-gated.
- **Skill sprawl from more-frequent drafting** — mitigated: existing cap (2 artifacts/scan),
  dedupe, demote list, and explicit-promotion boundary all unchanged.
- Rollback: one `--no-ff` merge revert; `autoSelfImprove:false` migration no-op means old configs
  don't error.
