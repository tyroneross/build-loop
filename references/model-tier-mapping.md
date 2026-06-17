<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Model Tier Mapping — Multi-Provider Substitution Reference

Build-loop is provider-agnostic at the tier level. Agent frontmatter uses Anthropic aliases (`fable`, `opus`, `sonnet`, `haiku`) by default because Claude Code is the primary host, but the **tier abstraction** (Frontier / Thinking / Code / Pattern) is what governs the role assignment. This reference documents how to swap providers cleanly.

## Canonical tier definitions

### Frontier tier
- **Role:** Planning synthesis AND verification verdicts. **Phase 2 Plan synthesis reaches Fable via the stakes-gated Advisor dispatch ladder** (`advisor` agent / peer host / already-Fable session; honestly-labeled inline-Opus fallback otherwise — `skills/build-loop/references/advisor-dispatch-ladder.md`); the Advisor v1 ladder is Phase 2 only, so Phase 1 Assess synthesis runs inline as today until v2. Plan content: frame goal, draft spec/ADRs, F-criteria, MECE partition. Verification-shaped agents whose verdicts gate downstream work: plan-critic, scope-auditor, independent-auditor, fix-critique, fact-checker, security-reviewer, overfitting-reviewer, promotion-reviewer.
- **Why this tier exists (above Thinking):** wrong plans dispatch N implementers into the wrong work, and wrong verdicts ship regressions. The user's standing priority is Accuracy > Speed > Cost; the compounding-risk surfaces pay the Frontier premium.
- **Benchmark contract:** clears the Thinking-tier contract AND benchmarks above the prior-generation Thinking-tier ceiling on at least one of SWE-bench Verified / ARC-AGI / GPQA Diamond.
- **Cost expectation:** highest. Use only on the planning + verification surface; never default for execution or coordination.
- **Anthropic default:** Fable 5 (`claude-fable-5`)
- **Verified equivalents (2026 Q2, advisory):** GPT-5.5 (`gpt-5.5`, OpenAI's frontier Codex model — complex coding, agentic, 1.1M ctx), GPT-5.4 (`gpt-5.4`, lower-cost frontier); future Claude generations above Opus
- **Local equivalents:** none — Frontier-class capability is not yet matched locally

### Thinking tier
- **Role:** Coordination + escalation. Routes work between subagents, ladders severity, runs causal-tree on stuck iterations, writes audit/learnings when no Frontier verdict is being rendered.
- **Benchmark contract:** SWE-bench Verified ≥78% AND competitive on ARC-AGI / GPQA Diamond / MMLU-Pro.
- **Cost expectation:** middle-high tier. Use for orchestration and the escalation target when execution hits ambiguity. Never default to Thinking for bounded execution.
- **Anthropic default:** Opus 4.8 (`claude-opus-4-8`; alias `opus` auto-tracks the latest Opus generation)
- **Verified equivalents (2026 Q2, advisory):** GPT-5.4 (`gpt-5.4`), Gemini 2.5 Pro
- **Local equivalents:** none yet — Thinking-tier work needs frontier-class context length and judgment; local models lag

### Code tier
- **Role:** Application. Apply a known rule, spec, or pattern to bounded input. Scoped implementation per a commit's owned-files. Adversarial critic vs rubric. Mechanical simplify. The "how" decisions when the "what" is already settled.
- **Benchmark contract:** SWE-bench Verified ≥75% AND tool-use accuracy ≥85% AND multi-turn coding rollout ≥80%.
- **Cost expectation:** ~3-5× cheaper than Thinking tier per token. The default for the bulk of build-loop work.
- **Anthropic default:** Sonnet 4.6 (`claude-sonnet-4-6`)
- **Verified equivalents:** Sonnet 4.7+ (when available), GPT-5.4 Mini (`gpt-5.4-mini` — fast coding + subagents)
- **Local equivalents:** qwen2.5-coder-32B-instruct (mid-quality), Codestral 22B (reasonable substitute for bounded refactor work)

### Pattern tier (a.k.a. Recognition)
- **Role:** Pure regex/syntactic match. Classify into known buckets. Log scan. Deterministic checklist verification. No judgment. No gradient — match-or-not.
- **Benchmark contract:** none formal. Empirical: doesn't hallucinate on bounded structured tasks; runs fast.
- **Cost expectation:** ~10-20× cheaper than Thinking tier. Use for high-volume mechanical sweeps.
- **Anthropic default:** Haiku 4.5 (`claude-haiku-4-5-20251001`)
- **Verified equivalents:** Haiku 4.6+ (when available), GPT-5 Nano (`gpt-5-nano` — fastest/cheapest, classify + summarize)
- **Local equivalents:** llama3.2-3b, qwen2.5-3b

## Substitution table (advisory, 2026 Q2)

| Provider | Frontier | Thinking | Code | Pattern |
|---|---|---|---|---|
| Anthropic (default) | Fable 5 (`fable`) | Opus 4.8 (`opus`) | Sonnet 4.6 (`sonnet`) | Haiku 4.5 (`haiku`) |
| OpenAI | `gpt-5.5` (Codex) | `gpt-5.4` | `gpt-5.4-mini` | `gpt-5-nano` |
| Google | next-gen Gemini Ultra (when it clears the contract) | `gemini-2.5-pro` | `gemini-2.5-flash` | `gemini-flash-lite` |
| Local (Ollama / MLX) | n/a — none meets contract yet | n/a — none meets contract yet | `qwen2.5-coder-32b` | `llama3.2-3b` |

⚠️ **Always verify benchmarks before swapping.** Table cells are best-effort as of build-loop's last update; model versions and rankings drift. Use `Skill("research")` or Context7 MCP to confirm current SWE-bench Verified scores before relying.

### Selectable model registry (the machine-readable source of truth)

The cells above are mirrored by `MODEL_REGISTRY` in `scripts/model_overrides.py`. List the selectable models per tier (and feed any tool) with:

```bash
python3 scripts/model_overrides.py --list-models          # all tiers
python3 scripts/model_overrides.py --list-models --tier frontier --json
```

The registry is **advisory**: override resolution still accepts any model id, so a brand-new model works the moment you put it in `modelOverrides` — it is simply flagged `registered: false` on the resolve envelope until it is added here. `TIER_DEFAULTS` (the Anthropic mapping) stays the fallback; registering a model makes it *selectable*, not the default.

### Standing tier-fallback policy (when a tier's model is unavailable)

When a tier's resolved model is **unavailable** at dispatch time (provider outage, quota, region gate) and the caller supplied no explicit per-call fallback, resolution walks DOWN a fixed **tier-to-tier** graph to the fallback tier's default. The policy is expressed in tier/role terms — `TIER_FALLBACK` in `scripts/model_overrides.py` holds the edges; the concrete model ids live only in `TIER_DEFAULTS`/`MODEL_REGISTRY`, so swapping a model never touches the rule.

| Tier (role) | Standing fallback tier |
|---|---|
| **Frontier** (judgment) | **Thinking** — and no further (invariant below) |
| **Thinking** (coordination) | **Code** |
| **Code** (execution) | **Pattern** |
| **Pattern** (recognition) | none — bottom of the graph |

**HARD INVARIANT — a frontier/judgment role never resolves below the Thinking tier.** Frontier's only permitted standing fallback is Thinking; it must NEVER silently degrade to the Code or Pattern tier. Resolution enforces this by walking at most one edge from Frontier: if the Thinking-tier default is itself unavailable, Frontier resolution STOPS at Thinking rather than walking on to Code/Pattern. Every other tier may keep walking down the graph until a usable default is found or the graph bottoms out. The rationale is durable: a verification/planning verdict produced by a Code- or Pattern-tier model is worse than a delayed verdict, so the judgment surface degrades only to the next reasoning-class tier (Thinking), never to an execution/recognition tier. See `feedback_model_org_fable5.md` (Frontier-unavailable → Thinking tier, never Code).

An **explicit per-call fallback wins** over the standing policy — passing `--fallback <model>` (or a `fallback=` argument) is treated as deliberate caller intent and skips the standing walk entirely.

```bash
# Drive the standing policy explicitly (frontier default unavailable):
python3 scripts/model_overrides.py --workdir "$PWD" --tier frontier \
  --unavailable fable --json
# -> { "model": "<thinking default>", "source": "tier-fallback", "fallback_tier": "thinking" }
```

## Three ways to swap

### 1. Edit agent frontmatter (one-time, per-host)

Each `agents/*.md` carries a `model:` field. Replace `opus` / `sonnet` / `haiku` with your provider's identifier. Example for OpenAI on a Codex host:

```yaml
# agents/build-orchestrator.md
---
name: build-orchestrator
model: gpt-5.4             # was: opus (Thinking tier)
---

# agents/implementer.md
---
name: implementer
model: gpt-5.4-mini        # was: sonnet (Code tier)
---
```

This is durable but requires re-editing on every plugin update. Prefer #2 below.

### 2. Runtime override via `.build-loop/config.json` (recommended)

```json
{
  "modelOverrides": {
    "frontier": "gpt-5.5",
    "thinking": "gpt-5.4",
    "code": "gpt-5.4-mini",
    "pattern": "gpt-5-nano"
  }
}
```

Configs that predate the `frontier` tier resolve `frontier` → `fable` automatically (built-in tier default in `scripts/model_overrides.py`), so older repos keep working without edits.

The orchestrator resolves this before dispatching each subagent with
`scripts/model_overrides.py`. Frontmatter `model:` becomes the fallback when an
override is absent for that tier.

```bash
python3 scripts/model_overrides.py \
  --workdir "$PWD" \
  --tier code \
  --fallback sonnet \
  --json
```

Resolution order is repo config first, then `.build-loop/state.json`
`config.modelOverrides`, then the supplied fallback. Use `--require` when a
tier must resolve to a concrete model before dispatch.

### 3. Per-dispatch override

When dispatching a subagent for a one-off task that needs a different tier:

```
Agent({
  subagent_type: "build-loop:implementer",
  model: "claude-opus-4-7",   // override Sonnet → Opus for this dispatch
  prompt: "..."
})
```

This is what happens during escalation (e.g. "2 consecutive failures on the same chunk → escalate to Thinking tier per `model-tiering`").

## Tier-vs-task quick reference

When you see a task in build-loop, classify it before assigning a tier:

| Task | Reasoning shape | Tier |
|---|---|---|
| Frame goal, ADRs, scope, MECE-partition | Planning synthesis | Frontier |
| Plan-critic vs rubric | Verification synthesis | Frontier |
| Implement commit's owned files | Application | Code |
| Severity-rank findings (post-verdict routing) | Coordination synthesis | Thinking |
| Mock-data scan | Recognition | Pattern |
| Trace caller-paths (scope-auditor) | Verification synthesis | Frontier |
| Independent-auditor vs diff | Verification synthesis | Frontier |
| Audit / learnings write (no verdict being rendered) | Coordination synthesis | Thinking |
| Recurring-pattern detection | Recognition | Pattern |

The decision tree (from `model-tiering/SKILL.md`):
1. "Single-correct answer derivable from a rule applied to bounded input?" → Application / Code tier
2. Else "Pure pattern-match, no gradient?" → Recognition / Pattern tier
3. Else, Synthesis. Then: "Is this a planning decision (what to build) or a verification verdict (did it hold)?" → Frontier tier
4. Else (routing, escalation, audit-synthesis without a verdict) → Thinking tier

## Dual-mode A/B test design (preserved)

Build-loop intentionally supports two dispatch modes to enable continued A/B testing on tier-mix tradeoffs:

### Mode A — Top-level / fan-out (default)
- **Invocation:** `/build-loop:run` invoked as a Skill from user session
- **Tier mix:** Thinking orchestrator + up to 4 Code-tier implementer subagents in parallel + Code-tier critic + Thinking-tier severity ranking + Thinking-tier audit
- **Anthropic mapping:** Opus orchestrator + Sonnet implementer fan-out
- **Best for:** features with ≥3 truly parallel-safe chunks, large feature size (≥10 commits), repetitive patterns

### Mode B — Inline / single-context (preserved for A/B comparison + small features)
- **Invocation:** `Agent(subagent_type="build-loop:build-orchestrator", ...)` from any session
- **Tier mix:** Thinking-tier orchestrator handles ALL phases inline (no-sub-sub-agents rule kicks in)
- **Anthropic mapping:** all-Opus single context
- **Best for:** small/medium features (≤6 commits), cross-cutting refactors where catching all-the-callsites matters more than per-token cost, sequential dependency chains, comparison runs against Mode A

The orchestrator detects which mode it's in via the dispatch path (top-level message vs subagent invocation) and adapts behavior at `agents/build-orchestrator.md:529-530`. **Both modes share the same plan, the same Phase 1-4 logic, and the same Phase 6 Learn signals.** The only difference is whether implementer work fans out to Code-tier subagents (Mode A) or runs inline in the Thinking-tier orchestrator's context (Mode B).

This dual-mode design is **not deprecated** — it's the intentional architecture for tier-comparison telemetry. Future build-loop changes that affect dispatch must preserve both modes.

## Multi-model implications for the dispatch test

When swapping providers, the dispatch-pattern A/B test should be re-run because:
- **Wall-clock per tier varies by provider.** GPT-5 Codex may be faster or slower than Sonnet 4.6 at scoped code application.
- **Cost ratios shift.** Some providers price the Thinking tier closer to the Code tier (smaller multiplier); others price wider.
- **Cross-context-window effects.** Mode B's "single Opus context" wins partly come from full-file-system visibility; the same effect may differ on a model with a smaller context window.
- **Tool-use fidelity.** Mode A's parallel implementer fan-out depends on the Code tier reliably calling Read/Edit/Bash tools without hallucination. This varies materially across providers.

When introducing a new provider to a project, prefer Mode B for the first 2-3 builds to establish a quality baseline, then enable Mode A once the new Code-tier model has shown stable tool-use behavior.

## Dynamic tier assignment (guide, not a fixed rule)

The orchestrator **judges each subtask's complexity at dispatch time** and assigns the tier that fits. This is adaptive, not a fixed table.

**Priority order: accuracy > speed > cost.** Pick the tier that does the work CORRECTLY first — never trade accuracy for a cheaper or faster model. Among accuracy-equivalent options, prefer the faster path (spawn Opus subagents to accelerate complex work; fan out in parallel). Optimize cost only after accuracy and speed are both satisfied — cost is the last lever, never the first. This is why every subagent's output is verified (accuracy) and why Opus subagents are used freely on hard tasks (speed on complexity beats pinching tier cost).

**Tier assignment guide:**

| Task shape | Tier |
|---|---|
| Pure recognition, extraction, classification, mechanical sweep — "find X", "list/grep Y", "scan for Z", "extract these fields", "run detector + summarize its JSON", "does this match the pattern". No rule-application, no cross-file reasoning. | **Pattern / Haiku** |
| Apply a known rule or spec to bounded input. Scoped implementation per owned-files. The "how" when the "what" is settled. | **Code / Sonnet** — default workhorse; prefer Sonnet over Haiku when in doubt |
| Coordination, routing, ambiguous-spec interpretation, novel architecture decision mid-execution, causal-tree on stuck iterations, user-trust prose where no verification verdict is being rendered. | **Thinking / Opus** — orchestrator default, AND available to accelerate genuinely complex execution subtasks |
| Planning synthesis (frame goal, draft spec/ADRs, F-criteria, MECE partition) **when stakes-gated via the Advisor dispatch ladder** (`synthesisDensity > 5`, `riskSurfaceChange`, `stakes >= medium`, or `dispatch_tier: frontier`) OR verification verdicts (plan-critic, scope-auditor, independent-auditor, fix-critique, fact-checker, security-reviewer, overfitting-reviewer, promotion-reviewer). | **Frontier / Fable** — wrong plans and wrong verdicts compound; pays the premium. Plan synthesis reaches Fable through the `advisor` agent / peer host / already-Fable session; when no trigger fires or no dispatch path is reachable it runs inline on the orchestrator's model (Opus), labeled honestly — the floor equals today's behavior. See `skills/build-loop/references/advisor-dispatch-ladder.md`. |

**Prefer Sonnet.** Sonnet is the workhorse for the bulk of build-loop's work. Down-tier to Haiku only for tasks that are genuinely trivial/mechanical — pure pattern-match, no judgment, no gradient. When in doubt, use Sonnet.

**Opus subagents are allowed** to accelerate complex subtasks — cross-file reasoning, novel design, ambiguous specs, hard refactors. Opus is no longer reserved for the orchestrator alone. The orchestrator MAY spawn an Opus subagent when a subtask is complex enough that a stronger model would produce materially better or faster results. Use Opus to accelerate complex work, not only for top-level synthesis.

Both escalation directions are active on every dispatch decision: escalate up when complexity exceeds the assigned tier; down-tier when the task is genuinely below it.

For `model: inherit` agents (fact-checker, fix-critique, root-cause-investigator), the **caller** passes the appropriate tier — the agent inherits what the caller assigned.

### Verify every subagent (the safety net for dynamic tiering)

Because tiers are assigned adaptively, every subagent's output is **checked before it is accepted**. The cheaper the tier, the stronger the check. This per-subagent verification is what makes dynamic (and occasionally cheaper) assignment safe.

Verification ties to build-loop's existing mechanisms:
- **verify-scope / verify-landed** (Phase 3 commit step) — confirms the implementer only touched owned files and the commit landed cleanly.
- **independent-auditor** (Phase 4 Review-A) — adversarial LLM-grade read of the full build's output.
- **implementer return envelope** — every subagent returns a structured envelope; `status: blocked | partial` routes to Iterate before the output is accepted.

No subagent output is trusted unchecked. The verification chain is a first-class requirement, not a backstop.

### Fan-out / workflow agents

When fanning out bounded agents via the Workflow tool, a dynamic-workflow, or a rallyflow mini-loop, assign each agent by the same guide:

- **Recognition/extraction/scan-and-summarize** → Haiku (Pattern tier) — genuinely trivial mechanical work
- **Apply rules or reason across files** → **Sonnet** (Code tier) — default for fan-out agents
- **Cross-file, novel, or ambiguous subtask** → Opus (Thinking tier) — when the subtask warrants it
- **Single synthesis agent** (aggregates fan-out results, cross-agent judgment) → Opus only when synthesis dimensions exceed the Code-tier contract

Fan-out breadth multiplies token cost linearly — but the right fix is matching tier to task complexity, not defaulting every agent to the cheapest tier. Prefer Sonnet for fan-out agents; drop to Haiku only for the genuinely mechanical bounded ones.

Concrete dispatch pattern:

```
Agent({
  subagent_type: "build-loop:implementer",
  model: "haiku",          // recognition task — scan for mock-data patterns
  prompt: "Scan files X..Z for hardcoded test data. Return a JSON list of findings."
})

Agent({
  subagent_type: "build-loop:implementer",
  model: "sonnet",         // default for rule-application / scoped implementation
  prompt: "Implement the auth middleware per the spec in intent.md. Owned files: ..."
})

Agent({
  subagent_type: "build-loop:implementer",
  model: "opus",           // complex subtask — cross-file refactor, ambiguous spec
  prompt: "Refactor the session management layer across auth/* and middleware/*. ..."
})
```

## Round-3 evidence (2026-05-07) — preserved for context

| Mode | Wall-clock | Tokens | Notes |
|---|---|---|---|
| A (Opus + Sonnet fan-out, 4-parallel Wave 1) | ~11 min | ~600K total (~50/50 Thinking/Code) | parallel-commit race required orchestrator-side recovery (~3-4 min of 11-min total) |
| B (Opus inline, serial) | ~23 min | ~150K Thinking only | 0 iterations, caught a schema field bug A's scoped implementer missed |

A's wall-clock advantage on round 3 was real (parallel structure exists in the feature); cost ratio is ~4× (A burns more tokens). Both modes shipped working features.
