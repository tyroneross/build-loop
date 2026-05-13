---
name: build-loop
description: "Orchestrated build loop for multi-step code work. TRIGGER on verb language ('build', 'implement', 'create', 'add', 'ship', 'wire up', 'integrate', 'refactor', 'migrate', 'rewrite', 'replace') OR symptom language ('fix', 'broken', 'doesn't work', 'isn't loading', 'not displaying', 'missing', 'should show', 'needs to', 'make it', 'show this differently') OR any task touching 2+ files, adding/removing an endpoint, crossing an architectural boundary, or attached screenshots of a bug. SKIP one-line edits, pure Q&A, conversational clarifications, status checks, and trivial typos/renames."
---

# Build Loop — Orchestrated Development

A 5-phase development loop (+1 optional): assess state and criteria, plan, execute, review (critic/validate/fact-check/report as sub-steps), iterate on review failures. Optional Phase 6 Learn detects cross-build recurring patterns and drafts experimental skills.

## Routing

Build-loop supports three modes, routed by the orchestrator:

- **Build** (default): Full 5-phase loop plus optional Learn for implementation tasks
- **Optimize**: Autoresearch-pattern optimization for measurable metrics (`/build-loop:optimize`)
- **Research**: Pre-decision analysis that produces a research packet (`/build-loop:research`)

The orchestrator classifies intent automatically. Users can override with the standalone commands.

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

Otherwise: pick the natural next step, note any reasonable assumption in the run record, and keep moving. One end-of-run report at the end. Drain non-destructive open items via Sub-step F Auto-Resolve before the end-of-run report — see Phase 4 below. See `agents/build-orchestrator.md` §Keep going until done for the orchestrator-side phrasing.

## Host Adapters

Build-loop keeps the core method host-neutral, then adapts the execution mechanics to the current coding host.

| Host | Primary surface | Subagent behavior |
|---|---|---|
| Claude Code | `agents/*.md`, slash commands, `Skill(...)`, `Agent` tool | Use the existing Claude orchestrator and agent definitions. Do not rewrite Claude agents for Codex behavior. |
| Codex | `skills/*/SKILL.md`, `AGENTS.md`, templates | Use `references/codex-subagents.md` and `templates/codex-worker-prompt.md` when the user explicitly authorizes subagents or parallel delegation. |
| Other coding tools | `AGENTS.md` | Follow the same phases and ownership packets with the host's available delegation primitives. |

**Codex permission gate**: generic Build Loop wording such as "parallel-safe groups" is not by itself authorization to spawn Codex subagents. In Codex, spawn workers only when the user explicitly asks for delegation/parallel agent work or uses a command flag such as `--parallel`. Without that signal, keep execution local while preserving the MECE plan.

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

Understand current state, load memory, detect tools, map architecture, capture north star + update intent, define goal and criteria. Writes `.build-loop/intent.md` + `.build-loop/goal.md`.

Key steps: detect plugins → set sub-routers → map architecture → load PRD if present → capture intent → for UI work load `references/ui-io-contract.md` and inventory affected inputs/outputs → define scoring criteria → synthesis-density routing (count `synthesis_dimensions`; escalate to thinking-tier when > 5).

**Load `skills/build-loop/references/phase-1-assess.md`** for the full step-by-step protocol including UI pre-flight, workspace concurrency checks, recovery check, and synthesis-density routing details.

## Phase 2: Plan — Steps & Optimization

Break work into executable steps, build dependency graph, MECE-partition file ownership, run plan acceptance gates.

Key steps: writing-plans skill → parallel-safe identification → intent mapping → UI input/output contract section when UI is in scope → MECE partition → optimization checklist → plan-verify (deterministic) → plan-critic (non-deterministic) → scope-auditor (caller audit).

**Load `skills/build-loop/references/phase-2-plan.md`** for the full protocol including spec-writing gate, mockup-first gate, Codex delegation, and plan acceptance steps.

## Phase 3: Execute — Build With Agents

Implement the plan using parallel subagents where possible, following the single-writer git contract.

Key steps: subagent-driven-development → model assignment (Sonnet default) → parallel dispatch → pass the UI input/output contract to UI implementers → single-writer git contract (implementers never commit) → C5 halt-and-ask backstop for architectural-class novel decisions.

**Load `skills/build-loop/references/phase-3-execute.md`** for the full protocol including Codex adapter, UI subagent prompt template, and coordination checkpoint policy.

## Phase 4: Review — Critic, Validate, Fact-Check, Simplify, Auto-Resolve, Report

Seven sub-steps run in order: A Critic → B Validate → C Optimize (opt-in) → D Fact-Check → E Simplify → F Auto-Resolve → G Report. F drains non-destructive items via `scripts/autonomy_gate.py` (auto/warn/confirm/block routing). G is final-pass-only.

Key steps: commit-auditor (build scope) adversarial read → IBR-first validation → code-based graders → live smoke gate → LLM judges → fact-checker + mock-scanner + architecture-rules in parallel → simplify → autonomy gate queue → final scorecard + run entry.

**Load `skills/build-loop/references/phase-4-review.md`** for sub-step details, gate matrices, routing rules, and the full Sub-step F Auto-Resolve protocol (all 4 verdict arms including `warn` exit-0 behavior).

## Phase 5: Iterate — Fix Review Failures + UX Queue (up to 5x)

Fix failures surfaced by Review plus drain the UX queue from Sub-step D Gates 7-8, systematically. Loops back to Review after each pass. Hard stop at 5 iterations.

Key steps: prioritized work list (Validate failures → blocker UX → major UX → optimization → IBR gaps) → fan-out up to 4 implementers → stuck-cascade (evidence-gap → memory re-check → parallel assess at 2 fails → causal-tree at 3 fails) → IBR re-validate hook → overflow to followup/.

**Load `skills/build-loop/references/phase-5-iterate.md`** for the full prioritized work list, status routing for all 9 implementer return values, convergence detection, and followup overflow protocol.

## Phase 6: Learn — Cross-Build Pattern Detection (optional)

Detect recurring patterns across recent runs, auto-draft experimental skills/agents. Runs after Review-G unless disabled. Requires `runs[] >= 3`.

Key steps: recurring-pattern-detector (Haiku) → filter (confidence: high OR count >= 4) → draft via self-improvement-architect (Sonnet) → Opus signoff → sample review sweep → notify.

**Load `skills/build-loop/references/phase-6-learn.md`** for the full detect-filter-draft-signoff flow, auto-promote rules, and user control commands.

## Memory — Global and Project-Scoped

Two stores: `~/.build-loop/memory/` (global, cross-project) and `<project>/.build-loop/memory/` (project-local). Every build reads both at Phase 1 Assess; writes go to exactly one based on scope.

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

This is the M4 primary signal — heartbeat staleness, no hook dependency, fires every fresh dispatch.

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
5. Record the decision in `.build-loop/memory/` (project) or `~/.build-loop/memory/` (global) as a `pattern` entry.

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

Review sub-step A (CRITIC) is the adversarial read-only pass; strong-checkpoint findings route back to EXECUTE without consuming iteration budget. Sub-step C (OPTIMIZE) is opt-in — runs the autoresearch-pattern optimization loop only when a mechanical metric exists; dispatches `optimize-runner` for autonomous iteration, then `overfitting-reviewer` for adversarial review. Sub-step F (AUTO-RESOLVE) drains the candidate queue of non-destructive open items by invoking `python3 scripts/autonomy_gate.py` for each; items route to `## Done`, `## Held`, or `## Blocked` in the final report based on the gate's verdict. Sub-step G (REPORT) invokes `scripts/write_run_entry.py` to append the run entry to `state.json.runs[]`; Phase 6 Learn scans that log.

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
- `references/refactor-history/` — Internal assessment of the 2026-04 refactor. `ASSESSMENT.md` explains rationale, `trace-comparison.md` shows before/after flow, `STANDALONE_TEST_RUN.md` validates the model, `scenarios/01..06` contain 6 test scenarios.
- `eval-guide.md` — How to interpret build-loop scorecards.
- `fallbacks.md` — Degraded-but-useful behavior when bridge plugins (NavGator, claude-code-debugger, IBR) are absent.
- `phases/fact-check.md` — Detailed fact-check sub-step specification.

Companion skills (each has its own SKILL.md; load via `Skill("build-loop:<name>")`):

- `build-loop:research` · `build-loop:optimize` · `build-loop:self-improve` — callable modes
- `build-loop:model-tiering` — reference for model selection
- `build-loop:architecture-{scan,impact,trace,rules,dead,review}` — native architecture skills sourced from NavGator (provenance + drift-detection via `build-loop:sync-skills`)
- `build-loop:debugging-memory` · `build-loop:debug-loop` · `build-loop:logging-tracer` — bundled debugger primitives (orchestrator owns when-to-fire, these own the procedural detail)
- `build-loop:plugin-builder` · `build-loop:mcp-builder` — plugin authoring (use together for plugins that expose MCP tools)
- `build-loop:authentication` — multi-provider auth reference library (Better Auth, Supabase, Google OAuth, Resend; routed by provider × topic)
- `build-loop:building-with-deepagents` — OSS deepagents framework (activates on `from deepagents import`)
