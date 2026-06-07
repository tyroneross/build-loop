<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 1: Assess (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full Assess phase: state understanding, goal definition, and scoring criteria.

## Phase 1: Assess — State, Goal, and Criteria

**Goal**: Know what exists AND what success looks like before writing any code. Combines situational awareness with goal definition so the plan phase has everything it needs.

### Understand current state

0. **Peer-detection (cheap fail-fast — runs BEFORE plugin detection so a peer collision is surfaced before any other Phase 1 cost is paid).** Bash, ≤4 commands; output goes into the assess report. **The check is automated end-to-end — never pauses to ask the user.** Any non-empty line is written to `state.json.assess.peerDetect[]` and surfaces in `## Notes from judges`. Phase 2 Plan auto-routes per default policy: if scope overlap is detected, the orchestrator fast-forwards (`git merge --ff-only`) when ancestry allows, otherwise splits the affected chunks out and continues, logging `WARN: peer_collision_<kind>` either way. Operator reviews the report post-hoc. Complements Rally Point session-presence (§"Multi-session concurrency" in `agents/build-orchestrator.md`) — Rally Point covers active *sessions*; this covers dormant *artifacts* (coordination notes, stale worktrees, unmerged branches) those sessions leave behind.

   ```bash
   ls .build-loop/coordination/*.md 2>/dev/null | grep -v /archived/   # live coordination notes
   git worktree list --porcelain                                       # all worktrees
   git worktree list --porcelain | awk '/^worktree /{print $2}' \
     | while read -r wt; do [ -d "$wt" ] && echo "$wt dirty=$(git -C "$wt" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"; done  # per-worktree dirty (Codex addition: dirty is stronger signal than branch merge status)
   git branch -a --no-merged main | grep -vE 'archive|HEAD'            # unmerged branches
   ```

   Helper errors (`grep -v`/`awk` non-zero) are NOT a failure — empty output means clean. Any non-empty line surfaces in the assess report for Phase 2 to reason about.

0a. **Credential preflight** (fail-soft, names only — no values ever surfaced): run

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/credential_preflight.py" \
     --workdir "$PWD" --json
   ```

   Write the result to `.build-loop/state.json.assess.credentialPreflight`. If `missing[]` is non-empty, surface each name as `[CREDENTIAL REQUIRED] <name>` in the Assess summary and repeat verbatim in the end-of-run readback. A missing credential is a "genuine inability to proceed" under the autonomy policy: log it, continue all work that does not require the key, and surface it in the readback — do NOT stop-and-ask. Script failure (non-zero exit / bad JSON) → log one warning line; never blocks Assess.

0b. **Stale-context triage** (fail-soft, proactive drift notice): run

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stale_context_check.py" \
     --workdir "$PWD" --json
   ```

   Write the result to `.build-loop/state.json.assess.staleContext`. For each path in `docs[]` where the doc is flagged stale, surface it as `[STALE CONTEXT] <path>` in the Assess summary so the agent notes drift before relying on a handoff/orchestration/continuation doc. The user should never have to ask "is this still relevant?" Script failure → log one warning line; never blocks Assess.

0c. **Memory-staleness triage** (fail-soft, proactive drift notice): run

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/memory_staleness_check.py" \
     --workdir "$PWD" --json
   ```

   Write the result to `.build-loop/state.json.assess.memoryStaleness`. When `stale: true`, surface `[MEMORY STALE] <slug> N commits behind HEAD — append a milestone/decision` in the Assess summary. Log the finding and continue — do NOT stop. The run should append a milestone or decision entry during Phase 6 Learn (or inline if the goal is memory-focused). Script failure → log one warning line; never blocks Assess.

1. **Detect available plugins and personal skills**: Run `node ${CLAUDE_PLUGIN_ROOT}/skills/build-loop/detect-plugins.mjs`. Write the JSON result into `.build-loop/state.json` under `availablePlugins`. All subsequent routing consults this object.
2. **Detect project type**: web app, API, library, mobile, CLI, monorepo, **Claude Code plugin**, one-shot new app, existing-app iteration. A plugin is detected by the presence of `.claude-plugin/plugin.json`, `hooks/hooks.json`, `skills/*/SKILL.md`, `commands/*.md`, `agents/*.md`, or `.mcp.json`. If detected, mark the build as "plugin work" in state.json and plan to load the `plugin-dev:*` skills before any manifest/hook/skill/agent/MCP/command/**scripts/** edits. **Any change to a file referenced via `${CLAUDE_PLUGIN_ROOT}/...` counts as plugin work** — this includes `scripts/*.py`, `references/*`, or anything else the plugin manifests, agents, or skills invoke at runtime. These files live in `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` at run time; editing only the source repo without syncing the cache leaves the runtime invocation broken (Lessons §5 + §5a in `plugin-hygiene-lessons.md`).
3. **Set sub-routers**: `uiTarget` (web / mobile / **macos** / null), `platform` (web / apple / react-native / null), `migrationSource` (replit / lovable / bolt / v0 / null). See the Capability Routing §Sub-routers rules. **macOS desktop is a first-class `uiTarget`** — distinct from `mobile` — because macOS has no simulator and routes visual-verify to `native-ax-driver` (or IBR `scan_macos` when present), never to `xcrun simctl`. Folding macOS into `mobile` was the routing bug from session-findings 2026-06-04.
4. **Detect available tools**: test runners (`package.json` scripts, `pytest.ini`, etc.), linters, deploy targets.
   - **Deployment policy**: read `.build-loop/config.json.deploymentPolicy` if present. Defaults are `preview: auto`, `testflight: auto`, `production: confirm`, `unknown: confirm`. Use `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/deployment_policy.py --workdir "$PWD" --command "<candidate push/deploy command>"` before any push/deploy. Treat helper errors as `confirm`.
5. **Map architecture** using best available approach:
   - If `.navgator/architecture/index.json` exists → invoke `Skill("build-loop:architecture-scan")` to refresh data, then `Skill("build-loop:architecture-impact")` on up to 5 highest-risk components for blast-radius. Output goes to `.build-loop/state.json.architecture.{scan,impact}`. Phase 2 Plan consults this for scoping. Flags high-fan-in hotspots, 2-hop dependents, layer-crossing risks, and prompts-in-scope when `triggers.promptAuthoring` is true.
   - Else if `gator:*` is available → use those commands.
   - Else → Explore agents → file reading.
5a. **Architecture portable handoff** (read on resume; write on fresh scan): after the architecture baseline above, the scan result must also be written to `.build-loop/architecture/handoff.md` as a self-contained snapshot — component map, key connections, runtime topology, LLM use-cases, and data flows — that a FRESH session can consume WITHOUT re-scanning. On a resumed or fresh session, Phase 1 reads `.build-loop/architecture/handoff.md` if it exists and its `updated_at` timestamp is within the staleness threshold (default: same as stale-context check), and skips the full re-scan. The format and field schema are specified in `agents/build-orchestrator.md` (architecture-scout section); this step only wires the read/write reference. Write failure → log one warning; never blocks.

5b. **Reads-from dependency enumeration**: For each component the build will change or add, enumerate every data path, contract, or invariant it reads (config files, state keys, schema fields, API contracts). For each, verify something writes it — grep the repo, check test fixtures, or confirm the schema. Record results in `.build-loop/state.json.assess.readsDependencies[]` as `{path, writer_found: true|false}`. Any path with `writer_found: false` is a BLOCKING unknown that must appear as `unverified` in the plan's `## Depends-on (reads-from)` section and be resolved before Phase 3.

6. **Observability baseline** (informational, no changes): run a stack-appropriate grep to classify the project's logging level (well-instrumented / print-only / silent) and write to `.build-loop/state.json.observability.level`. The orchestrator handles this inline — `Skill("build-loop:logging-tracer")` is reactive only and is loaded later if Review-B / Iterate hits a silent failure.
6b. **Attribution-layers detection** (informational, advisory only — never blocks, never pauses): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect_attribution_layers.py --workdir "$PWD"` when the repo has a GitHub origin remote. Write the result to `.build-loop/state.json.attribution`. When `should_advise: true`, surface the returned `advisory` line in the Phase 1 Assess report under `## Notes from judges`. Phase 2 Plan queues an automatic chunk to run `scripts/attribution_stamp.py` when the build scope is ≥ S; smaller scopes only surface the advisory and let the user run it manually. Per `feedback_advisory_checks_are_automated`: this routes to the run report, never to AskUserQuestion or `## Held`. Helper failure → treat as `should_advise: false` and log a one-line warning. The full procedure for stamping is in `skills/attribution-standard/SKILL.md`.

6a. **Runtime-server detection** (informational, no changes): run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/detect_runtime_server.py --workdir "$PWD" --json` and write the result to `.build-loop/state.json.triggers.runtimeServer` (boolean) plus `.build-loop/state.json.runtimeServerInfo` (full envelope: `server_module`, `sse_route`, `default_port`, `embedded_ui_module`, `event_handler_locations[]`, `evidence[]`). Phase 4 sub-step B Validate consults these for the live HTTP/SSE smoke gate. Helper failure → treat as `runtimeServer: false` and log a one-line warning; never blocks. Silent default for CLIs, libraries, plugins, and static-render web apps. Implements decision `_unscoped/0003` (live smoke required when build-loop touches a runtime server) — closes the pytest-with-mocks blind spot that let example-app ship 27 commits with two real bugs.
7. **Debugger context priming** (always; native to build-loop): invoke `Skill("build-loop:debugging-memory")` with `{ intent: "list-recent", project: "<current>" }` to summarize recent incidents in this project. One-line output; no action. If cross-project Coding Debugger is installed, the skill may use it; otherwise it falls through to `fallbacks.md#bug-memory`.
8. **Capture UI state** (if web/mobile): host browser/screenshot tooling or simulator/native-AX evidence when available → showcase capture → manual screenshot. Do not route to IBR unless the user explicitly requested it.
8a. **UI input/output inventory** (if `uiTarget != null`): load `skills/build-loop/references/ui-io-contract.md` and identify every affected user input and system output before component choices are made. Classify each by structural type, content format, persistence intent, operation/domain verb, component mapping, state matrix, modality fallback, validation/security layer, and traceability. Mirror a compact summary to `.build-loop/state.json.uiIOContract` when practical; the full contract is finalized in Phase 2.

8b. **Load short-term working context (Pillar 0 — structurally loaded first, inside memory bootstrap)**: `context_bootstrap.build_packet()` calls `load_current(workdir)` at its very entry, before any heavier memory work, and attaches the result as `packet['working_context']` (a serialized `WorkingContextEnvelope`). The working context is therefore available as part of the Phase-1 packet without a separate CLI step.

   Consume it directly from the packet:

   ```python
   wc = packet["working_context"]   # {exists, path, warm_read_latency_ms, parsed, reasons}
   ```

   Use `wc["parsed"]["links_down"]` as the entry pointers DOWN into long-term memory (P1 hybrid recall + P4 prior-art). Missing / corrupt `current.md` → `wc["exists"] == False` + `wc["reasons"]`; never blocks Phase 1 or bootstrap. Mirror `wc["warm_read_latency_ms"]` into `.build-loop/state.json.assess.workingContextLatencyMs` for the Phase 4G report.

   The standalone CLI (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/load_current.py --workdir "$PWD" --json`) remains available for manual inspection but is no longer a required Phase-1 step — the enforced path is via `build_packet()`.

9. **Load memory**: Run the automatic context bootstrap before planning:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/context_bootstrap.py \
     --workdir "$PWD" \
     --query "<goal-keywords>" \
     --output "$PWD/.build-loop/context-bootstrap.json" \
     --json
   ```

   The packet must include canonical `build-loop-memory` root/project `MEMORY.md` and `constitution.md` files, indexed recall, repo-local `.build-loop/feedback.md`, `.build-loop/state.json`, current plan/goal/intent, Codex memory registry `~/.codex/memories/MEMORY.md` plus linked rollout summaries, and best-effort Rally/coordination state when coordination context exists. If the root constitution is absent, `context_bootstrap.py` seeds it once from the shipped template before reading; it never overwrites existing root or project constitution files. Missing surfaces are recorded in `sources.*.reasons[]`; they do not block Phase 1 by themselves. See `references/memory-systems.md` §"Read protocol — Phase 1 Assess".

   **Cross-project prior art (P4 — payoff)**: the bootstrap also emits `packet.prior_art`, a compact cross-project digest of prior implementations + linked decisions for the task's classified capability. Powered by `scripts/capability_classifier.py` (deterministic, host-LLM-refinable) and `scripts/prior_art.py` (reuses P1 hybrid recall + scans `projects/<slug>/decisions/` and `projects/<slug>/lessons/` across other projects). The digest is compact (hard char cap — `prior_art.DEFAULT_MAX_TOTAL_CHARS`, 4000 chars) and absence-tolerant (empty memory → empty payload, never blocks). `build_packet()` **enforces** the write by calling `context_bootstrap.write_prior_art_to_intent(workdir, digest_text)` immediately after building the packet: when the digest is non-empty AND `<workdir>/.build-loop/` exists, it appends (or idempotently replaces) a `<!-- prior-art:start -->`…`<!-- prior-art:end -->` block in `.build-loop/intent.md` (creating the file when absent). Re-running replaces the block — never duplicates. The agent brief still carries the compact pointer line for quick orientation; the full digest body is in the file. Disable via `BUILD_LOOP_PRIOR_ART=0`.

   Then write the first live handoff snapshot:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/context_snapshot.py \
     --workdir "$PWD" \
     --trigger phase_transition \
     --phase assess \
     --message "Phase 1 context bootstrap complete" \
     --if-changed \
     --json
   ```

   This refreshes `.build-loop/context/current.md` for later agents. Snapshot failure is a context-quality warning, not a Phase 1 blocker.

9a. **Run identity + multi-session presence (Rally Point)** (always; runs at the Phase 1 preamble before any Rally Point write):
   1. Generate or resume durable run identity + isolated worktree: `execution = scripts/rally_point/build_loop_id.generate_or_resume(workdir="$PWD", tool="<tool-id>", session_id="<session-id>", provision_worktree=True)`. This writes `state.execution.build_loop_id` and `state.execution.run_label` when missing, preserves them on resume, and updates only `current_session_id`. **`provision_worktree=True` is mandatory: it provisions `.build-loop/worktrees/run-<id>` on `bl/run-<id>` (fail-closed; aborts the run rather than touch the canonical checkout) and records `state.execution.run_worktree_path` for all downstream git/file ops. Structural fix for shared-checkout contamination — see `docs/SPEC-run-worktree-isolation.md`.**
   2. Resolve the channel: `slug = scripts/rally_point/channel_paths.app_slug(cwd="$PWD")` (D1: worktree/clone-independent — main checkout and every worktree share one channel). Do NOT reimplement slug derivation.
   3. Write presence: `scripts/rally_point/presence.write_presence(channel, session_id=..., tool="claude_code", model=..., run_id="$RUN_ID", app_slug=slug, phase="assess", files_in_flight=[])`. Codex / Gemini / other hosts substitute their `tool` value. Fire-and-forget. The writer attaches top-level `build_loop_id` and `build_loop_run_label` from `state.execution`.
   4. Read active peers: `peers = scripts/rally_point/presence.read_active_presence(channel, exclude_session=...)` (also reaps stale presence past the heartbeat window — no daemon).
   5. Route per `agents/build-orchestrator.md` §Multi-session concurrency — **awareness only, never a hard block (D4)**:
      - No peers / no `files_in_flight` overlap → log one line per peer (tool, run_id, phase); continue.
      - Overlap with a peer's `files_in_flight` → surface a `soft-claim` WARNING (peer, files, phase); continue with awareness. Interactive MAY additionally `AskUserQuestion` to coordinate; headless logs + proceeds. No SAFE-STOP sentinel, no non-zero exit.
   6. Initialize the memory-index cursor: capture the current top-of-log timestamp from `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_index.py tail --limit 1 --json` (used by `--since` in subsequent phases to surface new peer learnings).

   **Supersedes** the legacy `ps aux | grep -c "[c]laude$"` advisory below — Rally Point presence is the canonical signal. Keep the legacy line as a fallback only when Rally Point is unavailable (older plugin cache without `scripts/rally_point/`).

10. **Load PRD if present** (strategic frame check): load `build-loop:prd-bridge`, run its Phase 1 Assess step. If `docs/prd-*.md` exists, the bridge reads frontmatter (`core_principles`, `load_when`, `evolves_when`), Navigation Map, and Section Index, mirrors them to `.build-loop/state.json.prd`, and surfaces staleness signals. If no PRD exists, the bridge writes a one-line recommendation in `state.json.prd.recommendation` pointing to `prd-builder` skill / `/build-loop:start-prd` command — surfaces in Sub-step G Report's `## Held` section, doesn't block. Step 11 below uses PRD as primary source of truth when present; falls back to fresh capture when absent.
11. **Capture north star + update intent**: When `state.json.prd.core_principles` is non-empty (a PRD was loaded by step 10), use it as the strategic frame; `intent.md` cites the PRD path + revision rather than re-deriving. Otherwise use `references/intent-capability-pack.md` to identify app/repo purpose, primary users, core jobs, update intent, user value, and non-goals fresh. Write `.build-loop/intent.md` and mirror compact fields to `.build-loop/state.json.intent`.

    **Intent restatement protocol (always-on, LLM-judged)**: as part of writing `intent.md`, follow `references/intent-capability-pack.md` § Intent restatement protocol — write a one-line concrete restatement always; when the orchestrator LLM judges the goal genuinely ambiguous (per Step A heuristics), extend with 1-3 approach options + tradeoffs + tagged assumptions. The judgment is the orchestrator LLM's, not a regex or detector script. For a concrete unambiguous goal this is one line and the flow moves on immediately — zero fast-path friction. Never `AskUserQuestion`, never `## Held`, never blocks Phase 1. Fail-safe: any error here is logged as one line; the build continues. Phase 2 Plan's "fork on uncertainty" rule consumes `state.json.intent.approach_options` when the LLM's confidence stays medium/low AND Phase 2 surfaces 2+ viable approaches.

12. **Assess modular structure**: Use `references/modular-systems-pack.md`. Identify current module boundaries, stable interfaces, coupling risks, likely MECE work partitions, and any justified modularity exception. Mirror compact fields to `.build-loop/state.json.structure`.
12a. **Capture approach lenses**: For any non-trivial architecture, workflow, dependency, UI/product, or long-lived interface recommendation, assess two separate answers before planning:
    - **Clean-sheet best approach**: what would be best for the use case if prior repo decisions, accumulated tech debt, and current implementation constraints did not exist.
    - **Current-constraints best approach**: what is best given the repo's existing code, dependencies, tools, debt, team/runtime constraints, migration cost, and delivery horizon.
    - **Bridge/backcast**: the smallest credible path from current state toward the clean-sheet target, including debt retired, dependencies added/removed, and decision points.

    Prior decisions are evidence, not axioms. Do not discard current constraints; isolate them so Phase 2 can decide whether they justify a compromise. Mirror the compact summary to `.build-loop/state.json.approachLenses` with `clean_sheet`, `current_constraints`, `constraint_delta`, and `bridge_backcast` fields.
13. **Check prior state**: Read `.build-loop/issues/` and `.build-loop/feedback.md` if they exist. Surface relevant items. If any issue affects the current user's experience, add it to the plan unless too large or risky; otherwise log and defer with user impact.
14. **Research trigger + depth gate**: run the deterministic classifier and cache the result:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research_trigger.py" \
     --workdir "$PWD" \
     --task "<goal text>" \
     --effort "<XS|S|M|L|XL>" \
     --cache-into-state \
     --json
   ```

   This writes `.build-loop/state.json.researchGate`. Use
   `references/research-trigger-policy.md` for trigger policy and t-shirt depth
   lower bounds. If `research_required: true`, run the Research plugin at the
   returned depth (`light`/`standard`/`deep`) or record why it was unavailable.
   If `blocks_final_claims: true`, final current/external/API claims need
   citations or an explicit unavailable/unverified note.
14a. **Active task surface**: when the user asks what remains, when Phase 1
   surfaces open work, or before queue-continuation decisions, run:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/task_surface.py" \
     --workdir "$PWD" \
     --json
   ```

   This is the canonical active view over existing state, queue, and
   project-scoped memory backlog surfaces. Do not create a second task ledger by
   default. See `references/task-capture-policy.md`.
15. **Recovery check**: This used to be a phase-level marker. As of v0.11 the canonical recovery surface is the `--resume` argument and the heartbeat-staleness path documented under §Resume Protocol. The pre-Assess resolver already ran by the time Phase 1 starts; if it returned `decision: "prompt_user"` and the user chose "fresh", proceed normally; if they chose `--resume`, you're not in this code path (the agent is in §0 Resume mode instead).
16. **Workspace concurrency check** (advisory, no blocking — surface as one-line notes):
    - **Concurrent sessions**: `ps aux | grep -c "[c]laude$"`. If `>1`, warn that other sessions on this repo can silently revert each other's work; the checkpoint reactions (severity + reason) tell you whether overlap is `merged_residue` / `squash_landed` / `active_conflict`. See `agents/build-orchestrator.md` §Multi-session concurrency.
    - **Branch divergence**: `git rev-list --count HEAD..origin/main` and `origin/main..HEAD`. If local main is ahead of origin AND a feature branch will be cut, recommend branching from `origin/main` directly (`git checkout -b <name> origin/main`) so unpushed local commits don't ride into the eventual squash and bundle under a misleading title.
    - **Recovery if symptoms appear during build** (file writes vanish, system reminders flag "intentional" reverts, `git status` clean): pause edits, run `ps aux | grep claude` + `git log --oneline -- <affected paths>` to identify the colliding session/squash, then re-apply dropped work on a fresh branch from `origin/main`.

### UI scope and mockup pre-flight (when uiTarget != null)

**UI pre-flight**: If project has `mockups/` or `.mockup-gallery/` and goal references selected mockups, run the design-rule scanner against the mockup HTML/CSS first to surface conflicts before coding:
   ```
   node "${CLAUDE_PLUGIN_ROOT}/skills/build-loop/scanners/audit-design-rules.mjs" --root=<mockups_dir> --platform=html --json
   ```
   Log conflicts to `.build-loop/issues/mockup-rule-conflicts.md`. Don't block — agents need to know upfront which rules trump the mockup. Mockups are intent, rules are law. See `phases/ui-validation.md` for full guidance.

### Define goal and scoring criteria

14. **State the goal** in concrete, measurable terms.
15. **Suggest 3-5 scoring criteria** from: functionality, code quality, UX, performance, security, accessibility, test coverage — select what's relevant to the project and goal. Include intent fidelity/user value when the change affects user experience or product behavior. Include modularity/MECE/scalability when the change spans modules, agents, domains, repo areas, data boundaries, or long-lived interfaces. Show for confirmation.

    **Warning/lint criteria MUST be relative, not absolute** (R4 from the 2026-05-19 iOS retro). An absolute "zero warnings tagged X" criterion false-fires on pre-existing warnings, forcing subagents to either lie, exit-fail honest work, or write apologetic prose. Author as **"no NEW warnings matching `<filter>` vs `git merge-base origin/main HEAD` using the same build command, destination, SDK, and filter"** (Codex correction: baseline only valid when capture and current invocations match). Inline diff helper:
    `comm -23 <(<current> 2>&1 | grep -E 'warning:' | grep -E '<filter>' | sort -u) <(<baseline> ... | sort -u)`. Persist baselines as plain text under `.build-loop/baselines/warnings-<base-sha>-<filter-slug>.txt` only when one is needed; ad-hoc capture is fine.

   **When `uiTarget != null`, the following criteria are REQUIRED and added automatically (not optional)**:
   - **UI-1 Design-rule compliance**: scanner exits 0 on changed files (must-fix=0). Grader: code (`audit-design-rules.mjs`).
   - **UI-2 Reduce Motion compliance**: every animation gated on platform's reduce-motion API. Grader: code (scanner rule `animation-without-reducemotion`).
   - **UI-3 Theme token usage**: no raw color literals or hardcoded radii outside theme files. Grader: code (scanner rules `uicolor-rgb-outside-theme`, `literal-corner-radius`, `hex-color-outside-theme`).
   - **UI-4 Accessibility labels**: icon-only graphics have explicit labels. Grader: code (scanner rule `sf-symbol-without-label` or web equivalent).
   - **UI-5 Input/output contract coverage**: every changed UI surface has a plan row naming user inputs, system outputs, data taxonomy, operation/domain verb, component mapping, states, modality fallback, validation/security, and traceability. Grader: code/document check (`check_checklist.py` Item 17 plus Review read).

   These exist because mockup-parity ≠ design-rule compliance, and component polish does not prove the UI handles the right data. Code that matches the mockup but omits an input, output, state, validation layer, or fallback is not production-ready. See `phases/ui-validation.md` and `references/ui-io-contract.md`.

16. **Design eval graders per criterion** using the grading hierarchy:
    - **Prefer code-based graders** (fast, deterministic, cheap): test suite pass/fail, lint/type check, build succeeds, schema validation, accessibility audit
    - **Use LLM-as-judge graders** when code can't check the criterion:
      - Binary pass/fail only — no Likert scales
      - One evaluator per dimension — no multi-dimension God Evaluator
      - Judge reasons in thinking tags, outputs only pass/fail
      - Use the running host model/session as judge
    - Each criterion gets: `description | grading method | pass condition | evidence required`
    - Load `eval-guide.md` in this skill directory for judge prompt template and scorecard format if needed.
17. **Write goal file**: Save to `.build-loop/goal.md` in the project directory.
18. **Synthesis-density routing** (REVISED 2026-05-07 round-4 — Phase 1 routing with explicit speed/quality lanes): if a plan file already exists, count its `synthesis_dimensions:` entries via `count_synthesis_dimensions()` in `scripts/plan_verify.py` (shared parser; do NOT write a second). Resolve tier in this priority order:
   1. **Explicit override** — `.build-loop/config.json.modelOverrides.thinking` or `state.json.config.modelOverrides.thinking` set OR plan/chunk frontmatter declares `tier: thinking` → route to thinking-tier.
    2. **Auto-escalate on density** — `count > 5` (6+ entries) → `tier: thinking` (synthesis-dense at commit level; fan-out loses cross-dimension coherence).
    3. **Default — Sonnet fan-out for speed** — `count` 1–5 OR `count == 0` → fan-out. Sonnet's ~33% wall-clock and ~28% token savings are real; C3-C5 backstops catch the residual recall gap.
    4. **Per-chunk override** — individual chunks may declare `tier: thinking` even when plan-level was fan-out.

   Write to `state.json.synthesisDensity` as `{count, escalated, reason}`. Routing target is `tier: thinking`, **never a hardcoded model name** (`scripts/model_overrides.py`: repo config → state snapshot → orchestrator frontmatter fallback → fail-loud). When `escalated == true`, do NOT fan out; execute inline at thinking-tier.

    **Why this shape:** n=6 A/B experiment (2026-05-07, `~/dev/research/topics/synthesis-decision-delegation/experiment-2026-05-07/`) showed β catches ~40% of α's novels — real quality gap — but also showed β saves ~33% wall-clock and ~28% tokens, and the C3-C5 backstops catch some leaks. Defaulting Opus universally would erase β's velocity; the `> 5` threshold matches the empirical inflection point where β's recall collapses (C5 at 5 dims surfaced 0 novels vs α's 5). Below that, fan-out is the right speed choice; above it, depth dominates. Plan/chunk-level overrides let the operator pick quality > speed when needed without changing the default. See `agents/build-orchestrator.md` Phase 1 for full procedure.

**Output**: Structured state summary + `.build-loop/intent.md` + `.build-loop/goal.md` with criteria. Brief.
