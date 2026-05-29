# Build Loop Plugin

Orchestrated 5-phase development loop (+1 optional) for significant multi-step code changes.

**Phases**: Assess → Plan → Execute → Review → Iterate (5x max). Optional: Learn (cross-build pattern detection).

Review has internal sub-steps: Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Report.

## Principles

- Self-sufficient: works without any specific tool installed. Build-loop owns its UI design route through `build-loop:ui-design`, `design-contract-specialist`, `skills/build-loop/references/recent-design-structures.md`, `skills/ui-design/references/ui-guidance-sources.md`, `ui-validator`, and `skills/build-loop/fallbacks.md`; external design tools are explicit-only accelerators, not automatic build routes. (As of 0.6.0 the debugger is bundled internally; see KNOWN-ISSUES "Plugin merge — 2026-05-02".)
- North star first: every build captures app/repo purpose, update intent, user value, and non-goals, then passes that intent to each subagent.
- Beauty in the basics: core flows, real data, clear hierarchy, working controls, useful states, and accurate information matter more than extra surface area.
- Modular by default, not by dogma: prefer high cohesion, loose coupling, stable interfaces, scalable boundaries, and MECE file/agent ownership unless a documented exception better serves the use case.
- Tools loaded on demand, not pre-loaded
- Guidelines for the creation process, guardrails for user-facing output
- Concise output — say only what the user needs to decide or act; cut narration, restated context, filler; no jargon. Lead each point with the finding. Progressive disclosure: headline first, files/detail below. Number points as standalone **bold-number** paragraphs with a blank line between (plain `1.` list syntax renders compressed). Style only, never a gate.
- No false data, no mock data in production, no unverified claims
- Diagnose before fixing, converge or escalate
- Research persistent problems before retrying — when a fix doesn't hold, the same criterion fails repeatedly, or behavior contradicts your model, escalate to internet research from trusted sources (T1 official docs/issue trackers first) to find the root cause. `root-cause-investigator` carries WebSearch; use it. A documented upstream bug or library/terminal behavior often explains an "impossible" intermittent failure faster than another local iteration — and stops you layering a workaround over a known root cause.
- Learn from recurring patterns — auto-draft experimental skills with A/B comparison, user keeps or removes
- Cherry-pick from companion tools, don't embed — except when integration density justifies a merge. Companion repos stay independent; build-loop consumes their artifacts only when the user or plan explicitly asks. claude-code-debugger was merged inline in 0.6.0 because the debugger is invoked from inside the build loop on every Review-B / Iterate failure (multiple times per build) — keeping it external created loose coupling without a benefit. Other companions remain separate.

## Claude Code Integration

- `/build-loop:run [goal]` — triggers the build-loop skill which orchestrates all 5 phases (the bare `/build-loop` form is deprecated due to a namesake collision with the skill of the same qualified name; see `KNOWN-ISSUES.md`)
- `/build-loop:debug <symptom>` — deep iterative root-cause investigation via the bundled `debug-loop` skill (also auto-invoked by the orchestrator on Review-B failures and Iterate attempts 2 and 3)
- `/build-loop:debugger`, `/build-loop:debugger-detail`, `/build-loop:debugger-scan`, `/build-loop:debugger-status`, `/build-loop:assess` — bundled debugger surface
- `/build-loop:self-improve` — run Phase 6 Learn alone against recent runs without a new build
- Build orchestrator agent (Opus 4.7) coordinates phase execution and spawns parallel subagents
- Fact-checker and mock-scanner agents run in parallel during Review sub-step D
- Recurring-pattern-detector (Haiku) + self-improvement-architect (Sonnet) run during Phase 6 Learn
- External skills used when available: `writing-plans`, `subagent-driven-development`, `calm-precision`, `verification-before-completion`, `plugin-dev:skill-development`, `navgator` — phases degrade gracefully without them

## Model Tiering

Build-loop is **multi-model**. Roles are assigned by **tier** (Thinking / Code / Pattern), not provider-specific identifiers. The Anthropic mapping (Opus 4.7 / Sonnet 4.6 / Haiku 4.5) is the default; equivalents from other providers (GPT-5, Gemini 2.5, qwen2.5-coder) substitute cleanly when their benchmarks meet the tier contract.

| Tier | Anthropic default | Role | Substitution rule |
|---|---|---|---|
| **Thinking** | Opus 4.7 | Orchestrator, plan, severity ranking, audit, scope-auditor, promotion-reviewer | SWE-bench Verified ≥78% AND frontier-class on ARC-AGI / GPQA |
| **Code** | Sonnet 4.6 | Implementer, optimize-runner, overfitting-reviewer, self-improvement-architect, synthesis-critic, alignment-checker, independent-auditor (chunk + build scope, consolidated 2026-05-23) | SWE-bench Verified ≥75% AND tool-use accuracy ≥85% |
| **Pattern** | Haiku 4.5 | Mock-scanner, recurring-pattern-detector | Fast/cheap; doesn't hallucinate on bounded structured tasks |
| (inherit) | session model | Fact-checker | Inherits from caller — context-driven |

Full provider substitution table + swap recipes: `references/model-tier-mapping.md`.

## Dual-mode dispatch (intentional A/B test architecture)

Build-loop supports two dispatch modes, both first-class:

- **Mode A — Top-level / fan-out (default):** invoked via `/build-loop:run` Skill from the user session. Thinking-tier orchestrator dispatches up to 4 Code-tier implementer subagents in parallel. Best for parallel-safe features (≥3 independent chunks) and large features (≥10 commits).
- **Mode B — Inline / single-context (preserved):** invoked via `Agent(subagent_type="build-loop:build-orchestrator", ...)`. Thinking-tier orchestrator handles all phases inline (no fan-out, no-sub-sub-agents rule). Best for small/medium features (≤6 commits), cross-cutting refactors, and as the comparison baseline for dispatch-mode A/B testing.

Both modes share the same plan, same Phase 1-4 logic, same Phase 6 Learn. The orchestrator auto-detects which mode it's in (`agents/build-orchestrator.md:529-530`). **Mode B is not deprecated** — it's the canonical baseline for tier-mix telemetry and works better for cross-cutting work where single-context visibility beats fan-out parallelism.

See `references/model-tier-mapping.md` §"Dual-mode A/B test design" for the full design.

### Concurrent dispatch isolation (NEW 2026-05-12)

When dispatching `build-loop:build-orchestrator` as a sub-agent OR when the caller has another long-running edit session on the same workdir, pass `isolation: "worktree"` to the Agent tool. The orchestrator does not enforce single-worktree-per-run; isolation is the caller's contract. The Agent tool creates a temporary `git worktree` for the dispatch; the agent's `HEAD`, index, and working tree are isolated from the parent session. On return, the worktree path + branch appear in the envelope and the caller merges or cherry-picks back. Without isolation, two writers (main session + background orchestrator, OR two orchestrators) on the same worktree race on `HEAD` and the index — symptom log: commits on the wrong branch, staged residue bundled into unrelated commits, branches switched under the dispatch's feet. Decision-doctor-cc 2026-05-11 lost 5–10 min of `git reset` / `cherry-pick` recovery to this class three separate times.

## Project Data

Runtime data stored in `.build-loop/` within consumer projects (created on first use):
- `goal.md` — current build goal
- `intent.md` — north star, update intent, user value, and non-goals
- `config.json` — optional repo flags, including deploymentPolicy and `dependencyCooldown.allowlist` (supply-chain: scopes/names exempt from the 7-day publish-age gate; default `["@tyroneross/*"]`). Phase 1 Assess runs `scripts/inject_dependency_cooldown.py` on JS projects; a PreToolUse hook backstops ad-hoc installs. Rule: `C-SUPPLY/dependency_cooldown`.
- `state.json` — iteration state, phase progress, compact intent/structure summaries, **`runs[]`** for self-improvement scanning
- `feedback.md` — post-build lessons
- `evals/` — scorecard archives
- `issues/` — discovered issues
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

- `skills/architecture/{scan,impact,trace,rules,dead,review}/SKILL.md` — copied from NavGator (`~/dev/git-folder/NavGator/`)
- `skills/debugging/{memory,store,assess,debug-loop}/SKILL.md` — copied from claude-code-debugger (`~/dev/git-folder/claude-code-debugger/`)

Each native SKILL.md carries `source:` (relative path from `~/dev/git-folder/`) and `source_hash:` (SHA-256 of the canonical file at copy time). The drift-detector at `skills/sync-skills/SKILL.md` (script: `scripts/sync_skills.py`) walks both trees, recomputes hashes, and reports anything that's drifted from upstream. Read-only — never auto-updates a SKILL.md.

The legacy bridges (`skills/navgator-bridge/`, `skills/debugger-bridge/`) are now deprecation stubs that point at the native skills; remove after one release cycle. The orchestrator (`agents/build-orchestrator.md`) calls native skills directly — Phase 1 Assess, Review-B Validate, Review-D Fact-Check, Review-F Report, and Phase 5 Iterate cross-layer pre-step.

**Why native, not bridges**: bridges drift silently against their upstream source; native sourced skills have provenance, are version-tracked, and can be audited with one script.

## Plugin Bridging Policy

When build-loop integrates capabilities from other plugins, **bridge artifacts and explicit actions, not default orchestration**. Programmatic calls (CLI flags, MCP tools, headless modes) compose well only when the user or plan explicitly requests them; viewer dashboards and persistent browser sessions don't belong inside an automated loop. IBR is explicit-only and is no longer auto-routed into UI builds.

**Documented exception**: `mockup-gallery` is invoked from Phase 2 Plan for major UI work (new pages, ≥40% redesigns) to draft black-and-white mockups before any UI is written. Mockup drafting IS the action, and the user has explicitly authorized this pattern as the only place build-loop spawns plugin UI.

## Cross-Tool Support

This repo includes `AGENTS.md` — the open-standard version of the build loop methodology. Non-Claude tools (Codex, Copilot, Cursor, etc.) can use that file directly for the same workflow without Claude-specific integration.

## Coordination

Multi-session, multi-tool runs (Claude Code + Codex; two Claude sessions; Claude + CI verifier) coordinate via Rally Point + a per-run coordination file. The binding rules — operating rule (verdicts gating), `post()`-mandatory channel writes, MECE packets for every write-handoff, release-surface verification, Phase D closeout — live in **`references/coordination-rules.md`**. New coord files start from **`references/coordination-file-template.md`** (drop-in shape; placeholders, mandatory sections, parser-compatible verdict headings).

Cheat-sheet (full detail in B1):

- Verifier verdicts (`PASS` / `VARIANCE` / `BLOCKED`) are gating, not advisory; Claude does not advance past `verification-pending` until the latest verifier verdict for that step is `PASS` or a resolved `VARIANCE`.
- Every cross-session signal goes through `scripts/rally_point/post.py` `post()` (bumps revision + appends record in canonical order — never raw `append_change`).
- Every write-handoff brief MUST include all four MECE fields (owns / does-not-own / interface-contract / integration-checkpoint); linted via `scripts/brief_mece_validator.py`.

For parity with non-Claude tools (which lack SessionStart hooks), the host-neutral preflight CLI is `rally start claude_code --human` — same envelope, same routing decision (`proceed_solo` / `join_active`). When intent/files are known, use `rally start claude_code --intent "<work>" --path "<file>" --json`; peers read active work from `active_peers[]` and last-known active/stopped session state from `peer_states[]`. When done, use `rally stop claude_code --session-id "<sid>" --reason "done" --json` so peers see the stop and file claims are released. Claude Code typically gets startup for free via SessionStart; the CLI is the manual equivalent when hooks are unavailable. If the binary isn't installed, proceed without it.

## Plugin Development

- Plugin manifest: `.claude-plugin/plugin.json`
- Test changes by installing locally: add repo path to `~/.claude/settings.json` under `projects.plugins`
- Runtime data goes in `.build-loop/` in consumer projects, not in the plugin repo


## Debugging Memory

This project uses @tyroneross/claude-code-debugger for debugging memory.

**Automatic behavior:**
- Past debugging sessions are stored and indexed
- Similar incidents surface automatically when investigating bugs
- Patterns are extracted from repeated issues
- Session stop hook mines audit trail for missed incidents

**Commands:**
- `/debugger "symptom"` - Search past bugs for similar issues
- `/debugger` - Show recent bugs, pick one to debug
- `/debugger-detail <ID>` - Drill into a specific incident or pattern
- `/debugger-status` - Show memory statistics
- `/debugger-scan` - Scan recent sessions for debugging work

The system learns from your debugging sessions automatically.
