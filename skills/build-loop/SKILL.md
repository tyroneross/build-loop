---
name: build-loop
description: "Orchestrated build loop for multi-step code work. TRIGGER on verb language ('build', 'implement', 'create', 'add', 'ship', 'wire up', 'integrate', 'refactor', 'migrate', 'rewrite', 'replace') OR symptom language ('fix', 'broken', 'doesn't work', 'isn't loading', 'not displaying', 'missing', 'should show', 'needs to', 'make it', 'show this differently') OR any task touching 2+ files, adding/removing an endpoint, crossing an architectural boundary, or attached screenshots of a bug. SKIP one-line edits, pure Q&A, conversational clarifications, status checks, and trivial typos/renames."
user-invocable: true
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- build-loop@tyroneross:canary:build-loop -->
<!-- canary-end -->

# Build Loop — Orchestrated Development

A 5-phase development loop with a mandatory Phase 6: assess state and criteria, plan, execute, review (critic/validate/fact-check/report as sub-steps), iterate on review failures. Phase 6 Learn (mandatory; always runs) detects cross-build recurring patterns and drafts experimental skills; below 3 runs it accrues; debug-only and budget-exhausted runs defer the expensive arm.

## Routing

**`/build-loop:run` is the ONLY human-facing command.** Type it with any request in plain language — or just describe what you need — and the orchestrator classifies intent and routes to the right internal mode. No flags, no picking a mode, no other commands to learn. Everything below is reached by intent, not by a separate command; agents/build-loop invoke these internally.

Intent → internal mode:

- **Build** (default): full 5-phase loop + mandatory Phase 6 Learn — implementation / fix / refactor / migrate / update / "add" / "wire up" language.
- **Debug**: deep iterative root-cause investigation — symptom language ("broken", "doesn't work", "failing"); also auto-invoked on Review-B failures.
- **Optimize**: metric-driven optimization loop — "speed up", "reduce", "improve" + a mechanical metric.
- **Research**: pre-decision analysis, research packet, no commits — "research", "evaluate", "compare", "should I".
- **Test**: static plugin-test suite — "test plugin", "validate plugin".
- **Root-cause analysis**: blameless RCA producing durable system levers — "root cause", "why did this fail", "post-mortem" (delegates to the `root-cause-analysis` skill).
- **Retrospective**: recursive learning retrospective on a build/project — "retrospective", "retro", "what did we learn", "review this project's trajectory" (delegates to `recursive-retrospective`). *Example: "I need a root cause and a retrospective" → run does both.*
- **Plan / spec**: "write a plan", "spec this" → spec-writing + plan-verify.
- **Repository maintenance / closeout**: repository structure, duplicate source, sibling consolidation, build artifacts, branches, worktrees, stashes, or local-main closeout → `repo-closeout`.
- **PRD**: "start a PRD", "spec out a new app" → prd-bridge / start-prd flow.
- **Self-improve / promote / knowledge / handoff / memory setup**: "scan recent runs", "promote this experiment", "record a decision", "hand this off", "set up memory" → the matching internal skill (self-improve, promotion-reviewer, knowledge, handoff, setup-memory).

**Design intent:** one command for humans, plain-language routing, everything else agent-invoked within build-loop. If a request doesn't match a mode, run treats it as a Build task or asks one clarifying question — it never makes the user pick a command.

### Parallelism config

Fan-out width is machine-aware. The cap is `effective_max_implementers(workdir)` from `scripts/parallelism.py`: `min(config.parallelism.maxImplementers, cpu_count−2, hard ceiling 12)`, defaulting to 8 when no config is present.

To raise the cap toward the hard ceiling, set in `.build-loop/config.json`:

```json
{ "parallelism": { "maxImplementers": 8 } }
```

Values above 12 are clamped. Values above `cpu_count−2` are clamped to `cpu_count−2` to leave headroom for the orchestrator and host OS.

## Autonomous Mode + Per-Commit Mode

Both are conditional modes — their flag tables, budget/iteration caps, question-timeout rules, self-recursive detection, and the per-commit dispatch contract (incl. the GAP-1 parent-dispatch audit + E3 Learn/retro contract) load on demand from `references/autonomous-and-per-commit-modes.md`. Read it when:

- the invocation carries `--long` / `--budget` / `--autonomous=false`, or the goal text matches a long-running keyword (`overnight`, `large-scale`, `multi-day`, …) → **Autonomous Mode** detail;
- `state.json.selfRecursive.enabled` is true, or the invocation carries `--per-commit` / `--no-per-commit` → **Per-Commit Mode** detail.

Default behavior with none of those signals: classic single-pass Phase 1–6, 2h budget, autonomous queue-drain on. The end-of-run `issues/` then `backlog/` drain is a SHIPPED DEFAULT (2026-06-04), reversible per-repo via `.build-loop/config.json` `sessionPrefs.continueFromQueues: "never"`.

## Scope Check

Before starting the loop, assess whether the task warrants it. If the task is a single file edit, a config change, or a fix under ~20 lines — skip the loop and just do it. The loop is for multi-step work where planning and validation add value.

## Keep going until done

Once the user accepts the plan, every phase is authorized scope. The orchestrator does not stop and ask the user between phases. Status updates are fine. Permission requests are not.

Completed, validated, authorized work commits automatically. Asking "should I commit?" or "want me to commit this?" is a workflow violation — committing validated work is a non-gated action (`scripts/autonomy_gate.py` classifies a plain `git commit` as `auto`, exit 0). This applies in both interactive and autonomous mode. The only commit-adjacent stops are autonomy-gate verdicts of `confirm` or `block` on a *push or deploy* command — never on the commit itself.

When build-loop manages a commit or push, official authorship stays human or service-owned. Do not set the git author, committer, GitHub author, push actor, release actor, or equivalent platform actor to `Claude Code`, `Codex`, or any other agent identity. Agent involvement can be recorded in commit-body notes, `.build-loop` run context, judge decisions, or other auxiliary metadata, but those records must not replace the official author or actor fields.

Issues found mid-build (failing tests, attestation drift, critic flags, discoverability gaps) route to Iterate. That is the loop's job. The default is to fix and continue.

The only valid reasons to stop and surface to the user:

- **An action whose autonomy verdict is `confirm` or `block`** (per `python3 scripts/autonomy_gate.py`). The gate is the single source of truth for what counts as "destructive or irreversible action not in the accepted plan." Do not introduce ad-hoc asks outside the gate.
- A destructive or irreversible action that was not in the accepted plan.
- A missing credential or secret only the user has.
- Externally-blocked work the user has to unblock.
- An explicit hand-off point the original plan named.
- A genuine scope branch the plan does not resolve, where the choice changes user-visible outcome.
- 8 hours wall-clock without a successful Review pass, or 5 consecutive Iterate failures on the same criterion.

**Explicitly NOT stops (do not surface these):** (a) posting a coordination handoff to a peer (Codex, another session) is *fire-and-continue* — keep executing your owned lane in parallel; only a verifier verdict that gates the *next dependent step* is a wait, never the handoff itself; (b) output volume or turn length — never a stop condition; (c) "continue or hold?" on remaining work that is authorized, isolated to your own lane/worktree, and determinate is a manufactured fork — finish it, then report once. A multi-step prune/refactor/migration with a defined item list runs to completion across that list.

Otherwise: pick the natural next step, note any reasonable assumption in the run record, and keep moving. One end-of-run report at the end. Drain non-destructive open items via Sub-step F Auto-Resolve before the end-of-run report — see Phase 4 below. See `agents/build-orchestrator.md` §Keep going until done for the orchestrator-side phrasing.

### Self-heal — reactive fix + proactive self-simplification (C-HEAL / self_heal_safe_issues)

Self-heal is **both reactive and proactive**. It is not only triggered by errors.

**Reactive arm:** when build-loop encounters, during any run: (a) an error or crash from its own tooling, a hook, a script, a Bash command, or a build/test/lint failure; OR (b) a quality or performance issue surfaced by any Review sub-step, self-review, fact-check, simplify, or efficiency scan — it ROOT-CAUSES and FIXES it, then continues. Route: produce the fix, classify it via `scripts/classify_action.py`. SAFE → apply, verify (re-run the failed action and relevant tests), commit, continue — no surface, no ask. RISKY → isolate to worktree-branch + log + continue main + surface in report. DECISION/PRODUCTION → surface/escalate.

**Proactive arm:** during deep self-review runs (and any build where `selfRecursive.enabled == true`), the self-review/self-heal loop ALSO proactively simplifies build-loop's own code to prevent issues, streamline work, and improve quality — reducing complexity, splitting oversized files, removing dead or duplicated logic, and adding missing tests. This arm is driven by `self_review.py`'s `self_simplification[]` findings (deep mode, self-recursive). See `references/self-review.md` for the full protocol including the SELF-MODIFICATION SAFETY GATE that is MANDATORY for any change to build-loop's own code.

**Self-modifications execute — they do not stop the loop.** A self-modification that is part of the accepted plan (including edits to the gate, tests, or the self-improvement loop) executes behind the test-suite gate: `self_mod_verify.py --scope auto --auto-revert` must return `verdict: pass`. Build-loop never halts a planned self-modification for human approval. Oversight is post-hoc: (a) self-modifying runs trigger an ADDITIONAL adversarial review (independent-auditor at build scope; the periodic deep self-review re-audits recent self-modifications) — non-blocking; (b) the end-of-run readback reports every self-modification and the additional-review findings. The loop stays on task and reports once, at the end.

**New-skill and new-script authoring:** the self-review/self-heal loop MAY author new skills AND new scripts when doing so prevents a class of issue or streamlines work. New scripts REQUIRE a colocated `test_<name>.py` — no untested script lands. New skills follow the Skill-on-Demand lifecycle (keep/promote/drop) documented below.

**Banned anti-pattern:** bypassing a fixable error and continuing or surfacing — `--no-verify` / `git commit -n`, skipping or xfail-ing a failing test, commenting out failing code, swapping in mock data, `|| true` to swallow a real failure — when a SAFE root-cause fix exists. A workaround is allowed ONLY when the root-cause fix classifies RISKY/DECISION/PRODUCTION or is genuinely infeasible (missing credential, external blocker); then record BOTH the workaround and the surfaced issue.

**Guardrails:** only SAFE auto-applies. Verify after every auto-fix (re-run the failed action). A fix that fails verification routes to the existing Iterate / stuck-cascade (5-fail cap). A fix that would balloon complexity routes to re-plan, not skip. Existing iterate caps provide loop-protection. For self-modifications of build-loop's own repo, the SELF-MODIFICATION SAFETY GATE in `references/self-review.md` §"Self-modification of the restricted repo" is MANDATORY and non-negotiable — it runs before every self-modification commit and auto-reverts on failure. The gate returning `fail` is not a stop — it is an auto-revert; the loop continues with the next item.

### Root cause before done (C-RCA / root_cause_before_done)

**Investigate every open issue to root cause before declaring done — verified by a second subagent.** Before any "done"/completion claim, investigate EVERY open issue — failed tests, loose ends, errors, warnings, minor issues — none are left unaddressed. For each, reach the ROOT CAUSE, not a surface patch. Use the debugging skills (`build-loop:debug-loop` / `root-cause-investigator` / `systematic-debugging`) and/or a **5-whys / causal-tree** analysis to determine the true cause and its blast radius (same root cause at other sites → fix all of them). The fix MUST address the root cause — a surface patch is a violation — AND MUST be verified by another, independent subagent before "done." The second-subagent check reuses `independent-auditor`, `fix-critique`, or a dispatched verifier — no new agent required.

**Closure test (counterfactual):** a root cause is not closed at "an actionable control." It is closed only when the named lever would have **prevented, detected, or contained THIS exact failure on the real input** (not a hand-constructed one) — a control that exists but stays dormant on the real signal does not count. **Fix strength:** prefer the strongest feasible control — `eliminate → impossible-state → automated-block → detect → contain → decision-support → docs` — over the reflex "add a detect-gate." A dependency you don't own is never "ignore it": isolate / validate / monitor / degrade / escalate / accept-residual-risk explicitly.

### Follow-up auto-drain at chunk boundary

A chunk boundary is not a checkpoint. When the orchestrator (or any session under the build-loop skill) is about to write a final report containing a "still-to-do" / "deferred" / "next pass" list of same-shape, same-intent items, route those items through the follow-up queue instead of writing them to the user as prose questions:

1. For each list item, write `.build-loop/followup/<run-id>-<index>-<slug>.md` with frontmatter:
   ```yaml
   intent_anchor: <stable path-or-section in intent.md>
   parent_run: <run-id of the just-completed run>
   shape: <"same-shape" | "adjacent">
   classify: <SAFE | RISKY | DECISION | PRODUCTION>   # from scripts/classify_action.py
   ```
   followed by the item body in markdown.
2. Filter `classify: PRODUCTION` items into `.build-loop/followup/needs-confirm/` and surface them ONCE in the report. Do not auto-execute.
3. After the report is committed, immediately enter a fresh Phase 5 iterate cycle to drain the remaining queue. Re-use the same alignment-checker, scope-auditor, and independent-auditor wiring as the in-run iterate loop — no new dispatch surface required.
4. The phrasing "want me to keep going with the rest?" / "should I continue with X next?" at a chunk boundary is a workflow violation when the items are same-shape and same-intent. C-FLOW/no_ask_at_chunk_boundary in `constitution.md` is the binding citation.

Stop conditions are unchanged from the in-run iterate loop: iterate-cap (25 in autonomous mode, 5 classic), budget exhaustion, any drained item classifying PRODUCTION, 5 consecutive iterate failures, an item whose intent_anchor does not resolve in the current `intent.md` (escalate as DECISION; do not silently widen scope), or explicit user pause.

This applies both to the orchestrator dispatched via `/build-loop:run` AND to any interactive Claude session that has the build-loop skill loaded (the skill description's verb/symptom triggers are broad — most multi-file work loads it). If a session produces a same-shape follow-up list mid-conversation without an active run, the equivalent action is to dispatch `/build-loop:run` with the list as the queue, not to ask "want me to do them?".

## Host Adapters

Build-loop keeps the core method host-neutral, then adapts the execution mechanics to the current coding host.

Role boundaries are canonical in `references/agent-role-taxonomy.md`: the live
Rally Point leadership lease decides who is lead; `implementer` is the current
coder subagent; database/API/frontend/performance agents are assessors unless
their brief explicitly hands them a bounded implementation task.

| Host | Primary surface | Subagent behavior |
|---|---|---|
| Claude Code | `agents/*.md`, slash commands, `Skill(...)`, `Agent` tool | Use the existing Claude orchestrator and agent definitions. Do not rewrite Claude agents for Codex behavior. |
| Codex | `skills/*/SKILL.md`, `AGENTS.md`, templates | Use `references/codex-subagents.md` and `templates/codex-worker-prompt.md` when the user explicitly authorizes subagents or parallel delegation. |
| Other coding tools | `AGENTS.md` | Follow the same phases and ownership packets with the host's available delegation primitives. |

**Codex permission gate**: generic Build Loop wording such as "parallel-safe groups" is not by itself authorization to spawn Codex subagents. In Codex, spawn workers only when the user explicitly asks for delegation/parallel agent work or uses a command flag such as `--parallel`. Without that signal, keep execution local while preserving the MECE plan.

**Native agent-rally capabilities**: build-loop vendors `skills/agent-rally-point/SKILL.md` and `skills/agent-rally-watcher/SKILL.md` as embedded mini-plugin skills. Use those skill entrypoints for Rally Point substrate or watcher work before reaching for the standalone repos. The grouped extraction contract is `scripts/rally_point/plugin_boundary.json`; validate it with `python3 scripts/agent_rally.py boundary --repo "$PWD" --check --json`.

**Ephemeral plan cleanup**: never delete a `.build-loop/plan*.md` or
`.build-loop/plans/*.md` project plan without first archiving it to
build-loop-memory via `python3 scripts/archive_project_plan.py <plan> --workdir
"$PWD"`. Use `--remove-source` only after the archive write succeeds.

**Coding-host coordination polling gate**: when a build-loop task involves more than one coding host, an active rally-point peer, an active coord file, any `inbox/<tool>.jsonl` message, or any `inbox/all.jsonl` broadcast, the current host must keep a cheap watcher live while work is in flight. Rally/coordination output is routing metadata only, not verification evidence; use it to decide who to coordinate with, then verify code/package/release facts against the authoritative source. Use a stable tool id (`claude_code`, `codex`, `cursor`, etc.). Run a one-shot status check first:

```bash
python3 scripts/coordination_status.py --workdir "$PWD" --session-id "$SESSION_ID" --tool "$TOOL_NAME" --json
```

If the status has `active_peers`, `coordination_file`, `inbox_unread_count > 0`, or a user asks whether another host has responded, start:

```bash
python3 scripts/coordination_watch.py --workdir "$PWD" --session-id "$SESSION_ID" --tool "$TOOL_NAME" --interval 5 --jsonl --baseline-current
```

Keep that process attached in the host's tool/session mechanism and poll it before commits, before final responses, and after any 30s work interval. When it emits a revision or inbox change, immediately rerun `coordination_status.py --tool "$TOOL_NAME"`, then run `python3 scripts/rally_point/inbox.py read --workdir "$PWD" --tool "$TOOL_NAME" --json` to read the resolved-channel inbox for `<tool>` plus the common broadcast inbox, and post the required channel response. Do not ask the user to paste peer messages that are already present in the rally channel, the addressed inbox, or the common broadcast inbox.

## Intent Capability Pack

Every build uses `references/intent-capability-pack.md`. Phase 1 captures the app/repo north star and the update intent. Phase 2 maps tasks to that intent. Phase 3 includes an intent packet in every subagent prompt. Phase 4 reviews intent fidelity, user value, UI intentionality, data integrity, and simplicity/scalability.

Core rule: build decisions should create user value and a delightful, trustworthy experience. Mock data, dead controls, unused navigation, decorative options, and excessive choices violate the pack when they reach user-facing or user-decision paths.

## UI Input/Output Contract

Every UI build uses `references/ui-io-contract.md`. Before component choices are locked, build-loop must name every affected user input and system output, classify its data shape, map the operation and domain verb, choose the matching input/output component, document states and modality fallbacks, and trace validation/security to the right layer.

For UI work, Phase 2 plans must include a `## UI Input/Output Contract` section. Phase 3 UI implementer prompts must carry that contract, and Phase 4 validation must check that changed UI surfaces still match it. This applies to forms, tables, charts, voice/audio, file workflows, generated AI output, and streaming responses.

## Modular Systems Pack

Every non-trivial build uses `references/modular-systems-pack.md`. Build-loop should default to modular, scalable, MECE structure with pyramid-structured plans and reports: high cohesion, loose coupling, stable interfaces, one clear file owner per changed file, and no unowned responsibilities.

This is a default, not dogma. If a simpler or more integrated approach better serves the use case, document `MODULARITY EXCEPTION: <reason>` in the plan or report and explain why that choice improves user value, performance, clarity, or delivery risk.

## Pay-it-Forward Architectural Posture

Every build that touches a typed protocol, interface boundary, schema, or multi-surface-capable behavior uses `skills/build-loop/references/pay-it-forward-arch.md`. The rule:

> *"I'd rather do a slightly harder thing now to avoid a more painful change in the future if not prohibited by costs or other concerns."*

When a chunk has two viable paths — **Path A** (minimum-viable, easy to ship) vs **Path B** (same user-visible behavior, but extends the typed contract for future surfaces) — **default to Path B** unless one of four gates blocks (time-budget >2×, missing dep/infra, missing design decision, empty foreclosed-future-capability list).

Phase 2 Plan MUST include a `Path A vs Path B` section for every chunk that fires the signal. Phase 4 Review-A Critic checks that the comparison was performed when applicable. Path B with flexibility-for-its-own-sake (plugin systems, abstract factories, hook architectures not tied to a named future capability) is the explicit anti-pattern — Path B must cite a named roadmap/PRD/intent.md capability that the typed contract unlocks.

## Capability Routing

Build-loop prefers installed plugins and skills over reinventing patterns. Each capability has three tiers: **preferred** → **secondary** → **inline fallback** (from `fallbacks.md`). Phase 1 runs `detect-plugins.mjs` and writes the result to `state.json.availablePlugins`. All routing consults that object.

**Load `skills/build-loop/references/capability-routing.md`** for the full routing table, trigger conditions (pyramid-principle, prompt-builder, deepagents), plugin/hook/skill/agent mandatory routing, external-knowledge sources, and sub-routers.

## Phase 1: Assess — State, Goal, and Criteria

Understand current state, load memory through the automatic context bootstrap, detect tools, map architecture, capture north star + update intent, assess clean-sheet vs current-constraints approach lenses, define goal and criteria. Writes `.build-loop/context-bootstrap.json`, `.build-loop/context/current.md` via `scripts/context_snapshot.py`, `.build-loop/intent.md`, and `.build-loop/goal.md`.

Key steps: detect plugins → set sub-routers → map architecture → run `scripts/context_bootstrap.py` (bootstrap surfaces queue counts+top items+progressive lessons in the packet; check `session_prefs.continue_from_queues` and ask the user ONCE when "ask" and any queue has items; see `agents/build-orchestrator.md` §"Queue surfacing + session preference" and `AGENTS.md` §"Memory bootstrap + queue surfacing" for the full surface+ask protocol) → run `scripts/research_trigger.py` to decide Research plugin depth and blocked final-claim handling → run `scripts/task_surface.py` when surfacing open work → load PRD if present → capture intent → capture approach lenses for non-trivial recommendations → for UI work load `references/ui-io-contract.md` and inventory affected inputs/outputs → define scoring criteria → synthesis-density routing (count `synthesis_dimensions`; escalate to thinking-tier when > 5).

**Load `skills/build-loop/references/phase-1-assess.md`** for the full step-by-step protocol including UI pre-flight, workspace concurrency checks, recovery check, and synthesis-density routing details.

## Phase 2: Plan — Steps & Optimization

Break work into executable steps, compare clean-sheet and current-constraints approaches, build dependency graph, MECE-partition file ownership, run plan acceptance gates.

Key steps: writing-plans skill → parallel-safe identification → intent mapping → `## Approach Lenses` for non-trivial recommendations → `## Research Context` when `state.json.researchGate.research_required` → UI input/output contract section when UI is in scope → MECE partition → optimization checklist → plan-verify (deterministic) → plan-critic (non-deterministic) → scope-auditor (caller audit).

**Load `skills/build-loop/references/phase-2-plan.md`** for the full protocol including spec-writing gate, mockup-first gate, Codex delegation, and plan acceptance steps.

## Phase 3: Execute — Build With Agents

Implement the plan using parallel subagents where possible, following the single-writer git contract.

Key steps: subagent-driven-development → model assignment (Sonnet default) → parallel dispatch → non-blocking context snapshots at dispatch/return boundaries → pass the UI input/output contract to UI implementers → single-writer git contract (implementers never commit) with pre/post commit context snapshots → `scripts/dogfood_reload_checkpoint.py` for self-recursive runtime-changing stages → C5 halt-and-ask backstop for architectural-class novel decisions.

**Load `skills/build-loop/references/phase-3-execute.md`** for the full protocol including Codex adapter, UI subagent prompt template, and coordination checkpoint policy.

## Phase 4: Review — Critic, Validate, Fact-Check, Simplify, Auto-Resolve, Report

Seven sub-steps run in order (A–G): Critic → Validate → Optimize (opt-in) → Fact-Check → Simplify → Auto-Resolve → Report. F (Auto-Resolve) drains non-destructive items via `scripts/autonomy_gate.py` (auto/warn/confirm/block routing). G (Report) is final-pass-only.

Key steps: independent-auditor (build scope) adversarial read → build-loop-owned UI validation when UI changed → code-based graders → live smoke gate → LLM judges → fact-checker + mock-scanner + architecture-rules in parallel → simplify → autonomy gate queue → final scorecard + run entry → **non-gating post-push retrospective dispatch** (`build-loop:retrospective-synthesizer` writes the 9-section `.build-loop/retrospectives/<date>/<run-id>.md` + ≤5-line summary surfaced inline; enforce-candidates land at `.build-loop/proposals/enforce-from-retro/` for human review — never auto-promoted; fire-and-continue, run-close is NOT delayed). On self-recursive runs, G appends `## Self-modifications (readback)` listing every self-modification attempted this run — file, what/why, gate verdict, additional-review finding — so the human sees results at the end without the loop stopping. Full spec in `agents/build-orchestrator.md` §G.

**Load `skills/build-loop/references/phase-4-review.md`** for sub-step details, gate matrices, routing rules, and the full Sub-step F Auto-Resolve protocol (all 4 verdict arms including `warn` exit-0 behavior).

**Independent commit auditor — boundary gate + dispatched judge.** Single consolidated auditor (2026-05-23 — replaces retired `commit-auditor` and earlier retired `sonnet-critic`). Two surfaces share the same context-gathering procedure and verdict taxonomy: (1) a PreToolUse Bash hook fires `scripts/audit_before_commit.py` on every `git commit` regardless of who initiates it (manual, Codex, build-loop, IDE) — deterministic packet-builder, hard-blocks (exit 2) on staged secrets and merge-conflict markers, bypass via `BUILDLOOP_AUDIT_BYPASS=1`; (2) the `independent-auditor` agent dispatches at Phase 3 chunk-close (chunk advisory) and Phase 4 Review-A (build scope) for LLM-grade judgment. Four verdicts: `yay (approve)` / `nay (reject)` / `suggest_correction` / `look_again`. **Auditor dispatch ladder (GAP-1):** a *nested* orchestrator (dispatched as a subagent, or per-commit mode) has no Agent tool and cannot dispatch the auditor — it walks `dispatched-agent → peer-host (rally / codex exec) → not-run:parent-must-dispatch`, records the chosen rung in `auditor_status`, and **never** lets inline self-reasoning masquerade as the independent auditor. A `not-run:parent-must-dispatch` run is not review-complete: under the parent-dispatch contract the dispatching parent owes the audit before Report. Full reference: `skills/build-loop/references/independent-auditor.md` + `references/phase-4-review.md` §"Sub-step A" + `agents/independent-auditor.md`.

## Phase 5: Iterate — Fix Review Failures + UX Queue (up to 5x)

Fix failures surfaced by Review plus drain the UX queue from Sub-step D Gates 7-8, systematically. Loops back to Review after each pass. Hard stop at 5 iterations.

Key steps: prioritized work list (Validate failures → blocker UX → major UX → optimization → UI coverage gaps) → fan-out up to `effective_max` implementers (see `scripts/parallelism.py effective_max_implementers(workdir)` — default 8; `min(config.parallelism.maxImplementers, cpu_count−2, hard ceiling 12)`) → stuck-cascade (evidence-gap → memory re-check → parallel assess at 2 fails → causal-tree at 3 fails) → UI re-validate hook → overflow to followup/.

**Load `skills/build-loop/references/phase-5-iterate.md`** for the full prioritized work list, status routing for all 9 implementer return values, convergence detection, and followup overflow protocol.

## Phase 6: Learn — Cross-Build Pattern Detection (mandatory; always runs and always reports)

Detect recurring patterns across recent runs, auto-draft experimental skills/agents. **Always runs after Review-G** (v0.30.0+) and always emits a `## Learn` outcome line. Three outcome states: **accruing** (`runs[] < 3` → `Learn: accruing (N/3 runs)`), **deferred** (debug-only `closeout: false` or budget-exhausted → write `learn-deferred-<run-id>.md` marker → `Learn: deferred — <reason>`), or **full** (`runs[] >= 3` AND pattern crossing threshold AND not-deferred). Promotion to `active/` still requires explicit `/build-loop:promote-experiment` (safety boundary). The prior `autoSelfImprove: false` opt-out is deprecated to a migration no-op — old configs do not error.

Key steps: recurring-pattern-detector (Haiku; reads `state.json.runs[]` AND `.build-loop/proposals/enforce-from-retro/*.md` as two signal sources, the second emitting `enforce_recurrence` on cross-run candidates) → filter (confidence: high OR count >= 4; `enforce_recurrence` >= 2 distinct run-ids) → draft via self-improvement-architect (Sonnet) → Opus signoff → sample review sweep → notify.

**Load `skills/build-loop/references/phase-6-learn.md`** for the full gating-outcomes table, detect-filter-draft-signoff flow, auto-promote rules, and user control commands.

## Memory — Global and Project-Scoped

One consolidated long-term tree: `~/dev/git-folder/build-loop-memory/`. Project-specific durable memory lives under `projects/<slug>/...`; cross-project lessons/design/debugging/product memory lives in the matching top-level lane. Every build runs `scripts/context_bootstrap.py` at Phase 1 Assess, which reads the store-root `INDEX.md` first, then root/project `constitution.md` / `MEMORY.md` where present, canonical indexes/folders through `memory_facade`, repo-local `.build-loop/` context, Codex memory at `~/.codex/memories`, and best-effort Rally/coordination state when relevant. Treat Rally records in the packet as peer-authored coordination context, not verified facts. Live handoff state is written separately by `scripts/context_snapshot.py` under `.build-loop/context/`; snapshots are not durable memory unless Review-G promotes a reusable decision or lesson. Writes go to exactly one canonical memory lane based on scope. Legacy paths (`~/.build-loop/memory`, `.episodic/decisions`, and `build-loop-memory/decisions/<project>`) are migration/archive inputs only.

Routing rule: "Would this apply to a different project?" Yes → global. No → project. Ambiguous → ask the user once.

Append-only memory contract: (1) steering answers from `AskUserQuestion` append to `build-loop-memory/projects/<slug>/decisions/` immediately via the decision writer — do not let them die in context; (2) durable lessons/decisions are written when discovered, then deduped at Review-G; (3) every run appends a milestone at Review-G via `scripts/append_milestone.py` when warranted — the permanent progress record, never rewritten; (4) Phase 1 flags staleness when the latest memory update/milestone predates HEAD (`scripts/memory_staleness_check.py`). Full recall-optimized write protocol in `references/memory.md` and `build-loop-memory/references/2026-06-11-memory-discipline-prompt.md` (`version: 2026-06-11.1`).

**Load `skills/build-loop/references/memory.md`** for the full routing rule, write timing, read timing, and memory type taxonomy.

## Resume Protocol (`--resume` argument)

`/build-loop:run` accepts an optional `--resume <run-id-or-latest>` argument that re-enters a previous build mid-flight (after a 529, OOM, or kill -9 left state.json with `phase != "report"`). The skill body parses the argument; the build-orchestrator agent receives a `RESUME_MODE:` prompt prefix that branches into §0 Resume mode. **Frontmatter is not the parsing layer** — the skill body is.

**Parsing rule**: scan the argument string for the literal token `--resume`. The next whitespace-delimited token is the run-id (or `latest`). Anything else is part of the goal text.

**On `--resume <run-id>` or `--resume latest`** — BEFORE Phase 1 Assess, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/resume_resolver.py --workdir "$PWD" --resume-arg "<run-id-or-latest>" --staleness-minutes 5`. Returns `decision: "resume" | "abort" | "fresh"`. On `resume`:

1. Read `.build-loop/intent.md` and `.build-loop/plan.md` (already on disk — DO NOT re-derive).
2. Dispatch build-orchestrator with prefix: `RESUME_MODE: run_id=<id>; remaining_chunks=<json>; iterate_attempt=<n>; concurrent_modifications=<json>`
3. Agent §0 handles the rest — skips Phase 1+2, jumps to Phase 3 on `remaining_chunks` only.

**On NO `--resume` (normal dispatch)** — BEFORE Phase 1 step 1, run the same resolver with `--resume-arg ""`. If it returns `decision: "prompt_user"`, surface to the user verbatim:
> "Incomplete build detected (run_id=X, last heartbeat N min ago, M of K chunks complete). Resume with `/build-loop:run --resume X` or start fresh? Starting fresh will not delete the incomplete state — it persists until manually cleared."

This is the crash-resume staleness signal — heartbeat staleness on `state.json.execution`, no hook dependency, fires every fresh dispatch. (A crash-recovery concern, distinct from concurrent-presence collision, which is owned solely by Rally Point presence — see `KNOWN-ISSUES.md` §M4.)

**Concurrent-modification handling**: when `concurrent_modifications` is non-empty in the resolver output, the agent's §0 branch surfaces each flagged chunk as `status: concurrent_modification_detected` and asks the user whether to redo the chunk (default) or keep the hand-edits.

## Efficiency

- No extraneous code. Every line serves the goal
- Terminal output: current phase, key decisions (one line each), status changes, failures. No restated instructions, no verbose reasoning, no "I will now proceed to..."
- Subagent context: minimum needed per job. Shared reads done once, passed as condensed summaries
- Tools: load on demand as each phase needs them. Do not pre-load tools or skills before they're relevant

## Tool Selection

Use the best available tool for each need. If a preferred tool is unavailable, improvise — never block on a missing dependency. The skill is self-sufficient; external tools make it faster but their absence does not stop the loop.

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
3. At Review-F, score its usefulness: did it reduce friction? Would you use it next build?
4. Decide: **keep**, **promote** (project → global), or **drop**.
   - Keep (project) — leave in `.build-loop/skills/`.
   - Promote — move to `~/.claude/skills/`, confirm with user.
   - Drop — delete and note in `.build-loop/feedback.md` why it didn't earn its keep.
5. Record the decision through `scripts/memory_writer.py` into `build-loop-memory/lessons/` or `build-loop-memory/projects/<slug>/lessons/` as a `pattern` entry.

**Self-review/self-heal loop extension:** the self-review/self-heal loop (proactive arm of C-HEAL) MAY author new skills AND new scripts when doing so prevents a class of issue or streamlines repeated work. New skills start project-local and follow this same keep/promote/drop lifecycle. Promotion to the build-loop plugin repo or `~/.claude/skills/` still requires user confirmation (global scope is consequential). New scripts MUST have a colocated `test_<name>.py` — no untested script lands. When the authoring happens inside a self-recursive build (editing build-loop itself), every new or modified file passes through the SELF-MODIFICATION SAFETY GATE in `references/self-review.md` §"Self-modification of the restricted repo" before commit.

**Never proliferate skills**. A skill that isn't used twice across builds should be dropped. Prefer extending an existing skill over creating a new one.

## Feedback — After Every Build

Append one line to `.build-loop/feedback.md` only if something surprising happened: a plan deviation, a tool that produced wrong results, a skill gap, an eval blind spot. Format: `YYYY-MM-DD | what happened | what to do differently`. No entry needed if the build went as expected.

On future `/build` runs, check this file and adjust proactively.

## Process Flow

```
ASSESS → PLAN → EXECUTE → REVIEW ──────────────────────────────────────────→ LEARN (opt)
                            ↑   │
                            │   ├─ A. CRITIC ──strong-checkpoint──→ (re-execute, no iter burn)
                            │   ├─ B. VALIDATE ──┐
                            │   ├─ C. OPTIMIZE ──┤ (opt-in, mechanical metric only)
                            │   ├─ D. FACT-CHECK ┤
                            │   ├─ E. SIMPLIFY   │
                            │   ├─ F. AUTO-RESOLVE (drain non-destructive open items via autonomy_gate)
                            │   └─ G. REPORT ────┘ (final pass only → scorecard + runs[] entry)
                            │                           │
                            └──── ITERATE (up to 5x) ←──┘ on B/D blocking failures
```

The diagram is the structure; per-sub-step routing detail lives once in `references/phase-4-review.md` (A strong-checkpoint → EXECUTE with no iteration burn; C `optimize-runner` + `overfitting-reviewer`; F `autonomy_gate.py` → `## Done`/`## Held`/`## Blocked`; G final-pass scorecard). The orchestrator agent owns the `runs[]` write — see `agents/build-orchestrator.md` §G.

## References

Contextual material loaded on demand (not at skill invocation):

- `references/phase-1-assess.md` — Full Phase 1 Assess protocol
- `references/phase-2-plan.md` — Full Phase 2 Plan protocol
- `references/phase-3-execute.md` — Full Phase 3 Execute protocol
- `references/verify-dispatch.md` — Post-dispatch 5-step git/test ground-truth checklist; walk after any dispatched agent claims commits landed / tests passed (a solicited peer agreeing is not independent verification)
- `references/dogfood-reload-checkpoint.md` — Self-recursive stop/reload/resume checkpoint and ACK/fallback protocol
- `references/phase-4-review.md` — Full Phase 4 Review protocol (sub-steps A–G, including Sub-step F Auto-Resolve with all 4 verdict arms)
- `references/phase-5-iterate.md` — Full Phase 5 Iterate protocol
- `references/phase-6-learn.md` — Full Phase 6 Learn protocol
- `references/memory.md` — Memory system: global vs project stores, routing rule, read/write policy
- `references/leadership.md` — Initiative + decision-escalation doctrine (decide-at-70%, self-research → memory → peers → persona panel → human-only-for-irreversible, parallel-work-before-idling, token-posture gauge). Synthesized from intent-based leadership / mission command / two-door decisions.
- `references/research-trigger-policy.md` — Research plugin trigger/depth gate, t-shirt depth lower bounds, and final-claim citation/unavailable rule
- `references/task-capture-policy.md` — Read-only active task surface over existing plan/state/queue/backlog surfaces; no new task ledger by default
- `references/backlog-system.md` — Host-agnostic, multi-repo backlog system: MD+YAML items (canonical truth) + regenerable INDEX, pure-stdlib `scripts/backlog.py` (new/sync/list), one-way mirror to personal memory. Read via `BACKLOG.md`→`INDEX.md`→grep; write via the CLI
- `references/agent-role-taxonomy.md` — Lead/peer/coder-assessor/reviewer/skill responsibility map; use before adding or renaming agents.
- `references/capability-routing.md` — Full capability routing table, trigger conditions, sub-routers
- `references/recent-design-structures.md` — Recent UI structure library loaded by `design-contract-specialist` in Phase 2. Structures are options, not mandates.
- `../ui-design/references/universal-design-principles.md` — Cross-medium communication and experience doctrine for app UI, writing, images, decks, docs, reports, spreadsheets, PDFs, and other information artifacts.
- `../ui-design/references/ui-guidance-sources.md` — Source map for local UI guidance across build-loop, UI Guidance, IBR, Mockup Gallery, document/deck plugins, research, vault, project-local hidden folders, and build-loop-memory.
- `references/refactor-history/` — Internal assessment of the 2026-04 refactor. `ASSESSMENT.md` explains rationale, `trace-comparison.md` shows before/after flow, `STANDALONE_TEST_RUN.md` validates the model, `scenarios/01..06` contain 6 test scenarios.
- `eval-guide.md` — How to interpret build-loop scorecards.
- `fallbacks.md` — Degraded-but-useful behavior when bridge plugins or rendered UI tooling are absent. IBR remains explicit-only through `build-loop:ibr-bridge`.
- `phases/fact-check.md` — Detailed fact-check sub-step specification.

Companion skills (each has its own SKILL.md; load via `Skill("build-loop:<name>")`):

- `build-loop:research` · `build-loop:optimize` · `build-loop:self-improve` — callable modes
- **Intent restatement protocol** — intrinsic to Phase 1 via `references/intent-capability-pack.md` § Intent restatement protocol. Always-on, LLM-judged, no script gate. One-line concrete restatement always; 1-3 approach options + tradeoffs + tagged assumptions when the orchestrator LLM judges the goal genuinely ambiguous. Auto-execute fast path preserved (concrete goal → one line, proceed). Reference templates for common ambiguity shapes in `references/intent-exploration-prompts.md` (load on demand only).
- `build-loop:model-tiering` — dynamically assign subagent tiers by complexity (guide, not rule): prefer Sonnet; Haiku only for trivial mechanical/recognition work; Opus subagents to accelerate complex subtasks (cross-file, novel, ambiguous, hard refactor); verify every subagent's output before accepting it (cheaper tier → stronger check) (`references/model-tier-mapping.md`)
- `build-loop:architecture-{scan,impact,trace,rules,dead,review}` — native architecture skills sourced from NavGator (provenance + drift-detection via `build-loop:sync-skills`)
- `build-loop:debugging-memory` · `build-loop:debug-loop` · `build-loop:logging-tracer` — bundled debugger primitives (orchestrator owns when-to-fire, these own the procedural detail)
- `build-loop:plugin-builder` · `build-loop:mcp-builder` — plugin authoring (use together for plugins that expose MCP tools)
- `build-loop:repo-closeout` — repository structure, source-of-truth, artifact retention, sibling consolidation, and safe local-main closeout
- `build-loop:authentication` — multi-provider auth reference library (Better Auth, Supabase, Google OAuth, Resend; routed by provider × topic)
- `build-loop:building-with-deepagents` — OSS deepagents framework (activates on `from deepagents import`)
- `build-loop:ui-design` — build-loop-owned UI design direction skill loaded before non-trivial UI implementation; `design-contract-specialist` writes the resulting `.build-loop/app-contract/ui.md`.
- `build-loop:telemetry` — OpenTelemetry-first observability guidance (LLM/agent → Phoenix/Langfuse + OpenInference/OpenLLMetry; web/server → OTel SDK + Sentry; mobile/iOS → Embrace/OTel-swift over Firebase). Loaded in Phase 1 when a build touches a server/LLM/mobile app with no telemetry, or Phase 2 when adding a service/LLM path; encodes the user's decided OTel + GenAI-semconv stack.

<!-- build-loop@tyroneross — canonical source: github.com/tyroneross/build-loop -->
