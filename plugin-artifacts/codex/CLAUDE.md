# Build Loop Plugin

Orchestrated 5-phase development loop with a mandatory Phase 6 Learn, for significant multi-step code changes.

**Phases**: Assess → Plan → Execute → Review → Iterate (5x max). Mandatory: Phase 6 Learn (always runs, always emits a `## Learn` outcome line; accruing / deferred / full depending on run state).

Phase 6 uses one proof-bearing command: `python3 scripts/learn/__main__.py run --workdir "$PWD" --run-id <recorded-run-id> --source review-g --json`. Complete its returned work orders through `attest`; a complete receipt is required.

Review has internal sub-steps: Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Auto-Resolve → Report.

## Principles

- **KISS + DRY — code and output (governing principle).** Before adding a rule, gate, schema, script, agent, or report section, first try to (a) delete something, (b) extend something that already exists, or (c) do nothing. A new mechanism must earn its place against a *named, observed* failure in this repo — never a cited statistic or a blog claim. Prefer one rule that covers many cases over many narrow rules; prefer one source of truth over duplicated logic. For output: say it once, in the fewest words that still carry the evidence; omit empty sections; headline first, detail on demand. Fewer rules and fewer lines is the default — growth is the exception that must be justified out loud. This principle outranks the others below when they tension: when in doubt, simplify.
  - **Every issue is a systems issue.** When something doesn't work, debugging is not done at the fix — it's done when the *system* is updated so the class of issue can't recur: a durable guidance, check, simplification, or restructure that addresses the root cause and the meta-point, never a surface patch. The default corrective move is to **reduce complexity** — fewer lines, fewer dependencies, fewer steps — or, when size is irreducible, better structure (split a large file into smaller ones, progressive disclosure) so the next agent or human navigates it with minimal cognitive load. The goal is the highest-quality, most efficient, durable, scalable outcome. **Scalable usually means simple over compact:** every node (rule, script, agent, step, dependency) is a failure site, so prefer more-readable-but-simpler over clever-but-dense.
- Self-sufficient: works without any specific tool installed. Build-loop owns its UI design route through `build-loop:ui-design`, `design-contract-specialist`, `skills/build-loop/references/recent-design-structures.md`, `skills/ui-design/references/ui-guidance-sources.md`, `ui-validator`, and `skills/build-loop/fallbacks.md`. Headless IBR verification is automatic for renderable UI design updates, comparisons, and audits when installed; native fallbacks preserve the route when it is absent. Native debugging is bundled as skills, not as an MCP server.
- North star first: every build captures app/repo purpose, update intent, user value, and non-goals, then passes that intent to each subagent.
- Beauty in the basics: core flows, real data, clear hierarchy, working controls, useful states, and accurate information matter more than extra surface area.
- Modular by default, not by dogma: prefer high cohesion, loose coupling, stable interfaces, scalable boundaries, and MECE file/agent ownership unless a documented exception better serves the use case.
- Tools loaded on demand, not pre-loaded
- Guidelines for the creation process, guardrails for user-facing output
- Concise output — say only what the user needs to decide or act; cut narration, restated context, filler; no jargon. Lead each point with the finding. Progressive disclosure: headline first, files/detail below. Number points as standalone **bold-number** paragraphs with a blank line between (plain `1.` list syntax renders compressed). Never use the contrastive-pivot construction ("not X — it's Y", "isn't X, it's Y", "not just X but Y"); state the point directly. Structured internal envelopes (subagent JSON returns, run records, judge-decisions, MECE briefs) are unconstrained — they exist for machines. The final user-facing report is enforced via `scripts/report_lint.py` at Phase 4G with one-pass auto-revise self-heal (WARN-mode; never a hard halt). The full contract is `skills/build-loop/references/output-style.md` (sentence level) and `skills/build-loop/references/status-output-format.md` (block level: the cold-read test — actor, specific object, modality — plus target-state-then-gap framing).
- No false data, no mock data in production, no unverified claims
- Diagnose before fixing, converge or escalate
- Bounded verification: default validation/smoke runs to quick checks (seconds–minutes); escalate to a long run only when a radical/unexpected result needs it, and surface that first. Prefer a fast live check that catches what a mocked/green suite hides over a long run that adds no signal.
- Commit and push authorship stays human or service-owned. Agent involvement can be noted in commit bodies, run notes, `.build-loop` context, judge decisions, or auxiliary metadata, but official git/GitHub author, committer, push actor, release actor, or equivalent platform actor fields must not be set to Claude Code, Codex, or another agent identity.
- Research persistent problems before retrying — when a fix doesn't hold, the same criterion fails repeatedly, or behavior contradicts your model, escalate to internet research from trusted sources (T1 official docs/issue trackers first) to find the root cause. `root-cause-investigator` carries WebSearch; use it. A documented upstream bug or library/terminal behavior often explains an "impossible" intermittent failure faster than another local iteration — and stops you layering a workaround over a known root cause.
- Learn from recurring patterns — auto-draft experimental skills with A/B comparison, user keeps or removes
- Cherry-pick from companion tools, don't embed — except when integration density justifies a merge. Companion repos stay independent; build-loop consumes their artifacts only when the user or plan explicitly asks. Build-loop keeps native debugging skills because Review-B and Iterate need deep investigation repeatedly; cross-project debugger memory stays optional in standalone Coding Debugger. Other companions remain separate.

## Claude Code Integration

- **`/build-loop:run [anything, in plain language]` is the ONLY human-facing command.** Describe the task — build, fix, debug, optimize, research, test, root-cause, retrospective, plan, PRD, self-improve — and the orchestrator classifies intent and routes to the right internal mode. No other commands, no mode flags. (The bare `/build-loop` form is deprecated — namesake collision with the skill; see `KNOWN-ISSUES.md`.)
- **All former mode/utility commands are now internal**, reached by intent, not by a separate command: debug, debugger (past-bug search), optimize, research, test, assess, self-improve, promote-experiment, verify-plan, start-prd, setup-memory, knowledge review mode, compose-handoff, rally-point. Agents/build-loop invoke the backing skills directly; humans just say what they want. The full intent→mode map lives in `skills/build-loop/SKILL.md` §Routing. Enforced by `scripts/test_command_surface_policy.py` (only `run` is a command).
- Build orchestrator agent (Opus, Thinking tier — coordination) coordinates phase execution and spawns parallel subagents. Phase 2 plan synthesis runs at **Frontier** *when the Advisor dispatch ladder is gated in* (`synthesisDensity > 5`, `riskSurfaceChange`, `stakes >= medium`, or `dispatch_tier: frontier`) — via the `advisor` agent (Rung 1), a peer host (Rung 2), or an already-Frontier session (Rung 0); when no stakes trigger fires or no dispatch path is reachable, the orchestrator synthesizes the plan inline on its own model and labels it honestly (Rung 3, today's behavior). The verification surface (plan-critic, scope-auditor, independent-auditor, …) runs at Frontier. See `skills/build-loop/references/advisor-dispatch-ladder.md`. The Advisor v1 ladder covers **Phase 2 plan synthesis only**; Phase 1 Assess synthesis runs inline as today (routing it through the Advisor is v2). "Frontier plans" is the *aspiration the ladder makes true on high-stakes Phase 2 plans* — not an unconditional guarantee. **Frontier resolves to Claude Opus 5 since 2026-07-28; high-coupling planning is the one carve-out that still routes to Fable** (`scripts/frontier_gate.py`) — see the tier table below for the evidence and its limits.
- Fact-checker and mock-scanner agents run in parallel during Review sub-step D
- Recurring-pattern-detector (Haiku) + self-improvement-architect (Sonnet) run during Phase 6 Learn
- External skills used when available: `writing-plans`, `subagent-driven-development`, `calm-precision`, `verification-before-completion`, `plugin-dev:skill-development`, `navgator` — phases degrade gracefully without them
- **Cross-file symbol work goes through `code-intel`, never the built-in `LSP` tool.** `code-intel refs|def|hover|symbols|diag <file>:<line>:<col>` is a CLI on PATH (no MCP, no tool approval) covering python/typescript/rust/swift/c; `code-intel doctor` reports per-language readiness. The built-in `LSP` tool only sees servers registered by an installed LSP plugin, and **it degrades silently**: on a language with no registered server it returns a confident, incomplete answer with no error. Observed 2026-08-29 in a private Next.js app — `findReferences` on an exported function returned 1 hit (the declaration) while `code-intel refs` correctly returned 4 including the two real importers. Treat a bare `LSP` result on an unconfirmed language as UNKNOWN, not empty. Same rule and same taxonomy as `agents/scope-auditor.md`; AGENTS.md carries the host-neutral version of this row.

## Model Tiering

Build-loop is **multi-model** and selects on **two orthogonal axes**, encoded as data in `references/model-taxonomy.json` (the single source of truth; `scripts/model_taxonomy.py` is the loader):

- **SEGMENT axis** — work role / primary output: Generative Reasoning, Agentic Execution, Representation/Retrieval, Realtime Interaction, Perception/Input Understanding, Generative Media, Governance/Evaluation. A reasoning model that merely accepts image/audio input is Generative Reasoning with a `multimodal-input` tag — segment is the *primary product role*, not the input modality.
- **CAPABILITY-TIER axis** — a 7-rung ladder: T0 (experimental/restricted frontier) · T1 (ultra-frontier) · T2 (frontier) · T3 (balanced workhorse) · T4 (efficient near-frontier) · T5 (utility/nano/edge) · T-S (specialist infrastructure, off the capability ladder).

Each agent declares a `(segment, tier)` ROLE — the durable KEY into the index. At dispatch the orchestrator resolves the role LIVE via `scripts/resolve_agent_model.py <agent>` (reuses `scripts/model_resolver.py`) and passes the result as the Agent tool's `model` parameter, OVERRIDING the frontmatter; `inherit` agents pass no override. The `model:` frontmatter is the index-DERIVED recommended fallback for fresh-install / non-build-loop hosts — generated, never hand-edited, kept in sync by `scripts/sync_agent_model_defaults.py` (`--check` for CI drift, `--apply` to regenerate; emits only harness-valid tokens). **Selection is Hybrid:** per `(segment, tier)` an ORDERED preferred list (capability rank, honoring Accuracy>Speed>Cost) — the resolver picks the highest-ranked AVAILABLE + host-reachable id, ties broken by release recency; users reorder via the index or config. **The index is user-editable and chat-maintainable:** on model intent in chat the agent reads/edits `references/model-taxonomy.json` and re-syncs (see `skills/model-tiering/SKILL.md` §"Chat-triggered index maintenance"). **A new model is adopted by classifying it once** (host-LLM rubric + WebSearch → record, into BOTH segment + tier; no vendor API call) — no agent edits.

**Back-compat:** the legacy tier tokens `frontier/thinking/code/pattern` fold onto ladder rungs `T1/T2/T3/T4` and remain accepted everywhere (config, plan frontmatter, route_decision, tests). The Anthropic mapping (Claude Opus 5 at both T1 and T2 / Sonnet 5 / Haiku 4.5) is the default, with Fable 5 retained as the second T1 entry; cross-provider equivalents (GPT-5.5/5.4 incl. Codex, Gemini 2.5, qwen2.5-coder) substitute when their benchmarks meet the tier contract. List the selectable models with `python3 scripts/model_overrides.py --list-models`. Model IDs live in the taxonomy data file, not here.

| Tier | Anthropic default | Role | Substitution rule |
|---|---|---|---|
| **Frontier** | Claude Opus 5 (Fable 5 = second T1 entry; deliberate override for high-coupling planning via `scripts/frontier_gate.py`) | **Phase 2 Plan synthesis via the Advisor dispatch ladder when stakes-gated** — `advisor` agent / peer host / already-Frontier session; honestly-labeled inline fallback otherwise (`references/advisor-dispatch-ladder.md`). (Advisor v1 = Phase 2 only; Phase 1 Assess synthesis runs inline as today until v2.) AND verification verdicts: plan-critic, scope-auditor, independent-auditor (chunk + build scope), fix-critique, fact-checker, security-reviewer, overfitting-reviewer, promotion-reviewer. **Plus all database actions** — `database-assessor`, migrations, schema/constraint DDL, data-plane rehearsals, repair/quarantine — pinned here by standing rule because they are irreversible-leaning and high blast-radius (`skills/model-tiering/SKILL.md` §"All database actions run at Frontier tier") | Clears Thinking-tier contract AND benchmarks above the prior-generation Thinking ceiling on at least one of SWE-bench Verified / ARC-AGI / GPQA Diamond |
| **Thinking** | Claude Opus 5 | Coordination (build-orchestrator, assessment-orchestrator), execution-escalation target, audit/learnings synthesis | SWE-bench Verified ≥78% AND frontier-class on ARC-AGI / GPQA |
| **Code** | Sonnet 5 | Execution default — implementer, optimize-runner, bounded domain assessors (api/frontend/perf — **not** db, which is pinned Frontier below), design-contract-specialist, ui-validator, retrospective-synthesizer, self-improvement-architect; deliberate Sonnet retentions on the verification surface for cost: synthesis-critic, alignment-checker (high-frequency, advisory only) | SWE-bench Verified ≥75% AND tool-use accuracy ≥85% |
| **Pattern** | Haiku 4.5 | Mock-scanner, recurring-pattern-detector, transcript-pattern-miner | Fast/cheap; doesn't hallucinate on bounded structured tasks |
| (inherit) | session model | root-cause-investigator | Inherits from caller — context-driven |

**Why Opus 5 took T1 on 2026-07-28, and what the evidence does not cover.** `prompt-model-benchmark-lab/observations/2026-07-28-audit-bakeoff-fable-vs-opus5-vs-sonnet5.json` records Opus 5 as a strict superset of Fable in 3/3 adversarial-audit rounds, including one where it reversed a defect Fable had explicitly cleared. Carry the caveats stated there: n=3 rounds, one repo, one task family; not a clean single-blind design (Fable ran live under time pressure, the others re-audited frozen pre-fix commits); effort was not pinned per arm; grading was orchestrator-adjudicated; Opus ran roughly 3× longer than Fable in one round. Treat it as a directional verification result, not a settled capability ranking. **The evidence is verification-shaped and says nothing about plan/spec authoring** — the only spec-authoring bake-off on record (Fable vs Codex, 2026-07-09) went to Fable, so planning on highly-coupled repos still routes to Fable via `scripts/frontier_gate.py`.

Full provider substitution table + swap recipes + the selectable-model registry: `references/model-tier-mapping.md`.

Selection and prompting are separate axes, and both are now data-driven. `references/model-taxonomy.json` also carries a `prompting_profiles` block keyed by capability rung (T0–T5; `T-S` is exempt) with five fields — `examples`, `constraint_posture`, `edge_case_handling`, `rationale`, `prompt_budget`. At dispatch the profile arrives on the `resolve_agent_model.py` envelope alongside `model`, so no separate lookup is needed. A newly-classified model inherits its rung's profile automatically; `unprofiled_tiers()` flags a rung added without one. The T4/T5 rows carry `confidence: weak` and `status_quo: true` — they encode today's behavior rather than an evidenced posture.

## Dual-mode dispatch (intentional A/B test architecture)

Build-loop supports two dispatch modes, both first-class:

- **Mode A — Top-level / fan-out (default):** invoked via `/build-loop:run` Skill from the user session. Thinking-tier orchestrator dispatches up to 4 Code-tier implementer subagents in parallel. Best for parallel-safe features (≥3 independent chunks) and large features (≥10 commits).
- **Mode B — Inline / single-context (preserved):** invoked via `Agent(subagent_type="build-loop:build-orchestrator", ...)`. Thinking-tier orchestrator handles all phases inline (no fan-out, no-sub-sub-agents rule). Best for small/medium features (≤6 commits), cross-cutting refactors, and as the comparison baseline for dispatch-mode A/B testing.

Both modes share the same plan, same Phase 1-4 logic, same Phase 6 Learn. The orchestrator auto-detects which mode it's in (`agents/build-orchestrator.md:529-530`). **Mode B is not deprecated** — it's the canonical baseline for tier-mix telemetry and works better for cross-cutting work where single-context visibility beats fan-out parallelism.

See `references/model-tier-mapping.md` §"Dual-mode A/B test design" for the full design.

### Privileged commands — one named, coalesced, recorded request (ENFORCED 2026-08-20)

**Rule: never invoke a command that asks macOS for an administrator password directly. Route it through `scripts/privileged_broker.py`.** The broker emits a machine-readable request (task/thread id, repository/worktree, exact argv, purpose, scope, initiating app, timestamp) and prints it next to the dialog, coalesces identical read-only requests behind one authorization within a bounded TTL, and appends every state transition to a hash-chained ledger. `--purpose` is mandatory.

Coalescing is narrow by construction: two requests share an authorization only when resolved argv, scope, trust domain, mutating flag, uid, and registry entry all match. **A mutating request never coalesces and never inherits another task's approval.** The broker never reads, stores, or replays the password — macOS authenticates; the broker only decides who triggers it and shares the resulting output.

**Enforcement:** `scripts/hooks/pre_bash_privileged.py`, wired into the PreToolUse:Bash dispatcher. It is the one gate that runs BEFORE the build-loop scope guard — an admin-password dialog is not repository work, so confining it to opted-in repos would be a hole in the control. A deny is a redirect: the reason carries the exact brokered command. Kill switch `BUILD_LOOP_HOOKS=off`. Registry: `scripts/privileged_commands.json` (data — add an entry, never special-case in code). Forensics and before/after counts: `scripts/privileged_audit.py`.

**History (2026-08-20):** one Codex turn ran `sfltool dumpbtm` at 01:29:11 and again at 01:29:25 PDT, producing two administrator-password dialogs naming only `sfltool` — no app, no repository, no reason. `sfltool dumpbtm` requires root and is undocumented (`sfltool(1)` describes only `sfltool archive`). The retry was rational: a refused privileged read returns empty stdout, indistinguishable from "the grep matched nothing", so the agent retried with `set -o pipefail` purely to recover a return code. Three sessions reached for the same host fact inside 27 minutes with nothing coalescing them. Durable lesson: **a failure that is shaped like a result will be retried.** The broker's negative cache — a denial is remembered and replayed for `negative_ttl_seconds` without a second dialog — is the control that closes it. Full contract: `skills/build-loop/references/privileged-request-broker.md`.

### Concurrent dispatch isolation (ENFORCED default — widened 2026-07-11 to file-editing writers)

**Rule (enforced, not advisory): ANY long-running headless or background writer that COMMITS *or edits files* must do so in a dedicated git worktree, never the live interactive checkout — commit authority is NOT required.** This covers every committing or long-running file-editing writer, not only Agent-tool dispatches: background `build-loop:build-orchestrator` sub-agents, headless launchd/cron pollers and watchers, and any process woken to do work. Two writers on one checkout race on `HEAD`, the index, and the working tree — symptom log: commits on the wrong branch, staged residue bundled into unrelated commits, branches switched under the dispatch's feet, freshly-regenerated artifacts pushed stale, and (2026-07-11) a commit-less editing subagent that outlived its dispatch ~8h and re-applied stale edits onto the live checkout after a branch switch (3 files contaminated; caught and restored).

For the **Agent-tool dispatch path**: when dispatching `build-loop:build-orchestrator` as a sub-agent OR when the caller has another long-running edit session on the same workdir, pass `isolation: "worktree"` to the Agent tool. The Agent tool creates a temporary `git worktree`; the agent's `HEAD`, index, and working tree are isolated from the parent. On return, the worktree path + branch appear in the envelope and the caller merges or cherry-picks back.

For the **headless/background path** (launchd, cron, pollers, watchers): the job must either point its `WorkingDirectory` at a dedicated worktree (`build-loop.worktrees/<agent>-<task>` or `.build-loop/worktrees/<slug>`), set `BUILD_LOOP_WORKTREE_ISOLATED=1` in its plist to declare it provisions a worktree before committing, or stay NOTIFY-ONLY (detect a transition and notify/inject, never edit or commit — the rally "watchers stay narrow" doctrine). The canonical in-repo wake surface (`scripts/wake_scheduler.py` = pure decision engine; `scripts/agent_rally_watcher/watch.py` = event emitter) is notify-only by contract.

**Enforcement:** `scripts/worktree_isolation_lint.py` is the regression artifact. It scans `~/Library/LaunchAgents/*autonomy*`/`*poller*`/`*watcher*` jobs whose `WorkingDirectory` is a live checkout (exit 1 = BLOCKER) and asserts the in-repo wake path stays notify-only — flagging both a `git commit` and a commit-less file-editing call (write/stage/restore) without worktree provisioning (rules `wake-path-grew-a-commit` / `wake-path-grew-a-file-edit`). Run it in Phase 1 Assess on self-recursive build-loop runs and before installing any background writer. Co-located test: `scripts/test_worktree_isolation_lint.py`. A companion preflight, `scripts/verify_worktree_target.py` (co-located test `scripts/test_verify_worktree_target.py`), asserts a dispatched isolation-worktree actually resolves to the INTENDED target repo (not the session repo) and emits the exact `git worktree add` command to self-correct on mismatch — enforcing the former doc-only "[worktree targets session repo]" note.

**History:** Decision-doctor-cc 2026-05-11 lost 5–10 min of `git reset` / `cherry-pick` recovery to this class three separate times. The control existed as prose from 2026-05-12 but was advisory and scoped only to the Agent-tool path, so a headless codex autonomy poller (`WorkingDirectory` = the live build-loop checkout) reproduced all three corruptions on 2026-06-22 — the meta-cause that prompted promoting this control from documentation to an enforced lint. On 2026-07-11 the doctrine was widened again: a commit-LESS Lane-5 file-editing subagent outlived its dispatch ~8h and re-applied stale edits onto the live shared checkout after a branch switch (3 files contaminated; caught via a late completion envelope and restored with `git restore`). The prior rule scoped isolation to writers that COMMIT, so a long-running editor that never commits fell through — the rule is now commit-authority-agnostic. Durable lesson: `memory/feedback_commitless_writers_need_worktree_isolation.md`.

## Project Data

Runtime data stored in `.build-loop/` within consumer projects (created on first use):
- `goal.md` — current build goal
- `intent.md` — north star, update intent, user value, and non-goals
- `config.json` — optional repo flags, including deploymentPolicy and `dependencyCooldown.allowlist` (supply-chain: scopes/names exempt from the 7-day publish-age gate; default `["@tyroneross/*"]`). Phase 1 Assess runs `scripts/inject_dependency_cooldown.py` on JS projects; a PreToolUse hook backstops ad-hoc installs. Rule: `C-SUPPLY/dependency_cooldown`.
- `state.json` — iteration state, phase progress, compact intent/structure summaries, **`runs[]`** for self-improvement scanning
- `feedback.md` — post-build lessons
- `evals/` — scorecard archives
- `issues/` — discovered issues (current-run bugs; short-lived). Repo-local, so inherently scoped to this repo.
- `.build-loop/backlog/items/<ID>.md` — classed deferred work, never an active Phase 5 queue. `planned` items can promote at a planning boundary; `initiative` items require user approval plus an isolated non-main worktree and cannot deploy to production; `decision` items surface only for their matching workstream while unrelated work continues. The personal mirror lives at `build-loop-memory/projects/<slug>/backlog/<ID>.md`; recall unions it with canonical items by ID. Cross-repo findings still route to the shared Operations Center queue.
- `release-pending.md` — user-created marker signaling "in-flight feature batch is complete; advise version bump." Read by Sub-step D Gate 6 (`scripts/version_advisor.py`). Empty file = use defaults; body = release notes. User deletes after the bump commit lands.
- `ux-queue/<id>.md` — UX-impacting findings from Sub-step D Gate 7 (`scripts/ux_triage.py`) and Gate 8 (UI coverage gaps), each with a complete fix plan from `templates/ux-fix-plan.md`. Drained by Phase 5 Iterate.
- `followup/<topic>.md` — overflow when iteration cap is reached with queue entries remaining. Becomes input to a subsequent `/build-loop:run` invocation; Plan phase is skipped for these entries.
- `skills/experimental/` — auto-drafted skills from Phase 6 Learn (remove with `rm -rf`)
- `agents/experimental/` — auto-drafted agents from Phase 6 Learn
- `skills/active/` — auto-promoted skills (opt-in; requires `autoPromote: true` + effective sample ≥ 8)
- `proposals/` — pending promotion/removal proposals awaiting user confirmation
- `experiments/<name>.jsonl` — A/B tracking log per experimental artifact
- `experiments/discarded.jsonl` — Opus-rejected drafts with reasons

## Native Architecture & Debugging Skills (Sourced from Canonical Repos)

Architecture and debugging are load-bearing for nearly every build, so build-loop owns native copies under:

- `skills/architecture/{scan,impact,trace,rules,dead,review}/SKILL.md` — copied from NavGator (a sibling repo checked out alongside build-loop, e.g. `<sibling-repos>/NavGator/`)
- `skills/debugging-memory/SKILL.md` — build-loop-native debugging workflow, op-routed (`{op: "search" | "store" | "assess"}`; per-op detail in `references/{search,store,assess}.md`) — plus `skills/debug-loop/SKILL.md` — adapted from the standalone debugger lineage

Each `skills/architecture/` SKILL.md carries `source:` (relative path from the sibling-repos root) and `source_hash:` (SHA-256 of the canonical file at copy time). The drift-detector at `skills/sync-skills/SKILL.md` (script: `scripts/sync_skills.py`) walks the architecture tree, recomputes hashes, and reports anything that's drifted from upstream. Read-only — never auto-updates a SKILL.md. (The former `skills/debugging/{memory,store,assess}` skills were folded into `debugging-memory` on 2026-07, pool-consolidation Inc 5; they are native/adapted with no canonical upstream, so drift-detection for them is retired.)

The legacy bridges (`skills/navgator-bridge/`, `skills/debugger-bridge/`) are now deprecation stubs that point at the native skills; remove after one release cycle. The orchestrator (`agents/build-orchestrator.md`) calls native skills directly — Phase 1 Assess, Review-B Validate, Review-D Fact-Check, Review-F Report, and Phase 5 Iterate cross-layer pre-step.

**Why native, not bridges**: bridges drift silently against their upstream source; native sourced skills have provenance, are version-tracked, and can be audited with one script.

## Plugin Bridging Policy

When build-loop integrates capabilities from other plugins, **bridge artifacts and bounded programmatic actions, not persistent orchestration**. Viewer dashboards and persistent browser sessions do not belong inside an automated loop. Non-verification IBR usage (interactive viewer, persistent browser sessions, full-suite dashboards) stays explicit-only.

**UI visual-verification is the carved-out exception (BL-3, `5c78fbd`).** When Build Loop updates, compares, or audits a renderable UI design and IBR is installed, route the verification step through `build-loop:ibr-bridge` as the primary verifier. When IBR is absent or cannot reach the surface, fall through to `native-ax-driver`, simulator/browser evidence, `ui-validator`, and the static scanner. The complete trigger and boundary contract lives in `references/ibr-ui-verification-policy.md`. Symbol-only verification (`nm`, `strings`, `otool`, "identifier present", "compiles cleanly") is never a substitute for visual/AX verification.

**Documented exception**: `mockup-gallery` is invoked from Phase 2 Plan for major UI work (new pages, ≥40% redesigns) to draft black-and-white mockups before any UI is written. Mockup drafting IS the action, and the user has explicitly authorized this pattern as the only place build-loop spawns plugin UI.

## Cross-Tool Support

This repo includes `AGENTS.md` — the open-standard version of the build loop methodology. Non-Claude tools (Codex, Copilot, Cursor, etc.) can use that file directly for the same workflow without Claude-specific integration.

## Coordination

Multi-session, multi-tool runs (Claude Code + Codex; two Claude sessions; Claude + CI verifier) coordinate via Rally Point + a per-run coordination file. The binding rules — operating rule (verdicts gating), `post()`-mandatory channel writes, MECE packets for every write-handoff, release-surface verification, Phase D closeout — live in **`references/coordination-rules.md`**. New coord files start from **`references/coordination-file-template.md`** (drop-in shape; placeholders, mandatory sections, parser-compatible verdict headings).

Cheat-sheet (full detail in B1):

- Verifier verdicts (`PASS` / `VARIANCE` / `BLOCKED`) are gating inside an active coordination file; Rally carries those peer-authored verdicts but does not independently verify them.
- Every cross-session signal goes through `scripts/rally_point/post.py` `post()` (bumps revision + appends record in canonical order — never raw `append_change`).
- Every write-handoff brief MUST include all seven MECE fields (owns / does-not-own / interface-contract / integration-checkpoint / allowed-tools / denied-tools / acceptance-criteria); linted via `scripts/brief_mece_validator.py`.

For parity with non-Claude tools (which lack SessionStart hooks), the manual preflight derives a session-qualified native Rally actor. A bare host family would merge concurrent Claude sessions into one squad, claim owner, and reader cursor:

```bash
BASE_TOOL=claude_code
RALLY_SESSION_ID="$(python3 scripts/rally_point/actor_identity.py --tool "$BASE_TOOL" --field session-id)"
RALLY_TOOL="$(python3 scripts/rally_point/actor_identity.py --tool "$BASE_TOOL" --session-id "$RALLY_SESSION_ID")"
rally enter --tool "$RALLY_TOOL" --session-id "$RALLY_SESSION_ID" --json
rally next --tool "$RALLY_TOOL" --json
```

When files are known, add `--path "<file>"` to `rally enter` and post the claim with `rally say claim --tool "$RALLY_TOOL" --subject "<work>" --path "<file>" --json`; peers read active work from Rally room state. When done, run `python3 scripts/agent_rally.py stop --workdir "$PWD" --tool "$BASE_TOOL" --session-id "$RALLY_SESSION_ID" --json` so only that exact session stops and releases its claims. Claude Code typically gets startup for free via SessionStart; the CLI is the manual equivalent when hooks are unavailable. If the binary isn't installed, proceed without it.

Rally is coordination metadata, not verification evidence. Use Rally to find peers, claims, handoffs, and verifier messages; use git, tests, manifests, registries, or GitHub directly for code, package, version, and release truth.

## Plugin Development

- Plugin manifest: `.claude-plugin/plugin.json`
- Test changes by installing locally: add repo path to `~/.claude/settings.json` under `projects.plugins`
- Runtime data goes in `.build-loop/` in consumer projects, not in the plugin repo
- Hooks are advisory and non-blocking by default. Stop hooks must emit valid JSON when they emit stdout, exit 0, and reserve blocking for explicit safety/security/integrity gates.
- Dogfooding build-loop against itself (self-recursive runs that arm per-commit mode + self-mod safety): launch with `claude --plugin-dir <path-to>/build-loop` (your local checkout). Background, alias, restart-boundary caveat: `skills/build-loop/references/self-recursive-dev.md`.


## Debugging Memory

This project uses build-loop-native debugging memory. Standalone Coding Debugger can be installed separately for cross-project memory.

**Automatic behavior:**
- Resolved incidents are stored under `.build-loop/issues/`
- Similar local incidents surface before hard debugging work
- Patterns from repeated issues can be promoted during Learn
- Optional Coding Debugger mirror adds cross-project recall when installed

**How to reach it (no separate commands — just `/build-loop:run` + plain language):**
- "debug this / why is X failing" → deep root-cause investigation
- "have we hit this bug before?" → past-incident search
- "show me incident <ID>" → drill into a specific incident/pattern
- "debug memory stats" → memory statistics
- "scan recent sessions for debugging work" → session scan

The system learns through explicit Review-F storage and Learn-phase promotion.
