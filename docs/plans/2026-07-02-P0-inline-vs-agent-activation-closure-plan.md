<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Plan: Close the inline-vs-agent gap — REVISED after Assess (the enforcement mostly exists)

**Date:** 2026-07-02 · **Mode:** build-loop Phase 2 plan (draft; run `plan-verify` + `plan-critic` before execute)
**Status:** DRAFT v2 — REVISED after reading the actual code. **Scope shrank ~90%: do NOT build new activation machinery.**
**Source:** `~/dev/research/build-loop-2wk-review-2026-07-02.md` (F1–F7) + Codex packet (Rally seq 3934) + this Assess.

---

## Assess finding (why v1 was wrong)

The v1 plan proposed a new attestation module + a new activation gate. **Reading the code shows both already exist and are wired** — building them would duplicate working machinery (violates the KISS+DRY governing principle). What exists:

- **`scripts/stop_closeout.py`** (Stop hook `hooks/closeout.sh`) already records INLINE runs to `state.json.runs[]` via `append_run`, with an honest floor attestation `auditor_status: not-run:parent-must-dispatch`, runs the gate, and drops a `closeout-pending/<run-id>.md` marker. It cannot dispatch agents (Stop hooks can't), by design.
- **`scripts/judgment_gate.py`** already IS the stakes-gated activation gate: reads stakes from the CURRENT run (`synthesisDensity>5`, `riskSurfaceChange`, `stakes>=medium`, `dispatch_tier:frontier`); PASS if no stakes (inline = documented Rung-3 floor) or dispatched-to-Frontier; **FAIL** if stakes fired + floor + agent-tool reachable (top-level); WARN if unreachable (nested, "parent owes it").
- **`scripts/append_run.py`** already carries `judge_decisions: []` and accepts `--extra-json`.
- **`scripts/reference_activation_audit.py`** audits reference-doc reachability (a different "activation" — not agent seats).

So F3's "empty rich runs in-window" decomposes into: (a) inline runs DO record, but a minimal/floor entry — correct, a Stop hook can't produce `judge_decisions`; (b) most 2-week work didn't enter a build-loop run context at all (direct edits / conversation); (c) the run schema drift (host null, mixed `manualInterventions` types) is real and separate.

## Goal (revised)

Close the two genuine gaps in the EXISTING enforcement, and normalize the run schema. No new subsystem.

## Deliverables (MECE chunks — all EXTEND existing files)

### C1 — Broaden judgment-gate coverage beyond auditor+advisor
- **Owns:** `scripts/judgment_gate.py`, `scripts/test_judgment_gate.py`.
- Today `_GOVERNED_AGENTS = {"independent-auditor", "advisor"}`. A stakes-gated run can skip `plan-critic`, `scope-auditor`, `security-reviewer` (when `riskSurfaceChange`), `fact-checker` without the gate noticing.
- Add a **required-seats-by-stakes** map: e.g. `riskSurfaceChange ⇒ security-reviewer required`; `synthesisDensity>5 ⇒ plan-critic + scope-auditor required`; always ⇒ independent-auditor at build scope. The gate verdict already handles floor-vs-dispatched; extend it to iterate the required set and report each missing seat in `missing_seats[]`.
- Keep the WARN/FAIL stakes logic identical (don't change thresholds).

### C2 — Close the WARN "parent owes it" loophole
- **Owns:** `scripts/judgment_gate.py` (WARN branch), `scripts/stop_closeout.py` (marker), `skills/build-loop/references/phase-5-iterate.md` (drain).
- When the gate WARNs "parent owes it" (nested/unreachable), it must write a `followup/judgment-owed-<run-id>.md` so the parent run is FORCED to resolve it (dispatch the seat) — instead of the WARN silently evaporating (the "sidestep vs fix" retro pattern). Phase 5 Iterate drains `followup/`, so this closes the loop.

### C3 — Normalize the run schema (fixes F3/F5 telemetry)
- **Owns:** `scripts/append_run.py` (build_record), `scripts/write_run_entry/*` validators, `scripts/stop_closeout.py`.
- Always record `host`; type `manualInterventions` as `[{phase, note}]` (accept + migrate legacy strings on read). Additive only — must not break `test_append_run` (16) / `test_write_run_entry` (16) / `test_stop_closeout` (31) / `test_judgment_gate`.

### C4 — Docs
- **Owns:** `CLAUDE.md` (note the inline-vs-agent parity contract is enforced by judgment_gate + broadened seats), `KNOWN-ISSUES.md`, this plan.

## Non-goals
- No new module (`activation_attest.py`/`activation_gate.py` from v1 are CANCELLED — judgment_gate is the home).
- Not forcing inline runs to become Agent dispatches (Mode B stays first-class).
- Not model-selection policy (P1) or the Rally lifecycle cleanup (doc C).

## Risks
- Breaking the 3 large existing test suites (63 tests) → additive changes only; run all suites per chunk.
- Over-blocking on the broadened seat set → new required-seats map defaults conservative; only `riskSurfaceChange`/`synthesisDensity>5` add seats; WARN (not FAIL) when agent-tool unreachable, same as today.

## Acceptance
- [ ] A stakes-gated run that skips `security-reviewer` under `riskSurfaceChange` shows it in `judgment_gate` `missing_seats[]` and FAILs (top-level) / WARNs+followup (nested).
- [ ] A WARN "parent owes it" writes a `followup/judgment-owed-*.md`.
- [ ] `host` present + `manualInterventions` typed in new runs; legacy strings still read.
- [ ] All existing suites green (append_run 16, write_run_entry 16, stop_closeout 31, judgment_gate); new cases mutation-verified (fail if the gate is stubbed).
- [ ] `plan-verify` exit 0 + `plan-critic` no HIGH.

## Verification
Deterministic unit tests per chunk; then an end-to-end inline run (stakes-gated, skip a seat) → confirm FAIL + `missing_seats` + followup; independent-auditor (Fable) on the diff before merge.
