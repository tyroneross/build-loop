---
name: build-loop
description: Use when making significant multi-step code changes requiring planning, parallel execution, and validation. Not for single-file edits or quick fixes.
---

# Build Loop — Orchestrated Development

An 8-phase development loop: assess current state, define goals with scoring criteria, plan and optimize execution, build with parallel agents, validate against internal evals, iterate on failures, fact-check output, and report results.

## Routing

Build-loop supports three modes, routed by the orchestrator:

- **Build** (default): Full 8-phase loop for implementation tasks
- **Optimize**: Autoresearch-pattern optimization for measurable metrics (`/build-loop:optimize`)
- **Research**: Pre-decision analysis that produces a research packet (`/build-loop:research`)

The orchestrator classifies intent automatically. Users can override with the standalone commands.

## Scope Check

Before starting the loop, assess whether the task warrants it. If the task is a single file edit, a config change, or a fix under ~20 lines — skip the loop and just do it. The loop is for multi-step work where planning and validation add value.

## Capability Routing

Build-loop prefers installed plugins and skills over reinventing patterns. Each capability has three tiers: **preferred** (the specialized plugin) → **secondary** (another installed plugin that can partially cover) → **inline fallback** (guidance text from `fallbacks.md`, injected verbatim into subagent prompts).

Phase 1 runs `node ${CLAUDE_PLUGIN_ROOT}/skills/build-loop/detect-plugins.mjs` and writes the result to `.build-loop/state.json` under `availablePlugins`. All routing consults that object.

### Core loop skills (always check)

| Skill | Used In | Fallback |
|-------|---------|----------|
| `writing-plans` | Phase 3 (Plan) | Write a structured plan directly: goal, tasks with exact file paths, dependency order, test commands |
| `subagent-driven-development` | Phase 4 (Execute) | Dispatch parallel agents manually using the Agent tool for independent file groups |
| `verification-before-completion` | Phase 8 (Report) | Run all test/build/lint commands and confirm output before claiming completion |
| `simplify` (slash: `/simplify`) | Phase 8 (after Report) | Self-review the diff: remove scaffolding, inline single-use helpers, delete dead branches |
| `build-loop:self-improve` | Phase 9 (Review) | Scan recent runs for recurring patterns, auto-draft experimental skills/agents with A/B tracking, notify user for keep/remove decisions |

### Capability routing table

| Capability | Preferred | Secondary | Inline fallback section |
|---|---|---|---|
| Web UI build | `ibr:scan-while-building`, `ibr:component-patterns`, `ibr:design-guidance`, `calm-precision` | `frontend-design:frontend-design` | `fallbacks.md#web-ui` |
| Web UI validation | `ibr:design-validation`, `ibr:scan`, `compare` MCP tool | `showcase:capture` for visual evidence | `fallbacks.md#web-ui` |
| Orchestrated UI build | `/ibr:build --from=build-loop` | existing ibr skills in sequence | `fallbacks.md#web-ui` |
| Mobile UI build | `ibr:component-patterns` (mobile-ui), `apple-dev` (if Apple), `calm-precision` | — | `fallbacks.md#mobile-ui` + `fallbacks.md#apple-dev` |
| Mobile UI validation | `ibr:native-testing`, `ibr:native-scan` | `showcase:capture` | `fallbacks.md#mobile-ui` |
| Design system tokens | `ibr:design-guidance` (§Configuration), `validate_tokens` MCP tool | — | `fallbacks.md#design-tokens` (reads consumer project's token files — never hardcodes) |
| Screenshot / visual evidence | `showcase:capture`, `showcase:record` | `screenshot` MCP tool | `fallbacks.md#screenshot` |
| Web content fetching (low LLM) | `scraper-app:web-scraper` SDK | — | `fallbacks.md#web-fetch` (flags LLM cost in report) |
| Deep debugging | `claude-code-debugger:debug-loop` + `debugger` MCP `search`/`store` | — | `fallbacks.md#debug` |
| Bug-pattern memory | `claude-code-debugger:debugging-memory` | — | `fallbacks.md#bug-memory` (greps `.build-loop/issues/` + `.bookmark/`) |
| Agent authoring | `agent-builder:agent-builder-anthropic` | `plugin-dev:agent-development` (if plugin work) | `fallbacks.md#agent-authoring` |
| DeepAgents / local-LLM agent work | `build-loop:building-with-deepagents` (SubAgent API, middleware stack, per-agent tool scoping, anti-patterns) | — | Read installed `deepagents` source: `python3 -c 'import deepagents, os; print(os.path.dirname(deepagents.__file__))'` then `graph.py` + `middleware/subagents.py` |
| Structured reports / handoffs | `pyramid-principle:pyramid-short-form` (Phase 8), `pyramid-long-form` (design docs) | — | `fallbacks.md#structured-writing` (SCQA + MECE skeleton) |
| Hosted-IDE migration (Replit / Lovable / Bolt / v0) | `replit-migrate:migration-scan`, `migrate-web`, `migrate-ios`; MCP tools `migrate_scan`, `migrate_plan_web`, `migrate_plan_native`, `migrate_map_apis`, `migrate_map_models`, `migrate_check_progress` | — | `fallbacks.md#migration` (manual inventory + stack-translation) |
| Prompt authoring / review / audit (system prompts, agent prompts, eval judges) | `prompt-builder:prompt-builder` skill; slash commands `/prompt-builder:optimize`, `/score`, `/compare`, `/save`, `/list`. Calibrates to model tier (T1/T2/T3) and deployment (interactive, backend, rag_pipeline, agent, plugin, eval_judge, personal_mobile). Returns 6-Part-Stack prompt + 5-dim score + diagnosis + `[ASSUMED:]` tags + `TEMPERATURE_HINT` | `prompt-builder` (personal skill, same name, loaded via Skill tool) | `fallbacks.md#prompt` |
| iOS / watchOS / macOS dev + deploy | `apple-dev` personal skill (via `Skill("apple-dev")`) | `replit-migrate:migrate-ios` (when migrating *to* native) | `fallbacks.md#apple-dev` |
| Architecture scan / impact trace (Phase 1 + Phase 7) | `build-loop:navgator-bridge` — reads `.navgator/architecture/` JSON for blast-radius analysis in Phase 1, runs `navgator rules` diff post-change in Phase 7 | `gator:*` commands if installed | Read component → edit → re-read downstream |
| Debugger memory-first gate (Phase 5 + Phase 6) | `build-loop:debugger-bridge` — calls `checkMemoryWithVerdict()` before iterating, escalates to parallel assessment or causal-tree investigation on stuck state; reads logs via `read_logs` MCP; feeds back `outcome` on applied fixes | `claude-code-debugger:debug-loop` direct | `fallbacks.md#debug` |
| Runtime visibility / observability (Phase 1 + reactive Phase 5/6) | `build-loop:logging-tracer-bridge` — passive observability scan at Phase 1; generates stack-appropriate structured logging / OTel at Phase 5/6 when debug-loop hits `evidence_gap` | `claude-code-debugger:logging-tracer` direct | Inline Tier-1 (zero-dep JSON logger) per stack |
| Self-improvement / recurring pattern detection (Phase 9) | `build-loop:self-improve` — runs after every build; detects recurring failures, diagnostics, file churn; drafts experimental skills/agents to `.build-loop/skills/experimental/`; auto-promotes to `.build-loop/skills/active/` on A/B wins, auto-removes on regression. Cross-project promotion via `/build-loop:promote-experiment <name>` | — | Manual review of `.build-loop/state.json.runs[]` |
| Context recovery after compaction | `bookmark:*` commands | — | Re-read last plan file in `.build-loop/` |

### Sub-routers (set during Phase 1)

**UI target**: if consumer project has `ios/`, `*.swift`, `Package.swift`, or `*.xcodeproj` → `uiTarget: "mobile"`, `platform: "apple"`. Else if `app.json` (Expo) or `App.tsx` with `react-native` → `uiTarget: "mobile"`, `platform: "react-native"`. Else → `uiTarget: "web"`, `platform: "web"`.

**Migration source**: if `.replit` / `replit.nix` present → `migrationSource: "replit"`. Lovable / Bolt / v0 export markers (e.g. `lovable.config`, `bolt.config`, `v0.dev` in comments) → corresponding source. `replit-migrate` skills generalize — load `migration-scan` for any of the above, override hints as needed.

**Apple deploy**: when `platform: "apple"` AND goal includes "deploy", "TestFlight", or "App Store" → Phase 7/8 invoke `apple-dev` deploy flow using ASC creds per `~/.claude/projects/-Users-tyroneross/memory/reference_asc_credentials.md`.

### Trigger Conditions

Some capabilities should fire proactively based on goal phrasing or files touched. Phase 1 ASSESS sets these flags in `.build-loop/state.json.triggers`, and Phase 4 EXECUTE consults them before dispatching each subagent.

**pyramid-principle** (structured writing)

Fires whenever the build produces user-visible prose or professional writing. Even small text should follow pyramid structure, and the logical ordering principle applies to design flow too.

Trigger if any of:

- Task touches user-visible text inside the app: copy, microcopy, empty-state messages, error messages, onboarding flow, help content, tooltips, toasts, form labels, email templates, notification text.
- Task creates or edits: `README.md`, `CHANGELOG.md`, `docs/**/*.md`, PR descriptions, release notes, design docs, status updates, exec summaries, handoff documents.
- Goal contains: "write", "draft", "summarize", "document", "one-pager", "brief", "memo", "deck", "slides", "presentation", "status update".
- Designing information architecture or section ordering: use the pyramid logic for top-down flow (governing thought, then MECE key lines, then support).

Action: load `pyramid-principle:pyramid-principle-core` first for ground rules, then the specific skill matching length and format. If absent, use `fallbacks.md#structured-writing`.

**prompt-builder** (prompt authoring or audit)

Fires when prompts are a core part of the product, not when prompts appear incidentally in code comments or test fixtures.

Trigger if any of:

- Building or editing prompts that the app sends to an LLM at runtime: document-generation prompts (ProductPilot style), chat-with-user system prompts, voice-interaction prompts (SpeakSavvy style), reranker prompts, eval-judge prompts.
- Robust agent or prompt pipeline present in the product: multi-step prompts, RAG, tool-use flows.
- Semantic search over user queries: use `prompt-builder` to revise the query before embedding or retrieval.
- Authoring a new agent's instructions (the body of an `agents/*.md` file serving as LLM guidance).
- File signals: `prompts/`, `system-prompt.*`, strings passed to `messages[{role:"system"}]`, `anthropic.messages.create`, `openai.chat.completions.create`, prompt templates in `.prompt` or `.txt` held as product assets.
- Goal contains: "system prompt", "agent prompt", "prompt engineering", "rewrite this prompt", "improve this prompt", "audit prompts", "eval judge".

Existing prompt guardrail: if the task touches an **existing** in-product prompt (not a new one), pause and ask the user before running `prompt-builder`. Prompts are often tuned against real evals; silent rewrites can regress quality. Offer the option, do not auto-apply.

Action: load `prompt-builder:prompt-builder` (plugin) if installed, else the personal `prompt-builder` skill, else `fallbacks.md#prompt`. For existing-prompt edits, capture before-and-after in `.build-loop/prompts/` with version suffixes so regressions are detectable.

**building-with-deepagents** (DeepAgents / local-LLM agent work)

Fires whenever the project uses the open-source `deepagents` package. DeepAgents has subtle API shape (SubAgent dict, middleware stack, per-agent tool scoping) that makes hand-rolled focus modes and flat-tool-list designs silently wrong — small local models exhibit tool-call hallucinations in ways that scoping fixes and prompt injection doesn't.

Trigger if any of:

- Repo grep: `from deepagents` or `import deepagents` in any Python source file
- `deepagents` in `pyproject.toml`, `requirements*.txt`, `uv.lock`, or `poetry.lock`
- Goal mentions: "agent", "sub-agent", "subagent", "planner/researcher/writer", "focus mode", "tool-call hallucination", "LangGraph agent", "ChatOllama", "local LLM agent"
- File signals: `create_deep_agent`, `SubAgent`, `AGENT_ROLES`, `agent_focus_prompt`
- Pain symptoms in the conversation: "`<namespace>.<tool>` is not a valid tool", "silent thinking", "model loaded forever", "threads vanish on restart"

Existing-agent guardrail: treat agent definitions like existing prompts — pause before rewriting, capture before-and-after in `.build-loop/agents/` with version suffixes. Tool scoping changes downstream behavior for every query; regressions are expensive to spot.

Action: load `build-loop:building-with-deepagents` before any code edit involving agent construction, tool binding, or streaming. The skill's `references/anti-patterns.md` lists 12 concrete bugs we've hit — verify none of your planned changes reintroduce them.

**Judgment: prompt-builder vs inline prompt**

Not every prompt needs the full engine. Use `prompt-builder` when the prompt is load-bearing. Craft a simple inline prompt when it is throwaway.

Use `prompt-builder` when any of these are true:

- The prompt ships in the product and runs at scale.
- The prompt is sent to end users or generates user-visible output.
- The prompt is part of an agent, eval judge, RAG pipeline, or semantic-search query rewriter.
- Output correctness is measured (evals exist or are planned).
- The prompt will be reused across features, or maintained over time.
- Token cost matters because it runs millions of times.

Roll your own inline prompt when all of these are true:

- One-shot usage inside the current build loop (dispatching a subagent, asking Claude to transform a file, generating a migration script).
- Not persisted to the product codebase.
- Output is checked once by the orchestrator, not by an eval.
- A short direct instruction is clearer than a 6-Part Stack.

Default when uncertain: if the prompt text will exist in the repo after the build, use `prompt-builder`. If it only exists as a line in an orchestrator message during this build, inline is fine.

### Plugin / hook / skill / agent work — mandatory

If Phase 1 detects that the task touches plugin components, Phase 3 must map each task to the authoritative skill below and Phase 4 must load that skill. **Do not infer plugin formats from memory or by reading another plugin's config.**

| Task surface | Skill (authoritative) | Fallback |
|---|---|---|
| `.claude-plugin/plugin.json` | `plugin-dev:plugin-structure` | Read `RossLabs-AI-Toolkit/LESSONS-LEARNED.md` — paths must start with `./` |
| `hooks/hooks.json` or hook scripts | `plugin-dev:hook-development` + run `plugin-dev/scripts/hook-linter.sh` | Command hooks default; silent-exit pattern; NO prompt hooks on PostToolUse/Stop/SessionStart |
| Slash commands (`commands/*.md`) | `plugin-dev:command-development` | — |
| Subagents (`agents/*.md`) | `plugin-dev:agent-development` + `RossLabs-AI-Toolkit/agents/` | `fallbacks.md#agent-authoring` |
| MCP servers (`.mcp.json`) | `plugin-dev:mcp-integration` | `.mcp.json` must NOT wrap with `mcpServers` key (Method 1) |
| `~/.claude/settings.json` | `plugin-dev:plugin-settings` | — |
| New skill (SKILL.md) | `plugin-dev:skill-development` + `skill-builder` (personal) | Official skill format; SKILL.md ≤200 lines |
| New plugin end-to-end | `plugin-builder` (personal) → delegates into `plugin-dev:*` | — |

### External knowledge — check before coding

| Source | When | How |
|---|---|---|
| `/cookbook` | Claude API patterns: tool calling, PTC, code execution, Agent SDK, RAG, thinking, structured output, batch, caching | Invoke `/cookbook` or read `~/.claude/projects/-Users-tyroneross/memory/reference_claude_cookbook.md` |
| `RossLabs-AI-Toolkit/LESSONS-LEARNED.md` | Any plugin work | Read during Phase 1 ASSESS |
| `context7` MCP | Any library/framework use | `query-docs` / `resolve-library-id` — do NOT code from training data |
| `research` skill | Factual claims, pricing, versions | T1 official docs → T4 forums; 2-source minimum |

## Efficiency

- No extraneous code. Every line serves the goal
- Terminal output: current phase, key decisions (one line each), status changes, failures. No restated instructions, no verbose reasoning, no "I will now proceed to..."
- Subagent context: minimum needed per job. Shared reads done once, passed as condensed summaries
- Tools: load on demand as each phase needs them. Do not pre-load tools or skills before they're relevant

## Tool Selection

Use the best available tool for each need. If a preferred tool is unavailable, improvise — never block on a missing dependency. The skill is self-sufficient; external tools make it faster but their absence does not stop the loop.

## Phase 1: ASSESS — Understand Current State

**Goal**: Know what exists before changing anything. Scope assessment to files and directories relevant to the stated goal. On large codebases, limit to 2-3 focused exploration passes.

1. **Detect available plugins and personal skills**: Run `node ${CLAUDE_PLUGIN_ROOT}/skills/build-loop/detect-plugins.mjs`. Write the JSON result into `.build-loop/state.json` under `availablePlugins`. All subsequent routing consults this object.
2. **Detect project type**: web app, API, library, mobile, CLI, monorepo, **Claude Code plugin**, one-shot new app, existing-app iteration. A plugin is detected by the presence of `.claude-plugin/plugin.json`, `hooks/hooks.json`, `skills/*/SKILL.md`, `commands/*.md`, `agents/*.md`, or `.mcp.json`. If detected, mark the build as "plugin work" in state.json and plan to load the `plugin-dev:*` skills before any manifest/hook/skill/agent/MCP/command edits.
3. **Set sub-routers**: `uiTarget` (web / mobile / null), `platform` (web / apple / react-native / null), `migrationSource` (replit / lovable / bolt / v0 / null). See the Capability Routing §Sub-routers rules.
4. **Detect available tools**: test runners (`package.json` scripts, `pytest.ini`, etc.), linters, deploy targets.
5. **Map architecture** using best available approach:
   - If `.navgator/architecture/index.json` exists → load `build-loop:navgator-bridge` skill, run its Phase 1 blast-radius read. Output goes to `.build-loop/state.json.navgator.phase1`. Phase 3 PLAN consults this for scoping. Flags high-fan-in hotspots, 2-hop dependents, layer-crossing risks, per-file `navgator impact` for top 5 risks, and prompts-in-scope when `triggers.promptAuthoring` is true.
   - Else if `gator:*` is available → use those commands.
   - Else → Explore agents → file reading.
5a. **Observability baseline** (informational, no changes): load `build-loop:logging-tracer-bridge` to classify the project's logging level (well-instrumented / print-only / silent). Recorded in `.build-loop/state.json.observability`. If Phase 5 fails with a silent failure, Phase 6 may trigger this bridge reactively.
5b. **Debugger context priming** (if `availablePlugins.claudeCodeDebugger`): call `build-loop:debugger-bridge` Phase 1 step — invokes `list` MCP to summarize recent incidents in this project. One-line output; no action.
6. **Capture UI state** (if web/mobile): IBR scan if available → showcase capture → manual screenshot.
7. **Load memory**: Read `~/.build-loop/memory/MEMORY.md` (global) then `.build-loop/memory/MEMORY.md` (project). Project memory overrides global on conflict. See §Memory.
8. **Check prior state**: Read `.build-loop/issues/` and `.build-loop/feedback.md` if they exist. Surface relevant items.
9. **Research gate**: If project uses external frameworks/APIs/deploy targets, check current official docs (Context7 → research skill → WebSearch) before building assumptions.
10. **Recovery check**: If `.build-loop/state.json` exists with incomplete phases, offer to resume from last completed phase.

**Output**: Structured state summary. Brief.

## Phase 2: DEFINE — Goal, Scoring, Evaluation Criteria

**Goal**: Define the target and how to measure success — before writing any code.

1. **State the goal** in concrete, measurable terms
2. **Suggest 3-5 scoring criteria** from: functionality, code quality, UX, performance, security, accessibility, test coverage — select what's relevant to the project and goal. Show for confirmation
3. **Design eval graders per criterion** using the grading hierarchy:

**Prefer code-based graders** (fast, deterministic, cheap):
- Test suite pass/fail, lint/type check, build succeeds, schema validation, accessibility audit

**Use LLM-as-judge graders** when code can't check the criterion:
- Binary pass/fail only — no Likert scales
- One evaluator per dimension — no multi-dimension God Evaluator
- Judge reasons in thinking tags, outputs only pass/fail
- Use Claude (the running instance) as judge

Each criterion gets: `description | grading method | pass condition | evidence required`

Load `eval-guide.md` in this skill directory for judge prompt template and scorecard format if needed.

4. **Write goal file**: Save to `.build-loop/goal.md` in the project directory

## Phase 3: PLAN — Steps & Optimization

**Goal**: Break work into executable steps, then optimize the plan before execution.

1. **Invoke `writing-plans` skill** for detailed task breakdown
2. **Identify parallel-safe tasks** vs sequential dependencies — build a dependency graph
3. **Define subagent integration points**: Where do agents need to coordinate? Where must outputs be tested together?
4. **Research check**: For any external framework, API, or deployment target — verify current docs before coding

**Optimization checklist** (review the plan for these before proceeding):
- Can more tasks run in parallel? Unnecessary sequential bottlenecks?
- Can subagent context be smaller? Shared reads that should be done once?
- Missing dependencies, interface mismatches, env assumptions?
- Changes that could conflict with each other (oscillation risk)?
- Define coordination checkpoints where subagents must sync

**Output**: Plan file with dependency graph, integration points, and optimization notes.

## Phase 4: EXECUTE — Build With Agents

**Goal**: Implement the plan using parallel subagents where possible.

1. **Use `subagent-driven-development`** — dispatch subagents per task
2. **Model assignment**: Default implementer `model: sonnet`, `effort: medium`. Consult `Skill("build-loop:model-tiering")` for task-specific defaults and escalation triggers
3. **Parallel agents** where dependency graph allows
4. **Each agent gets**: minimal context + clear integration contract + relevant doc context for external APIs
5. **UI work**: Load `calm-precision` skill and follow it
6. **Surface pre-existing issues**: Don't silently ignore problems discovered during implementation. Log to `.build-loop/issues/` with context
7. **Coordination checkpoints**: At defined sync points, verify agent outputs align before continuing

## Phase 4.5: CRITIC — Adversarial Read-Only Review

**Goal**: Catch scope drift, patch-over-root-cause, missed edge cases, and rubric violations before they reach validation — using a separate read-only agent that has no incentive to sandbag.

1. **Dispatch `sonnet-critic`** per chunk (or per batch of chunks if they share a rubric). The critic has tools=[Read, Grep, Glob] only — no Edit, no Write
2. **Input**: the rubric from `.build-loop/goal.md` + the implementer's diff (changed files or `git diff HEAD~1`)
3. **Output**: JSON with `findings`, `strong_checkpoint_count`, `guidance_count`, `pass` boolean
4. **Routing**:
   - `pass: true` → proceed to Phase 5
   - `pass: false` with `strong-checkpoint` findings → route back to Phase 4 for fixes (not Phase 6 — no iteration counter burn yet)
   - Findings marked `guidance` → record in `.build-loop/issues/` and proceed
5. **Escalation**: if the same chunk fails critic twice, escalate the implementer to Opus per `model-tiering` skill §Escalation Triggers

Skip this phase only when the chunk is trivial (single-file typo fix, config value change) or when no rubric criteria apply.

## Phase 4.7: AUTO-OPTIMIZE — Metric-Driven Post-Build Optimization

**Goal**: After implementation is built and committed, run autonomous optimization passes on anything with a mechanical metric. Uses Karpathy's autoresearch pattern: constrained scope + mechanical metric + atomic changes + git commit/revert.

**Load the `build-loop:optimize` skill for the full protocol.**

1. **Discover targets**: Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_loop.py --detect --workdir "$PWD"` to find available optimization targets
2. **`simplify` is always available**: Reduces line count in files changed by Phase 4. Metric = total lines, direction = lower, guard = build passes. Catches dead imports, unused files, redundant code
3. **Other targets** appear when the repo has the right tooling (build script → optimize-build, test runner → optimize-tests, bundler → optimize-bundle)
4. **Run sequentially after Phase 4 commits** — not in parallel with implementation subagents
5. **Budget**: 3-5 iterations for post-build (polish, not deep optimization)
6. **Results**: Dispatch `overfitting-reviewer` after the loop. Archive to `.build-loop/optimize/experiments/`

**Skip this phase** when:
- No mechanical metric exists for the changes made
- The build was trivial (single-file, <20 lines)
- User explicitly opts out

Optimization results feed into Phase 5 (Validate) as additional evidence.

## Phase 5: VALIDATE — Eval Against Scoring Criteria

**Goal**: Test every criterion from Phase 2 with evidence.

**Code-based graders first** (fast, deterministic):
```
test suite       → pass/fail
lint / type check → pass/fail
build            → pass/fail
accessibility    → threshold pass/fail (if web)
schema validation → pass/fail
custom assertions → pass/fail
```

**LLM-as-judge graders second** (for nuanced criteria):
- Each criterion → its own focused judge prompt
- Binary pass/fail output only
- No multi-dimension scoring in a single prompt

**Evidence collection**:
- Every pass/fail must have evidence: command output, screenshot, or judge reasoning
- Use `verification-before-completion` for evidence-based claims
- No criterion marked "pass" without proof

**Output**: Scorecard with pass/fail per criterion + evidence

### Memory-first gate (when a criterion fails)

Before routing failures to Phase 6, load `build-loop:debugger-bridge`. For each failed criterion with an error-like signal (exception, test failure, build error), call its Phase 5 memory gate. **Memory is a hypothesis, not a patch — every verdict routes to Phase 6 as an adapted plan by default**:

- `KNOWN_FIX` → adapt prior incident as Phase 6 plan. Direct-apply only when all three gates hold: file match + version match + second validation signal (stack frame, error class, or log entry). Otherwise behave as LIKELY_MATCH.
- `LIKELY_MATCH` → adapt prior incident as Phase 6 plan
- `WEAK_SIGNAL` → note reference, investigate normally
- `NO_MATCH` → standard Phase 6 fallthrough; store at Phase 8 for future learning

Skip silently when `availablePlugins.claudeCodeDebugger` is false. See `debugger-bridge/SKILL.md` for the direct-apply gate specification.

## Phase 6: ITERATE — Fix & Retry (up to 5x)

**Goal**: Fix failures systematically, not blindly.

If any criterion fails:
1. **Diagnose root cause** — don't just retry
2. **Create targeted fix plan** for failed criteria only
3. **Execute fix** (subagents if parallel-safe)
4. **Re-validate ONLY failed criteria** — re-run their specific graders
5. **Track**: iteration count, what failed, what was attempted, what changed

**Convergence detection**:
- Same criterion fails 2x with same root cause → escalate to user
- Fix A breaks criterion B (oscillation) → flag and ask user
- 3+ criteria fail simultaneously after a fix → systemic issue, stop and reassess

**Hard stop at 5 iterations**. Report remaining failures in Phase 8.

Log each iteration to `.build-loop/state.json`.

### Stuck-iteration escalation (debugger-bridge Phase 6)

At the start of each attempt (not just the first), invoke `build-loop:debugger-bridge` Phase 6 logic:

- **After 2 consecutive same-root-cause failures** → parallel multi-domain assessment via `claude-code-debugger:assess`. Pass `model: sonnet` to domain assessors explicitly (override the debugger's default `inherit` to prevent 4× Opus fan-out from the Opus 4.7 orchestrator).
- **After 3 consecutive failures on the same criterion** → causal-tree investigation via `claude-code-debugger:debug-loop`. Runs its own 7-phase cycle internally; returns with fix applied or hard-stop.
- Skip when `availablePlugins.claudeCodeDebugger` is false.

## Phase 7: FACT CHECK & MOCK SCAN

**Goal**: Nothing false, fabricated, or placeholder reaches the user.

Two gates. Run in parallel for speed. Load `phases/fact-check.md` in this skill directory for detailed guidance.

**Gate A — Fact Checker**: Trace every rendered %, $, score, count, or assessment to its data source. Flag "always", "never", "100%", "guaranteed" — replace with accurate language unless genuinely absolute. Verify scoring logic produces displayed values. Every rendered metric needs a traceable path: source → transformation → display.

**Gate B — Mock Data Scanner**: Lightweight scan of production code paths for residual mock/placeholder data — hardcoded fake data, placeholder text, faker/random in display paths, stubs replacing real implementations. Exclude test files and dev-only code.

**Gate C — Architectural Violation Check** (if NavGator available): load `build-loop:navgator-bridge`, run its Phase 7 violation check. Executes `navgator rules --json` and classifies:
- Blocking (route to Phase 6): `circular-dependency`, `layer-violation`, `database-isolation`, `frontend-direct-db` at error severity
- Warning (include in report): `hotspot`, `high-fan-out`, `orphan`
- Recurrences: flags violations that match entries in `.navgator/lessons/lessons.json` so user sees "this type has appeared N times in this project"

Skip silently when `.navgator/architecture/index.json` does not exist.

Blocking issues (any gate) → route back to Phase 6 (Iterate). Warnings → include in report.

## Phase 8: REPORT — Present Results

**Goal**: Clear, honest summary with certainty markers.

- **Scorecard**: Final pass/fail per criterion with evidence
- **✅ Known**: Verified working features (with proof)
- **⚠️ Unknown**: Untested or uncertain areas
- **❓ Unfixed**: Issues remaining after iteration limit
- **Discovered issues**: Pre-existing problems from `.build-loop/issues/` — user decides: fix now, defer, or dismiss
- **Fact check results**: Any unverifiable claims or mock data warnings

Write scorecard to `.build-loop/evals/YYYY-MM-DD-<topic>-scorecard.md`.

**Store debugger incidents**: if `build-loop:debugger-bridge` tracked any failures resolved during this run (Phase 5 or Phase 6), invoke the debugger `store` MCP tool for each resolved incident. **Also** invoke the `outcome` MCP tool for each Phase 5 gate where a prior `KNOWN_FIX` or `LIKELY_MATCH` was applied — reports back worked/failed/modified. This is both sides of the memory-first gate's feedback loop; skip it and the debugger's verdict classifier never improves.

**Orphan scan** (if NavGator available): invoke `build-loop:navgator-bridge` Phase 8 — runs `navgator dead`, diffs against Phase 1 baseline, surfaces new orphans introduced this build.

**Also: append a run entry to `.build-loop/state.json.runs[]`** for Phase 9's self-improvement scan. Schema:

```json
{
  "date": "<ISO-8601 UTC>",
  "goal": "<short goal text>",
  "outcome": "pass | fail | partial",
  "phases": { "1": { "status": "pass|fail", "duration_s": N, "root_cause": "?" }, ... },
  "diagnosticCommands": ["shell commands run during build"],
  "filesTouched": ["absolute paths edited"],
  "manualInterventions": [{ "phase": N, "note": "short description" }]
}
```

Capture `filesTouched` from `git diff --name-only` relative to the pre-build HEAD. `diagnosticCommands` and `manualInterventions` come from orchestrator state tracking during the run.

## Phase 8.5: SIMPLIFY — Trim The Diff

**Goal**: remove incidental complexity added during iteration without changing behavior.

Run `/simplify` (or load the `simplify` skill directly) against the changed files. Focus:
- Inline single-use helpers that were extracted "just in case"
- Delete dead branches, commented-out code, and unused imports
- Collapse try/except that catches a thing that can't happen
- Remove validation for invariants that the type system or upstream already guarantees
- Reduce abstractions that have exactly one call site

Preserve: public API surface, test coverage, observability (logging/tracing), documented behavior. If a simplification would break evidence collection or monitoring, keep it.

For **plugin work specifically**: also re-run `plugin-dev/scripts/hook-linter.sh` against any touched `hooks.json`, and `grep` the manifest for `../` or bare paths (per `RossLabs-AI-Toolkit/LESSONS-LEARNED.md` 2026-04-05). Silent manifest failures are worse than loud ones.

## Phase 9: REVIEW — Self-Improvement Scan

**Goal**: detect recurring patterns across recent runs, auto-draft experimental skills/agents to address them, notify the user for keep/remove decisions. Closes the loop between "build N times" and "build N+1 is faster because we learned".

**Load the `build-loop:self-improve` skill for the full protocol.**

Runs automatically after Phase 8.5 on every build. Also user-invocable via `/build-loop:self-improve` (to trigger a scan without a build).

Quick flow:

1. **Detect** — dispatch `recurring-pattern-detector` (Haiku). Reads `.build-loop/state.json.runs[]`, returns JSON list of patterns crossing confidence threshold.
2. **Filter** — keep only `high` confidence or `count >= 4`; manual interventions at lower threshold.
3. **Draft** — for each kept pattern, dispatch `self-improvement-architect` (Sonnet). Writes to `.build-loop/skills/experimental/<name>/SKILL.md` with an A/B Experiment section.
4. **Signoff** — orchestrator (Opus 4.7) reviews: approve / revise once / discard.
5. **Track** — baseline metric recorded in `.build-loop/experiments/<name>.jsonl`. Subsequent runs that match the skill's trigger append applied entries. After sample size complete, decision emitted.
6. **Notify** — concise synthesis appended to Phase 8 report, including removal command.

**Skip** when:
- `.build-loop/state.json.runs[]` has fewer than 3 entries
- Detector returns no patterns crossing threshold
- User has set `.build-loop/config.json.autoSelfImprove: false`

**User control**:
- Remove any artifact: `rm -rf .build-loop/skills/experimental/<name>/`
- Inspect tracking: `cat .build-loop/experiments/<name>.jsonl`
- Disable globally: create `.build-loop/config.json` with `{"autoSelfImprove": false}`

**What this phase will NOT do**:
- Modify the build-loop plugin repo
- Promote artifacts cross-project without explicit `/build-loop:promote-experiment` invocation (not yet implemented)
- Run more than once per build

## Memory — Global and Project-Scoped

Build-loop maintains two memory stores. Every build reads both; writes go to exactly one based on scope.

**Global memory**: `~/.build-loop/memory/`

- Applies across every project this user builds.
- Examples: "Deployment to Vercel uses `vercel deploy --prebuilt` when `ENABLE_AUTH=true`"; "Neon is the default Postgres for Next.js 16 projects"; "TestFlight upload uses ASC API key from `~/.appstoreconnect/private_keys/`"; "User prefers zero-dep scripts over package additions".
- Structure: one file per fact/lesson/tool-discovery. Index in `~/.build-loop/memory/MEMORY.md` (line-per-entry: `- [Title](file.md) — hook`).
- Types: `tool`, `deployment`, `library-choice`, `user-preference`, `pattern`.

**Project memory**: `<project>/.build-loop/memory/`

- Applies only to the current project.
- Examples: "This app's design system lives in `src/styles/tokens.css`, not Tailwind"; "Routes under `/admin/` require `requireAdmin()` guard"; "The `custom_themes` table has a user_id VarChar bug from 2026-04-13 — see migration note".
- Same structure as global; index in `.build-loop/memory/MEMORY.md`.
- Types: `design`, `convention`, `gotcha`, `decision`, `contract`.

### Routing rule (always ask this question)

**"Would this apply to a different project?"**

- **Yes** → global (`~/.build-loop/memory/`). Deployment tools, library choices, general user preferences, reusable patterns.
- **No** → project (`.build-loop/memory/`). Design tokens, internal APIs, project-specific gotchas, per-repo conventions.
- **Ambiguous** → ask the user once, then save. Don't guess.

### When to write memory

- User states a preference or convention: save immediately.
- A build surfaces a new tool/library/deployment pattern worth reusing: save after Phase 8.
- A project-specific gotcha or decision emerges: save during Phase 8 REPORT.
- Do NOT save: ephemeral task details, things already derivable from code or git log, state that changes per build.

### When to read memory

- Always during Phase 1 ASSESS.
- Before deploying: check global deployment memory.
- Before UI work: check project design memory.
- Before adopting a new library: check global library-choice memory.

## Skill-on-Demand — Build, Use, Keep or Drop

Build-loop can author new skills mid-flow when a repeated task pattern emerges and no existing skill covers it.

**When to author a new skill:**

- A procedure has repeated ≥3 times across builds OR is complex enough that a subagent prompt keeps growing.
- No existing skill (global or project) matches.
- The procedure has a clear trigger and a deterministic output format.

**Where to write it (two tiers):**

- **Project-local skill**: `<project>/.build-loop/skills/<name>/SKILL.md` — only loaded for this project. Use for project-specific procedures (e.g., "run the custom smoke-test suite for this app").
- **Global skill**: `~/.claude/skills/<name>/SKILL.md` — loaded for every session. Requires user confirmation before writing (global scope is consequential).

**Procedure:**

1. Draft the skill during Phase 4 if the need arises. Use the `plugin-dev:skill-development` skill if available, else `fallbacks.md#agent-authoring` format (but for skills — name, description, body ≤200 lines, progressive disclosure).
2. Use it immediately in the current build.
3. At Phase 8, score its usefulness: did it reduce friction? Would you use it next build?
4. Decide: **keep**, **promote** (project → global), or **drop**.
   - Keep (project) — leave in `.build-loop/skills/`.
   - Promote — move to `~/.claude/skills/`, confirm with user.
   - Drop — delete and note in `.build-loop/feedback.md` why it didn't earn its keep.
5. Record the decision in `.build-loop/memory/` (project) or `~/.build-loop/memory/` (global) as a `pattern` entry.

**Never proliferate skills**. A skill that isn't used twice across builds should be dropped. Prefer extending an existing skill over creating a new one.

## Feedback — After Every Build

Append one line to `.build-loop/feedback.md` only if something surprising happened: a plan deviation, a tool that produced wrong results, a skill gap, an eval blind spot. Format: `YYYY-MM-DD | what happened | what to do differently`. No entry needed if the build went as expected.

On future `/build` runs, check this file and adjust proactively.

## Process Flow

```
ASSESS → DEFINE → PLAN → EXECUTE → CRITIC → OPTIMIZE → VALIDATE
                            ↑         ↓         ↓           ↓
                            │    strong-    (if metric   All pass? ──yes──→ FACT CHECK ──pass──→ REPORT → SIMPLIFY → FEEDBACK
                            │  checkpoint   exists)          ↓                  ↓
                            └───(re-execute)                no            blocking issues
                                                             ↓                  ↓
                                                         ITERATE ←──────────────┘
                                                        (up to 5x)
```

CRITIC (Phase 4.5) is the adversarial read-only pass. Strong-checkpoint findings route back to EXECUTE without consuming iteration budget.

OPTIMIZE (Phase 4.7) runs the autoresearch-pattern optimization loop when a mechanical metric exists. Dispatches `optimize-runner` for autonomous iteration, then `overfitting-reviewer` for adversarial review. Skipped when no metric applies.
