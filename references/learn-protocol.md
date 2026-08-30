<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 6 Learn Protocol — orchestrator reference

Mandatory cross-build pattern detection (v0.30.0+). Phase 6 **always runs and always emits a `## Learn` outcome line** in the Review-G report. The expensive arm (Sonnet draft + Opus signoff) stays conditional on `runs[] >= 3` AND a pattern crossing threshold AND not-deferred. See §"Gating outcomes" below for the three Review-G outcome states (accruing / deferred / full). The prior `autoSelfImprove: false` opt-out is deprecated to a migration no-op — old configs do not error.

## Executable contract

Run `python3 scripts/learn/__main__.py run --workdir "$PWD" --run-id <recorded-run-id> --source review-g --json`. This stdlib command owns outcome selection, deterministic detection, consolidation, accruing, sample review, receipts, and `runs[].learn`. The receipt at `.build-loop/learn/<run-id>.json` is the state source of truth. Add `--comment "<free-form note>"` when human or agent context should travel with the structured stage state.

Agent-only stages return as `work_orders[]`; the caller dispatches the named role, then attaches its result with `python3 scripts/learn/__main__.py attest ...`. Architect attestation creates the required promotion-reviewer order. Phase 6 is complete only when the receipt says `status: complete`; `scripts/run_close_lint.py --require-learn` enforces that boundary. The detailed steps below define each stage's behavior, while the runner defines sequencing and proof.

## Gating outcomes (decide once at Phase 6 entry)

| State | Trigger | What runs | Review-G line |
|---|---|---|---|
| **Accruing** | `runs[] < 3` | Detector (cheap) + consolidation only — skip Sonnet draft; **then fire the accruing miner (EC-01, non-gating): `python3 scripts/learn_accruing.py fire --workdir "$PWD" --json`** so signal accrues toward n=3 instead of the run idling | `Learn: accruing (N/3 runs)` |
| **Deferred** | debug-only (`closeout: false` in dispatch envelope) OR budget-exhausted (`budget_check.py` envelope `action == "finalize_and_stop"` at Phase 6 entry) | Detector + consolidation; write `.build-loop/proposals/learn-deferred-<run-id>.md` marker with `{reason, runs_count, budget_action}`; skip Sonnet draft + Opus signoff so Learn never blows the budget ceiling | `Learn: deferred — <reason>` |
| **Full** | `runs[] >= 3` AND detector returned a pattern AND not deferred | All steps below 4–9 fire | `Learn: <N> patterns drafted` (or `Learn: 0 patterns above threshold (N runs scanned)` when detector returned nothing) |

Deprecated `autoSelfImprove: false` is read for migration safety only: when present and `false`, log a one-line `state.json.warnings[]` entry (`"autoSelfImprove: false is deprecated; ignored (migration no-op)"`) and proceed as if the key were absent. Decision-3 of the design: promotion to `active/` still requires explicit `/build-loop:promote-experiment` — that safety boundary is unchanged.

## Steps

1. Run the executable contract once. It reads run history, retro candidates, learning objects, and bounded tool traces; consolidates memory; filters and deduplicates patterns; caps new work at two patterns; and performs the sample sweep.
2. Read the receipt. No patterns means no agent dispatch and zero LLM cost for Learn.
3. Dispatch only roles returned in `work_orders[]`. `self-improvement-architect` writes an experimental artifact. `implementer` realizes a returned enforcement specification. `promotion-reviewer` reviews a drafted or sample-eligible artifact.
4. Attach each result with `attest`. Architect completion requires an existing repository-relative artifact. Reviewer completion requires a verdict. Failed or pending work keeps the receipt incomplete.
5. Emit the receipt's `learn_line`, then run `scripts/run_close_lint.py --require-learn`. Promotion to `active/` remains user-confirmed through `/build-loop:promote-experiment`.

## Constraints

Never write outside `.build-loop/` and the canonical `build-loop-memory/` helper paths. Cross-project promotion (into the plugin repo) stays behind `/build-loop:promote-experiment <name>` — user-invoked only.
