<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Capability Routing (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full capability routing table, trigger conditions, and plugin/hook/skill/agent mandatory routing rules.

## Capability Routing

Build-loop prefers repo-owned agents and bundled skills for core loop decisions. External plugins are accelerators only when explicitly requested or when a row below names them as secondary. Each capability has three tiers: **preferred** (build-loop-owned surface) → **secondary** (another installed plugin or skill that can partially cover) → **inline fallback** (guidance text from `fallbacks.md`, injected verbatim into subagent prompts).

Phase 1 runs `node ${CLAUDE_PLUGIN_ROOT}/skills/build-loop/detect-plugins.mjs` and writes the result to `.build-loop/state.json` under `availablePlugins`. All routing consults that object.

### Core loop skills/assets (always check)

| Skill | Used In | Fallback |
|-------|---------|----------|
| `writing-plans` | Phase 2 (Plan) | Write a structured plan directly: goal, tasks with exact file paths, dependency order, test commands |
| `subagent-driven-development` | Phase 3 (Execute) | Dispatch parallel agents manually using the host's available delegation tool for independent file groups |
| `verification-before-completion` | Phase 4 sub-step G (Report) | Run all test/build/lint commands and confirm output before claiming completion. For app/UI changes this is NOT sufficient alone — also run the runtime UI⇄source-of-truth parity check (`runtime-parity-verification`); compile-green and a screenshot do not prove the running flow works |
| `runtime-parity-verification` | Phase 4 Review-B + Phase 5 Iterate (any `uiTarget != null` or user-visible flow) | Cross-check the rendered/queryable UI against the authoritative backend (DB/API/daemon/tool-state), screen-independently, and keep a validated per-repo smoke. Catches the "action does nothing / not showing / shows empty despite real data / stale projection" class. Drivers: web `ui-validator`; macOS `native-ax-driver` / IBR `scan_macos`; iOS `idb`; agent = tool-result vs rendered output. Reference smoke: easy-terminal `tools/smoke_launch.py` |
| `simplify` (slash: `/simplify`) | Phase 4 sub-step E (Simplify) | Self-review the diff: remove scaffolding, inline single-use helpers, delete dead branches |
| `complexity_detector.py` (accelerator, not a gate) | Phase 4 sub-step E (Simplify) | Diff-scoped stdlib-AST hotspot detector for changed Python; surfaces high-severity hotspots for a simpler rewrite, apply-vs-advise via existing Review-B + independent-auditor. Optional Python aid — the default Simplify pass reasons over the diff language-agnostically (see `phase-4-review.md` §"Sub-step E: Simplify") |
| `build-loop:self-improve` | Phase 6 (Learn) | Scan recent runs for recurring patterns, auto-draft experimental skills/agents with A/B tracking, notify user for keep/remove decisions |
| Intent capability pack | Phases 1-4 | Read `references/intent-capability-pack.md`; write `.build-loop/intent.md`; pass the intent packet to every subagent |
| Modular systems pack | Phases 1-4 | Read `references/modular-systems-pack.md`; partition files/tasks MECE; prefer modular scalable boundaries unless an exception is documented |
| Codex subagent adapter | Phase 3 (Execute, Codex only) | Read `references/codex-subagents.md`; use `templates/codex-worker-prompt.md` for authorized Codex workers |

### Phase quick reference

| # | Phase | Purpose | Sub-steps / key actions |
|---|---|---|---|
| 1 | **Assess** | Understand state + define goal & criteria | detect tools, map architecture, load memory, write `intent.md` + `goal.md` |
| 2 | **Plan** | Break work, identify parallel-safe, optimize | writing-plans skill → dependency graph |
| 3 | **Execute** | Build per plan | parallel subagents, Sonnet default, Opus escalation |
| 4 | **Review** | Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Auto-Resolve → Report | sub-steps A-G; B-D can route to Iterate; F drains non-destructive items via autonomy_gate; G runs only on final pass |
| 5 | **Iterate** | Fix Review failures, loop back to Review | max 5x; orchestrator stuck-iteration cascade (evidence-gap repair → memory re-check → parallel assess at 2 fails → causal-tree at 3 fails) |
| 6 | **Learn** | Cross-build pattern detection + experimental skill drafting | optional; requires `runs[] >= 3`; auto-promote opt-in |

### Capability routing table

| Capability | Preferred | Secondary | Inline fallback section |
|---|---|---|---|
| Web UI build | `build-loop:ui-design` + `build-loop:design-contract-specialist` (`trigger_point: phase2-design-direction`) + `calm-precision` + `templates/ui-subagent-prompt.md` | `frontend-design:frontend-design` only when explicitly requested | `fallbacks.md#web-ui` |
| Web UI validation | `ui-validator` agent + `audit-design-rules.mjs` + browser/screenshot artifact | `showcase:capture` for visual evidence | `fallbacks.md#web-ui` |
| Orchestrated UI build | `build-loop:ui-design` → build-loop-owned design direction → implementer fan-out → ui-validator/design-contract reconciliation | explicit user-invoked design tool artifacts passed to `design-contract-specialist` | `fallbacks.md#web-ui` |
| Mobile UI build (`uiTarget: "mobile"` — iOS/watchOS sim) | `build-loop:ui-design` + `build-loop:design-contract-specialist` + `calm-precision` + `apple-dev` | — | `fallbacks.md#mobile-ui` + `fallbacks.md#apple-dev` |
| Mobile UI validation (iOS sim) | `xcrun simctl io booted screenshot` for static; `idb ui tap` for interaction | `showcase:capture` | `fallbacks.md#mobile-ui` |
| macOS desktop UI build (`uiTarget: "macos"`) | `build-loop:ui-design` + `build-loop:design-contract-specialist` + `calm-precision` + `apple-dev` | — | `fallbacks.md#mobile-ui` + `fallbacks.md#apple-dev` |
| macOS desktop UI validation | IBR `scan_macos` when `availablePlugins.ibr == true`; else `native-ax-driver` against the running `.app` (pid-anchored). NEVER `xcrun simctl` (no macOS simulator). NEVER `nm`/`strings` as substitute. | `showcase:capture` for screenshot evidence | `fallbacks.md#mobile-ui` |
| Design system tokens | `design-contract-specialist` reads project token/theme/component files and records the source in `.build-loop/app-contract/ui.md` | — | `fallbacks.md#design-tokens` (reads consumer project's token files — never hardcodes) |
| Recent design structures | `design-contract-specialist` reads `references/recent-design-structures.md` and selects by product/workflow/data fit | explicit design-tool artifacts passed as evidence | `fallbacks.md#web-ui` |
| Screenshot / visual evidence | `showcase:capture`, `showcase:record` | `screenshot` MCP tool | `fallbacks.md#screenshot` |
| Web content fetching (low LLM) | `scraper-app:web-scraper` SDK | — | `fallbacks.md#web-fetch` (flags LLM cost in report) |
| Deep debugging | `build-loop:debug-loop` + `build-loop:debugging-memory` native search/store | standalone Coding Debugger only when explicitly installed for cross-project memory | `fallbacks.md#debug` |
| Bug-pattern memory | `build-loop:debugging-memory` | — | `fallbacks.md#bug-memory` (greps `.build-loop/issues/` + `.bookmark/`) |
| Agent authoring | `agent-builder:agent-builder-anthropic` | `plugin-dev:agent-development` (if plugin work) | `fallbacks.md#agent-authoring` |
| DeepAgents / local-LLM agent work | `build-loop:building-with-deepagents` (SubAgent API, middleware stack, per-agent tool scoping, anti-patterns) | — | Read installed `deepagents` source: `python3 -c 'import deepagents, os; print(os.path.dirname(deepagents.__file__))'` then `graph.py` + `middleware/subagents.py` |
| Structured reports / handoffs | `pyramid-principle:pyramid-short-form` (Review-F reports), `pyramid-long-form` (design docs) | — | `fallbacks.md#structured-writing` (SCQA + MECE skeleton) |
| Hosted-IDE migration (Replit / Lovable / Bolt / v0) | `replit-migrate:migration-scan`, `migrate-web`, `migrate-ios`; MCP tools `migrate_scan`, `migrate_plan_web`, `migrate_plan_native`, `migrate_map_apis`, `migrate_map_models`, `migrate_check_progress` | — | `fallbacks.md#migration` (manual inventory + stack-translation) |
| Prompt authoring / review / audit (system prompts, agent prompts, eval judges) | `prompt-builder:prompt-builder` skill; slash commands `/prompt-builder:optimize`, `/score`, `/compare`, `/save`, `/list`. Calibrates to model tier (T1/T2/T3) and deployment (interactive, backend, rag_pipeline, agent, plugin, eval_judge, personal_mobile). Returns 6-Part-Stack prompt + 5-dim score + diagnosis + `[ASSUMED:]` tags + `TEMPERATURE_HINT` | `prompt-builder` (personal skill, same name, loaded via Skill tool) | `fallbacks.md#prompt` |
| iOS / watchOS / macOS dev + deploy | `apple-dev` personal skill (via `Skill("apple-dev")`) | `replit-migrate:migrate-ios` (when migrating *to* native) | `fallbacks.md#apple-dev` |
| Web deploy verification (Vercel) | Vercel MCP (`mcp.vercel.com` remote OAuth, only if user adds it to `.mcp.json`) | Vercel CLI via `scripts/verify_deploy.py` | `fallbacks.md#web-deploy-verify` |
| Strategic frame / PRD grounding (Assess + Review) | `build-loop:prd-bridge` — reads `docs/prd-*.md` frontmatter (`core_principles`, `load_when`) + Navigation Map + Section Index in Phase 1; verifies diff doesn't violate principles in Phase 5 Fact-Check; recommends `prd-builder` skill if no PRD exists. Falls back to grep on principle keywords if frontmatter parser unavailable. | `prd-builder` skill direct invocation | Phase 1 captures north-star + intent fresh into `intent.md` (existing fallback) |
| Architecture scan / impact trace (Assess + Review) | `build-loop:architecture-scan` (Assess refresh), `build-loop:architecture-impact` (blast-radius), `build-loop:architecture-rules` (Review violation check), `build-loop:architecture-dead` (orphan scan) — read `.navgator/architecture/` JSON; native skills sourced from NavGator with provenance and drift-detection via `build-loop:sync-skills` | `gator:*` commands if installed | Read component → edit → re-read downstream |
| Debugger memory-first gate (Review + Iterate) | `build-loop:debugging-memory` — verdict gate (`KNOWN_FIX` / `LIKELY_MATCH` / `WEAK_SIGNAL` / `NO_MATCH`) with strict direct-apply triple-gate (file + version + secondary signal) and Review-F outcome feedback. Orchestrator owns the when-to-fire policy (Review-B + every Iterate attempt) and routes to this skill. | `build-loop:debug-loop` direct (when memory says enter the loop or 3 same-criterion failures) | `fallbacks.md#debug` |
| Runtime visibility / observability (Assess + reactive Review/Iterate) | `build-loop:logging-tracer` — generates stack-appropriate structured logging / OTel with ephemeral-by-default policy (Mechanism A: `DEBUG_TRACE=1` runtime gate; Mechanism B: `git-stash` throwaway). Invoked reactively when an Iterate attempt flags `evidence_gap: true`. Orchestrator runs the passive Assess scan inline (no skill call needed) and only loads this skill when instrumentation is actually being added. | — | `fallbacks.md#logging-fallback` (inline Tier-1 zero-dep JSON logger per stack) |
| Self-improvement / recurring pattern detection (Phase 6 Learn) | `build-loop:self-improve` — runs after every build; detects recurring failures and manual interventions; drafts experimental skills/agents to `.build-loop/skills/experimental/`. Auto-promote to `.build-loop/skills/active/` requires opt-in (`autoPromote: true`) plus effective non-confounded sample ≥ 8; regressions and inconclusive results write proposals to `.build-loop/proposals/` for user confirmation — never auto-remove. Cross-project promotion via `/build-loop:promote-experiment <name>` | — | Manual review of `.build-loop/state.json.runs[]` |
| Context recovery after compaction | `bookmark:*` commands | — | Re-read last plan file in `.build-loop/` |
| Claude Code plugin authoring / review | `plugin-builder` (personal skill), `plugin-dev:*` family | `build-loop:plugin-hygiene-lessons.md` enforces manifest/hook/marketplace rules in Review-D | Read `plugin-hygiene-lessons.md` verbatim |

### Sub-routers (set during Phase 1)

**UI target**: prefer the most specific match — order matters.

1. **macOS desktop (`uiTarget: "macos"`, `platform: "apple"`)** — `*.xcodeproj` or `Package.swift` is present AND any of: (a) project has NO `ios/` directory AND has `Sources/` / `App/` with `*.swift`; (b) Xcode project's `SUPPORTED_PLATFORMS` / deployment target indicates macOS; (c) repo grep shows `import AppKit` or `import SwiftUI` paired with `WindowGroup`/`Window` (macOS scene types) and no `UIKit` import. macOS has no simulator; validation routes to `native-ax-driver` or IBR `scan_macos`, never to `xcrun simctl`.
2. **iOS/watchOS mobile (`uiTarget: "mobile"`, `platform: "apple"`)** — `ios/` directory present, OR `*.xcodeproj`/`Package.swift` with `UIKit` import / `iOS` deployment target. Validation uses the iOS simulator screenshot path.
3. **React Native mobile (`uiTarget: "mobile"`, `platform: "react-native"`)** — `app.json` (Expo) or `App.tsx` with `react-native` import.
4. **Web (`uiTarget: "web"`, `platform: "web"`)** — fallback for everything else with a UI surface.

Tie-breaker: if signals are mixed (an Apple project with both `ios/` and a macOS target), set `uiTarget: "mobile"` and surface a one-line note in Assess; the build can override via `state.json.uiTarget` if the goal targets the macOS surface.

**Migration source**: if `.replit` / `replit.nix` present → `migrationSource: "replit"`. Lovable / Bolt / v0 export markers (e.g. `lovable.config`, `bolt.config`, `v0.dev` in comments) → corresponding source. `replit-migrate` skills generalize — load `migration-scan` for any of the above, override hints as needed.

**Apple deploy**: when `platform: "apple"` AND goal includes "deploy", "TestFlight", or "App Store" → Phase 7/8 invoke `apple-dev` deploy flow using ASC creds per `~/.claude/projects/-Users-tyroneross/memory/reference_asc_credentials.md`. Apply deployment policy first: TestFlight/App Store Connect upload/export defaults to `auto`; App Store production release/submission defaults to `confirm`.

**Web deploy verify**: fires when `.vercel/project.json` or `vercel.json` is present AND the build performed a push/deploy → Phase 4 Review-B invokes `scripts/verify_deploy.py` (preferred-tier upgrade: Vercel MCP only if the user has added it to `.mcp.json`). Infra failures return `skipped`, never block the build.

## Trigger Conditions

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

- Building or editing prompts that the app sends to an LLM at runtime: document-generation prompts (Example App style), chat-with-user system prompts, voice-interaction prompts (example app style), reranker prompts, eval-judge prompts.
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
| `hooks/hooks.json` or hook scripts | `plugin-dev:hook-development` + run `plugin-dev/scripts/hook-linter.sh` | Command hooks default; Stop stdout must be valid JSON; advisory/non-blocking unless an explicit safety/security/integrity gate opts into blocking; NO prompt hooks on PostToolUse/Stop/SessionStart |
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
| `research` skill | Factual claims, pricing, versions | Run `scripts/research_trigger.py` first; T1 official docs → T4 forums; 2-source minimum |
