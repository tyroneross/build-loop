---
name: build-loop
description: "Orchestrated build loop for multi-step code work. TRIGGER on verb language ('build', 'implement', 'create', 'add', 'ship', 'wire up', 'integrate', 'refactor', 'migrate', 'rewrite', 'replace') OR symptom language ('fix', 'broken', 'doesn't work', 'isn't loading', 'not displaying', 'missing', 'should show', 'needs to', 'make it', 'show this differently') OR any task touching 2+ files, adding/removing an endpoint, crossing an architectural boundary, or attached screenshots of a bug. SKIP one-line edits, pure Q&A, conversational clarifications, status checks, and trivial typos/renames."
user-invocable: true
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- build-loop@tyroneross:canary:build-loop -->
<!-- canary-end -->

# Build Loop — Orchestrated Development

A 5-phase development loop (+1 optional): assess state and criteria, plan, execute, review (critic/validate/fact-check/report as sub-steps), iterate on review failures. Optional Phase 6 Learn detects cross-build recurring patterns and drafts experimental skills.

## Routing

Build-loop supports three modes, routed by the orchestrator:

- **Build** (default): Full 5-phase loop plus optional Learn for implementation tasks
- **Optimize**: Autoresearch-pattern optimization for measurable metrics (`/build-loop:optimize`)
- **Research**: Pre-decision analysis that produces a research packet (`/build-loop:research`)

The orchestrator classifies intent automatically. Users can override with the standalone commands.

## Autonomous Mode (Queue-Drain Loop)

Autonomous mode generalizes Phase 5 Iterate into a self-replenishing worker that drains its own `ux-queue/` + `issues/` + `proposals/`, alignment-checks each item against the original intent, executes the aligned subset, and commits in batches until the queue is empty or the wall-clock budget elapses. Default since this mode shipped (`--autonomous=false` opts back to classic one-pass).

### Flag surface

| Invocation | Effect |
|---|---|
| `/build-loop:run "goal text"` | default mode, 2h budget, autonomous=true |
| `/build-loop:run --long "goal text"` | long mode, 8h budget |
| `/build-loop:run --budget 4h "goal text"` | custom budget (overrides `--long`) |
| `/build-loop:run --budget 30m "goal text"` | accepts `30s`, `30m`, `4h`, or bare integer seconds |
| `/build-loop:run --autonomous=false "goal text"` | classic single-pass; queue items become `followup/` |
| `/build-loop:run "overnight refactor of auth ..."` | keyword `overnight` → long mode, 8h |

**Flag precedence (strict, top wins):**

1. `--budget <duration>` — explicit duration always wins; mode tagged `custom`.
2. `--long` — sets mode `long`, budget 8h.
3. Keyword detection in goal text — only when `--long` not explicitly set.
4. Default — mode `default`, budget 2h, autonomous=true.

`--autonomous=false` is orthogonal: it can combine with any budget flag but disables the queue-drain loop entirely. With autonomous off, `--budget` still tracks wall-clock but the orchestrator runs classic Phase 1–6 once and reports.

### Keyword fallback

Case-insensitive whole-word match against the goal text (or `intent.update_intent`). Detection runs ONLY when `--long` is not explicit on the command line. The flag always wins over keyword inference.

| Keyword | Example phrasings |
|---|---|
| `long` | "long refactor of …" |
| `long-running` | "long-running migration" |
| `overnight` | "overnight build" |
| `large-scale` | "large-scale rewrite" |
| `multi-day` | "multi-day backfill" |

Keyword list is configurable via `.build-loop/config.json.autonomy.keywordsLong[]`. The default list above is hard-coded in the orchestrator.

### Budget tracking

The orchestrator writes `state.execution.budget` at autonomous-mode start:

```json
{
  "mode": "default | long | custom",
  "started_at": "<iso8601 UTC>",
  "deadline_at": "<iso8601 UTC>",
  "last_checkin_at": "<iso8601 UTC> | null",
  "commits_since_push": 0,
  "checkin_interval_pct": 50
}
```

`scripts/budget_check.py` reads this block at every iterate-loop entry, every commit, and every phase boundary, returning a routing envelope (`continue | checkin | finalize_and_stop`). The script is informational — exit 0 always; sub-5ms compute.

**Resume contract**: when a budget block exists and the run resumes via `--resume <run_id>`, the orchestrator MUST reuse the original `deadline_at`. A 2h budget that crashed at 1h59m does NOT get a fresh 2h. `scripts/resume_resolver.py._resolve_budget_on_resume()` is the single source of truth for this rule and surfaces the preserved budget under `budget_resume.preserve_deadline: true`.

### Iteration caps

| Mode | Per-build cap | Per-item cap |
|---|---|---|
| Classic (autonomous=false) | 5 | n/a |
| Autonomous default | 25 | 3 same-verdict |
| Autonomous long | 25 | 3 same-verdict |

`maxIterateAttemptsAutonomous` is configurable in `.build-loop/config.json.autonomy.maxIterateAttemptsAutonomous`.

### Per-Phase A constraint

Phase A (current ship) wires queue drain + alignment-check + time budget. **Pushes stay manual** — `scripts/autonomous_push.py` and the K-commit batch-push policy ship in Phase B. The `should_push_now` field returned by `budget_check.py` is informational in Phase A; the orchestrator surfaces it in check-ins but does not push autonomously yet.

## Per-Commit Mode (Self-Recursive Builds)

Per-commit mode splits a multi-commit build into one independent orchestrator dispatch per commit, so each commit reviews and lands cleanly before the next one starts. It activates automatically when the working directory IS the runtime — that is, when the user is editing the build-loop plugin itself (or any plugin whose runtime symlink points back to the working tree). It can also be explicitly opted into or out of via skill arguments.

### Detection

Phase 1 Assess writes `selfRecursive.enabled: true|false` to `.build-loop/state.json` (commit 1 wired this via `scripts/detect_self_recursive.py`). The skill body MUST read this field BEFORE deciding which dispatch shape to use. If the field is absent, treat it as `false`.

### Mode Resolution

| Skill arg | `selfRecursive` | Resulting mode |
|---|---|---|
| `--per-commit` (explicit) | either | per-commit |
| `--no-per-commit` (explicit) | either | single-orchestrator |
| (none) | true | per-commit (default for self-recursive) |
| (none) | false | single-orchestrator (today's behavior) |

Passing both `--per-commit` and `--no-per-commit` is a user error — fail loud with a one-line message naming the conflict and stop before any dispatch.

### Dispatch Contract (Per-Commit Mode)

1. **Plan first, dispatch many.** The skill body invokes a single planning orchestrator (Phase 1 Assess + Phase 2 Plan only). Its return must include a per-commit work list at `.build-loop/per-commit-plan.json` with this exact JSON shape:

   ```json
   {
     "run_id": "run_<UTC>_<hash>",
     "commits": [
       {
         "id": "c1",
         "subject": "feat(scripts): add foo helper",
         "scope": "...",
         "files_planned": ["scripts/foo.py", "tests/test_foo.py"],
         "spec": "verbatim packet for the implementer orchestrator",
         "depends_on": []
       }
     ],
     "branch": "feat/...",
     "from_branch": "main"
   }
   ```

2. **Per-commit orchestrator dispatch.** For each commit in the plan (respecting `depends_on`), the skill body dispatches a fresh `Agent(subagent_type="build-loop:build-orchestrator", ...)` carrying ONLY that commit's packet plus a `PER_COMMIT_DISPATCH: { commit_id, run_id, prior_commit_hashes }` prompt prefix. Each dispatched orchestrator runs Phase 3 Execute + Phase 4 Review for ITS commit only, then commits and returns. The dispatched orchestrator's behavior on the prefix is documented in `agents/build-orchestrator.md` §0a.

3. **Aggregate.** The skill body collects each orchestrator's return envelope and writes a final report combining all commits' results. On partial failure (commit N fails), do NOT dispatch downstream commits; retain `.build-loop/per-commit-plan.json` so a subsequent `/build-loop:run --resume` invocation can pick up where it stopped.

### State.json schema

The per-commit dispatcher tracks its own progress under a `perCommit` block alongside the existing `execution` block:

```json
{
  "perCommit": {
    "enabled": true,
    "mode_source": "self_recursive_default|explicit_flag|opt_out",
    "plan_path": ".build-loop/per-commit-plan.json",
    "completed": [{"commit_id": "c1", "hash": "abc123", "completed_at": "..."}],
    "in_flight": "c2",
    "queued": ["c3"]
  }
}
```

M2's `execution.iterate_attempt` continues to track per-commit-orchestrator attempt counters (each dispatched orchestrator manages its own iterate counter) — do not duplicate iteration tracking inside `perCommit`.

## Scope Check

Before starting the loop, assess whether the task warrants it. If the task is a single file edit, a config change, or a fix under ~20 lines — skip the loop and just do it. The loop is for multi-step work where planning and validation add value.

## Keep going until done

Once the user accepts the plan, every phase is authorized scope. The orchestrator does not stop and ask the user between phases. Status updates are fine. Permission requests are not.

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

| Host | Primary surface | Subagent behavior |
|---|---|---|
| Claude Code | `agents/*.md`, slash commands, `Skill(...)`, `Agent` tool | Use the existing Claude orchestrator and agent definitions. Do not rewrite Claude agents for Codex behavior. |
| Codex | `skills/*/SKILL.md`, `AGENTS.md`, templates | Use `references/codex-subagents.md` and `templates/codex-worker-prompt.md` when the user explicitly authorizes subagents or parallel delegation. |
| Other coding tools | `AGENTS.md` | Follow the same phases and ownership packets with the host's available delegation primitives. |

**Codex permission gate**: generic Build Loop wording such as "parallel-safe groups" is not by itself authorization to spawn Codex subagents. In Codex, spawn workers only when the user explicitly asks for delegation/parallel agent work or uses a command flag such as `--parallel`. Without that signal, keep execution local while preserving the MECE plan.

**Native agent-rally capabilities**: build-loop vendors `skills/agent-rally-point/SKILL.md` and `skills/agent-rally-watcher/SKILL.md` as embedded mini-plugin skills. Use those skill entrypoints for Rally Point substrate or watcher work before reaching for the standalone repos. The grouped extraction contract is `scripts/rally_point/plugin_boundary.json`; validate it with `python3 scripts/agent_rally.py boundary --repo "$PWD" --check --json`.

**Coding-host coordination polling gate**: when a build-loop task involves more than one coding host, an active rally-point peer, an active coord file, any `inbox/<tool>.jsonl` message, or any `inbox/all.jsonl` broadcast, the current host must keep a cheap watcher live while work is in flight. Use a stable tool id (`claude_code`, `codex`, `cursor`, etc.). Run a one-shot status check first:

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

Key steps: detect plugins → set sub-routers → map architecture → run `scripts/context_bootstrap.py` → load PRD if present → capture intent → capture approach lenses for non-trivial recommendations → for UI work load `references/ui-io-contract.md` and inventory affected inputs/outputs → define scoring criteria → synthesis-density routing (count `synthesis_dimensions`; escalate to thinking-tier when > 5).

**Load `skills/build-loop/references/phase-1-assess.md`** for the full step-by-step protocol including UI pre-flight, workspace concurrency checks, recovery check, and synthesis-density routing details.

## Phase 2: Plan — Steps & Optimization

Break work into executable steps, compare clean-sheet and current-constraints approaches, build dependency graph, MECE-partition file ownership, run plan acceptance gates.

Key steps: writing-plans skill → parallel-safe identification → intent mapping → `## Approach Lenses` for non-trivial recommendations → UI input/output contract section when UI is in scope → MECE partition → optimization checklist → plan-verify (deterministic) → plan-critic (non-deterministic) → scope-auditor (caller audit).

**Load `skills/build-loop/references/phase-2-plan.md`** for the full protocol including spec-writing gate, mockup-first gate, Codex delegation, and plan acceptance steps.

## Phase 3: Execute — Build With Agents

Implement the plan using parallel subagents where possible, following the single-writer git contract.

Key steps: subagent-driven-development → model assignment (Sonnet default) → parallel dispatch → non-blocking context snapshots at dispatch/return boundaries → pass the UI input/output contract to UI implementers → single-writer git contract (implementers never commit) with pre/post commit context snapshots → C5 halt-and-ask backstop for architectural-class novel decisions.

**Load `skills/build-loop/references/phase-3-execute.md`** for the full protocol including Codex adapter, UI subagent prompt template, and coordination checkpoint policy.

## Phase 4: Review — Critic, Validate, Fact-Check, Simplify, Auto-Resolve, Report

Seven sub-steps run in order: A Critic → B Validate → C Optimize (opt-in) → D Fact-Check → E Simplify → F Auto-Resolve → G Report. F drains non-destructive items via `scripts/autonomy_gate.py` (auto/warn/confirm/block routing). G is final-pass-only.

Key steps: independent-auditor (build scope) adversarial read → build-loop-owned UI validation when UI changed → code-based graders → live smoke gate → LLM judges → fact-checker + mock-scanner + architecture-rules in parallel → simplify → autonomy gate queue → final scorecard + run entry.

**Load `skills/build-loop/references/phase-4-review.md`** for sub-step details, gate matrices, routing rules, and the full Sub-step F Auto-Resolve protocol (all 4 verdict arms including `warn` exit-0 behavior).

**Independent commit auditor — boundary gate + dispatched judge.** Single consolidated auditor (2026-05-23 — replaces retired `commit-auditor` and earlier retired `sonnet-critic`). Two surfaces share the same context-gathering procedure and verdict taxonomy: (1) a PreToolUse Bash hook fires `scripts/audit_before_commit.py` on every `git commit` regardless of who initiates it (manual, Codex, build-loop, IDE) — deterministic packet-builder, hard-blocks (exit 2) on staged secrets and merge-conflict markers, bypass via `BUILDLOOP_AUDIT_BYPASS=1`; (2) the `independent-auditor` agent dispatches at Phase 3 chunk-close (chunk advisory) and Phase 4 Review-A (build scope) for LLM-grade judgment. Four verdicts: `yay (approve)` / `nay (reject)` / `suggest_correction` / `look_again`. Full reference: `skills/build-loop/references/independent-auditor.md` + `agents/independent-auditor.md`.

## Phase 5: Iterate — Fix Review Failures + UX Queue (up to 5x)

Fix failures surfaced by Review plus drain the UX queue from Sub-step D Gates 7-8, systematically. Loops back to Review after each pass. Hard stop at 5 iterations.

Key steps: prioritized work list (Validate failures → blocker UX → major UX → optimization → UI coverage gaps) → fan-out up to 4 implementers → stuck-cascade (evidence-gap → memory re-check → parallel assess at 2 fails → causal-tree at 3 fails) → UI re-validate hook → overflow to followup/.

**Load `skills/build-loop/references/phase-5-iterate.md`** for the full prioritized work list, status routing for all 9 implementer return values, convergence detection, and followup overflow protocol.

## Phase 6: Learn — Cross-Build Pattern Detection (optional)

Detect recurring patterns across recent runs, auto-draft experimental skills/agents. Runs after Review-G unless disabled. Requires `runs[] >= 3`.

Key steps: recurring-pattern-detector (Haiku) → filter (confidence: high OR count >= 4) → draft via self-improvement-architect (Sonnet) → Opus signoff → sample review sweep → notify.

**Load `skills/build-loop/references/phase-6-learn.md`** for the full detect-filter-draft-signoff flow, auto-promote rules, and user control commands.

## Memory — Global and Project-Scoped

One consolidated long-term tree: `~/dev/git-folder/build-loop-memory/`. Project-specific durable memory lives under `projects/<slug>/...`; cross-project lessons/design/debugging/product memory lives in the matching top-level lane. Every build runs `scripts/context_bootstrap.py` at Phase 1 Assess, which reads canonical root/project `MEMORY.md` and `constitution.md` files, canonical indexes/folders through `memory_facade.py`, repo-local `.build-loop/` context, Codex memory at `~/.codex/memories`, and best-effort Rally/coordination state when relevant. Live handoff state is written separately by `scripts/context_snapshot.py` under `.build-loop/context/`; snapshots are not durable memory unless Review-G promotes a reusable decision or lesson. Writes go to exactly one canonical memory lane based on scope. Legacy paths (`~/.build-loop/memory`, `.episodic/decisions`, and `build-loop-memory/decisions/<project>`) are migration/archive inputs only.

Routing rule: "Would this apply to a different project?" Yes → global. No → project. Ambiguous → ask the user once.

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
- `references/phase-4-review.md` — Full Phase 4 Review protocol (sub-steps A–G, including Sub-step F Auto-Resolve with all 4 verdict arms)
- `references/phase-5-iterate.md` — Full Phase 5 Iterate protocol
- `references/phase-6-learn.md` — Full Phase 6 Learn protocol
- `references/memory.md` — Memory system: global vs project stores, routing rule, read/write policy
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
- `build-loop:model-tiering` — reference for model selection
- `build-loop:architecture-{scan,impact,trace,rules,dead,review}` — native architecture skills sourced from NavGator (provenance + drift-detection via `build-loop:sync-skills`)
- `build-loop:debugging-memory` · `build-loop:debug-loop` · `build-loop:logging-tracer` — bundled debugger primitives (orchestrator owns when-to-fire, these own the procedural detail)
- `build-loop:plugin-builder` · `build-loop:mcp-builder` — plugin authoring (use together for plugins that expose MCP tools)
- `build-loop:authentication` — multi-provider auth reference library (Better Auth, Supabase, Google OAuth, Resend; routed by provider × topic)
- `build-loop:building-with-deepagents` — OSS deepagents framework (activates on `from deepagents import`)
- `build-loop:ui-design` — build-loop-owned UI design direction skill loaded before non-trivial UI implementation; `design-contract-specialist` writes the resulting `.build-loop/app-contract/ui.md`.

<!-- build-loop@tyroneross — canonical source: github.com/tyroneross/build-loop -->
