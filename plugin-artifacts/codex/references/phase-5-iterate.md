<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 5: Iterate (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full Iterate phase: prioritized work list, fan-out, stuck-cascade, and convergence detection.

## Phase 5: Iterate — Fix Review Failures + UX Queue (up to 5x)

**Goal**: Fix failures surfaced by Review *plus* drain the UX queue accumulated by Sub-step D Gates 7-8, systematically not blindly. Loops back to Review after each pass.

Entered when Review sub-step A, B, or D finds blocking issues OR `.build-loop/ux-queue/` is non-empty. Critic-only failures (strong-checkpoint from A without touching B) route to Execute instead — no iteration counter burn.

**Iterate input contract (prioritized work list)**:

| Priority | Source | Notes |
|---|---|---|
| 1 | Blocking Validate failures (Sub-step B) | Test/lint/build/UI validation failures |
| 2 | Blocker UX queue entries with `architecture_impact: false` | `.build-loop/ux-queue/*.md` filtered |
| 3 | Major UX queue entries with `architecture_impact: false` | Same source, lower severity |
| 4 | Optimization findings (Sub-step C) | Opt-in |
| 5 | UI coverage-gap queue entries (`dimension: test-coverage`) | Lowest — additions, not fixes |
| **deferred** | Any UX entry with `architecture_impact: true` | Surfaces in Review-F for explicit user confirmation; Iterate does not pick up |

The "code is cheap, AI agents build fast" framing: the orchestrator does NOT defer based on patch size. It defers only when `architecture_impact: true` (new component, new data flow, navigation graph change, schema migration, auth provider swap). Everything else is fair game for the current loop.

## Premise re-validation gate (MANDATORY before scheduling any queue item)

A queue item is written at one moment and executed at another. By the time it surfaces, the bug may be fixed, the file moved, or the precondition false. Before scheduling any item drained from `.build-loop/queue/`, `.build-loop/issues/`, `.build-loop/ux-queue/`, or `.build-loop/followup/`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/premise_revalidation.py" gate --item <path> --repo "$PWD" --json
```

Exit 0 = `fresh`, schedule it. Exit 1 = do not schedule; route by `reason_code`:

| Verdict | Meaning | Route |
|---|---|---|
| `fresh` | Within the window, or re-validated with evidence | Schedule |
| `stale_needs_revalidation` | Past the window (default 7d), never re-checked | Re-check the premise against the live repo, then `validate --note "<what you checked>"`; if it has resolved, close the item with that receipt |
| `premise_broken` | A cited path or commit the item depends on is genuinely gone | Close the item with the receipt; do not implement |
| `needs_human_recheck` | A cited path **relocated** (same basename elsewhere), or extraction was ambiguous | Re-read the item against the new path before acting |

Freshness falls back to `created` when `validated` is absent, so an item filed minutes ago is fresh by construction — the gate cannot deadlock a queue fed by the current run. Stamp freshness only through:

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/premise_revalidation.py" validate --item <path> --note "<evidence>"
```

`--note` is required. A bare timestamp asserts freshness without evidence, which is the failure being fixed.

**Why `needs_human_recheck` is a separate verdict from `premise_broken`.** 2026-08-07: a sweep of 67 queue cards found 16 (24%) whose premise had already resolved — one claimed 65 commits were stranded by a push blocker that had cleared two days earlier. Several card specs even carried hand-written *"VALIDATE FIRST (mandatory) … if stale, write a receipt and STOP"* prose, which proves the need was known and unautomated. The sharper finding: **the careful human sweep was itself stale** — it reported a file as deleted when the file had been relocated, and a repo as 0 ahead when it was 6. A missing path is therefore never enough to declare a premise dead; the gate checks for a same-basename relocation first and asks for a human re-read instead of concluding. Staleness is also repo-dependent (one repo measured 4/4 stale, another 12 live to 1 stale), so the gate is per-item, never a blanket policy.

Mirrors the Operations Center design (`validated_at` + refusal at the same gate that already refuses an unrunnable card, commit `3fd0a23`) rather than reinventing it.

**Fan-out** (mode-dependent): After dequeue, partition entries by `files_touched` into independent groups (no overlapping files).

- **Top-level mode** (orchestrator invoked directly via the user's session): dispatch up to 4 `implementer` subagents in parallel via `Agent(subagent_type="build-loop:implementer", ...)` per the bundled `agents/implementer.md` (Sonnet 5, scoped tools=[Read, Write, Edit, Bash, Glob, Grep]). Hard cap from `~/.claude/CLAUDE.md` §Sub-Agents. Sequential groups process after the parallel batch.
- **Subagent mode** (orchestrator was itself spawned via `Agent(...)` so the no-sub-sub-agents rule applies): degrade to **inline-implementer mode** — iterate the queue serially, apply each fix following the implementer's protocol (scope to `files_touched`, refuse `architecture_impact: true`, verify locally before declaring fixed). No parallelism, same quality bar. The orchestrator surfaces the degradation in Review-F.

In both modes, each pass returns the same structured outcome (status + files_changed + verifications). Status routing covers all 9 implementer return values:
- `fixed` → mark done (delete the .md)
- `partial` → keep entry, re-pass next iteration
- `scope_breach` → ask user before extending scope
- `deferred_architecture` → Review-F surfaces for explicit user confirmation
- `evidence_stale` → regenerate via `ux_triage.py --clear`, then re-pass
- `plan_malformed` → same as `evidence_stale` (regenerate); log id to `.build-loop/state.json.malformedPlans[]`
- `needs_dependency` → ask user; never auto-add deps
- `failed` → re-pass with implementer's `notes` as `additional_context`; after 2 attempts escalate to Opus per `model-tiering`; after 3 surface as ❓ Unfixed
- `concurrent_modification_detected` → abort current parallel batch (orchestrator partition bug; never transient)

Results re-enter Sub-step B for re-validation. For Validate failures (no queue entry), construct an inline plan in the same shape and treat identically.

**UI re-validate hook (when uiTarget != null)**: After each implementer subagent reports back AND before re-entering Sub-step B Validate, invoke headless IBR for affected renderable surfaces when installed, following `../../../references/ibr-ui-verification-policy.md`. Run `ui-validator` in parallel for web, with native AX or simulator/browser evidence as the fallback. If no renderable surface can be resolved, record the gap and fall back to `audit-design-rules.mjs`.

**Infra self-heal before counting an attempt (C-HEAL / self_heal_safe_issues).** When an Iterate attempt's own tooling, hook, or Bash command FAILS (infra error — non-zero exit that is not a graded-criterion failure, e.g. a pre-commit hook crash, a lint runner that throws on a binary file, a script that errors on a missing env var): ROOT-CAUSE and FIX that infra error first. Classify via `scripts/classify_action.py`. SAFE → apply, verify (re-run the failed action), commit, then resume the Iterate attempt. RISKY/DECISION/PRODUCTION → isolate/surface per the normal routing table. An infra self-heal does NOT burn the iterate budget; only a graded-criterion failure (test/lint/validate failure against the rubric) burns a count. This prevents `--no-verify` bypasses and other workarounds from masking fixable infra errors.

Per attempt:
1. **Diagnose root cause** — don't just retry. Start the failure brief in plain language, then trace visible symptom -> technical failure -> upstream dependency/interface/process failure -> first controllable system failure. Actor-blame phrases such as "agent forgot" or "model missed context" are not terminal causes unless paired with the missing control that allowed them. Split the brief into two axes — **creation** (why the defect existed at all) and **escape** (why no control caught it before the surface) — a bug often needs both fixed. The root cause is closed only when the named fix passes the **counterfactual**: it would have prevented/detected/contained THIS exact failure on the real input, not a hand-constructed one.
2. **Stuck-iteration cascade (always on)**: at the START of EACH attempt, the orchestrator runs the cascade in order — see `agents/build-orchestrator.md` §Phase 5 for the full ladder. Summary:
   - **Evidence-gap repair (highest priority)**: if the prior gate flagged `evidence_gap: true`, invoke `Skill("build-loop:logging-tracer")` with intent `repair`. Ephemeral-by-default — Mechanism A (`DEBUG_TRACE=1` runtime gate) or Mechanism B (`git-stash` throwaway). Re-run the failed criterion; if output is now informative, proceed with new context.
   - **Memory-first re-check**: invoke `Skill("build-loop:debugging-memory")` again with the new symptom (it may have shifted shape after the prior fix attempt).
   - **2 consecutive same-root-cause failures** → parallel multi-domain assessment via `build-loop:debugging-memory` `{op:"assess"}`. Pass `model: sonnet` to domain assessors explicitly (override `inherit` default to prevent 4× Opus fan-out from the Opus 4.7 orchestrator). The full procedure is documented in `skills/debug-loop/SKILL.md` §"If stuck — parallel multi-domain assessment".
   - **3 consecutive same-criterion failures** → causal-tree investigation via `Skill("build-loop:debug-loop")`. Runs its own 7-phase cycle internally; returns with fix applied or hard-stop.
3. **Build the prioritized work list** from the table above (Validate failures + UX queue).
4. **Partition for parallel fan-out**: group by disjoint `files_touched`; dispatch ≤4 subagents in parallel.
5. **Execute fixes**; for UI files, run the UI re-validate hook before continuing.
6. **Loop back to Review sub-step B** (Validate). Sub-step A (Critic) usually skipped on re-runs unless the fix touched new files. Sub-steps C-F run only on final pass.
7. **Followup overflow**: when the iteration cap (5) is reached and queue entries remain, write them to `.build-loop/followup/<topic>.md` for a subsequent `/build-loop:run` invocation. Plan content is already complete — the followup build skips its own Plan phase for these entries.
   - **`judgment-owed-<run-id>.md`** entries (written by `stop_closeout` when a stakes-gated inline run closed at the inline floor) mean: **dispatch the owed verification layer(s) named in the file for that run** (the Frontier auditor/advisor it skipped), then the file is cleared automatically on the next passing Stop. Do not treat it as a code work-item — it is a dispatch-the-judgment debt.
8. **Track**: attempt count, what failed, what was attempted, what changed, queue depth before/after each pass.

**Convergence detection**:
- Same criterion fails 2x with same root cause → escalate to user
- Fix A breaks criterion B (oscillation) → flag and ask user
- 3+ criteria fail simultaneously after a fix → systemic issue, stop and reassess

**Stop condition (QM v0.13.0 — severity-aware, replaces the blunt 5-cap for critical/high)**. The 5-iteration cap still bounds the loop, but it **cannot finalize with an open `critical` or `high` finding** (the no-critical/high exit gate in Review-G, `review_finding_gate.py`, blocks the final pass). On reaching the cap:
- **Open `critical`/`high` remain** → do NOT silently ship as ❓ Unfixed. Escalate to the user with the blocking findings and their `closure_proof` gaps; the build does not pass until they close or the user explicitly waives. (If the same approach keeps failing, re-plan instead of burning more iterations.)
- **Only `medium`/`low` remain** → proceed to Review sub-step G Report with those marked ❓ Unfixed and routed to `.build-loop/followup/<topic>.md` for a subsequent run.

Log each iteration to `.build-loop/state.json`.
