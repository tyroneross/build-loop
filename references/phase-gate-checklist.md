<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase Gate Checklist — orchestrator routing detail

Loaded on demand by the orchestrator. Covers Phase 1 Assess (full protocol) and Phase 4 Review (sub-steps A–G). See `skills/build-loop/SKILL.md` for the full spec; this file is the orchestrator's routing checklist.

## Phase 1 Assess detail (full protocol)

Extracted from `agents/build-orchestrator.md` §Phase 1 Assess. The agent body keeps a high-level bullet list and links here for the full procedure.

1. **Capability shortlist (mandatory, always — fires before everything else)**: run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/capability_shortlist.py --phase 1 --intent "<goal-keywords>" --json --cache-into-state` to populate `state.json.activeCapabilities["1"]` with ≤8 relevant capabilities. **This step fires regardless of whether subagent fan-out is anticipated downstream** — Phase 2 and Phase 3 dispatchers read the cache (Priority 16), and inline-execution builds (no fan-out) leave the cache cold otherwise (Run 5 regression, Priority 19). The `--cache-into-state` flag exercises the same atomic write path that subagents read via `read_active_capabilities()`. If the registry is missing the script auto-rebuilds it; rebuild manually with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_capability_registry.py --workdir "$PWD"` only when surfaces change.

2. **Detect plugins**: run `node ${CLAUDE_PLUGIN_ROOT}/skills/build-loop/detect-plugins.mjs` and write the JSON result into `.build-loop/state.json` under `availablePlugins`.

3. **Self-recursion check** (Priority — plugin-developer dogfooding signal): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect_self_recursive.py --workdir "$PWD" --json` and write the result to `.build-loop/state.json.selfRecursive`. The detector verifies three conditions: (1) `<workdir>/.claude-plugin/plugin.json` exists with a `name`, (2) some entry under `~/.claude/plugins/` is a symlink resolving back to the workdir (legacy direct OR per-version cache layout), and (3) `<workdir>/.git/` exists. When `self_recursive: true`, set `state.json.selfRecursive.enabled: true` and surface to the user in the Phase 1 Assess brief: "🔁 Self-recursive build detected — working copy is the runtime. Per-commit mode available via `/build-loop:run --per-commit`." When false, the `reason_if_false` field (one of `not_a_plugin | no_runtime_link | not_a_git_repo | symlink_check_failed`) is informational only — do not block. Per-commit dispatch itself is implemented in a downstream commit; this step only writes the detection result and surfaces the note.

4. **Drift + branch echo** (only if the self-recursion check above returned `self_recursive: true`): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/version_drift_warning.py --workdir "$PWD" --json` and `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/working_branch_echo.py --workdir "$PWD" --json` in parallel. Mirror outputs to `.build-loop/state.json.versionDrift` and `.build-loop/state.json.workingCopy` via the same atomic temp+rename pattern used by `scripts/write_run_entry.py`. If `drift_detected: true`, surface to the user: `"⚠️ {warning_message}"`. Always surface the working-copy echo when self-recursive: `"{message}"`. Both are informational — they never block the build.

5. **Capability shortlist (per-phase, downstream)**: build-loop now exposes ~113 surfaces. To stay inside Anthropic's Tool Search ≤8-candidate guidance, narrow the decision space before each phase. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_capability_registry.py --workdir "$PWD"` once at session start (registry cached at `.build-loop/capability-registry.json`; rebuild only when surfaces change). For Phases 2/4/6 (which need their own bucket), dispatch `Skill("build-loop:capabilities")` with the phase number and goal text, OR shell out: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/capability_shortlist.py --phase <N> --intent "<goal>" --json --cache-into-state`. Treat the shortlist as the routing baseline for that phase; only escalate outside it when no entry fits.

6. **Set sub-routers + triggers**: set sub-routers (`uiTarget`, `platform`, `migrationSource`) and triggers (`structuredWriting`, `promptAuthoring`, `promptEditingExisting`, `riskSurfaceChange`) per `references/trigger-rules.md` and `skills/build-loop/references/capability-routing.md` §Trigger Conditions. Write under `.build-loop/state.json.triggers`.

7. **Auto-infer `riskSurfaceChange` from constitution overlap** (NEW 2026-05-12, plan §12.7 P4): immediately after the constitution load (memory step 0 below), run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/infer_risk_surface.py --workdir "$PWD" --json`. Merge `risk_surface_change: true` from the detector into `.build-loop/state.json.triggers.riskSurfaceChange` — never downgrade a manual `true` to `false`. Mirror `matched_rules`, `constitution_evidence`, and `generic_evidence` to `state.json.triggers.riskSurfaceEvidence` so the Phase 1 Assess brief can surface which constitution rules tripped. Closes the §11.4 Sim G gap where auth-touching diffs shipped without security-reviewer firing because the manual flag was missed. Helper failure → preserve existing trigger value and log a one-line warning; never blocks.

8. **Load memory** (executable read protocol — full detail in `references/memory-systems.md` §"Read protocol — Phase 1 Assess"):
   0. `Read("~/.build-loop/memory/constitution.md")` (global durable invariants) and `Read("~/.build-loop/memory/projects/<slug>/constitution.md")` (project overrides if present; slug from `scripts/_paths.derive_slug_from_cwd`). Constitution loads ahead of MEMORY.md because rules cited as `constitution:<rule_id>` outlive any single build and gate advisory-judge severity. Empty/absent global constitution: skip silently (judges fall back to MEMORY.md feedback entries). Cache touched rule IDs in `.build-loop/state.json.constitution.loadedRuleIds[]` so commit-auditor + promotion-reviewer can cite them at Phase 3 checkpoints without re-reading.
   1. `Read("~/.build-loop/memory/MEMORY.md")` (global) and `Read("~/.build-loop/memory/projects/<slug>/MEMORY.md")` (project). Project overrides global on key conflict. Empty/absent files: skip silently.
   2. `Read(".build-loop/state.json")` and inspect `runs[-3:]` for prior-build context (goals, outcomes, root_cause). Empty `runs[]`: skip.
   3. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_facade.py recall --query "<goal-keywords>" --limit 10` for unified read across all four backends (runs/decisions/semantic/debugger). Inspect `reasons[]` for backend-unavailable signals; never block on them.
   4. Invoke `Skill("build-loop:debugging-memory")` with `intent: "list-recent"` for recent debugger incidents (one-line summary). MCP unreachable → fall through to `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md#bug-memory`.
   5. **Backend health check** (Priority 17): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/backend_health.py --workdir "$PWD"` and the script writes the envelope to `state.json.architecture.backendHealth`. Surface the one-line summary in the Phase 1 Assess brief so the user can see which memory backends are operational. Exits 0 even when backends are down — graceful degradation is the contract; the summary tells the user what to expect from `recall()` for the rest of the build.

   See `references/memory-systems.md` §"Read protocol — Phase 1 Assess" for return-shape contracts and graceful-degradation behavior.

9. **Architecture baseline + blast-radius** (architecture-scout subagent, fires unconditionally): dispatch `Agent(subagent_type="build-loop:architecture-scout", prompt='task: baseline')`. The scout decides native vs NavGator per task, runs the scan + impact + ACP build, persists a baseline decision, and returns a ≤500-word envelope. Before dispatch, check `state.json.architecture.stale`; if true and ACP older than 5 min, the scout will await scan completion (default) — pass `task: baseline; no_arch_await: true` to override. If `triggers.promptAuthoring` or `triggers.promptEditingExisting` is true, also invoke `mcp__plugin_navgator__llm_map`. Cache the envelope to `.build-loop/architecture/scout-cache/baseline.json`.

10. **Observability baseline**: detect the project stack and run a passive observability scan (no code changes at Assess). Language-aware grep for `console.{log|error|warn}` (web), `print()` / `pprint()` (Python), and structured loggers (winston/pino/structlog/loguru/zap/log/slog) in `package.json` / `pyproject.toml` / `requirements.txt` / `go.mod`. Classify into `well-instrumented` / `print-only` / `silent`. Write to `.build-loop/state.json.observability.level`. Informational; do NOT load `Skill("build-loop:logging-tracer")` here — the skill is reactive only.

11. **Runtime-server detection** (informational, no changes — implements decision `_unscoped/0003`): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect_runtime_server.py --workdir "$PWD" --json` and write the result to `.build-loop/state.json.triggers.runtimeServer` (boolean) plus `.build-loop/state.json.runtimeServerInfo` (envelope: `server_module`, `sse_route`, `default_port`, `embedded_ui_module`, `event_handler_locations[]`, `evidence[]`). Phase 4 sub-step B Validate consults these for the live HTTP/SSE smoke gate. Helper failure → treat as `runtimeServer: false` and log a one-line warning; never blocks. Silent default for CLIs, libraries, plugins, and static-render web apps. Closes the pytest-with-mocks blind spot that let example-app ship 27 commits with two real bugs.

12. **Pre-commit baseline detection** (NEW 2026-05-07, prevents intermediate-state contract-change blockers): check for baseline-tracking pre-commit tools that reject any worsening tsc/lint count. Test: `test -f .betterer.results || grep -q 'betterer\|lint-staged.*--baseline' package.json 2>/dev/null`. If a baseline tool is detected, write `.build-loop/state.json.preCommit.hasBaseline = true` so Phase 2 plan-writing flags sole-consumer contract changes for bundling (or `--update` baseline reset). See `~/.claude/projects/-Users-tyroneross/memory/feedback_buildloop_pre_commit_baseline.md` for the pattern.

13. **Deployment policy**: load `.build-loop/config.json.deploymentPolicy` if present. Default to `preview: auto`, `testflight: auto`, `production: confirm`, `unknown: confirm`. Before any push/deploy, evaluate the exact command with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/deployment_policy.py" --workdir "$PWD" --command "$CANDIDATE_DEPLOY_COMMAND"`.

14. **Intent capability pack**: read `skills/build-loop/references/intent-capability-pack.md`. Capture app/repo purpose, primary users, core jobs, update intent, user value, and non-goals. Write `.build-loop/intent.md` and mirror a compact version into `.build-loop/state.json.intent`.

15. **UI input/output contract** (when `uiTarget != null`): read `skills/build-loop/references/ui-io-contract.md`, inventory affected user inputs and system outputs, and mirror a compact summary to `.build-loop/state.json.uiIOContract` when practical. The full contract is finalized in Phase 2.

16. **Modular systems pack**: read `skills/build-loop/references/modular-systems-pack.md`. Capture module boundaries, stable interfaces, coupling risks, likely MECE work partitions, and any justified modularity exception. Mirror into `.build-loop/state.json.structure`.

17. **Define goal + criteria**: state goal concretely; suggest 3-5 scoring criteria; write to `.build-loop/goal.md`. See `skills/build-loop/references/phase-1-assess.md` §"Define goal and scoring criteria".

18. **Synthesis-density routing** (REVISED 2026-05-07 round-4 — Phase 1 routing rule with explicit speed/quality lanes): when a plan exists at this point in Phase 1, count its `synthesis_dimensions:` entries by calling `count_synthesis_dimensions()` from `scripts/plan_verify.py` (do NOT invent a second parser; share the block-walker with the vague-value lint). Then resolve the routing tier in this priority order:
    1. **Explicit user override** — if `state.json.config.modelOverrides.thinking` is set OR the plan declares `tier: thinking` in its frontmatter, route to thinking-tier regardless of count.
    2. **Auto-escalate on density** — if `count > 5` (6+ entries), the commit is synthesis-dense at the COMMIT level; route to `tier: thinking` automatically. Fan-out loses cross-dimension coherence at this density even with each individual dimension well-specified.
    3. **Default — Sonnet fan-out for speed** — `count` in 1–5 range OR `count == 0` keeps the default fan-out path. Sonnet's velocity advantage (~33% wall-clock, ~28% tokens) is real and the C3 attestation_lint, C4 synthesis-critic, and C5 halt-and-ask backstops fire post-commit to catch the residual recall gap. Use this lane when speed dominates.
    4. **Per-commit override available** — if a chunk in the plan declares `tier: thinking` at the chunk level, that chunk specifically routes to thinking even if the plan-level decision was fan-out. For mixed-density plans where some chunks are architectural and others are mechanical.

    Write the routing verdict to `state.json.synthesisDensity` as `{count: N, escalated: true|false, reason: "<override|density|default|chunk-override>"}`. **Routing target is `tier: thinking`, never a hardcoded model name** — Phase 3 resolves the identifier through the same tier abstraction used by the C5 halt-and-ask resolver (`state.json.config.modelOverrides.thinking` → orchestrator frontmatter `model:` → fail-loud if neither resolves).

    **Why this shape (vs the round-4 first draft of "any dim escalates"):** the n=6 A/B experiment showed β catches ~40% of α's novels — quality gap is real. But β saves ~33% wall-clock and ~28% tokens, and the C3-C5 backstops catch some of the gap on commits without too much architectural depth. Default-Opus would erase β's velocity entirely; default-Sonnet at low density preserves it. The `> 5` threshold matches the empirical inflection point in the experiment data: C5 (5 dims, the densest commit) is where β's recall collapsed to 0. Below that, β's recall is poor but non-zero, and the backstops materially help.

    Effect on Phase 3: when `synthesisDensity.escalated == true`, the orchestrator does NOT dispatch parallel implementer subagents for that plan; it executes the chunks inline at `tier: thinking`. When `escalated == false`, fan-out proceeds with the C3/C4/C5 backstops watching. The dual-mode dispatch table still applies — escalation overrides the default fan-out path on a per-plan or per-chunk basis. Skip this step cleanly when no plan file exists yet (re-evaluate at the end of Phase 2 if needed).

19. **Downstream consultation rule**: every downstream phase consults `availablePlugins` and `triggers` before dispatching a subagent.

20. **Phase 1 done**: Phase 1 produces `.build-loop/intent.md`, `.build-loop/goal.md`, populated `.build-loop/state.json` (triggers, availablePlugins, observability, runtimeServer, preCommit, synthesisDensity, architecture.backendHealth, selfRecursive, versionDrift, workingCopy, activeCapabilities[1], constitution.loadedRuleIds, riskSurfaceEvidence, uiIOContract), and the architecture baseline cache at `.build-loop/architecture/scout-cache/baseline.json`. Proceed to Phase 2 Plan.

## Phase 4 Review (sub-steps A–G)

### Sub-step A — Critic

- Dispatch `commit-auditor` at `scope: "build"` on Execute's full diff (`<pre_build_sha>..HEAD`) with full `rubric_criteria_ids` and `task_ids_in_scope` covering every plan T-N. Verdict envelope shape per `agents/commit-auditor.md`. Replaces retired `sonnet-critic` per plan §15.1.
- **Auto-Resolve routing**: variances with `auto_fixable: true` AND `severity ≤ minor` AND `suggestion` naming a single `file:line` go to the Sub-step F Auto-Resolve queue. Action label `"judge fix: <variance.id>"`, command `"edit <file>"`. Autonomy gate routes them — `auto` executes, `warn` executes with `[warn]` Done prefix, `confirm` to `## Held`, `block` to `## Blocked`. Major variances + non-auto-fixable + judgment calls go to Sub-step G Report's `## Notes from judges` for user review.
- **Strong-checkpoint variances (severity=major with `verdict=new_approach`) route to Execute (no iteration counter burn) — never to Auto-Resolve.**
- On `guidance` → log to `.build-loop/issues/` and proceed.
- Skip A on re-reviews after Iterate unless Iterate touched new files.
- **If `triggers.riskSurfaceChange` is true**, also dispatch `security-reviewer` (Sonnet 4.6, read-only) in parallel with `commit-auditor`; load `Skill("build-loop:security-methodology")` for the rubric. Findings JSON: `CRITICAL` or `HIGH` → route back to Execute (no iteration burn, same as `strong-checkpoint`); `MEDIUM` / `LOW` → log to `.build-loop/issues/security-findings.json` and proceed.
- After Phase 3 Execute, also load `Skill("build-loop:defenseclaw-bridge")` if the build produced any agent-builder-style artifacts (`tool-contract*.md`, `agent-manifest*.md`, `guardrail*.md`, `system-boundary*.md`, `flow-topology*.md`, `role-card*.md`) — the bridge writes a DefenseClaw spec skeleton to `<project>/.defenseclaw/generated/`; spec-only, no runtime install.

### Sub-step B — Validate

Order: UI-validator-first (when `uiTarget != null`) → code graders → runtime smoke gate (see below) → LLM-as-judge → plugin-tests advisory check → memory-first gate on every failure.

**UI-validator-first when `uiTarget != null`**: dispatch `ui-validator` with `triggerPoint: "phase4-review-b"`; see `agents/ui-validator.md`; supersedes the legacy `scripts/ibr_quickpass.py` shell-out, which the agent still uses as a fallback when `@tyroneross/ibr-core` is not installed — see RFC #30.

**UI-validator routing**: `pass` proceeds; `fail` routes `failing_assertion` to Iterate (same rubric pattern as Phase 3 chunk-close); `skipped (auth-gap)` records `⚠️ ui-validate skipped — auth fixture missing` in Review-G and falls through to scanners.

**IBR-first when present and UI work** (fallback path): load `Skill("build-loop:ibr-bridge")` and run the quick-pass BEFORE any other validator:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ibr_quickpass.py --workdir "$PWD" --scope changed
```

Interpret the JSON: `pass == ran` → green-light, proceed to D; any `fail` → route the failing test to Iterate (test assertion is the rubric, no extra critic burn); `no_tests` or `ibr_unavailable` → fall through to scanners. The script writes `.build-loop/ibr-quickpass.json` for Sub-step D Gate 8 to read.

If `availablePlugins.ibr` and UI work AND quick-pass green, also invoke `ibr:design-validation` for web or `ibr:native-testing` for mobile for design-rule depth. If IBR is absent and the build touches UI files, paste `fallbacks.md#web-ui` into the validation subagent prompt.

#### Runtime smoke gate (post-tests, pre-LLM-judges)

After code-based graders pass, if any changed file matches a runtime-smoke trigger pattern (see `references/runtime-smoke-triggers.md`), invoke:

```bash
python3 scripts/runtime_smoke.py --changed-files <list> --workdir "$PWD" --json
```

The script auto-detects an adapter from the project's manifest. Status `pass` proceeds; `fail` routes the changed surface to Iterate (treat the smoke envelope's `findings` list as the rubric); `skipped` (no trigger matched OR no adapter for the project's stack) records `runtime_smoke: skipped (<reason>)` in the Review-G report and proceeds. Adapter exit 2 (runner error) is treated like a transient grader outage — log and proceed with a Review-G warning. **Library-only repos with no dev server cleanly skip — never fail.**

**SSE-specific contract gate** (when `triggers.runtimeServer == true` AND the diff touches `runtimeServerInfo.server_module` OR `runtimeServerInfo.embedded_ui_module`): in addition to the adapter-driven smoke above, run the live HTTP/SSE contract check documented in `skills/build-loop/references/phase-4-review.md` §Sub-step B Validate (5-step procedure: restart server → wait for HTTP 200 → curl POST against `<sse_route>` for 5s → parse handlers in the embedded UI → fail when any observed event type lacks a handler arm). Implements decision `_unscoped/0003`; closes the silent-server / ignored-client class of bug. Skip step 4 (handler parsing) when `embedded_ui_module: null` — API-only services have no embedded UI to compare. Infrastructure failures (server won't start, curl errors) log to `.build-loop/issues/live-smoke-<date>.md` and surface as `⚠️ untested live-flow` in Review-G; only the contract violation itself fails the build.

#### Plugin-tests advisory check (auto-runs when build touches plugin metadata; non-blocking)

If Phase 3 Execute's diff contains any of these path globs, run `Skill("build-loop:plugin-tests")` as part of Validate:

- `*.claude-plugin/plugin.json`, `*.claude-plugin/marketplace.json`
- `commands/*.md` (added/renamed/removed)
- `skills/*/SKILL.md` (added/renamed/removed, or `name:` / `description:` frontmatter changed)
- `agents/*.md` (added/renamed/removed)
- `.mcp.json` or any path referenced by `mcpServers` in plugin.json
- `hooks/hooks.json`

Concretely run from the repo root: `for t in scripts/test_skill_resolution.py scripts/test_plugin_manifest.py scripts/test_mcp_registration.py scripts/test_trigger_phrases.py scripts/test_bridge_preflight.py; do python3 "$t" || EXIT=1; done; exit ${EXIT:-0}`. **Findings are advisory, not blocking.** Record results to `.build-loop/state.json.pluginTests` as `{exit, failingScripts: [...], details}`. Surface a one-line summary in the Review-F Report. Do NOT route to Iterate on exit 1; do NOT gate the build.

#### Memory-first gate (always on)

Runs on every Review-B criterion failure with an error-like signal (exception, test failure, build error). Skip when failure is expected and mapped (TDD "tests must fail until impl complete") or iteration is from user feedback rather than a reproducible bug. Steps:

1. **Read logs first** — call `read_logs` MCP to pull structured log entries for the failure window:
   ```
   mcp__plugin_build-loop-debugger__read_logs({
     source: "project",
     severity: "error",
     query: "<criterion keyword>",
     since: "<phase_5_start_timestamp>"
   })
   ```
   If `read_logs` returns nothing but the test failed silently, set `evidence_gap: true` in the gate record.
2. **Synthesize a symptom string** ≤ 200 chars. Preserve error type, file, key phrase.
3. **Invoke `Skill("build-loop:debugging-memory")`** with `{ symptom, budget: 2500 }` — native skill. Returns verdict `KNOWN_FIX | LIKELY_MATCH | WEAK_SIGNAL | NO_MATCH`.
4. **Act on verdict** — memory is a hypothesis, not a patch. Default for every verdict is route to Iterate as adapted plan. Direct-apply for `KNOWN_FIX` requires the strict triple-gate enforced inside the `debugging-memory` skill.
5. **Record the gate** in `.build-loop/state.json.debuggerGates.review_b`.
6. **Fallback when MCP unavailable**: paste `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/fallbacks.md#bug-memory` into the gate. Flag `⚠️ debugger MCP unavailable — using local grep fallback` in Review-F.

### Sub-step C — Optimize (opt-in)

Only when a mechanical metric exists AND user hasn't opted out. Load `build-loop:optimize`. Archive to `.build-loop/optimize/experiments/`. Feed results back to Review-B as evidence.

### Sub-step D — Fact-Check

Dispatch `fact-checker` + `mock-scanner` in parallel. **Plus** when code changed in this build, dispatch `Agent(subagent_type="build-loop:architecture-scout", prompt='task: review-rules')` in parallel — the scout runs the rules check, diffs against `.episodic/architecture/known_violations.json`, writes decisions for new violations via `scripts/capture_arch_violation.py`, and returns a `route:` recommendation in `follow_up`. If `route: "iterate"`, route the scout's findings into Iterate's prioritized work list. For cross-layer changes, escalate to `Skill("build-loop:architecture-review")` for the full 5-phase integrity review.

**Plus the new gates from `skills/build-loop/references/phase-4-review.md` §Sub-step D**:

- **Gate 6 — Version-Bump Advisor** (when `pluginWork: true`): `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/version_advisor.py --workdir "$PWD"`. State `hold` (default) → one-line note in Review-F. State `suggest` (marker `.build-loop/release-pending.md` exists) → propose semver and ask user before any plugin.json edit. Never auto-bump.
- **Gate 7 — UX Triage** (when `uiTarget != null`): `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ux_triage.py --workdir "$PWD" --clear`. Each `blocker`/`major` finding becomes a queue entry in `.build-loop/ux-queue/`. Then dispatch `performance-assessor` (full-app sweep) and `fact-checker` (broader file glob, full rendered surface) in parallel for the agent-augmentation portion; merge their findings into the same queue.
- **Gate 8 — IBR Coverage-Gap** (when `uiTarget != null` AND IBR available): read `.build-loop/ibr-quickpass.json.untested_surfaces`. For each, generate a draft `.ibr-test.json` to `.ibr-tests/_draft/<id>.ibr-test.json` via `mcp__plugin_ibr_ibr__plan_test` (programmatic only). Add a queue entry with `dimension: test-coverage`. Drafts never auto-promote.

Blocking (Gates 1–4) → Iterate. Queue entries (Gates 7–8) → flow into Phase 5's prioritized work list. Warnings → Report.

### Sub-step E — Simplify

Invoke `/simplify` on changed files. Preserve public API, tests, observability, user value, and modular boundaries needed for scalability, accuracy, security, testability, or stable interfaces. Do not simplify by removing necessary states, accuracy, scalability, accessibility, or real data paths. If integrated simplification is better, record `MODULARITY EXCEPTION`.

### Sub-step F — Auto-Resolve

Drain non-destructive open items. Run `python3 scripts/autonomy_gate.py` against each candidate item from Sub-steps A and D; execute `auto` verdicts, record `confirm` in `## Held`, record `block` in `## Blocked`. For `warn` verdicts (exit 0): execute the action, record in `## Done` with `[warn] <reason>` prefix, and append one entry to `state.json.runs[].autonomyEvents[]` for match-rate tracking. Strong-checkpoint findings never enter this queue.

### Sub-step G — Report (final pass only)

Runs only when all prior sub-steps pass OR when iteration cap is hit. Writes final artifacts and closes the build.

The report markdown sections, in this order:

- `## Done` — every F-criterion verified pass + every Auto-Resolve `auto` item, with one-line evidence each. `warn` items also appear here, prefixed with `[warn] <reason>`.
- `## Held` — items the autonomy gate verdicted as `confirm`. Body: action label + the gate envelope's `reason` field verbatim. The user runs held commands manually if they want. Build-loop does NOT prompt or auto-execute these.
- `## Blocked` — items the autonomy gate verdicted as `block`, same shape as Held.
- `## Status markers` — ✅ Known / ⚠️ Untested / ❓ Unfixed (existing convention; preserve).

**Forbidden in the report**:

- "Open Recommendations" headers
- "Next Action" sentences phrased as questions
- Bullets phrased as `Want me to X?` / `Should I Y?`
- Lists that invite operator selection of which items to execute

Empty categories get the header followed by `_(none)_`. Do not omit empty sections. The autonomy gate (`scripts/autonomy_gate.py`) is the authority — see `references/autonomy-config.md` for precedence.

Write scorecard to `.build-loop/evals/YYYY-MM-DD-<topic>-scorecard.md`. See `references/memory-systems.md` for the run-entry write protocol and `references/learn-protocol.md` for Phase 6 hooks. Quick checklist:

- Write scorecard to `.build-loop/evals/`.
- Append run entry via `scripts/write_run_entry.py` (NEVER hand-write JSON).
- Invoke `Skill("build-loop:debugging-store")` for each newly resolved Review-B/Iterate failure.
- Invoke `Skill("build-loop:architecture-dead")` for the orphan scan.
- Run the deployment policy gate before any push/deploy.
- Run the episodic memory capture (transcript scan).

**Debugger store + outcome**, **orphan scan**, **deployment policy gate**, and **run entry append** all apply here — see `skills/build-loop/references/phase-4-review.md` §Sub-step G: Report for the full step-by-step protocol.

Review also checks the intent pack and modular systems pack: does the result advance the north star, satisfy the update intent, avoid fake data in user-decision paths, remove or avoid dead UI, use the simplest durable approach that protects user experience, keep ownership MECE, and preserve modular boundaries that matter?
