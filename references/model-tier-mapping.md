<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Model Tier Mapping — Multi-Provider Substitution Reference

Build-loop is provider-agnostic at the tier level. Agent frontmatter uses Anthropic aliases (`opus`, `sonnet`, `haiku`) by default because Claude Code is the primary host, but the **tier abstraction** (Thinking / Code / Pattern) is what governs the role assignment. This reference documents how to swap providers cleanly.

## Canonical tier definitions

### Thinking tier
- **Role:** Synthesis. Cross-file judgment. Ambiguity resolution. Plan drafting. Severity ranking after critic findings. Audit / learnings. The "what and why" decisions.
- **Benchmark contract:** SWE-bench Verified ≥78% AND competitive on ARC-AGI / GPQA Diamond / MMLU-Pro.
- **Cost expectation:** highest tier. Use sparingly — only for true synthesis tasks. Never default to Thinking for execution.
- **Anthropic default:** Opus 4.7 (`claude-opus-4-7`)
- **Verified equivalents (2026 Q1, advisory):** GPT-5 Thinking, Gemini 2.5 Pro
- **Local equivalents:** none yet — Thinking-tier work needs frontier-class context length and judgment; local models lag

### Code tier
- **Role:** Application. Apply a known rule, spec, or pattern to bounded input. Scoped implementation per a commit's owned-files. Adversarial critic vs rubric. Mechanical simplify. The "how" decisions when the "what" is already settled.
- **Benchmark contract:** SWE-bench Verified ≥75% AND tool-use accuracy ≥85% AND multi-turn coding rollout ≥80%.
- **Cost expectation:** ~3-5× cheaper than Thinking tier per token. The default for the bulk of build-loop work.
- **Anthropic default:** Sonnet 4.6 (`claude-sonnet-4-6`)
- **Verified equivalents:** Sonnet 4.7+ (when available), GPT-5 Codex
- **Local equivalents:** qwen2.5-coder-32B-instruct (mid-quality), Codestral 22B (reasonable substitute for bounded refactor work)

### Pattern tier (a.k.a. Recognition)
- **Role:** Pure regex/syntactic match. Classify into known buckets. Log scan. Deterministic checklist verification. No judgment. No gradient — match-or-not.
- **Benchmark contract:** none formal. Empirical: doesn't hallucinate on bounded structured tasks; runs fast.
- **Cost expectation:** ~10-20× cheaper than Thinking tier. Use for high-volume mechanical sweeps.
- **Anthropic default:** Haiku 4.5 (`claude-haiku-4-5-20251001`)
- **Verified equivalents:** Haiku 4.6+ (when available), GPT-5 Mini
- **Local equivalents:** llama3.2-3b, qwen2.5-3b

## Substitution table (advisory, 2026 Q1)

| Provider | Thinking | Code | Pattern |
|---|---|---|---|
| Anthropic (default) | Opus 4.7 | Sonnet 4.6 | Haiku 4.5 |
| OpenAI | GPT-5 Thinking | GPT-5 Codex | GPT-5 Mini |
| Google | Gemini 2.5 Pro | Gemini 2.5 Flash | Gemini Flash Lite |
| Local (Ollama / MLX) | n/a — none meets contract yet | qwen2.5-coder-32B | llama3.2-3b |

⚠️ **Always verify benchmarks before swapping.** Table cells are best-effort as of build-loop's last update; model versions and rankings drift. Use `Skill("research")` or Context7 MCP to confirm current SWE-bench Verified scores before relying.

## Three ways to swap

### 1. Edit agent frontmatter (one-time, per-host)

Each `agents/*.md` carries a `model:` field. Replace `opus` / `sonnet` / `haiku` with your provider's identifier. Example for OpenAI on a Codex host:

```yaml
# agents/build-orchestrator.md
---
name: build-orchestrator
model: gpt-5-thinking      # was: opus (Thinking tier)
---

# agents/implementer.md
---
name: implementer
model: gpt-5-codex         # was: sonnet (Code tier)
---
```

This is durable but requires re-editing on every plugin update. Prefer #2 below.

### 2. Runtime override via `.build-loop/config.json` (recommended)

```json
{
  "modelOverrides": {
    "thinking": "gpt-5-thinking",
    "code": "gpt-5-codex",
    "pattern": "gpt-5-mini"
  }
}
```

The orchestrator (`agents/build-orchestrator.md` Phase 3) reads this before dispatching each subagent. Frontmatter `model:` becomes the fallback when an override is absent for that tier.

⚠️ Implementation status (2026-05-07): the override-reading code path is documented but not yet wired in `agents/build-orchestrator.md`. Treat this section as design intent; until wired, use option #1.

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
| Frame goal, ADRs, scope | Synthesis | Thinking |
| Plan-critic vs rubric | Application | Code |
| Implement commit's owned files | Application | Code |
| Severity-rank findings | Synthesis | Thinking |
| Mock-data scan | Recognition | Pattern |
| Trace caller-paths (scope-auditor) | Synthesis | Thinking |
| Adversarial critic vs diff | Application | Code |
| Audit / learnings write | Synthesis | Thinking |
| Recurring-pattern detection | Recognition | Pattern |

The decision tree (from `model-tiering/SKILL.md`):
1. "Single-correct answer derivable from a rule applied to bounded input?" → Application / Code tier
2. Else "Pure pattern-match, no gradient?" → Recognition / Pattern tier
3. Else → Synthesis / Thinking tier

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

The orchestrator **judges each subtask's complexity at dispatch time** and assigns the tier that fits. This is adaptive, not a fixed table. Quality comes first — prefer the tier that does the work right; then prefer the cheapest tier that still satisfies that quality bar.

**Tier assignment guide:**

| Task shape | Tier |
|---|---|
| Pure recognition, extraction, classification, mechanical sweep — "find X", "list/grep Y", "scan for Z", "extract these fields", "run detector + summarize its JSON", "does this match the pattern". No rule-application, no cross-file reasoning. | **Pattern / Haiku** |
| Apply a known rule or spec to bounded input. Scoped implementation per owned-files. Adversarial critique vs a rubric. The "how" when the "what" is settled. | **Code / Sonnet** — default workhorse; prefer Sonnet over Haiku when in doubt |
| Synthesis, cross-file architectural decisions, ambiguous spec, novel design, hard refactor, user-trust prose. | **Thinking / Opus** — orchestrator default, AND available to accelerate genuinely complex subtasks |

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
