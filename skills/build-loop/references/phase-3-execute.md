<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Phase 3: Execute (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full Execute phase: parallel subagent dispatch, single-writer git contract, and C5 halt-and-ask backstop.

## Phase 3: Execute — Build With Agents

**Goal**: Implement the plan using parallel subagents where possible.

1. **Use `subagent-driven-development`** — dispatch subagents per task
2. **Model assignment**: Default implementer `model: sonnet`, `effort: medium`. Consult `Skill("build-loop:model-tiering")` for task-specific defaults and escalation triggers
3. **Parallel agents** where dependency graph allows
4. **Each agent gets**: minimal context + clear integration contract + relevant doc context for external APIs + the intent packet from `.build-loop/intent.md` + the MECE ownership packet from the plan (`owns`, `does not own`, `interface contract`, `integration checkpoint`)
4a. **Implementers do NOT commit** (NEW 2026-05-07 — single-writer git contract). Implementers modify the working tree and return `files_changed` + `commit_subject` + `commit_body` in their envelope. The orchestrator commits sequentially after each parallel batch returns: `git add -- <files>` + `git commit -m <subject> -m <body>`, one implementer at a time through the pre-commit hook. This prevents the parallel-commit race that lost 3 of 4 commits in example-app round 3 (2026-05-07). See `agents/build-orchestrator.md` §"Phase 3 commit step" for the full procedure.
4b. **Halt-and-ask backstop for architectural-class decisions** (NEW — C5). When an implementer encounters a synthesis-class decision NOT in the plan's `synthesis_dimensions` AND it's architectural-class (where a phase lives, defensive contract shape, error-propagation policy, persistence boundary, hard-fail counters), the implementer returns `status: "blocked"` with the decision in `novel_decisions[]` and does NOT commit. The orchestrator dispatches each blocked decision to the configured Thinking-tier resolver (per `references/model-tier-mapping.md` — never a hardcoded model name), persists resolutions to `state.json.novelDecisionResolutions[]`, and re-dispatches the implementer with resolutions appended to its brief. Hard-fail counter N=3 per chunk; exhausted chunks surface as ❓ Unfixed in Review-F. C3's attestation lint and C4's synthesis-critic still cover what they can grade — C5 catches what falls outside both. Full procedure: `agents/build-orchestrator.md` §"Phase 3 halt-and-ask branch".
5. **Codex execution adapter**: If running in Codex, load `references/codex-subagents.md` before any spawn decision. Spawn `explorer` or `worker` subagents only when the Codex permission gate passed; otherwise execute locally. When spawning a worker, use `templates/codex-worker-prompt.md`, prefer explicit prompt packets over full context forks, and require the worker return changed files, validation, unresolved risks, and integration notes.
6. **UI work (when `uiTarget != null`)**: Every UI subagent prompt MUST be prepended with the verbatim contents of `templates/ui-subagent-prompt.md` (loaded as raw text, not as a link). The template injects:
   - Mandate to load `calm-precision`, read `.build-loop/app-contract/ui.md` when present, and use external platform/design skills only when explicitly requested by the orchestrator
   - Mandate to apply the plan's `## UI Input/Output Contract` from `references/ui-io-contract.md`
   - Mockup-vs-rule conflict policy: rule wins; subagent must report `RULE BEATS MOCKUP:` decisions
   - Inline anti-pattern checklist (status pills, ungated animations, theme-token bypass, Dynamic Type, accessibility labels, touch targets, VoiceOver consistency, no fake buttons)
   - Required env hooks (e.g. `@Environment(\.accessibilityReduceMotion)` on SwiftUI animations)
   - Self-verification: run scanner before returning, zero must-fix on changed files

   Subagents cannot rely on parent context — knowledge that doesn't enter the prompt doesn't reach the code. The template entering the prompt is non-negotiable. Plus also load `calm-precision` skill at the orchestrator level for cross-cutting decisions. Apply "beauty in the basics": every visible element needs a purpose, working behavior, clear hierarchy, useful states, accurate data, and an explicit input/output contract.
7. **Surface pre-existing issues**: Don't silently ignore problems discovered during implementation. If an issue affects users and is local to the current build, plan and fix it automatically. If it is too large/risky, log to `.build-loop/issues/` with user impact and proposed fix.
7a. **Simplify as you go**: remove dead code AND prefer the clearest, equal-or-better-performing logic/architecture — never just deletion; preserve behavior + correctness.
8. **Coordination checkpoints**: At defined sync points, verify agent outputs align before continuing
