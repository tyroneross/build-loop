---
name: model-tiering
description: Use when choosing a model tier for a subagent, deciding code-tier vs thinking-tier in frontmatter, or escalating mid-flow. Covers the multi-model abstraction — Opus/Sonnet/Haiku are Anthropic-default mappings; the tier abstraction is provider-portable.
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Model Tiering — Authoritative Reference for Build-Loop

Governs model selection across all build-loop phases. Build-loop is **multi-model** — the role assignments below are tier-based (thinking / code / pattern), not provider-specific. Anthropic's Opus/Sonnet/Haiku is the default mapping; equivalents from other providers map to the same tiers.

Use `references/agent-role-taxonomy.md` for responsibility boundaries. This
skill answers "which tier should run the role?", not "who owns the work?".

## Tier abstraction (canonical)

| Tier | Anthropic default | Role | Equivalents (advisory — verify benchmarks before swapping) |
|---|---|---|---|
| **Thinking** | Opus 4.7 | Synthesis, planning, ambiguity resolution, severity ranking, audit/learnings, cross-file judgment | GPT-5 Thinking, Gemini 2.5 Pro, future Claude tier; any model >= Opus 4.6 on SWE-bench Verified + Frontier-class on ARC-AGI / MMLU-Pro |
| **Code** | Sonnet 4.6 | Application — apply rule to bounded input, scoped implementation, adversarial critic, mechanical refactor | Sonnet 4.7+, GPT-5 Codex, qwen2.5-coder-32B (local); any model with SWE-bench Verified within ~5pt of Sonnet 4.6 (currently ~79.6%) |
| **Pattern** | Haiku 4.5 | Recognition — regex/syntactic match, classification into known buckets, log scan, deterministic checklist | Haiku 4.6, GPT-5 Mini, llama3.2-3b (local); any small/fast model that handles structured pattern matching |

**Rule of substitution:** tier A's swap target must score within tolerance of the default on the benchmark relevant to its role. For Code tier that's SWE-bench Verified ≥75% AND tool-use accuracy ≥85%; for Thinking tier that's SWE-bench ≥78% AND ARC-AGI / GPQA Diamond competitive; for Pattern tier no benchmark — just "fast and cheap, doesn't hallucinate on bounded structured tasks."

## Provider-swap recipe

Build-loop's agent frontmatter uses Anthropic model aliases (`opus`, `sonnet`, `haiku`) because Claude Code is the primary host. To run on a different provider:

1. **One-time edit per agent:** open each `agents/*.md` and change the `model:` field to your provider's equivalent. The tier (Thinking/Code/Pattern) determines the substitution target.
2. **Runtime override:** `.build-loop/config.json.modelOverrides` accepts `{ thinking: "<id>", code: "<id>", pattern: "<id>" }`. The orchestrator reads this before dispatching subagents (see `references/model-tier-mapping.md` for full schema).
3. **Per-dispatch override:** any orchestrator dispatch may pass `model: <id>` in the subagent prompt to force that call.

The role-and-task table below uses tier names. The Anthropic-default mapping in the right column is illustrative; substitute your equivalents at swap time.

## When to use this skill

- Choosing `model:` field in an agent frontmatter
- Orchestrator deciding which subagent tier to spawn for a task
- Estimating cost tradeoffs before starting a build
- Deciding whether to escalate mid-flow after failures
- Evaluating whether to swap providers (use the Tier abstraction table above as the contract)

## Evidence base (2026 Q1)

| Claim | Source | Certainty |
|-------|--------|-----------|
| Sonnet 4.6: 79.6% SWE-bench Verified | Anthropic announcement + SWE-bench leaderboard | ⚠️ T2, single-source |
| Opus 4.6: 80.8% SWE-bench Verified (1.2pt gap — smallest in Claude history) | Same | ⚠️ T2, single-source |
| Sonnet 4.6 uses 70% fewer tokens than 4.5 on complex file ops with +38% accuracy | Anthropic Sonnet 4.6 announcement | ⚠️ T2, single-source |
| Pricing: Sonnet $3/$15 per MTok input/output | Anthropic pricing page | ⚠️ verify before billing |
| Pricing: Opus $15/$75 per MTok input/output (5x gap) | Anthropic pricing page | ⚠️ verify before billing |

## MECE primitive: cognitive type of the task

Before consulting the role table, classify the task by reasoning shape. The MECE cut is the kind of thinking the task requires; lifecycle stage (plan/execute/review) is a second-order cut that often mixes types.

| Reasoning shape | Model | What it means | Example tasks |
|---|---|---|---|
| **Synthesis** — combine N inputs into a novel decision; cross-file/cross-system reasoning; ambiguity resolution; severity ranking | **Opus** | The "what" and "why" calls. No single rule produces the answer. | Frame goal, draft spec/ADRs, trace call-paths across files, rank finding severity, escalate stuck iteration with causal-tree, write audit/learnings |
| **Application** — apply a known rule, spec, or pattern to bounded input; produce an artifact that matches a contract | **Sonnet** | The "how" call when "what" is decided. Single-correct-answer derivable from a rule. | Implement a commit's owned files per spec, write tests for given F-criteria, adversarial critic vs rubric, mechanical simplify, fact-check with named source |
| **Recognition** — pure regex/syntactic match; classify into known buckets; no judgment | **Haiku** | No gradient — matches or doesn't. | Mock-data scan, log pattern detection, file inventory, cross-run pattern detection, deterministic checklist verification |

**Decision tree:** "Does this task have a single-correct answer derivable from a rule applied to bounded input?" → Yes = Application/Sonnet. Else "Is the answer pure pattern-match?" → Yes = Recognition/Haiku. Else = Synthesis/Opus.

## Default assignments

| Task | Reasoning shape | Model | effort | Why |
|------|------|-------|--------|-----|
| Frame & plan: goal, ADRs, scope, F-criteria, MECE partition | Synthesis | Opus | medium | Ambiguity resolution; wrong plan compounds |
| Plan-verify deterministic checklist | Recognition | (script) | — | No model; runs `plan_verify.py` |
| Plan-critic adversarial review against rubric+checklist | Application | Sonnet | high | Bounded — rubric is the rule. Separation drives quality |
| **Scope auditor (NEW — Plan→Execute boundary)**: trace callers of every modified-API symbol; annotate `caller_audit:` per commit | Synthesis | Opus | medium | Cross-file call-path tracing that fanned-out implementers can't do (round-2 lesson) |
| Code execution — bounded chunk, spec clear | Application | Sonnet | medium | Default. High accuracy, 5× cheaper |
| Code execution — ambiguous spec | Synthesis | Opus | medium | Interpretation cost cheaper than rework |
| Adversarial critic pass (read-only diff vs rubric) | Application | Sonnet | high | Bounded — rubric vs diff is rule-application; separation effect |
| Code review — severity ranking + recommendation order (given findings) | Synthesis | Opus | medium | Cross-finding judgment about what matters most |
| Mock data scanning | Recognition | Haiku | low | Regex only |
| Fact-checking — trace metric → source, judge accuracy | Synthesis | Opus | medium | Cross-system; "is this number real?" requires cross-context judgment |
| Fact-checking — trace metric → named-source pattern (rule-bound) | Application | Sonnet | medium | When the source-pattern rule is explicit |
| Simplify — apply known simplifications | Application | Sonnet | medium | Inline single-use helper, delete dead branch — bounded |
| Debugging — symptom-to-known-pattern match | Application | Sonnet | high | Memory-first gate's "Application until the rule runs out" |
| Debugging — causal-tree after 2 consecutive failures | Synthesis | Opus | high | Synthesis takes over when rule-match exhausts |
| Novel architecture decision | Synthesis | Opus | medium | Cross-file impact; wrong call is expensive |
| Writing user-facing prose (copy, microcopy, errors) | Synthesis | Opus | medium | Tone, restraint, and nuance matter |
| Audit / learnings / Phase 6 promotion-decision | Synthesis | Opus | medium | Cross-run synthesis |
| Recurring-pattern detection across runs[] | Recognition | Haiku | low | Pattern-match across structured logs |

## Round 2 evidence (2026-05-07, example-app news-podcast iteration 2)

n=2 dispatch-pattern A/B comparison on a 6-commit feature reversed the round-1 belief that Skill-path (Sonnet fan-out) is materially cheaper across the board:

| Dimension | A — Skill-path / Sonnet fan-out | B — Agent-tool / inline-Opus |
|---|---|---|
| Wall-clock | ~70-80 min | ~30 min |
| Token mix | ~50% Opus orchestrator + ~50% Sonnet implementer | ~100% Opus single-context |
| News-podcast jest | 52/0 | 59/0 |
| Iterations needed | 1 (cross-file prop wiring missed by Sonnet) | 0 |
| LLM judge speaker-flow | not run live (proxy from B) | 4/4 × 3 samples |

Findings that updated the model tiering:

1. **Sonnet implementers are scoped to `files_owned`** and miss cross-file integration gaps (the AIBriefPage→PodcastGenerator props case). This motivates the new Scope Auditor role above.
2. **Orchestrator overhead (research dispatches, plan-critic, iterate coordination, audit) burns ~50% of total tokens at Opus rate** on small/medium features. Sonnet's lower per-token rate doesn't dominate at 6-commit scale.
3. **Inline-Opus is faster wall-clock** when there's no real parallelism to exploit. Fan-out parallelism is only a win when ≥3 chunks are truly independent.
4. **Plan-critic on Sonnet caught 17 substantive findings** on a written spec — confirms "rubric-application = Sonnet" is robust.

These findings inform the role assignments, especially the rubric-application=Sonnet vs severity-assessment=Opus split for code review.

## Escalation triggers (stay on Sonnet UNLESS)

- 2 consecutive failures on the same chunk after a retry at effort=high
- Spec is ambiguous and interpretation will materially change implementation
- A cross-file architectural decision surfaces mid-execution that wasn't in the plan
- Critic flags a "strong-checkpoint" finding that requires judgment, not just a fix
- Novel error pattern not found in `.build-loop/issues/` or debugging memory
- Task produces user-visible prose where tone and restraint are load-bearing

## Techniques that work

- **Self-refine with external verification** (tests, lint, type-check). External oracle is non-negotiable — without it, self-refine is circular.
- **Adversarial critic loop** (writer agent + read-only reviewer agent). Separation is what makes it work. Same model reviewing its own output doesn't catch errors.
- **Best-of-N sampling with self-certainty voting** on HARD chunks only (flagged by plan or first-pass failure). N=3. Cost = ~3x Sonnet, still under 1.5x single-pass Opus.
- **Test-time compute** (effort=medium default, effort=high on retry). Easier problems benefit from revisions; harder problems need parallel sampling — not just more thinking on one path.
- **Plan-then-execute split** (Opus plans once, Sonnet executes many). Established pattern. Amortizes Opus cost across N subagent calls.

## Techniques to avoid

- **Multi-agent debate** (3+ agents argue toward consensus). ⚠️ ICLR 2025 MAD analysis shows majority voting captures most gains; debate adds cost without consistent wins. Use simple voting instead.
- **Self-critique without adversarial separation**. A model editing its own output won't reliably catch its own errors. Use a separate read-only reviewer or rely on external tests.
- **Chain of Density**. Summarization-specific technique; not applicable to code work.
- **Best-of-N by default**. Only on hard chunks. Blanket best-of-N wastes tokens on easy tasks where effort=high is sufficient and cheaper.

## Cost math quick reference

> ⚠️ **Advisory only.** The numbers below are directional heuristics based on single-source token-profile estimates and public pricing as of the skill's last update. They are **not** verified against real usage telemetry and should not be used as hard routing logic. Treat them as "this tier costs roughly this much more than that tier," not as commitments. Pricing, token profiles, and model output lengths all drift over time. Before using these ratios in any cost-minimization decision, pull actual usage data from the last 30 days of builds and re-derive the numbers for your workload.

| Configuration | Relative cost (advisory) |
|---------------|--------------|
| Single-pass Opus | ~5x baseline |
| Single-pass Sonnet 4.6 | ~0.3x (70% fewer tokens in observed samples) |
| Sonnet 4.6, effort=high | ~0.6x |
| Sonnet 4.6, best-of-3 + critic | ~1.2x |
| Sonnet 4.6 best-of-3 + critic vs single-pass Opus | ~4x cheaper |

❓ Best-of-N + critic vs single-pass Opus on SWE-bench has not been directly benchmarked.

**How to convert these into routing decisions**: don't. Use the numbers to sanity-check a tier choice after the fact ("was this worth the 5x?"), not to justify forcing a model swap. When real telemetry disagrees with this table, trust telemetry and file an issue to update the table.

## How the build-loop uses this

Orchestrator (Opus 4.7) spawns implementer subagent (Sonnet, effort=medium) → external verification gate (tests/lint/types) → Sonnet critic agent (read-only, effort=high) → if strong-checkpoint flagged, escalate to Opus for judgment call. See `agents/build-orchestrator.md §Escalation Triggers`. The **tier mapping** is the policy; the cost numbers above are advisory context, not the basis for overrides.

Haiku is only used for Phase 7B mock scanning. Never for reasoning tasks.

## Pin vs inherit in agent frontmatter

Not every agent should hard-pin its model. Use this rule:

- **Pin** (`model: opus | sonnet | haiku`) when the task has a clear right tier and cost/quality drift from user's session choice would be a bug. Examples: `independent-auditor` (Sonnet advisory judge across chunk + build scope, consolidated 2026-05-23 — replaces retired `commit-auditor` and earlier `sonnet-critic`), `mock-scanner` (pattern matching only), `build-orchestrator` (Opus judgment at plan/review boundaries).
- **Inherit** (`model: inherit`) when user intent should flow through. The user's main-session choice is itself a cost/speed preference; respect it. Pair with a "recommended: X" note in this skill rather than forcing via frontmatter. Example: `fact-checker` — recommended Sonnet, but inherit honors whatever tier the user picked upstream.
- **Override mechanism**: users can override any pin by passing `model:` when spawning the agent or by editing the frontmatter. Pins are defaults, not locks.

Forward-compat note: pinned family aliases (`sonnet`, `opus`) auto-track latest versions (e.g., 4.6 → 4.7). `inherit` additionally picks up brand-new tiers (e.g., a future Flash-class model) without frontmatter edits.

## Limitations of this guidance

- ⚠️ Sonnet 4.6 token-efficiency claim is single-source (Anthropic announcement). Treat as directionally correct, not proven.
- ❓ Best-of-N + critic hasn't been tested against single-pass Opus on SWE-bench specifically.
- ⚠️ Escalation triggers are heuristics, not proven thresholds. Revise after observing 5+ real builds and logging outcomes to `.build-loop/memory/`.

## When to consult `model-router`

For Phase 3 (Execute) sub-agent dispatch, prefer the standalone router over inline tier reasoning when it is available:

```bash
python3 ~/.claude/scripts/model-router.py \
  --task "<one-line task summary>" \
  --complexity auto \
  --phase execute \
  --task-id "<task-id-for-cost-ledger>" \
  --json
```

The router returns:

```json
{
  "provider": "ollama-mcp" | "codex" | "claude",
  "model": "<model-id>",
  "tool_call": {"name": "<mcp-tool-name>", "args": {...}},
  "reason": "...",
  "evidence_refs": ["<paths to docs that justify this decision>"]
}
```

Why prefer the router:
- **Evidence-cited**: every decision references the doc that supports it (DOE results, model-tiering policy, cost-ledger design)
- **Deterministic**: same input → same output, auditable across builds
- **task_id propagates** to MCP tool args, so `cost-ledger-reader.py --by-task` shows per-build-phase economics
- **Free**: heuristic-only, no LLM call to decide

Fallback when router is unavailable: use the inline tier rules above. The router's policy mirrors them, so behavior is consistent either way.

Full contract and routing matrix: `~/dev/research/topics/llm/llm.build-loop-router-integration-2026-04.md`
