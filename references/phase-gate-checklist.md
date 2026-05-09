# Phase 4 Review Sub-Steps A–F — orchestrator checklist

Loaded on demand at Phase 4 Review. Review runs as 6 ordered sub-steps. See `skills/build-loop/SKILL.md` §Phase 4 for the full spec; this file is the orchestrator's routing checklist.

## Sub-step A — Critic

- Dispatch `sonnet-critic` on Execute's diff.
- On `strong-checkpoint` → back to Execute, no iteration burn.
- On `guidance` → log to `.build-loop/issues/` and proceed.
- Skip A on re-reviews after Iterate unless Iterate touched new files.
- **If `triggers.riskSurfaceChange` is true**, also dispatch `security-reviewer` (Sonnet 4.6, read-only) in parallel with `sonnet-critic`; load `Skill("build-loop:security-methodology")` for the rubric. Findings JSON: `CRITICAL` or `HIGH` → route back to Execute (no iteration burn, same as `strong-checkpoint`); `MEDIUM` / `LOW` → log to `.build-loop/issues/security-findings.json` and proceed.
- After Phase 3 Execute, also load `Skill("build-loop:defenseclaw-bridge")` if the build produced any agent-builder-style artifacts (`tool-contract*.md`, `agent-manifest*.md`, `guardrail*.md`, `system-boundary*.md`, `flow-topology*.md`, `role-card*.md`) — the bridge writes a DefenseClaw spec skeleton to `<project>/.defenseclaw/generated/`; spec-only, no runtime install.

## Sub-step B — Validate

Code graders → LLM-as-judge.

**IBR-first when present and UI work**: load `Skill("build-loop:ibr-bridge")` and run the quick-pass BEFORE any other validator:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ibr_quickpass.py --workdir "$PWD" --scope changed
```

Interpret the JSON: `pass == ran` → green-light, proceed to D; any `fail` → route the failing test to Iterate (test assertion is the rubric, no extra critic burn); `no_tests` or `ibr_unavailable` → fall through to scanners. The script writes `.build-loop/ibr-quickpass.json` for Sub-step D Gate 8 to read.

If `availablePlugins.ibr` and UI work AND quick-pass green, also invoke `ibr:design-validation` for web or `ibr:native-testing` for mobile for design-rule depth. If IBR is absent and the build touches UI files, paste `fallbacks.md#web-ui` into the validation subagent prompt.

### Plugin-tests advisory check (auto-runs when build touches plugin metadata; non-blocking)

If Phase 3 Execute's diff contains any of these path globs, run `Skill("build-loop:plugin-tests")` as part of Validate:

- `*.claude-plugin/plugin.json`, `*.claude-plugin/marketplace.json`
- `commands/*.md` (added/renamed/removed)
- `skills/*/SKILL.md` (added/renamed/removed, or `name:` / `description:` frontmatter changed)
- `agents/*.md` (added/renamed/removed)
- `.mcp.json` or any path referenced by `mcpServers` in plugin.json
- `hooks/hooks.json`

Concretely run from the repo root: `for t in scripts/test_skill_resolution.py scripts/test_plugin_manifest.py scripts/test_mcp_registration.py scripts/test_trigger_phrases.py scripts/test_bridge_preflight.py; do python3 "$t" || EXIT=1; done; exit ${EXIT:-0}`. **Findings are advisory, not blocking.** Record results to `.build-loop/state.json.pluginTests` as `{exit, failingScripts: [...], details}`. Surface a one-line summary in the Review-F Report. Do NOT route to Iterate on exit 1; do NOT gate the build.

### Memory-first gate (always on)

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

## Sub-step C — Optimize (opt-in)

Only when a mechanical metric exists AND user hasn't opted out. Load `build-loop:optimize`. Archive to `.build-loop/optimize/experiments/`. Feed results back to Review-B as evidence.

## Sub-step D — Fact-Check

Dispatch `fact-checker` + `mock-scanner` in parallel. **Plus** when code changed in this build, dispatch `Agent(subagent_type="build-loop:architecture-scout", prompt='task: review-rules')` in parallel — the scout runs the rules check, diffs against `.episodic/architecture/known_violations.json`, writes decisions for new violations via `scripts/capture_arch_violation.py`, and returns a `route:` recommendation in `follow_up`. If `route: "iterate"`, route the scout's findings into Iterate's prioritized work list. For cross-layer changes, escalate to `Skill("build-loop:architecture-review")` for the full 5-phase integrity review.

**Plus the new gates from `skills/build-loop/references/phase-4-review.md` §Sub-step D**:

- **Gate 6 — Version-Bump Advisor** (when `pluginWork: true`): `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/version_advisor.py --workdir "$PWD"`. State `hold` (default) → one-line note in Review-F. State `suggest` (marker `.build-loop/release-pending.md` exists) → propose semver and ask user before any plugin.json edit. Never auto-bump.
- **Gate 7 — UX Triage** (when `uiTarget != null`): `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ux_triage.py --workdir "$PWD" --clear`. Each `blocker`/`major` finding becomes a queue entry in `.build-loop/ux-queue/`. Then dispatch `performance-assessor` (full-app sweep) and `fact-checker` (broader file glob, full rendered surface) in parallel for the agent-augmentation portion; merge their findings into the same queue.
- **Gate 8 — IBR Coverage-Gap** (when `uiTarget != null` AND IBR available): read `.build-loop/ibr-quickpass.json.untested_surfaces`. For each, generate a draft `.ibr-test.json` to `.ibr-tests/_draft/<id>.ibr-test.json` via `mcp__plugin_ibr_ibr__plan_test` (programmatic only). Add a queue entry with `dimension: test-coverage`. Drafts never auto-promote.

Blocking (Gates 1–4) → Iterate. Queue entries (Gates 7–8) → flow into Phase 5's prioritized work list. Warnings → Report.

## Sub-step E — Simplify

Invoke `/simplify` on changed files. Preserve public API, tests, observability, user value, and modular boundaries needed for scalability, accuracy, security, testability, or stable interfaces. Do not simplify by removing necessary states, accuracy, scalability, accessibility, or real data paths. If integrated simplification is better, record `MODULARITY EXCEPTION`.

## Sub-step F — Report (final pass only)

See `references/memory-systems.md` for the run-entry write protocol and `references/learn-protocol.md` for Phase 6 hooks. Quick checklist:

- Write scorecard to `.build-loop/evals/`.
- Append run entry via `scripts/write_run_entry.py` (NEVER hand-write JSON).
- Invoke `Skill("build-loop:debugging-store")` for each newly resolved Review-B/Iterate failure.
- Invoke `Skill("build-loop:architecture-dead")` for the orphan scan.
- Run the deployment policy gate before any push/deploy.
- Run the episodic memory capture (transcript scan).

Review also checks the intent pack and modular systems pack: does the result advance the north star, satisfy the update intent, avoid fake data in user-decision paths, remove or avoid dead UI, use the simplest durable approach that protects user experience, keep ownership MECE, and preserve modular boundaries that matter?
