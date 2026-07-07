<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 4: Review — orchestrator routing detail

Extracted from `agents/build-orchestrator.md` §"Phase 4: Review (sub-steps A–G)". The agent body keeps a tight per-substep summary + a pointer here. The full per-sub-step procedural detail (trigger profiles, plugin-tests path globs, Gate 6/7/8 specifics, scorecard) lives in `references/phase-gate-checklist.md` §"Phase 4 Review (sub-steps A–G)". This file holds the orchestrator-routing detail that was extracted from the agent body: mandatory runs[] write, retrospective dispatch, self-modifications readback, post-deploy verification gate.

## Sub-step A — Critic (routing detail)

**Auto-invoke coordination check (Trigger 3 of 3)** runs before independent-auditor dispatches: execute the branching pseudocode in `references/auto-invoke-coordination.md`; on `mode=coordinated`, ensure all per-chunk verdicts in the active coord file are PASS or resolved-VARIANCE before proceeding (a `verification-pending` chunk blocks build-scope critique). Then run the quality-gate trigger profile (`scripts/review_trigger.py`) and dispatch `independent-auditor` at build scope (+ `security-reviewer` when `triggers.riskSurfaceChange`, + a second-vendor reviewer when `cross_vendor_required` and a peer host is reachable). Verdict routing: `nay`+critical/high → Execute (no iteration burn) or re-plan; medium/low+single-`file:line` → Auto-Resolve. Full trigger-profile invocation, cross-vendor reconciliation, and verdict table in `references/phase-gate-checklist.md` §"Sub-step A — Critic".

After independent-auditor returns, dispatch `Agent(subagent_type="build-loop:design-contract-specialist", prompt='trigger_point: phase4-review-a')` once per build with the aggregate of all chunks' `design_doc_delta` + `schema_delta` envelopes. Specialist writes the build-wide app-contract update; its `violations_found[]` flow into the Phase 4 Report alongside independent-auditor's findings.

## Sub-step B — Validate (routing detail)

UI-validator-first when `uiTarget != null` (see `agents/ui-validator.md`); UI input/output contract check; code graders; runtime smoke gate (`scripts/runtime_smoke.py` + SSE-specific contract gate when server module touched); **pytest-collection gate (`scripts/pytest_collect_gate.py` — full-suite-load check, every run on Python-bearing repos; `fail` routes to Iterate with the broken import as the rubric; closes the changed-area-only blindspot)**; LLM-as-judge; plugin-tests advisory; memory-first gate on every failure. Full procedural detail in `references/phase-gate-checklist.md` §"Sub-step B — Validate".

## Sub-step C — Optimize (opt-in)

Only when a mechanical metric exists. See `references/phase-gate-checklist.md` §"Sub-step C — Optimize".

## Sub-step D — Fact-Check

`fact-checker` + `mock-scanner` + `architecture-scout (review-rules)` in parallel; plus Gates 6/7/8. See `references/phase-gate-checklist.md` §"Sub-step D — Fact-Check".

## Sub-step E — Simplify

`/simplify` on changed files; preserve API/tests/observability/user value. Default pass = remove dead code AND restructure over-complex logic/architecture into clearer, equal-or-better-performing forms (clear, behavior-preserving wins only). `complexity_detector.py` is a Python-specific accelerator, not a gate; the agent reasons over the diff language-agnostically; apply-vs-advise reuses Review-B + independent-auditor. Mandatory every-pass telemetry: `update_execution_state(state_path,'review_e_pass',files_scanned=[...],is_final=<bool>)` — measurement only, never changes what E does.

## Sub-step F — Auto-Resolve

`python3 scripts/autonomy_gate.py` against each candidate from A/D; `auto` executes, `warn` executes with `[warn]` prefix + autonomyEvents entry, `confirm` → `## Held`, `block` → `## Blocked`. Strong-checkpoint findings never enter this queue. See `references/phase-gate-checklist.md` §"Sub-step F — Auto-Resolve".

## Sub-step G — Report (final pass only)

Scorecard, debugger outcomes, episodic memory capture, deployment policy gate, post-deploy verification gate (below). The blocking **no-critical/high exit gate** (`review_finding_gate.py` — any open `critical`/`high` without closure routes back to Phase 5 Iterate), the **report-section spec** (`## Done`/`## Held`/`## Blocked`/`## Status markers` + evidence contract + `build_report_lint.py` + forbidden patterns), and **auto-version-bump** are documented in `references/phase-gate-checklist.md` §"Sub-step G — Report (final pass only)" — execute them from there; do not re-derive their procedures here.

**Research citation gate.** Before emitting the final report, read
`.build-loop/state.json.researchGate`. When `blocks_final_claims: true`, any
current/external/API/package claim in the report must cite the research packet
or explicitly say the evidence was unavailable and the claim is unverified. If
`packet_path` is non-null, include a one-line `research_packet:` evidence item
in `## Done` or `## Status markers`.

**Reference-capture report field.** Mirroring the researchGate citation contract,
every run reports one `references captured:` line in `## Done` or `## Status
markers`: `references captured: N (<files>)` when an external fetch informed a
decision and was captured via the canonical writer, `none — no external fetch
informed a decision` when no web/doc fetch fed a decision, or `skipped:
<rationale>` when a fetch informed a decision but capture was intentionally not
run. This makes the default-on capture trigger
(`references/research-trigger-policy.md` §"Reference Capture") accountable in the
report instead of advisory-only.

**Style lint (MANDATORY, warn-mode)** — run on the final user-facing report draft before emitting:

```
python3 scripts/report_lint.py <draft.md> --json
→ total==0: emit as-is
→ total>0: revise the draft ONCE per skills/build-loop/references/output-style.md (translate jargon, fix headline, add validation line, remove contrastive-pivots), re-run, emit (append a one-line "[warn] style-lint findings remain" to ## Done if any persist)
→ script error: append "[warn] style-lint skipped" and continue
```

The lint enforces `skills/build-loop/references/output-style.md` (concise headline + validation line + jargon blocklist) on user-facing output only; internal envelopes stay structured.

### Mandatory `runs[]` write + `## Judge decisions` block (orchestrator-owned)

`references/phase-gate-checklist.md` §G delegates these to the build-orchestrator agent; dispatch-path-independent, fire every time regardless of how the agent was invoked. Collect every judge/auditor verdict that fired this run (`plan-critic`, `independent-auditor`, `scope-auditor`, `fact-checker`, `mock-scanner`, `security-reviewer`, `synthesis-critic`, `architecture-scout`, `ui-validator`, etc.) into a JSON list at `.build-loop/judge-decisions.json` (shape per `agents/promotion-reviewer.md` §"Verdict envelope"); when no judge fired, write `[]` (the empty array is the signal). **Preserve the `independent-auditor`'s `oracle_completeness` object verbatim** — copy it onto that judge's `judge_decisions[]` entry so a green gate's oracle coverage (`full|partial|thin`) is recorded, not dropped (`write_run_entry` validates it; see `agents/independent-auditor.md` §"Oracle completeness"). Then run:

**Assemble the harness config block (MANDATORY — report at the model+harness level, not model alone).** Reporting a run with only the model id confounds any later model-vs-model read, because undisclosed harness config is itself a first-class reliability lever (arXiv:2605.23950). Before the call, write a small JSON object to a temp file describing THIS run's harness and pass it as `--harness-json`:

```bash
cat > "$HARNESS_JSON" <<'EOF'
{
  "tools": ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Agent"],
  "context_budget": 200000,
  "scaffold": "build-loop mode-A (fan-out), orchestrator=opus/thinking, implementer=sonnet/code"
}
EOF
```

- `tools` — the tool-set available this run (a list, or a count when the list is long).
- `context_budget` — the compaction/context threshold in tokens for this run.
- `scaffold` — the dispatch mode (A fan-out / B inline) plus the orchestrator + implementer tier mix (mirror the values the orchestrator resolved this run; see `agents/build-orchestrator.md` §"Dual-mode dispatch" and §"Model Tiering").

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/write_run_entry/__main__.py" \
  --workdir "$PWD" \
  --goal "<goal>" \
  --outcome <pass|fail|partial> \
  --scope build \
  --files-touched-from-git \
  --judge-decisions-json .build-loop/judge-decisions.json \
  --harness-json "$HARNESS_JSON" \
  [--budget-summary-json <tmp>] [--models-json <tmp>]
```

This invocation MUST fire on every Phase 4G regardless of dispatch path (Skill, Agent tool, per-commit, resume). The `--harness-json` block lands as `state.json.runs[].harness` (additive; older readers ignore it). **`--scope build` arms the review-completeness gate** (`bl-enforce-independent-auditor-dispatch`): a `pass` that touched code with no real `independent-auditor` verdict in `judge-decisions.json` exits **3** and writes no entry — an inline self-audit is not a substitute. On exit 3, dispatch the `independent-auditor` at build scope (Review-A), append its verdict to `judge-decisions.json`, and re-run; do not reach Report with an empty/inline-only auditor record on shipped code. (Use `--scope chunk`/`none` only for per-chunk or non-shipping runs.)

### Mandatory milestone append (every run, append-only)

Immediately after `write_run_entry/__main__.py` completes, run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/append_milestone.py \
  --workdir "$PWD" \
  --summary "<one-line what shipped this run>" \
  --run-id "<run_id>" --json
```

This appends a record to `build-loop-memory/projects/<slug>/milestones.jsonl` and is never rewritten — it is the permanent progress log for this project. The Phase 1 staleness check (`scripts/memory_staleness_check.py`) compares this file's latest milestone commit against HEAD; a stale milestone surfaces as a memory-gap warning before planning begins.

**Return envelope MUST end with a `## Judge decisions` block** sourced verbatim from `state.json.runs[-1].judge_decisions[]` — one line per entry: `- {judge_id} → {checkpoint_id} → {verdict} — {variances[0].why_it_matters || meta_guidance[0] || "no_brief"}`; on empty list emit `None fired — bypass_reason: <one-line reason: trivial scope, judges skipped, etc.>` so absence is itself communicated. `## Judge decisions` is appended after the report-section spec's `## Status markers`.

### Post-push retrospective dispatch (non-gating, in-flow)

Immediately after the closing push step completes (push succeeded OR was held by the briefed do-not-push marker — both count as "run-close"), dispatch the `retrospective-synthesizer` agent. This is **non-gating** — fire-and-continue; do NOT await its envelope before closing the run.

```python
# in-flow dispatch — do NOT await
Agent(
    subagent_type="build-loop:retrospective-synthesizer",
    isolation="worktree",
    prompt=f"task: post-push retrospective for run {run_id}; workdir: {workdir}",
    input={
        "run_id":  state["execution"]["build_loop_id"],
        "workdir": str(workdir),
    },
)
# continue to the rest of run-close immediately
```

The agent invokes `python3 -m retrospective --workdir <workdir> --run-id <id> --json` (located via Python on `sys.path`-modified scripts/), which writes `.build-loop/retrospectives/<YYYY-MM-DD>/<run-id>.md` + `<run-id>.summary.md` atomically. The summary file is surfaced inline in the end-of-run readback as a `## Retrospective summary` block (≤5 lines). Any enforce-candidate the synthesizer drafts lands at `.build-loop/proposals/enforce-from-retro/<run-id>-<NN>.md` for human review — never silently promoted. Stop-hook fallback is intentionally NOT used (commit `5c2a030` — subagent Stop hooks fire unreliably; in-flow dispatch is the durable mechanism). The synthesizer's `synthesize.run` is non-raising by contract; failures degrade to `status="degraded"` with a reason and the run still closes cleanly.

### `## Self-modifications (readback)` block (include only when self-modifications occurred this run)

When `selfRecursive.enabled == true` and at least one self-modification was attempted, append a `## Self-modifications (readback)` section to the end-of-run report (after `## Judge decisions`). Format — one row per attempted self-modification:

```
| File | What / Why | Gate verdict | Additional-review finding |
|------|-----------|--------------|--------------------------|
| scripts/foo.py | simplified retry loop to reduce nesting / SAFE self-simplification finding | pass | pass |
| scripts/self_mod_verify.py | extended blast-radius check to include hook scripts | pass | flag: auditor noted test coverage gap in hook-script branch (non-blocking) |
```

Rows with `gate verdict: fail` show `auto-reverted` in the additional-review column (the gate already reverted; no further review needed). The additional-review finding column reports the independent-auditor's verdict for that change (`pass` / `flag: <one-line finding>`). This section is the human's readback on what the loop did to itself — it replaces the need to stop mid-run. If no self-modifications occurred, omit the section entirely.

## Post-deploy verification gate

Production-web analogue of the Review-B runtime smoke gate. **Fire when** a deploy actually ran this build (deployment policy gate returned `auto` and the deploy/push executed, OR the pushed branch auto-deploys via Vercel) AND the project is Vercel-linked (`.vercel/project.json` or `vercel.json`); skip otherwise.

**Invoke** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_deploy.py --workdir "$PWD" --changed-route <route> [...] --json` with the routes this build changed (API handlers, pages); it resolves the latest prod deployment, polls `vercel inspect` to terminal, then probes the prod root + each changed route.

**Route on `status`**:

- `pass` → proceed.
- `fail` → Phase 5 Iterate with the envelope's `findings` as rubric (deployment `ERROR`/`CANCELED`, non-200 prod root, changed-route `5xx`/unreachable).
- `skipped` → record `deploy_verify: skipped (<reason>)` in Review-G and proceed (infra state — no Vercel link, CLI missing, not authed, network — **never** hard-fails).

**Heuristic**: a `401`/`403` on a protected changed route is **healthy** (function deployed and running, just refused the unauthenticated probe); only `5xx`/build-error fails. If the user added the Vercel MCP (`mcp.vercel.com`) to `.mcp.json`, prefer it over the CLI (do not add it automatically). Degraded procedure: `fallbacks.md#web-deploy-verify`.
