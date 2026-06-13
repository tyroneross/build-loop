<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 4: Review (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the seven sub-steps A–G including Sub-step F Auto-Resolve with all 4 routing arms (auto/warn/confirm/block).

## Phase 4: Review — Critic, Validate, Fact-Check, Simplify, Auto-Resolve, Report

**Goal**: evaluate the built output against the rubric and decide pass / fail / iterate. Everything that used to live in phases 4.5, 4.7, 5, 7, 8, and 8.5 happens here as ordered sub-steps. One phase heading, seven sub-steps, single exit point.

Review runs every time we need an evaluation (initial post-Execute, and again after each Iterate pass). The report sub-step (G) writes final artifacts only on the LAST pass — intermediate Reviews skip it.

### Sub-step A: Critic (adversarial read-only)

Catch scope drift, patch-over-root-cause, missed edge cases, and rubric violations before spending tokens on full validation. Uses a separate read-only agent with no incentive to sandbag.

0. **Quality-gate trigger profile (QM v0.13.0, single source of truth — F4)**: run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review_trigger.py --context .build-loop/state.json $(git diff --name-only origin/main..HEAD | sed 's/^/--changed-file /') --json`. The returned `{independent_review_required, cross_vendor_required, reasons}` is the **single source** for when adversarial/cross-vendor review fires — don't invent separate heuristics. Triggers cover `riskSurfaceChange`, architecture-boundary crossing, new dependency/runtime, auth/file/network/persistence/security/model-tool changes, and low-confidence critic output.
1. **Dispatch `independent-auditor`** at `scope: "build"` against the full build diff (`<pre_build_sha>..HEAD`). Consolidated 2026-05-23 — single source of truth replacing both retired `commit-auditor` (chunk + build scope) and earlier retired `sonnet-critic`. The auditor has tools=[Read, Grep, Glob, Bash] (Bash for `git diff`), no Edit/Write. For Phase 3 step 7 (per-chunk advisory), dispatch the same `independent-auditor` with `diff_sha_range: <chunk_parent_sha>..<chunk_sha>` and `reason: "chunk-advisory"`. **Cross-vendor (QM v0.13.0)**: when the profile sets `cross_vendor_required` and a peer host is reachable (rally channel / `codex exec`), fan out a second-vendor reviewer in parallel and reconcile by severity+evidence; if no peer host can execute, record `cross_vendor: untested` — never claim it ran (per host-agent-is-the-LLM, this is the host's peer, not a vendored API call).

   **Auditor dispatch ladder & parent-dispatch contract (GAP-1 — the LLM auditor is never silently skipped).** Dispatching `independent-auditor` via `Agent(subagent_type=...)` requires the Agent tool. A *nested* orchestrator — one dispatched as a subagent (`Agent(subagent_type="build-loop:build-orchestrator")`, Mode B) or running per-commit mode — does **not** have the Agent tool, because the harness blocks sub-subagents. The historical failure (2026-06-06 IBR retro, 4+ runs): the nested orchestrator silently substituted inline self-reasoning and reported it as "independent-auditor ran inline", rubber-stamping a HIGH cookie-leak + 2 MEDIUM findings a real dispatch later caught. To make that impossible, walk this ladder and record `auditor_status` honestly:

   1. **Agent tool present** (top-level / Mode A) → dispatch `independent-auditor` at build scope as above → `auditor_status: ran:dispatched-agent`.
   2. **No Agent tool, peer host reachable** → run the auditor as a **peer process** over the same channel the cross-vendor reviewer uses (rally channel handoff / `codex exec <prompt>` — reachable because the orchestrator retains Bash even when nested). Reconcile the peer's JSON envelope into `.build-loop/judge-decisions.json` with `judge_id: "independent-auditor"` (a real, cross-host verdict that satisfies the `write_run_entry --scope build` gate honestly) → `auditor_status: ran:peer-host(<host>)`. Prefer this over the not-run signal whenever a peer host can execute.
   3. **Neither reachable** → `auditor_status: not-run:parent-must-dispatch` (or `cross-vendor-deferred` when a peer host exists but cannot execute this pass). Then, **all of**: (a) do NOT write any `judge_id` containing `independent-auditor` for inline self-reasoning — *inline self-audit is not the independent auditor*, and a mislabeled record would defeat the gate; (b) do NOT report a `scope=build` code-touching run as a review-complete `pass` — use `outcome: partial`; (c) surface `auditor_status: not-run:parent-must-dispatch` in the orchestrator's return envelope.

   **Parent-dispatch contract.** A run or commit whose envelope carries `auditor_status: not-run:parent-must-dispatch` (or `cross-vendor-deferred`) is **NOT review-complete**. The dispatching parent — the top-level session that *does* have the Agent tool (the `/build-loop:run` skill body, or the human-driving session) — MUST, on receiving such an envelope: (1) dispatch `Agent(subagent_type="build-loop:independent-auditor")` on the run's diff range (`<pre_build_sha>..HEAD`); (2) append its verdict to `.build-loop/judge-decisions.json`; (3) re-run `write_run_entry --scope build` so the review-completeness gate passes, and only then finalize Report. The existing gate (`scripts/write_run_entry`, `review_completeness_error` → exit 3 on a `pass` + `scope=build` + files-touched run lacking a real auditor verdict) is the structural backstop: it cannot be satisfied by an honest nested orchestrator, which is what forces the parent to finish the audit instead of shipping un-audited code.
2. **Input**: the rubric from `.build-loop/goal.md` + the implementer's diff (`git diff HEAD~1` or the changed-file set).
3. **Output**: JSON envelope with `verdict` ∈ {yay, nay, suggest_correction, look_again} + normalized `findings[]` (`severity: critical|high|medium|low`). See `agents/independent-auditor.md` for the full schema.
4. **Routing** (QM v0.13.0 normalized severities; legacy `major→high`, `minor→medium`, `info→low`):
   - `verdict: yay` → proceed to sub-step B (Validate)
   - `verdict: nay` (paired with a `critical`/`high` finding) → route back to **Execute** for fixes (strong-checkpoint; no iteration counter burn yet on critic-only failures). If the diff reveals the *plan* is wrong, re-plan instead — orchestrator's call.
   - `verdict: suggest_correction` with `auto_fixable: true` AND `severity in {medium, low}` → Auto-Resolve queue (Sub-step F)
   - `verdict: look_again` → operator gathers the named `missing_artifacts` and re-runs the auditor
   - `severity: medium|low` findings → record in `.build-loop/issues/` and proceed; `critical|high` never proceed silently (they block the final pass — see Sub-step G no-critical/high exit gate)
5. **Escalation**: if the same chunk fails critic twice, escalate the implementer to Opus per `model-tiering` skill §Escalation Triggers.
6. **Skip** on re-reviews after Iterate (critic already saw the diff at first pass) unless Iterate touched different files. Skip entirely for trivial chunks (single-file typo, config value).
7. **Push-hold marker (set on blocking verdict, clear on resolution)** — close the "autonomous push of un-reviewed work" defect at the git layer, not the app layer. When the auditor returns `verdict: nay` OR `verdict: suggest_correction` OR `verdict: look_again` AND those findings are NOT yet resolved, immediately set the push-hold marker so a parallel autonomous push (self-review `apply_push`, `codex-autonomy-poller`, any path that doesn't consult `deployment_policy.py`) gets blocked by `hooks/git/pre-push`:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/push_hold.py --set \
       --source review-a \
       --reason "<verdict> from independent-auditor (run <run_id>)" \
       --auditor-verdict "<verdict>" \
       --finding-ids "<comma-separated finding ids>" \
       --run-id "<run_id>" --json
   ```

   The marker is auto-detected as a hold by the pre-push hook even when the orchestrator crashes mid-run — that's the whole reason it lives at the git layer. On re-audit pass (verdict `yay`, OR every prior `critical`/`high` finding now has `resolved: true` in `state.json.runs[-1].judge_decisions[]`), CLEAR the marker:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/push_hold.py --release \
       --reason "auditor findings resolved (run <run_id>)" --json
   ```

   The state.json signal is a backstop: if the marker was somehow lost, `push_hold.evaluate_push` will still detect an unresolved blocking verdict in `runs[-1].judge_decisions[]` and block. The explicit marker takes precedence; both are honored. Bypass exists at `BUILDLOOP_PUSH_HOLD_BYPASS=1` (logged to `.build-loop/audit-log.md`) for genuine emergencies — never in autonomous mode without an explicit operator decision. The pre-push hook is installed via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/install_git_hooks.py --install` (idempotent; mirror of the existing `audit_before_commit.py` install pattern).

### Sub-step B: Validate (graders + memory-first gate)

Test every criterion from Assess with evidence.

**UI validation — build-loop-owned route** (`uiTarget != null`): dispatch `ui-validator` first, then run the static design-rule scanner and UI input/output contract check. Build-loop does not auto-route to IBR for validation. If the user explicitly requested IBR, treat that as a manual auxiliary validator and keep its findings out of the default gate order.

**UI validation fallback**: paste `fallbacks.md#web-ui` into the validation subagent prompt when `ui-validator` cannot render the route. The fallback contains 10 specific grep checks (Gestalt violations, touch targets, missing handlers, missing aria-labels, status-pill anti-patterns, off-token colors, non-8pt spacing, console leftovers, mock data) plus a file-check matrix for landmarks, focus styles, and viewport tags. Findings get `⚠️ static-analysis only — browser/simulator evidence unavailable` in the Review-G report. This is the standalone UI validation path — degraded vs rendered validation, but not silent.

**UI input/output contract validation** (`uiTarget != null`): read the plan's `## UI Input/Output Contract` section and compare it to changed UI files before visual validation. Confirm every user input and system output in the changed surface has a data taxonomy, operation/domain verb, component mapping, state coverage, modality fallback when relevant, validation/security layer, and schema/API/design-system trace. Missing coverage is a Validate failure unless the change is copy-only and the contract explicitly says no data surface changed.

**Calm Precision core-consideration validation** (`uiTarget != null`): check `.build-loop/app-contract/ui.md` or the implementer return envelope for the relevant Calm Precision principles, foundations, and implementation effects. Missing consideration is a Validate failure for non-trivial UI work, because Calm Precision is a design gate, not a passive reference.

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
- iOS / macOS / watchOS: `xcrun simctl io booted screenshot` after installing and launching the build, or the built-in native AX driver for macOS interaction checks
- Web: browser/screenshot tooling against the dev server URL
- Fallback: static scanner + explicit missing-visual-evidence note

**The audit is mandatory; the verdict is advisory.** The screenshot/browser artifact path MUST be written to `state.json.runs[].artifacts.uiAudit[]` and surfaced in the Phase 4 Report `## Notes from judges` section. **Findings are WARN-only — never block the build** (same posture as `synthesis-critic`). The user retains final visual judgment; build-loop's job is to guarantee the artifact lands in the operator's view before any TestFlight/preview/production push. Subjective UI judgments (intended chrome change vs scope creep, deliberate layout shift vs regression) cannot be mechanically distinguished from intended changes — a fail-closed audit would halt every legitimate UI change with false-positive layout-shift noise. Per [[pattern_buildloop_coordination_default]]. **Scope-creep signal:** if the diff vs prior baseline shows substantial pixel delta on routes the plan did NOT name as touched, surface in `## Notes from judges` as `scope_creep_signal` with the route list — advisory only; build does NOT pause and does NOT route to `## Held`.

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

**Pytest-collection gate (full-suite-load check, every run)**: after code-based graders pass and before LLM judges, invoke `python3 scripts/pytest_collect_gate.py --workdir "$PWD" --json` on Python-bearing repos. The gate runs `pytest scripts/ tests/ --collect-only` with `PYTHONPATH` stripped (matching the spec's `env -u PYTHONPATH` discipline). Collection-only is the bar — it does NOT require the full suite to execute green (db/live tests legitimately skip via their markers); it only verifies that every test module *loads*. `pass` (exit 0, no findings) proceeds; `fail` (exit 1, one or more import/syntax errors) routes to Iterate using `findings[]` as the rubric — each finding carries `{file, line, error_class, message}` pointing at the broken module so the next iteration fixes the import rather than papering over the test; `exit 2` (runner error — pytest not found or a usage error with no parseable output) carries `status="fail"` with `error_class="RunnerError"` and `stderr_tail` — treat it exactly as `fail` and route to Iterate; `skipped` (no `pyproject.toml`/`pytest.ini`/`setup.cfg` and no test paths — library-only repo) records `pytest_collect: skipped (<reason>)` in Review-F and proceeds. **Non-standard layouts**: when `pyproject.toml` is present but the default `scripts/`/`tests/` paths are absent, the gate skips with a loud reason naming the gap — pass `--paths <dir> [...]` so a Python-bearing repo with tests elsewhere is not silently bypassed. **Why this gate exists** (every issue is a systems issue): build-loop's run gate historically scoped to changed-area tests, so a broken import that quietly removed an entire test module from coverage would not fail the build — exactly how 8750d2a's psycopg breakage and the EXECUTION_SCHEMA_VERSION miss hid for multiple runs. The collection gate closes that gap with one cheap check; the gate file is `scripts/pytest_collect_gate.py`, its regression tests are `scripts/test_pytest_collect_gate.py`.

**Memory-first gate (on any failing criterion)**: before routing failures to Iterate, the orchestrator runs the gate (read_logs → synthesize symptom → invoke `Skill("build-loop:debugging-memory")` → act on verdict). See `agents/build-orchestrator.md` §Phase 4 sub-step B for the orchestrator's exact when-to-fire and gate-recording policy. **Memory is a hypothesis, not a patch — every verdict routes to Iterate as an adapted plan by default**:

- `KNOWN_FIX` → adapt prior incident as the Iterate fix plan. Direct-apply only when all three gates hold: file match + version match + second validation signal (stack frame, error class, or log entry). Otherwise behave as LIKELY_MATCH.
- `LIKELY_MATCH` → adapt prior incident as the Iterate fix plan
- `WEAK_SIGNAL` → note reference in the Iterate plan, investigate normally
- `NO_MATCH` → standard Iterate fallthrough; store at sub-step G Report for future learning

The memory gate is always on. Build-loop bundles native debugging-memory skills and file-backed search/store, with standalone Coding Debugger available only as an optional cross-project memory plugin when explicitly installed. If structured memory is unavailable, the orchestrator falls through to the local-grep fallback. The strict direct-apply triple-gate spec lives in `skills/debugging-memory/SKILL.md` §"Direct-apply gate (strict)".

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

### Sub-step D: Fact-Check & Mock/Privacy Scan

Nothing false, fabricated, or placeholder reaches the user. Three gates, run in parallel. Load `phases/fact-check.md` for detailed guidance.

- **Gate 1 — Fact Checker**: Trace every rendered %, $, score, count, or assessment to its data source. Flag "always", "never", "100%", "guaranteed" — replace with accurate language unless genuinely absolute. Every rendered metric needs a traceable path: source → transformation → display.
- **Gate 2 — Mock/Privacy Data Scanner**: Run via `mock-scanner`. Lightweight scan of production code paths and public release/package surfaces for residual mock/placeholder data and private data leaks — hardcoded fake data, placeholder text, faker/random in display paths, stubs replacing real implementations, live-looking API keys/secrets, absolute local paths, private vault/wiki/session paths, persona/profile exports, customer/user lists, resumes, calendars, private notes, transcripts, hostnames, Rally runtime logs, worktree bundles, and other personal or machine-specific data. Exclude test files, dev-only code, and clearly synthetic documentation examples.
- **Gate 3 — Architectural Violation Check**: invoke `Skill("build-loop:architecture-rules")` (no plugin gate — the native skill no-ops cleanly when `.navgator/architecture/index.json` is absent). Executes `navgator rules --json` and classifies blocking (`circular-dependency`, `layer-violation`, `database-isolation`, `frontend-direct-db` at error) vs warning (`hotspot`, `high-fan-out`, `orphan`). Flags recurrences against `.navgator/lessons/lessons.json`. For cross-layer changes, escalate to `Skill("build-loop:architecture-review")` for the full integrity review.
- **Gate 4 — Plugin Cache Sync Check** (only when `pluginWork: true`): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_cache_sync.py --host claude --source <plugin-source-repo>` for Claude runtime surfaces. If the build changes Codex-visible surfaces (`.codex-plugin/`, `AGENTS.md`, `README.md`, `skills/`, or `commands/`), also run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_cache_sync.py --host codex --source <plugin-source-repo>`. `[DIVERGED]`, `[MISSING IN CACHE]`, or stale installed Codex versions are **blocking** when they affect the host being used — runtime invocations will hit stale or missing files. Fix with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sync_plugin_cache.py --source <plugin-source-repo> --host <claude|codex|all>`, which syncs committed `HEAD` by default; use `--dirty --file <path>` only for explicit temporary runtime testing. Defer version bumps until the feature batch is declared complete (see Gate 6). Missing cache with no installed version skips silently (user has not installed the plugin, nothing to break).
- **Gate 5 — Design-Rule Scanner** (only when `uiTarget != null`): run `audit-design-rules.mjs` across full project (broader than Sub-step B's changed-files scope). Surfaces any pre-existing must-fix violations newly observable due to scanner rule additions. Pre-existing findings on first run are logged to `.build-loop/issues/` with break-what-if analysis (user decides scope). New-content findings are blocking. See `phases/ui-validation.md` for tuning.
- **Gate 5a — UI Input/Output Contract Scan** (only when `uiTarget != null`): walk the full rendered surface touched by the build and trace every input/output against `## UI Input/Output Contract`. Flag user-visible data without a component mapping, validation layer, state branch, or source trace. New-content gaps are blocking; pre-existing gaps are logged to `.build-loop/issues/` with user impact and recommended follow-up.
- **Gate 6 — Version-Bump Advisor** (only when `pluginWork: true`): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/version_advisor.py --workdir "$PWD"`. Default state is `hold` — emits a one-line note in Review-F: `"N commits accumulated since vX.Y.Z. Holding version. Create .build-loop/release-pending.md when the batch is ready."` Switches to `suggest` only when `.build-loop/release-pending.md` exists; in `suggest` mode, Review-F proposes `vA.B.C` (semver inferred from Conventional Commits) and asks for explicit user confirmation before any plugin.json edit. Never auto-bumps. Never blocks. The marker file is the user's release signal; build-loop only ever advises.
- **Gate 7 — UX Triage** (only when `uiTarget != null`): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ux_triage.py --workdir "$PWD" --clear`. Static-scans for four dimensions — interactability, performance, data-accuracy beyond current scope, usability — across the full project (not just changed files). Each `blocker` or `major` finding becomes a queue entry in `.build-loop/ux-queue/<id>.md` with a complete fix plan, evidence, files-touched, and an `architecture_impact` flag. Minor findings stay in the Review-F report only. The agent layer augments static findings with `performance-assessor` and `fact-checker` agent dispatches against the same surface for dimensions the static scanner can't fully cover. Queue entries feed into Phase 5 Iterate (see "Iterate input contract" below). Never block the current build — UX rot fixes ride along, they don't gate.
- **Gate 8 — UI Coverage-Gap** (only when `uiTarget != null`): compare changed surfaces against existing project test files and the UI input/output contract. If a changed critical surface has no interaction/render coverage, add a queue entry to `.build-loop/ux-queue/` with `dimension: test-coverage` and a proposed repo-native test plan. Build-loop does not auto-draft `.ibr-test.json` files.

Blocking issues (Gates 1-4) -> route to Iterate; do not halt the run. For Gate 2 privacy findings, the orchestrator invokes the appropriate implementer, auditor, or specialist agent to remediate, then re-runs validation. Prefer `.gitignore` plus untracking for runtime/generated files, archive or private-store relocation over deletion for useful evidence, and redaction/scrubbing over removing useful public documentation. Queue entries (Gates 7-8) -> flow into Phase 5's prioritized work list. Warnings -> include in Report (sub-step G). Auto-bumping is forbidden.

### Sub-step E: Simplify (trim the diff)

Simplify = remove dead code AND restructure over-complex logic/architecture into clearer, equal-or-better-performing forms; preserve behavior + correctness. Both categories run as the default pass on every build.

Run `/simplify` (or load the `simplify` skill directly) against the changed files. The running build-loop subagent reasons over the diff directly and language-agnostically — no external tool required. Focus:

**Dead code (remove):**
- Inline single-use helpers extracted "just in case"
- Dead branches, commented-out code, unused imports
- Collapse try/except that catches a thing that can't happen
- Remove validation for invariants the type system or upstream already guarantees
- Reduce abstractions that have exactly one call site

**Over-complex logic/architecture (restructure — clear wins only):**
- Deep nesting that flattens without behavioral change (early-return, extracted predicate)
- Duplicated logic that a single well-named extraction eliminates (DRY)
- Accidental-quadratic or redundant multi-pass loops where a single-pass equivalent is obvious
- Needless indirection layers that obscure the data flow without protecting a boundary

For changed Python files, `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/complexity_detector.py --changed-files <changed .py files> --json` is a precise accelerator — its ranked hotspot envelope (`high_complexity`, `deep_nesting`, `accidental_quadratic`, `redundant_multipass`, `needless_indirection`) focuses attention on `severity: "high"` candidates. It is diff-scoped, zero-dependency, and non-fatal on unparseable/missing paths. Use it when it applies; reason directly over the diff for all other languages.

**Apply vs. advise.** APPLY a restructure only if all hold: (a) it is a clear win (not a lateral rewrite); (b) the existing test subset for the touched files still passes — reuse the Sub-step B Validate machinery on E's changed paths only, not the full gate; (c) public signatures and observable behavior are unchanged. If ambiguous, uncertain-architectural, or fails (b)/(c) → do not apply; emit as an advisory finding via the existing independent-auditor surface (Phase 4 Report `## Notes from judges`). No perf gate, no benchmark, no cost-proxy — equal-or-better performance is an unmeasured bonus, never asserted or gated.

Applied edits flow through the existing single-writer Phase 3 commit contract — part of the build's diff, not a side-channel.

Preserve: public API surface, test coverage, observability (logging/tracing), documented behavior, and modular boundaries that protect user value, scalability, accuracy, security, testability, or stable interfaces. If an integrated simplification is better, document `MODULARITY EXCEPTION: <reason>`. For **plugin work**: also re-run `plugin-dev/scripts/hook-linter.sh` against any touched `hooks.json` and `grep` the manifest for `../` or bare paths.

**Self-recursive builds:** when `selfRecursive.enabled == true` (the build is editing build-loop itself), Sub-step E also consumes `self_review.py`'s `self_simplification[]` findings as an additional hotspot source. Any simplification applied to build-loop's own code from this list MUST pass `python3 scripts/self_mod_verify.py --scope full --auto-revert --json` (`verdict: pass`) before it is committed. A `verdict: fail` auto-reverts the change; it does not route to Iterate — move it to `.build-loop/proposals/needs-human/` instead.

**Report.** Log one line: `[Simplify] N hotspots, M applied, K advised`; record applied/advised counts in the Sub-step G report. An applied rewrite that later fails a re-validate routes like any Sub-step B failure (Phase 5 Iterate, existing 5x cap).

#### Sub-step E telemetry

**Sub-step E telemetry (mandatory, every Review pass, all builds).** After E completes for this Review pass, the orchestrator MUST append one row to `state.json["reviewE"]` via:

```python
update_execution_state(state_path, 'review_e_pass',
                        files_scanned=[<files E actually inspected this pass>],
                        is_final=<True iff this is the final Review pass>)
```

This is **measurement infrastructure, not a factor** — it is present and identical on every build regardless of any cadence policy. It records *what E did this pass*; it must NOT change *what E does*. `pass_idx` auto-derives from the existing row count (0-based). When a cadence policy scopes E to only iterate-changed files on Review re-entry, the recorded `files_scanned` naturally shrinks on non-first passes — that difference is the signal a deterministic scorer reads. Telemetry write failure is logged, never blocks the build.

### Sub-step F: Auto-Resolve (drain non-destructive open items)

Drain the candidate auto-resolve queue before writing the final scorecard. Items in the queue come from four sources:

- **Sub-step A Critic** — findings with normalized `severity in {medium, low}` AND `suggestion` naming a single `file:line` (canonical independent-auditor finding fields per `agents/independent-auditor.md`; QM v0.13.0 — legacy `minor→medium`, `info→low`)
- **Sub-step D Fact-Check & Mock Scan** — non-blocking gate findings (e.g. `Plugin Cache Sync` divergence, `Version-Bump Advisor` notes when `release-pending.md` is absent, single-file documentation drift)
- **Quality / performance findings this run** (C-HEAL / self_heal_safe_issues) — any quality or performance issue surfaced by this run's self-review, fact-check, simplify, or efficiency scan whose fix classifies SAFE. Auto-fix them in-pass; do NOT defer to followup, do NOT surface. RISKY/DECISION findings from this same path still route to `## Held` / report as usual.
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
- Strong-checkpoint findings from Sub-step A — a `nay` routes back to Execute (no iteration counter burn).
- Sub-step B Validate failures — those route to Phase 5 Iterate.
- Anything matching deployment_policy.py heuristics — autonomy_gate delegates to deployment_policy automatically; the verdict still flows through `auto | confirm | block`, but the source-of-truth is deployment_policy for those items.

The auto-resolve queue is rebuilt from scratch per Phase 4 invocation. Items not drained on a given pass don't carry forward unless explicitly re-surfaced by Sub-steps A/D on the next pass.

### Sub-step G: Report (only on final Review pass)

Runs only when all prior sub-steps pass OR when iteration cap is hit. Writes final artifacts and closes the build.

**No-critical/high exit gate (QM v0.13.0 Piece 3, BLOCKING).** Before this final pass may report `pass`, collect every reviewer findings JSON produced this run (independent-auditor + security-reviewer) and run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review_finding_gate.py --findings-json <each.json> --json`. It normalizes legacy (`major→high`, `minor→medium`, `info→low`; security `CRITICAL|HIGH|MEDIUM|LOW` case-insensitively; ambiguous→`high`) and returns `{pass, blocking_count, ...}`, exit 1 when any `critical`/`high` finding is open (not `closed` + `closure_proof`). **Exit 1 → the final pass is blocked; route the blocking findings to Phase 5 Iterate** (the fixed 5-iteration cap cannot finalize with an open critical/high). Exit 0 → proceed. Medium/low never block here — they route through the ux-queue/followup with explicit disposition; they are never silently skipped.

**Judgment-dispatch gate (BLOCKING on stakes-gated runs).** The advisor/auditor ladders RECORD which rung fired but nothing ENFORCED it, so an inline run (skill-as-methodology, no orchestrator dispatch) silently sat at the inline-Opus floor and the Frontier judgment never happened (observed: agent-rally-point v0.1.2 ran 16 commits with 0 Fable dispatches until the user asked why). Before this final pass may report `pass`, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/judgment_gate.py --workdir "$PWD" --run-id <this run> --agent-tool-available <true|false> --json`. Pass `--run-id` so stakes/statuses/ledger are scoped to THIS run only (never a stale top-level trigger or a prior run's ledger row — the gate reads stakes from the run record); omit it to default to the latest `runs[]` entry. Pass `--agent-tool-available false` only for a nested orchestrator / no-Agent-tool context. Stakes-conditional (mirrors the ladders): no stakes trigger → `pass`; stakes fired + `auditor_status`/`advisor_status` shows a real Frontier/peer dispatch → `pass`; stakes fired + the inline floor (`fallback:inline-opus` / `not-run:parent-must-dispatch` / unrecorded) with the Agent tool reachable → **exit 1, `fail`**. **Exit 1 → the run is NOT review-complete: dispatch the independent-auditor (and, for a stakes-gated Phase 2, the advisor) to Frontier, then re-run.** Also fails on an `agent-ledger.jsonl` `verify`/`author` action recorded at a non-frontier tier. This closes the inline-substitution hole — the same class as the inline self-audit masquerading as the independent auditor.

Final report sections, in this order:

- `## Done` — every verified pass + every Auto-Resolve `auto` item, with one-line evidence each.
- `## Held` — items Auto-Resolve verdicted as `confirm`, with the `reason` field from `autonomy_gate.py` quoted verbatim. The user may run any held command manually if they want to. Build-loop does NOT prompt or auto-execute these.
- `## Blocked` — items Auto-Resolve verdicted as `block`, same shape as Held.
- `## Status markers` — ✅ Known / ⚠️ Untested / ❓ Unfixed (existing convention; keep this section).

Research citation gate: before emitting, read
`.build-loop/state.json.researchGate`. When `blocks_final_claims: true`, every
current/external/API/package claim in the report must cite the research packet
or explicitly say the evidence was unavailable and the claim is unverified. If
`packet_path` is non-null, add a compact `research_packet:` evidence item to
`## Done` or `## Status markers`.

Reference-capture report field (mirrors the researchGate citation contract):
every run reports one `references captured:` line in `## Done` or `## Status
markers` — `references captured: N (<files>)` when one or more external fetches
informed a decision and were captured via the canonical writer, `none — no
external fetch informed a decision` when no web/doc fetch fed a decision, or
`skipped: <rationale>` when a fetch informed a decision but capture was
intentionally not run. This makes the default-on capture trigger
(`references/research-trigger-policy.md` §"Reference Capture") accountable in the
run report instead of advisory-only.

Before emitting the final report, write the draft to a temp file and run BOTH linters (orthogonal — structural vs style):

```bash
python3 scripts/build_report_lint.py <draft.md> --json    # structural: parallel_batch, merge_plan, evidence triplets
python3 scripts/report_lint.py       <draft.md> --json    # style: headline shape, validation line, jargon, contrastive pivot, length
```

Structural lint (`build_report_lint.py`):

- Exit 0 → emit the report.
- Exit 1 → revise the report before emitting it. The linter blocks vague verified/known claims, missing `parallel_batch` / `parallel_skipped_reason`, and missing `merge_plan` fields.
- Exit 2 → lint outage. Record `[warn] build-report-lint skipped (<reason>)` in `## Done` and continue.

Style lint (`report_lint.py`) — WARN with self-heal, never a hard halt. The user has asked for enforced concise, no-jargon user-facing output (`skills/build-loop/references/output-style.md` is the contract):

- `summary.total == 0` → emit the report.
- `summary.total > 0` → auto-revise the draft ONCE to clear the findings (translate jargon to plain language per the contract's blocklist, rewrite a missing headline as a one-sentence statement of what changed, add a validation line naming the exact command/method that verified the work, remove contrastive-pivot constructions), then re-run the lint. If a second pass still has findings, emit the report with a `[warn] report-lint findings remain after one revise pass` line in `## Done` and continue. Never block on style.
- Script error / file not found → record `[warn] report-lint skipped (<reason>)` in `## Done` and continue.

The two lints are orthogonal: structural rules live in `build_report_lint.py`, style/jargon rules live in `report_lint.py`. Neither replaces the other. The lints target ONLY the final user-facing report markdown; internal envelopes between agents stay structured/jargon-ok.

Evidence contract: each verified/known claim carries its evidence in the compact form `✅ <claim> [<method> → <artifact>]` (e.g. `✅ auth works [pytest → ci.log]`); `@<observer>` only when the observer isn't this run's orchestrator. One line per claim — no restated context or process narration. Multi-chunk or parallel reports must include `merge_plan:` with `clean_against`, `conflicts_with`, and `suggested_order`.

**Forbidden in the report**:
- Recommendation-list headers (e.g. headers that invite operator selection of which items to execute)
- "Next Action" sentences that read like questions
- Any bullet phrased as `Want me to X?` or `Should I Y?`
- Any list that presents items as choices for the operator to pick from

If a category is empty (no Held items, no Blocked items), omit the section entirely — no header, no `_(none)_` placeholder. A reader infers "none" from absence.

Write scorecard to `.build-loop/evals/YYYY-MM-DD-<topic>-scorecard.md`.

**Debugger store + outcome**: for each resolved Review-B/Iterate failure, write a native `.build-loop/issues/<incident>.md` incident note with `{symptom, root_cause, fix, tags, files}`. If `availablePlugins.codingDebugger` is true and the run explicitly requested cross-project memory, mirror the same outcome to standalone Coding Debugger. Both sides of the memory feedback loop — local store and outcome status — are required for learning.

**Orphan scan**: invoke `Skill("build-loop:architecture-dead")` — runs `navgator dead`, diffs against the Phase 1 Assess baseline, surfaces ONLY new orphans introduced this build. No-ops cleanly when `.navgator/architecture/index.json` is absent.

**Deployment policy gate** (before any push/deploy): run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/deployment_policy.py" \
  --workdir "$PWD" \
  --command "$CANDIDATE_DEPLOY_COMMAND"
```

Follow the returned `action`: `auto` may proceed after Review passes; `confirm` requires an explicit user confirmation in chat before running the command; `block` must not run and should be reported as a configured repo policy. Defaults favor speed for preview/TestFlight and safety for production/unknown.

**Auto-version-bump (LAST step before push/merge for plugin-bearing repos)**: when `plugin.json` (or `.claude-plugin/plugin.json`) exists at repo root AND `git diff --name-only origin/main..HEAD` includes any path outside `docs/`, `tests/`, `*.md`: bump the patch segment of `plugin.json:version`; mirror the new version into every locally-known `.claude-plugin/marketplace.json` (search `~/dev/git-folder/`, `~/.claude/plugins/marketplaces/`) entry referencing this plugin; commit `chore(version): bump <plugin-name> to <new-version>`. No minor/major bumps; no bumps for docs-only diffs.

**Post-deploy verification gate (after a deploy actually ran)**: once a deploy executed — i.e. the deployment policy gate returned `auto` and the deploy/push command ran, or the pushed branch auto-deploys via Vercel — invoke `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify_deploy.py --workdir "$PWD" --changed-route <route> [--changed-route <route> ...] --json`. The script detects a Vercel link (`.vercel/project.json` or `vercel.json`), resolves the latest production deployment, polls `vercel inspect` to a terminal state, then probes the prod root + each changed route. `pass` proceeds; `fail` routes to Iterate using the envelope's `findings` as the rubric; `skipped` (no Vercel link, CLI missing, not authed, or other transient infra) records `deploy_verify: skipped (<reason>)` in Review-F and proceeds. An auth-gated `401`/`403` on a protected route is **healthy** (function deployed and running) — only a `5xx`/build-error is a real failure. Never block the build on infra. See `agents/build-orchestrator.md` §"Review: Post-deploy verification gate" for the routing rules and `fallbacks.md#web-deploy-verify` for the inline degraded procedure.

**Append a run entry to `.build-loop/state.json.runs[]`** for Learn (Phase 6) to scan. The orchestrator agent owns the invocation — see `agents/build-orchestrator.md` §G for the canonical call (including `--judge-decisions-json` and `--budget-summary-json`). Schema and flags are owned by `scripts/write_run_entry/__main__.py --help`; do not hand-write JSON.
