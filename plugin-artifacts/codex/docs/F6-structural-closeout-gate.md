<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# f6 — Structural closeout gate (fire the Learn/judgment closeout at run-close, not on a human prompt)

Self-contained build spec. Implement with `/build-loop:run` (or hand to a fresh agent). Origin: retro enforce-candidates `bl-20260611-arp-provision-hardening-01` and `bl-20260612-buildloop-learn-gates-02`.

## Problem

build-loop's run-close artifacts only run inside the orchestrator's **Review-G**:
- `scripts/append_run.py` — records the run into `.build-loop/state.json.runs[]` so Phase 6 Learn can see it.
- `scripts/judgment_gate.py` — checks the Frontier (Fable) judgment layer was dispatched on a stakes-gated run.
- the retrospective-synthesizer + memory closeout.

An **inline** run (skill-as-methodology on the host loop, no orchestrator dispatch) reaches **none** of them. They are documented as "MUST run" but nothing fires them, so they only run when the user explicitly asks. Observed this session: **3 consecutive inline sessions** where the user had to prompt for the closeout; the recurring `manual_intervention` signal across two retros.

## Goal

When a build-loop run ends, the closeout fires **structurally — no human prompt**: record the run, run the dispatch gate, and surface (never block) a one-line verdict if the Frontier judgment layer was skipped.

## What to build

A **`Stop` hook** (Claude Code) + the **Codex `Stop`** equivalent, shipped in build-loop's `hooks/hooks.json` (`${CLAUDE_PLUGIN_ROOT}`) and `.codex/hooks.json` (git-toplevel path), backed by one new script `hooks/closeout.sh` (or extend an existing Stop/SessionStart closeout hook if one exists — do not duplicate). On stop:

1. **Self-gate:** walk up for `.build-loop/`. Absent → silent `exit 0` (safe to install globally). Present but no run touched this session → silent exit 0.
2. **Record the run** via `python3 "$PLUGIN_ROOT/scripts/append_run.py" --workdir "$PWD" --run-id <id> --goal "<derived>" --outcome <done|partial|blocked> [--manual-intervention ...]`. Derive run-id/goal/outcome from `state.json` + `git`. `append_run` is already idempotent on `run_id`, so if Review-G already wrote the run, this is a no-op (it refuses to overwrite a richer orchestrator record).
3. **Run the gate** `python3 "$PLUGIN_ROOT/scripts/judgment_gate.py" --workdir "$PWD" --run-id <id> --agent-tool-available false --json`. A Stop hook has **no Agent tool**, so pass `false` → a stakes-gated, judgment-skipped run returns **WARN** (not fail). Surface the verdict as an advisory `systemMessage`.
4. **Leave a marker** the next `SessionStart` surfaces (`.build-loop/closeout-pending/<run-id>.md`) reminding to run the retrospective-synthesizer + memory closeout — a Stop hook **cannot dispatch agents**, so it cannot run the retro itself.

## Contract (non-negotiable)

- **Advisory + fail-open:** `exit 0` always; emit valid JSON; never `decision: block`. (build-loop hook charter; `hooks/rally-coordination-hook.sh` precedent.)
- **Self-gate** on `.build-loop/` presence; silent elsewhere.
- **Idempotent** with Review-G (no double-record; per-run sentinel so a Stop firing twice is a no-op).
- **Minimal-PATH safe:** hooks run under `/usr/bin:/bin`. Resolve `python3` absolutely or `command -v`-guard; missing tool → `exit 0`. (See memory `reference_hooks_minimal_path_failopen`.)
- **Honest labeling:** the hook makes the gap **visible**, it does not make the Fable judgment happen. A Stop-recorded run carries `source: append_run` and a floor `auditor_status`; the gate WARNs that judgment was skipped. It never silently marks the run as judged.

## Honest scope limit

A Stop hook cannot dispatch agents, so f6 = **auto-record + auto-surface the gap**, which is the enforcement that ends the prompting. It does **not** auto-dispatch Fable (that needs an Agent-tool context, i.e. orchestrator Mode A) — that is a separate, harder piece, out of scope here.

## Acceptance

- After an inline build-loop session ends, `state.json.runs[]` has the run **without any prompt**.
- The Stop hook surfaces the `judgment_gate` verdict (WARN when stakes-gated + judgment skipped).
- Self-gates cleanly: no `.build-loop/` → silent; no `python3` → silent `exit 0`.
- Idempotent with Review-G (no double-record).
- `closeout-pending/<run-id>.md` written; next SessionStart surfaces it once.
- Tests (`hooks/test_closeout.sh` or equivalent): Stop with `.build-loop/state.json` present → append_run ran + gate verdict surfaced + exit 0; no `.build-loop/` → silent exit 0; `python3` absent → silent exit 0; second Stop in the same run → no-op.

## Process discipline (bake in — these were the session's failures)

1. **Read the canonical contracts FIRST**, not the surface: the Claude `Stop` hook output schema (exit codes, `systemMessage`/`decision`), build-loop's existing `hooks/hooks.json` + `hooks/_*` + any SessionStart closeout (`hooks/session-start-closeout.sh`), `scripts/append_run.py` + `scripts/judgment_gate.py` interfaces (already built).
2. **Don't duplicate** — extend an existing closeout hook if present.
3. New script needs a colocated test; self-modification passes the SELF-MODIFICATION SAFETY GATE (`references/self-review.md`).
4. **Dispatch the Fable independent-auditor on the result BEFORE declaring done**, and run `judgment_gate.py` on the build-loop repo itself (dogfood). Inline self-tests are not sufficient — that lesson is why this spec exists.
