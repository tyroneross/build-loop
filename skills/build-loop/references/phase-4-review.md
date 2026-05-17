# Phase 4: Review (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the seven sub-steps A–G including Sub-step F Auto-Resolve with all 4 routing arms (auto/warn/confirm/block).

## Phase 4: Review — Critic, Validate, Fact-Check, Simplify, Auto-Resolve, Report

**Goal**: evaluate the built output against the rubric and decide pass / fail / iterate. Everything that used to live in phases 4.5, 4.7, 5, 7, 8, and 8.5 happens here as ordered sub-steps. One phase heading, seven sub-steps, single exit point.

Review runs every time we need an evaluation (initial post-Execute, and again after each Iterate pass). The report sub-step (G) writes final artifacts only on the LAST pass — intermediate Reviews skip it.

### Sub-step A: Critic (adversarial read-only)

Catch scope drift, patch-over-root-cause, missed edge cases, and rubric violations before spending tokens on full validation. Uses a separate read-only agent with no incentive to sandbag.

1. **Dispatch `commit-auditor`** at `scope: "build"` against the full build diff (`<pre_build_sha>..HEAD`). Replaces the retired `sonnet-critic` per plan §15.1 — one Opus judge across all checkpoints. The auditor has tools=[Read, Grep, Glob, Bash] (Bash for `git diff`), no Edit/Write. For the chunk-scope variant fired at Phase 3 step 7, see `agents/commit-auditor.md`.
2. **Input**: the rubric from `.build-loop/goal.md` + the implementer's diff (`git diff HEAD~1` or the changed-file set).
3. **Output**: JSON with `findings`, `strong_checkpoint_count`, `guidance_count`, `pass` boolean.
4. **Routing**:
   - `pass: true` → proceed to sub-step B (Validate)
   - `pass: false` with `strong-checkpoint` findings → route back to Execute for fixes (not Iterate — no iteration counter burn yet on critic-only failures)
   - Findings marked `guidance` → record in `.build-loop/issues/` and proceed
5. **Escalation**: if the same chunk fails critic twice, escalate the implementer to Opus per `model-tiering` skill §Escalation Triggers.
6. **Skip** on re-reviews after Iterate (critic already saw the diff at first pass) unless Iterate touched different files. Skip entirely for trivial chunks (single-file typo, config value).

### Sub-step B: Validate (graders + memory-first gate)

Test every criterion from Assess with evidence.

**UI validation — IBR-first when present** (`uiTarget != null`): load `Skill("build-loop:ibr-bridge")` and run the quick-pass protocol BEFORE static scanners or LLM judges:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ibr_quickpass.py --workdir "$PWD" --scope changed
```

If the suite passes, the changed surface is green-lit and Sub-step D continues. If any test fails, route the failing test directly to Iterate — its assertion is the rubric, no extra critic burn. If the script reports `no_tests`, the Coverage-gap gate (Sub-step D) generates initial drafts; Sub-step B falls through to static scanners in the meantime. Per the bridge skill, only headless/programmatic IBR surfaces are used — the IBR viewer is never opened.

**UI validation when IBR is absent**: paste `fallbacks.md#web-ui` into the validation subagent prompt. The fallback contains 10 specific grep checks (Gestalt violations, touch targets, missing handlers, missing aria-labels, status-pill anti-patterns, off-token colors, non-8pt spacing, console leftovers, mock data) plus a file-check matrix for landmarks, focus styles, and viewport tags. Findings get `⚠️ static-analysis only — install IBR for computed-CSS verification` flag in the Review-F report. This is the standalone UI validation path — degraded vs IBR, but not silent.

**UI input/output contract validation** (`uiTarget != null`): read the plan's `## UI Input/Output Contract` section and compare it to changed UI files before visual validation. Confirm every user input and system output in the changed surface has a data taxonomy, operation/domain verb, component mapping, state coverage, modality fallback when relevant, validation/security layer, and schema/API/design-system trace. Missing coverage is a Validate failure unless the change is copy-only and the contract explicitly says no data surface changed.

**Code-based graders first** (fast, deterministic):
```
test suite           → pass/fail
lint / type check    → pass/fail
build                → pass/fail
accessibility        → threshold pass/fail (if web)
schema validation    → pass/fail
custom assertions    → pass/fail
design-rule scan     → must-fix=0 pass/fail (uiTarget != null only)
ui io contract       → pass/fail (uiTarget != null only)
```

**Design-rule scan** (when `uiTarget != null`):
```
node "${CLAUDE_PLUGIN_ROOT}/skills/build-loop/scanners/audit-design-rules.mjs" --root=<project> --platform=<swiftui|react|web> --json
```
Exit 0 = clean. Exit 1 = warnings only (continue, log). Exit 2 = must-fix found (fail; route to Iterate).

This is the static-analysis gate that catches what mockup-parity misses — colored status pills, ungated `.repeatForever`, raw `UIColor` outside Theme, literal `cornerRadius`, body-copy `.font(.system(size:))`, icon-only `Image(systemName:)` without accessibility labels. Maintained in `scanners/audit-design-rules.mjs`, dependency-free Node 18+, per-platform packs.

**Visual validation** (REQUIRED when `uiTarget != null`): the static scanner cannot catch rendering bugs — an upside-down arc, an invisible track stroke, a row clipped behind a floating tab bar, a chip that wraps. After the scanner passes, render the actual screen via the platform's preferred tool:
- iOS / macOS / watchOS: `mcp__plugin_ibr_ibr__native_scan` against booted simulator (install + launch the build first)
- Web: `mcp__plugin_ibr_ibr__scan` against the dev server URL
- Fallback: `xcrun simctl io booted screenshot` or browser-driven `playwright` if IBR is unavailable

For returning-user states (post-onboarding screens, dashboards with data), use the DebugSeeder pattern (see `templates/ui-subagent-prompt.md` §DebugSeeder) so visual states can be verified in seconds without manual data entry. Build 55 of a real shipped app passed scanner exit 0 but rendered an upside-down semicircle gauge with stray tick marks because no one rendered the actual screen — visual validation is non-negotiable for UI work.

**Live HTTP/SSE smoke** (REQUIRED when `triggers.runtimeServer == true` AND the diff touches `runtimeServerInfo.server_module` OR `runtimeServerInfo.embedded_ui_module`): pytest with mocked SDKs is necessary but not sufficient for projects that ship a live server — it does not iterate real DOM trees, does not open SSE connections, and does not render embedded HTML. Implements decision `_unscoped/0003`. The 5-step procedure:

1. Restart the server in background. Read `state.json.runtimeServerInfo.start_command` if present, else fall back to `uv run <package> --serve --port <default_port>` derived from `pyproject.toml`'s package name and `runtimeServerInfo.default_port`. Redirect stdout/stderr to `/tmp/buildloop-serve.log` for forensic surface in Review-F.
2. Wait up to 15s for `/api/status` (or `/`) to return HTTP 200 — poll once per second.
3. Run a 5-second curl POST against the SSE route:
   ```
   curl -sN -X POST http://localhost:<port><sse_route> \
       -H 'Content-Type: application/json' -d '<minimal-prompt>' --max-time 5 \
       | grep -oE '"type":\s*"[^"]+"' | sort -u
   ```
4. Read the UI's event-handler switch at the locations from `runtimeServerInfo.event_handler_locations[]` and extract every handled event type (regex on `d\.type === '([^']+)'` and `d\.type == "([^"]+)"`). Skip this step when `embedded_ui_module: null` (API-only services have no embedded UI to compare).
5. **Fail the build** when an observed event type from step 3 has no matching handler arm from step 4 — this is the silent-server, ignored-client class of bug. Surface as a Validate failure → routes to Iterate.

If any infrastructure step fails (server won't start, curl errors, can't parse handler) → log evidence to `.build-loop/issues/live-smoke-<date>.md` and surface as `⚠️ untested live-flow` in Review-F. Do NOT fail the build on infrastructure issues — only on the specific server/client contract violation. Heavier integration tests (Playwright/Selenium) are still the right answer for full correctness; this gate is the cheapest check that catches what pytest-with-mocks cannot.

**LLM-as-judge graders second** (for nuanced criteria):
- Each criterion → its own focused judge prompt
- Binary pass/fail output only
- No multi-dimension scoring in a single prompt

**Evidence collection**:
- Every pass/fail must have evidence: command output, screenshot, or judge reasoning
- Use `verification-before-completion` for evidence-based claims
- No criterion marked "pass" without proof

**Runtime smoke gate (post-tests, pre-LLM-judges)**: after code-based graders pass, invoke `python3 scripts/runtime_smoke.py --changed-files <list> --workdir "$PWD" --json` whenever any changed file matches a runtime-smoke trigger. The script auto-detects a dev-server adapter from the project's manifest (Next.js today; FastAPI, Express, and SSE-consumer adapters are documented future slots). `pass` proceeds; `fail` routes to Iterate using the smoke envelope's `findings` as the rubric; `skipped` (no trigger matched or no adapter for this stack) records `runtime_smoke: skipped (<reason>)` in Review-F and proceeds — library-only repos never fail this gate. See `references/runtime-smoke-triggers.md` for the full trigger-pattern table and adapter roadmap, and `agents/build-orchestrator.md` §"Review-B: Runtime smoke gate" for the routing rules.

**Memory-first gate (on any failing criterion)**: before routing failures to Iterate, the orchestrator runs the gate (read_logs → synthesize symptom → invoke `Skill("build-loop:debugging-memory")` → act on verdict). See `agents/build-orchestrator.md` §Phase 4 sub-step B for the orchestrator's exact when-to-fire and gate-recording policy. **Memory is a hypothesis, not a patch — every verdict routes to Iterate as an adapted plan by default**:

- `KNOWN_FIX` → adapt prior incident as the Iterate fix plan. Direct-apply only when all three gates hold: file match + version match + second validation signal (stack frame, error class, or log entry). Otherwise behave as LIKELY_MATCH.
- `LIKELY_MATCH` → adapt prior incident as the Iterate fix plan
- `WEAK_SIGNAL` → note reference in the Iterate plan, investigate normally
- `NO_MATCH` → standard Iterate fallthrough; store at sub-step G Report for future learning

The memory gate is always on — the debugger (skills + MCP) is bundled with build-loop as of 0.6.0. If the MCP server fails to start, the orchestrator falls through to a local-grep fallback. The strict direct-apply triple-gate spec lives in `skills/debugging-memory/SKILL.md` §"Direct-apply gate (strict)".

**Output**: per-criterion pass/fail with evidence. Any `fail` → Iterate. All `pass` → sub-step C.

### Sub-step C: Optimize (opt-in, only with a mechanical metric)

Metric-driven autonomous optimization using Karpathy's autoresearch pattern. Opt-in — runs only when a mechanical metric exists AND the user hasn't disabled it.

**Load the `build-loop:optimize` skill for the full protocol.**

1. **Discover targets**: Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_loop.py --detect --workdir "$PWD"`.
2. **`simplify` is always available** when code changed: reduces line count in files changed by Execute. Metric = total lines, direction = lower, guard = build passes.
3. **Other targets** appear when the repo has the right tooling (build script → optimize-build, test runner → optimize-tests, bundler → optimize-bundle).
4. **Budget**: 3-5 iterations (polish, not deep optimization).
5. **Post-loop**: dispatch `overfitting-reviewer`. Archive to `.build-loop/optimize/experiments/`.

**Skip** when: no mechanical metric, build was trivial (<20 lines), or user opts out. Optimization results feed back into Validate as additional evidence.

### Sub-step D: Fact-Check & Mock Scan

Nothing false, fabricated, or placeholder reaches the user. Three gates, run in parallel. Load `phases/fact-check.md` for detailed guidance.

- **Gate 1 — Fact Checker**: Trace every rendered %, $, score, count, or assessment to its data source. Flag "always", "never", "100%", "guaranteed" — replace with accurate language unless genuinely absolute. Every rendered metric needs a traceable path: source → transformation → display.
- **Gate 2 — Mock Data Scanner**: Lightweight scan of production code paths for residual mock/placeholder data — hardcoded fake data, placeholder text, faker/random in display paths, stubs replacing real implementations. Exclude test files and dev-only code.
- **Gate 3 — Architectural Violation Check**: invoke `Skill("build-loop:architecture-rules")` (no plugin gate — the native skill no-ops cleanly when `.navgator/architecture/index.json` is absent). Executes `navgator rules --json` and classifies blocking (`circular-dependency`, `layer-violation`, `database-isolation`, `frontend-direct-db` at error) vs warning (`hotspot`, `high-fan-out`, `orphan`). Flags recurrences against `.navgator/lessons/lessons.json`. For cross-layer changes, escalate to `Skill("build-loop:architecture-review")` for the full integrity review.
- **Gate 4 — Plugin Cache Sync Check** (only when `pluginWork: true`): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_cache_sync.py --host claude --source <plugin-source-repo>` for Claude runtime surfaces. If the build changes Codex-visible surfaces (`.codex-plugin/`, `AGENTS.md`, `README.md`, `skills/`, or `commands/`), also run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_cache_sync.py --host codex --source <plugin-source-repo>`. `[DIVERGED]`, `[MISSING IN CACHE]`, or stale installed Codex versions are **blocking** when they affect the host being used — runtime invocations will hit stale or missing files. Fix: resync/reinstall the local cache. Defer version bumps until the feature batch is declared complete (see Gate 6). Missing cache with no installed version skips silently (user has not installed the plugin, nothing to break).
- **Gate 5 — Design-Rule Scanner** (only when `uiTarget != null`): run `audit-design-rules.mjs` across full project (broader than Sub-step B's changed-files scope). Surfaces any pre-existing must-fix violations newly observable due to scanner rule additions. Pre-existing findings on first run are logged to `.build-loop/issues/` with break-what-if analysis (user decides scope). New-content findings are blocking. See `phases/ui-validation.md` for tuning.
- **Gate 5a — UI Input/Output Contract Scan** (only when `uiTarget != null`): walk the full rendered surface touched by the build and trace every input/output against `## UI Input/Output Contract`. Flag user-visible data without a component mapping, validation layer, state branch, or source trace. New-content gaps are blocking; pre-existing gaps are logged to `.build-loop/issues/` with user impact and recommended follow-up.
- **Gate 6 — Version-Bump Advisor** (only when `pluginWork: true`): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/version_advisor.py --workdir "$PWD"`. Default state is `hold` — emits a one-line note in Review-F: `"N commits accumulated since vX.Y.Z. Holding version. Create .build-loop/release-pending.md when the batch is ready."` Switches to `suggest` only when `.build-loop/release-pending.md` exists; in `suggest` mode, Review-F proposes `vA.B.C` (semver inferred from Conventional Commits) and asks for explicit user confirmation before any plugin.json edit. Never auto-bumps. Never blocks. The marker file is the user's release signal; build-loop only ever advises.
- **Gate 7 — UX Triage** (only when `uiTarget != null`): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ux_triage.py --workdir "$PWD" --clear`. Static-scans for four dimensions — interactability, performance, data-accuracy beyond current scope, usability — across the full project (not just changed files). Each `blocker` or `major` finding becomes a queue entry in `.build-loop/ux-queue/<id>.md` with a complete fix plan, evidence, files-touched, and an `architecture_impact` flag. Minor findings stay in the Review-F report only. The agent layer augments static findings with `performance-assessor` and `fact-checker` agent dispatches against the same surface for dimensions the static scanner can't fully cover. Queue entries feed into Phase 5 Iterate (see "Iterate input contract" below). Never block the current build — UX rot fixes ride along, they don't gate.
- **Gate 8 — IBR Coverage-Gap** (only when `uiTarget != null` AND IBR available): read `.build-loop/ibr-quickpass.json.untested_surfaces` (written by Sub-step B). For each uncovered surface, generate a draft `.ibr-test.json` via `mcp__plugin_ibr_ibr__plan_test` (or `ibr generate-test --headless`), write to `.ibr-tests/_draft/<id>.ibr-test.json`, and add a queue entry to `.build-loop/ux-queue/` with `dimension: test-coverage`. Drafts are suggestions — the user accepts by `mv` out of `_draft/`, rejects by deleting. Never auto-promotes. See `Skill("build-loop:ibr-bridge")` Coverage-gap protocol for full detail.

Blocking issues (Gates 1-4) → route to Iterate. Queue entries (Gates 7-8) → flow into Phase 5's prioritized work list. Warnings → include in Report (sub-step G). Auto-bumping is forbidden.

### Sub-step E: Simplify (trim the diff)

Remove incidental complexity added during Execute/Iterate without changing behavior.

Run `/simplify` (or load the `simplify` skill directly) against the changed files. Focus:
- Inline single-use helpers extracted "just in case"
- Delete dead branches, commented-out code, unused imports
- Collapse try/except that catches a thing that can't happen
- Remove validation for invariants the type system or upstream already guarantees
- Reduce abstractions that have exactly one call site

Preserve: public API surface, test coverage, observability (logging/tracing), documented behavior, and modular boundaries that protect user value, scalability, accuracy, security, testability, or stable interfaces. If an integrated simplification is better, document `MODULARITY EXCEPTION: <reason>`. For **plugin work**: also re-run `plugin-dev/scripts/hook-linter.sh` against any touched `hooks.json` and `grep` the manifest for `../` or bare paths.

#### Deep mode (opt-in — default is the light pass above)

Light E above is the **default and unchanged**. Deep mode is an opt-in flag (`deepSimplify: true` in `.build-loop/config.json`, or `--deep-simplify` on the run) that adds *one consolidated, diff-scoped pass* on top of light E for **changed Python files only**. It goes beyond light E's conservative readability trimming — it may change abstraction/behavior shape — but only for *clear* wins, and it adds **no new safety machinery** (reuses the gates already in this Review pass).

1. **Detect.** Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/complexity_detector.py --changed-files <build's changed .py files> --json`. The stdlib-`ast` detector (zero deps) emits a ranked hotspot envelope (`high_complexity`, `deep_nesting`, `accidental_quadratic`, `redundant_multipass`, `needless_indirection`). It is diff-scoped — never a whole-repo scan. Unparseable / non-`.py` / missing paths are skipped, never fatal.
2. **Propose.** For each `severity: "high"` hotspot, the running build-loop subagent (no external LLM API) proposes a simpler — and, where it falls out naturally, faster — rewrite. `severity: "advisory"` hotspots are *not* rewritten in-pass; they surface as advisory only.
3. **Apply vs. advise.** APPLY a rewrite **only if all hold**: (a) it is a clear win (not a lateral rewrite); (b) the existing test subset for the touched files still passes — reuse the Sub-step B Validate machinery on E's changed paths only, not the full gate; (c) public signatures and observable behavior are unchanged — the detector's own AST-signature comparison plus the existing **commit-auditor** behavior advisory. If a rewrite is ambiguous, uncertain-architectural, or fails (b)/(c) → do **not** apply; emit it as an **advisory variance via the existing commit-auditor surface** (Phase 4 Report `## Notes from judges`). No new verifier, no perf gate, no benchmark, no cost-proxy — the "faster" outcome is an unmeasured bonus, never asserted or gated.
4. **Report.** Log one line: `[Simplify:deep] N hotspots, M applied, K advised`; record applied/advised counts in the Sub-step G report. An applied rewrite that later fails a re-validate routes like any Sub-step B failure (Phase 5 Iterate, existing 5x cap).

Deep-mode applied edits flow through the existing single-writer Phase 3 commit contract — they are part of the build's diff, not a side-channel. With the flag off, none of the above runs and Sub-step E is the light pass verbatim.

#### Sub-step E telemetry

**Sub-step E telemetry (mandatory, every Review pass, all builds).** After E completes for this Review pass, the orchestrator MUST append one row to `state.json["reviewE"]` via:

```python
update_execution_state(state_path, 'review_e_pass',
                        files_scanned=[<files E actually inspected this pass>],
                        is_final=<True iff this is the final Review pass>)
```

This is **measurement infrastructure, not a factor** — it is present and identical on every build regardless of any cadence policy. It records *what E did this pass*; it must NOT change *what E does*. `pass_idx` auto-derives from the existing row count (0-based). When a cadence policy scopes E to only iterate-changed files on Review re-entry, the recorded `files_scanned` naturally shrinks on non-first passes — that difference is the signal a deterministic scorer reads. Telemetry write failure is logged, never blocks the build.

### Sub-step F: Auto-Resolve (drain non-destructive open items)

Drain the candidate auto-resolve queue before writing the final scorecard. Items in the queue come from three sources:

- **Sub-step A Critic** — variances with `auto_fixable: true` AND `severity ≤ minor` AND `suggestion` naming a single `file:line` (canonical commit-auditor variance fields per `agents/commit-auditor.md`)
- **Sub-step D Fact-Check & Mock Scan** — non-blocking gate findings (e.g. `Plugin Cache Sync` divergence, `Version-Bump Advisor` notes when `release-pending.md` is absent, single-file documentation drift)
- **Operator queue** — items previously deferred via the `## Held` section of a prior build's report

For each item:

1. Build a short `<label>` and the corresponding shell `<command>` describing the action.
2. Invoke `python3 scripts/autonomy_gate.py --workdir "$PWD" --action "<label>" --command "<command>" --json` (single source of truth — see `references/autonomy-config.md`).
3. Route on the verdict:
   - `auto` (exit 0) → execute the action via the appropriate implementer/script and record the result in `## Done` for Report.
   - `warn` (exit 0) → execute the action (does not block), record in `## Done` with `[warn] <reason>` prefix, and emit a one-line entry to `state.json.runs[].autonomyEvents[]` for match-rate tracking. See `references/autonomy-config.md` §"Warn-before-block workflow" for the autonomyEvents shape.
   - `confirm` (exit 1) → record in `## Held` with the `reason` field from the gate's envelope verbatim. Do NOT prompt the operator inline.
   - `block` (exit 2) → record in `## Blocked` with the same reason field.

Cap auto-execute attempts per item at the existing Iterate ceiling (5x). After the cap, demote to `## Held` with reason `"auto-resolve cap reached after N attempts"`.

**What does NOT belong in Auto-Resolve:**
- Strong-checkpoint findings from Sub-step A — those continue routing back to Execute (no iteration counter burn).
- Sub-step B Validate failures — those route to Phase 5 Iterate.
- Anything matching deployment_policy.py heuristics — autonomy_gate delegates to deployment_policy automatically; the verdict still flows through `auto | confirm | block`, but the source-of-truth is deployment_policy for those items.

The auto-resolve queue is rebuilt from scratch per Phase 4 invocation. Items not drained on a given pass don't carry forward unless explicitly re-surfaced by Sub-steps A/D on the next pass.

### Sub-step G: Report (only on final Review pass)

Runs only when all prior sub-steps pass OR when iteration cap is hit. Writes final artifacts and closes the build.

Final report sections, in this order:

- `## Done` — every verified pass + every Auto-Resolve `auto` item, with one-line evidence each.
- `## Held` — items Auto-Resolve verdicted as `confirm`, with the `reason` field from `autonomy_gate.py` quoted verbatim. The user may run any held command manually if they want to. Build-loop does NOT prompt or auto-execute these.
- `## Blocked` — items Auto-Resolve verdicted as `block`, same shape as Held.
- `## Status markers` — ✅ Known / ⚠️ Untested / ❓ Unfixed (existing convention; keep this section).

**Forbidden in the report**:
- Recommendation-list headers (e.g. headers that invite operator selection of which items to execute)
- "Next Action" sentences that read like questions
- Any bullet phrased as `Want me to X?` or `Should I Y?`
- Any list that presents items as choices for the operator to pick from

If a category is empty (no Held items, no Blocked items), write the header followed by `_(none)_`. Do not omit empty sections.

Write scorecard to `.build-loop/evals/YYYY-MM-DD-<topic>-scorecard.md`.

**Debugger store + outcome** (if `availablePlugins.claudeCodeDebugger`): for each resolved Review-B/Iterate failure, invoke `store` MCP with `{symptom, root_cause, fix, tags, files}`. For each Review-B memory-gate entry where a prior `KNOWN_FIX`/`LIKELY_MATCH` was applied, invoke `outcome` with `worked`/`failed`/`modified`. Both sides of the memory feedback loop — skipping either breaks learning.

**Orphan scan**: invoke `Skill("build-loop:architecture-dead")` — runs `navgator dead`, diffs against the Phase 1 Assess baseline, surfaces ONLY new orphans introduced this build. No-ops cleanly when `.navgator/architecture/index.json` is absent.

**Deployment policy gate** (before any push/deploy): run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/deployment_policy.py" \
  --workdir "$PWD" \
  --command "$CANDIDATE_DEPLOY_COMMAND"
```

Follow the returned `action`: `auto` may proceed after Review passes; `confirm` requires an explicit user confirmation in chat before running the command; `block` must not run and should be reported as a configured repo policy. Defaults favor speed for preview/TestFlight and safety for production/unknown.

**Post-deploy verification gate (after a deploy actually ran)**: once a deploy executed — i.e. the deployment policy gate returned `auto` and the deploy/push command ran, or the pushed branch auto-deploys via Vercel — invoke `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_deploy.py --workdir "$PWD" --changed-route <route> [--changed-route <route> ...] --json`. The script detects a Vercel link (`.vercel/project.json` or `vercel.json`), resolves the latest production deployment, polls `vercel inspect` to a terminal state, then probes the prod root + each changed route. `pass` proceeds; `fail` routes to Iterate using the envelope's `findings` as the rubric; `skipped` (no Vercel link, CLI missing, not authed, or other transient infra) records `deploy_verify: skipped (<reason>)` in Review-F and proceeds. An auth-gated `401`/`403` on a protected route is **healthy** (function deployed and running) — only a `5xx`/build-error is a real failure. Never block the build on infra. See `agents/build-orchestrator.md` §"Review: Post-deploy verification gate" for the routing rules and `fallbacks.md#web-deploy-verify` for the inline degraded procedure.

**Append a run entry to `.build-loop/state.json.runs[]`** for Learn (Phase 6) to scan. Delegate to `scripts/write_run_entry.py` — do not hand-write JSON; the script owns the schema, atomic writes, legacy-state migration, and per-experiment confound fan-out. Invocation example in `agents/build-orchestrator.md` §Report & Memory Write. Schema (as the script emits):

```json
{
  "run_id": "run_<ISO-basic>_<sha256(goal)[:8]>",
  "date": "<ISO-8601 UTC>",
  "goal": "<short goal text>",
  "outcome": "pass | fail | partial",
  "phases": { "assess": { "status": "pass|fail", "duration_s": N, "root_cause": "?" }, "plan": {...}, "execute": {...}, "review": {...}, "iterate": {...} },
  "diagnosticCommands": ["shell commands run during build"],
  "filesTouched": ["absolute paths edited"],
  "manualInterventions": [{ "phase": "review", "note": "short description" }],
  "active_experimental_artifacts": []
}
```

Capture `filesTouched` from `git diff --name-only` relative to the pre-build HEAD. `diagnosticCommands` and `manualInterventions` come from orchestrator state tracking. `active_experimental_artifacts` lists experimental skills that triggered this run (for Learn's confound tracking).
