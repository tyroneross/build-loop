---
name: model-bakeoff
description: Use to run a controlled multi-model bake-off — have N models (e.g. Opus 4.8, Sonnet 5.0, GPT-5.5) each independently diagnose→plan→execute the SAME bounded change in isolated git worktrees, then deterministically re-score their committed code on a fresh server, merge the best-of (grafting distinct wins from the others), and repeat per change. Triggers on "bake-off", "compare models on this task", "which model is best at", "run the same change across models and score them". NOT for choosing a model tier/segment for one agent — use `model-tiering`.
user-invocable: false
---

# Model Bake-off Harness

Run a fair, evidence-based competition where several models each solve the *same* change end-to-end, then merge the best result. One orchestrator (this session) coordinates; contestants are single agents (measure the model, not a multi-agent loop). Repeat per change, accumulating merges on one experiment branch.

## Validation contract (define BEFORE any dispatch)

Fix the grading before running — a metric invented after seeing outputs bends to them.

1. **Metric + rubric + judge, up front.** Write the exact success metric, the rubric, and the pass/fail threshold BEFORE dispatch. Name the **independent judge** (a non-contestant model, e.g. Fable) for subjective dims. No post-hoc scoring.
2. **Reproduce the baseline FAILURE first.** To prove an intervention (profile/scaffold/fix) makes a model succeed, first REPRODUCE the failure deterministically. n=1 anecdotal failures don't generalize — a task where the control already passes has no fix to validate. (2026-07-09: "kimi fails multi-crate Rust" did NOT reproduce on synthetic 2-crate tasks — the control passed both a 3-file crate and a 2-crate cross-crate task via the default profile; the original sandbox-dogfood failure was task-specific, not a general ceiling.)
3. **Task at the failure boundary.** If every arm passes, the task is too easy → no signal to separate arms. Calibrate difficulty until the control fails.
4. **A control arm.** Always include the default-profile / no-intervention (and, for harness work, the no-harness one-shot) arm to isolate the intervention's marginal value.

## Model + thinking identity (mandatory data contract)

A benchmark measures a **model at a thinking level and mode**, not a model name alone. Use Benchmark Lab's existing `abc-comparison/v2` artifact; do not create a Build Loop-only benchmark schema. Every arm adds:

- `model`: exact provider model/version, separate from the host or harness.
- `effort`: exact effective provider/runtime label, or `unknown`.
- `effort_normalized`: `none|minimal|low|medium|high|xhigh|max|ultra|unknown`.
- `effort_provenance`: `source_document|experiment_config|runtime_log|unknown`.
- `effort_evidence`: source page/table/footnote for published data, or exact config/command/log evidence for our test.
- `mode`: `single_agent|multi_agent|adaptive|unknown`.

These are additive arm fields on the canonical artifact, following the existing field-observation contract. Benchmark Lab owns validation, normalization, and aggregation, but its current converter does not retain these additive fields. Preserve the raw artifact as source evidence until that Lab-owned extension lands; ingest acceptance alone is not proof of retention. Never infer effort from a model default or translate provider labels without evidence. If a source omits row-level effort, record `unknown`, cite the omission, and mark the comparison effort-confounded/directional. `ultra` or another multi-agent mode is a distinct treatment arm, never a higher single-agent score.

## When scoring: check the CODE and the OUTPUT, and distrust the rig

- **Check the produced code AND its oracle output — never "it ran."** Read the code; run the oracle; parse the RIGHT signal. (2026-07-09: an auto-grep matched the lib unit-test line "0 passed" and mislabeled a 2/5-passing control as FAIL; multi-binary `cargo test` needs pass/fail SUMMED across binaries, not first-match. Always confirm the specific test binary that carries the assertions.)
- **Separate the measurement apparatus from the thing measured.** The **rig** = everything that is NOT the model or the code under test: the grading script, the exact command + flags you invoke, the tooling environment (installed binaries, language toolchain, PATH), and the fixtures/scaffold. A **rig bug** is a defect in that apparatus that yields a false pass or a false fail wrongly blamed on the model. Before trusting any `0` or any FAIL, confirm the rig produced a *valid run*: the command parsed, the tools were present, the oracle actually executed against real output. Three rig bugs on 2026-07-08, each first mis-scored as a model failure: (a) `pytest` absent from the grading env → a contestant looped on a false-negative it could never clear; (b) `harness swarm --segments` expects a manifest FILE, not an integer count → the treatment arm produced 0 files twice on a malformed command; (c) an auto-grep read the wrong `cargo test` binary's summary line → a 2-of-5-passing run was labeled FAIL. None were the model; all were the rig.

## Roster & dispatch (verified handles)
- Opus 4.8 → `Agent(model: "opus")`; Sonnet 5.0 → `Agent(model: "sonnet")` (`sonnet` = latest, NOT 4.x — older Sonnets have no clean subagent handle).
- Codex/OpenAI → read the exact model id and requested effort from the experiment manifest; pass both explicitly (for example `model: "gpt-5.6-terra"`, `config: {model_reasoning_effort: "high"}`). Capture the effective runtime config in the arm log; do not rely on `~/.codex/config.toml` defaults or guess `-codex` suffixes.
- Claude effort → use a host surface that can set the requested effort. If the available dispatch primitive cannot set it, record the effective value as `unknown`; do not label the arm `xhigh` from intent alone.
- Independent judge: prefer a NON-contestant model (e.g. Fable). If unavailable, the orchestrator scores subjective dims with over-cited evidence + a stated caveat, and leans on deterministic dims.

## Per-change protocol
1. **Baseline:** branch the experiment off clean `origin/main` (not a dirty/active branch). Confirm no concurrent session collides.
2. **Manifest:** pre-register each arm's exact model/version, requested thinking level, mode, dispatch surface, and evidence path in the run manifest. Pre-create the `abc-comparison/v2` arm fields above; update `effort` and `effort_evidence` from effective runtime evidence before scoring.
3. **Scaffold** one worktree per contestant off the experiment branch HEAD: `git worktree add -b bakeoff/<Cn>-<model> <path> <branch>`; `npm ci` (or lockfile-equiv) per worktree; copy `.env.local`.
4. **Brief** (IDENTICAL for all): give the *symptom* + acceptance criteria + a fair equal entrypoint pointer — WITHHOLD the diagnosis (that's what's scored). Add repo guardrails (see below).
5. **Dispatch** all contestants in parallel (Agent arms `run_in_background: true`; Codex arm blocks the turn — fine, the others run concurrently). Write the effective thinking setting and its runtime evidence back to the run manifest and observation arm before scoring.
6. **Commit stranded Codex work:** Codex's sandbox usually cannot write an external worktree's `.git` (`index.lock: Operation not permitted`). The orchestrator commits it: `git -C <worktree> add -A && git commit`. (RESULT.md is often gitignored → `git add -f`.)
7. **Score deterministically** (don't trust self-reports — re-run each contestant's committed code):
   - Objective dims computed in code: typecheck, build, test, betterer, + the change-specific success criterion run on a fresh server. Weight these highest.
   - Subjective dims (diagnosis depth, plan, code quality, intent fidelity) by the judge reading diffs + captured outputs.
8. **Scorecard** (rubric below) → **merge** best base onto the experiment branch, **grafting** distinct wins from the others (each graft: fixes a real gap the winner has, is isolable from the loser's *harmful* parts, verified by re-running). Document graft rationale.
9. **Re-verify the merged result**, regenerate coverage, validate the canonical observation through Benchmark Lab when available, commit. Then next change.

## Rubric (max 50; tune weights per task)
success-criteria attainment ×3 (objective) · build/typecheck/test/betterer ×2 (objective) · diagnosis accuracy ×2 · plan quality ×1 · code quality+scope ×1 · intent fidelity ×1. Objective dims dominate; the LLM judge is confined to subjective dims.

## Hard-won lessons (do these or the scoring is wrong)
- **UI-faithful inputs.** Score with inputs the real UI can actually send. (A driver that sent an out-of-range `timeHorizonDays` the UI caps at 90 unfairly zeroed 2 of 3 contestants whose validators — correctly — rejected it.)
- **Dynamic free-port allocation; NEVER blanket-kill by port.** External processes steal fixed ports; `pkill`-by-port killed *peer contestants'* live dev servers mid-run twice. Allocate a guaranteed-free port per server, check before bind, kill only your own PIDs.
- **Multi-sample runtime scoring.** LLM output is non-deterministic (temp>0). A single sample misled once (a model's terse run read as failure). Take ≥3 samples for the pass/fail criterion; report pass-rate.
- **Write-path changes contaminate a shared DB.** For UI/render/summary fixes that self-heal or persist, contestants' writes to a shared DB poison each other's before/after and self-heal the very rows you test. Prefer **function-level tests** (feed the exact bad input through each contestant's exported cleaner — no DB writes). For write-heavy/auth stages, DO NOT share a live DB (per-contestant schema/branch, or serialize); require a UNIQUE test-user id per contestant and id-scoped (not suffix-scoped) cleanup.
- **Verify against the REAL path, not the self-reported one.** A contestant's "5/5 pass" exercised a code path real traffic doesn't; the real-UI re-run showed 0/3. Re-run the actual user flow.
- **Betterer/coverage baseline.** Fresh worktrees need `npm run test:coverage` before betterer (coverage-summary.json). On merge, keep the repo-level baseline; `betterer --update` only to *include new tested code* — NEVER bake in a *lowered* baseline caused by skipped/failing tests (that weakens the guardrail for everyone).
- **No schema migrations against a shared DB.** Forbid `prisma migrate`/`db push`; use existing columns / JSON blobs.

## Repo guardrails to put in every brief
Work ONLY in your worktree; no edits outside it; no deploy/push; no `--no-verify`; no DDL against the shared DB; unique per-model test-user id + id-scoped cleanup; verify by RUNNING (name the exact verification mechanism: curl the endpoint, CDP virtual authenticator for WebAuthn, function-level test for cleaners, screenshots for UI).

## Output
Per stage: a `SCORECARD.md` (rubric table + verdict + merge/graft rationale). At the end:

- `RESULTS.md`: cross-stage scoreboard, each arm labeled `model_id @ thinking level/mode`, per-model performance pattern, where multi-model merge beat any single model, and scoring-integrity caveats.
- `OBSERVATION.json`: canonical `abc-comparison/v2` rounds with each arm's exact model,
  thinking fields, result metrics, and provenance. This is the machine-readable source of
  truth; Benchmark Lab is the sole normalization and aggregation owner.

Preserve the raw JSON whether or not Benchmark Lab ingestion is available. Until the Lab
converter and storage retain these fields, do not use successful ingest as evidence that
thinking provenance survived, and do not aggregate results across thinking settings.
