---
name: model-tiering
description: Use when choosing a model tier or segment for a subagent, deciding a role descriptor (segment + tier) in frontmatter, or escalating mid-flow. Covers the two-axis taxonomy (work-role segment × 7-rung capability ladder) — Opus/Sonnet/Haiku are Anthropic-default mappings; selection is provider-portable and data-driven. NOT for benchmarking models head-to-head on a task — use `model-bakeoff`.
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Model Tiering — Authoritative Reference for Build-Loop

Governs model selection across all build-loop phases. Build-loop is **multi-model** — the role assignments below are tier-based (thinking / code / pattern), not provider-specific. Anthropic's Opus/Sonnet/Haiku is the default mapping; equivalents from other providers map to the same tiers.

Use `references/agent-role-taxonomy.md` for responsibility boundaries. This
skill answers "which tier should run the role?", not "who owns the work?".

## Two-axis taxonomy (the source of truth)

Selection runs on **two orthogonal axes**, encoded as data in `references/model-taxonomy.json` (loader: `scripts/model_taxonomy.py`):

- **SEGMENT** — the work role / primary output: Generative Reasoning, Agentic Execution, Representation/Retrieval, Realtime Interaction, Perception/Input Understanding, Generative Media, Governance/Evaluation. Segment is the *primary product role* — a reasoning model that accepts image/audio input is Generative Reasoning with a `multimodal-input` tag, not Perception.
- **CAPABILITY-TIER** — a 7-rung ladder: T0 experimental/restricted frontier · T1 ultra-frontier · T2 frontier · T3 balanced workhorse · T4 efficient near-frontier · T5 utility/nano/edge · T-S specialist infrastructure (off the ladder).

Agents declare a `(segment, tier)` ROLE; the resolver (`scripts/model_resolver.py resolve_role`) walks the per-cell ordered preferred list and returns the highest-ranked AVAILABLE + host-reachable model (ties → release recency). New models are classified once (`scripts/classify_model_tier.py`, host-LLM, both axes) — no agent edits. **The 4-tier table below is the legacy alias view** — `frontier/thinking/code/pattern` fold to `T1/T2/T3/T4` (Generative Reasoning segment) and remain accepted everywhere for back-compat. Dormant segments (Realtime/Perception/Media) are data + reference only — no resolver wiring yet.

## Tier abstraction (legacy 4-token alias view — Generative Reasoning T1–T4)

| Tier | Anthropic default | Role | Equivalents (advisory — verify benchmarks before swapping) |
|---|---|---|---|
| **Frontier** | Fable 5 | **Phase 2 Plan synthesis (frame goal, draft spec/ADRs, F-criteria, MECE partition) via the Advisor dispatch ladder when stakes-gated** — `advisor` agent / peer host / already-Fable session; honestly-labeled inline-Opus fallback otherwise (`references/advisor-dispatch-ladder.md`). (Advisor v1 = Phase 2 only; Phase 1 Assess synthesis runs inline as today until v2.) AND verification judgment (plan-critic, scope-auditor, independent-auditor, fix-critique, fact-checker, security-reviewer, overfitting-reviewer, promotion-reviewer) | GPT-5.6 Sol; future models that clear the Thinking-tier contract and prior ceiling |
| **Thinking** | Opus 4.8 | Coordination — build-orchestrator, assessment-orchestrator — and the escalation target for execution (ambiguous spec, 2 consecutive failures, cross-file surprise) and audit/learnings synthesis when Frontier is unavailable | GPT-5.6 Terra for routine orchestration; GPT-5.6 Sol for genuinely ambiguous escalation; Gemini Pro-class equivalents |
| **Code** | Sonnet 5 | Application — apply rule to bounded input, scoped implementation, mechanical refactor, bounded domain assessment | GPT-5.6 Terra, qwen2.5-coder-32B (local); any model within the Code-tier benchmark tolerance |
| **Pattern** | Haiku 4.5 | Recognition — regex/syntactic match, classification into known buckets, log scan, deterministic checklist | GPT-5.6 Luna, Haiku-class or local small/fast models that handle bounded structured work |

**Rule of substitution:** tier A's swap target must score within tolerance of the default on the benchmark relevant to its role. For Code tier that's SWE-bench Verified ≥75% AND tool-use accuracy ≥85%; for Thinking tier that's SWE-bench ≥78% AND ARC-AGI / GPQA Diamond competitive; for Frontier tier that's clearing the Thinking-tier contract AND scoring above the prior-generation Thinking-tier ceiling on at least one of SWE-bench Verified / ARC-AGI / GPQA Diamond; for Pattern tier no benchmark — just "fast and cheap, doesn't hallucinate on bounded structured tasks."

**Why Frontier sits above Thinking for plan + verification (and not for execution):** wrong plans and wrong verdicts compound — a bad plan dispatches N implementers into the wrong work, and a bad verdict ships a regression. The user's standing priority is Accuracy > Speed > Cost (`feedback_accuracy_speed_cost_priority.md`), so the planning and verification surfaces — where one miscall poisons everything downstream — pay the Frontier premium. Execution and coordination stay on Sonnet/Opus because they're either bounded application (Sonnet implementer applies a settled plan) or routing (Opus orchestrator chooses which subagent runs next, with deterministic gates as the safety net).

## Harness amplifies, does not replace, model capability

A better harness (scaffold, tool-set, context budget, verify loop) lifts a weak model *up to* its capability ceiling — it does not push the model *past* it. Treat the harness as a large, routinely-undervalued multiplier, not a substitute for tier. The evidence:

- **Variance decomposition** over 25k+ agent runs / 8 domains attributes ~41.4% of outcome variance to base-model reasoning vs ~1.5% to scaffold (a ~28:1 ratio) — the ceiling is a model property.
- **SWE-agent** raised the *same* GPT-4 Turbo from 3.8% (RAG pipeline) to 12.5% (ACI scaffold) — a 3.3× harness lift, yet still only 12.5% *absolute*. A weak base stays absolutely unreliable on hard tasks no matter the harness.
- Internal dogfood corroboration: after a transport/parser fix unblocked a small local model's tool calls (0→8 parsed), the *model* ceiling remained — it still could not produce compiling Rust.

**Routing consequence (keep as-is):** route hard, open-ended reasoning UP to the frontier tier; route bounded, oracle-checkable work DOWN to local/small models. Do NOT invest harness effort expecting a small/local model to clear reasoning-heavy tasks — that is a ceiling failure, not a scaffold failure, and no amount of harness engineering fixes it. When a reliability failure appears (agent lies about success, doesn't follow through, unsafe action), route to harness/verify/guardrail fixes, not to a bigger prompt or a smaller-model-plus-better-scaffold gamble.

Source: `build-loop-memory/research/2026-07-06-ai-coding-fundamentals-and-harness-claims.md` (Claim 4 — "MIXED, leaning CHALLENGE on the strong form"; variance-decomposition + SWE-agent + internal ledger).

## Provider-swap recipe

Build-loop's agent frontmatter uses Anthropic model aliases (`fable`, `opus`, `sonnet`, `haiku`) because Claude Code is the primary host. To run on a different provider:

1. **Edit the INDEX, not each agent.** `model:` frontmatter is index-DERIVED (generated by `scripts/sync_agent_model_defaults.py`), so do not hand-edit it. To swap providers, reorder the preferred list / change the default for the `(segment, tier)` cell in `references/model-taxonomy.json` (or classify a new model once via `scripts/classify_model_tier.py`), then run `python3 scripts/sync_agent_model_defaults.py --apply` to regenerate every agent's `model:`. The role's `(segment, tier)` is the durable key; the tier determines the substitution target.
2. **Runtime override:** `.build-loop/config.json.modelOverrides` accepts `{ frontier: "<id>", thinking: "<id>", code: "<id>", pattern: "<id>" }`. The orchestrator resolves this through `scripts/model_overrides.py` before dispatching subagents (see `references/model-tier-mapping.md` for full schema). Configs without `frontier` resolve frontier → `fable` by default.
3. **Per-dispatch override:** any orchestrator dispatch may pass `model: <id>` in the subagent prompt to force that call.

The role-and-task table below uses tier names. The Anthropic-default mapping in the right column is illustrative; substitute your equivalents at swap time.

### GPT-5.6 Codex policy

- **Sol** is approved for planning synthesis and gating verification agents: `advisor`, `plan-critic`, `scope-auditor`, `independent-auditor`, `fix-critique`, `fact-checker`, `security-reviewer`, `overfitting-reviewer`, and `promotion-reviewer`.
- **Terra** is approved for ordinary orchestration, bounded implementation, domain assessment, advisory review, and Learn drafting. It is the Codex default for `build-orchestrator`, `assessment-orchestrator`, and `implementer`.
- **Luna** is approved for bounded recognition agents: `mock-scanner`, `recurring-pattern-detector`, and `transcript-pattern-miner`. Prefer a deterministic script when the rule is fully expressible.
- Start at the lowest effort that passes the real verifier: Luna `low|medium`, Terra `medium` (`high` for complex bounded work or first retry), Sol `medium` (`high` for adversarial/security verdicts). Use `xhigh` only after evidence of a miss; reserve `max` and Ultra for rare cases with justified consequence or meaningful independent streams.
- Escalate Luna→Terra when rule application appears; Terra→Sol when ambiguity, high consequence, or repeated verified failure appears. Model size never weakens least privilege, confirmation, sandboxing, or independent verification.

Source: OpenAI GPT-5.6 System Card (2026-07-09), retained in build-loop-memory with the full PDF and routing extract.

## Chat-triggered index maintenance (host-LLM-driven)

The model index (`references/model-taxonomy.json`) is **user-editable and chat-maintainable**. When the user expresses model intent in conversation, recognize it and act on the index directly — this is host-LLM-driven per the repo's "host coding agent is the LLM" rule: you recognize the intent and run deterministic scripts; there is NO vendor API call and NO hard hook. (A `UserPromptSubmit` hook that pre-detects these phrasings is an OPTIONAL future hardening, not required — the LLM recognizing intent is the mechanism.)

**Trigger phrasings (illustrative, not exhaustive):** "check the model(s)", "is there a newer model", "what's the current frontier model", "change the `<tier>`/`<segment>` model", "use `<model>` for `<role>`", "what model is `<agent/tier>` using", "swap `<model>` in", "reorder the preferred list".

**On a CHECK / NEWER intent** ("check the models", "is there a newer model", "what is X using"):
1. Read the index — `python3 scripts/model_taxonomy.py --segment <seg> --tier <tier>` for one cell, or `--json` for the summary.
2. Report the CURRENTLY RECOMMENDED model vs what is AVAILABLE: run `python3 scripts/resolve_agent_model.py <agent> --json` (or `model_resolver.py --segment <s> --tier <t> --json`) and read back `model` + `resolution_path`.
3. If the user names a model the index does not know, OFFER to classify it via the existing host-LLM flow: `python3 scripts/classify_model_tier.py lookup <id>` returns a WebSearch query + parse rubric; you run the search, decide segment + tier, then `record <id> --tier <tier> --segment <seg> --provider <vendor> [--provenance verified]`. No vendor API call — you (the host LLM) do the interpretation.

**On a CHANGE / USE intent** ("use gpt-5.5 for frontier", "change the code model to X", "make sonnet first"):
1. Edit the index `references/model-taxonomy.json` with the smallest change: reorder the `preferred[<segment>][<tier>]` list (capability-rank order — the first available wins), change a cell's default, or add an already-classified model id. A documented jsonpatch-style single-field edit is enough — no helper script needed (KISS). If the model is not yet classified, classify it first (step above).
2. Regenerate the derived defaults: `python3 scripts/sync_agent_model_defaults.py --apply`. This rewrites every affected agent's `model:` from the new index state (only harness-valid tokens are written; a cross-provider recommendation keeps the existing token and is reported).
3. Confirm: `python3 scripts/sync_agent_model_defaults.py --check` returns 0 drift; report the changed agents back.

Dispatch always resolves the role LIVE through `resolve_agent_model.py`, so an index edit takes effect on the next dispatch even before a sync — `sync` only keeps the on-disk `model:` fallback honest.


## When to use this skill

- Choosing `model:` field in an agent frontmatter
- Orchestrator deciding which subagent tier to spawn for a task
- Estimating cost tradeoffs before starting a build
- Deciding whether to escalate mid-flow after failures
- Evaluating whether to swap providers (use the Tier abstraction table above as the contract)

## Evidence base (2026 Q1–Q2)

| Claim | Source | Certainty |
|-------|--------|-----------|
| Sonnet 5 reaches prior Opus-tier quality on coding/agentic work (Code-tier default since 2026-06) | claude-api skill cache (T1 — Anthropic) | ✅ T1 qualitative; no single SWE-bench figure published yet |
| Sonnet 5 uses a new tokenizer — ~30% more tokens for the same text vs Sonnet 4.6 | claude-api skill cache (T1 — Anthropic) | ✅ T1 — re-baseline token budgets, do not reuse 4.6 counts |
| (prior-gen baseline) Sonnet 4.6: 79.6% SWE-bench Verified | Anthropic announcement + SWE-bench leaderboard | ⚠️ T2, single-source; historical calibration |
| (prior-gen baseline) Opus 4.6: 80.8% SWE-bench Verified (1.2pt gap — smallest in Claude history) | Same | ⚠️ T2, single-source; historical |
| (prior-gen baseline) Sonnet 4.6 uses 70% fewer tokens than 4.5 on complex file ops with +38% accuracy | Anthropic Sonnet 4.6 announcement | ⚠️ T2, single-source; superseded by Sonnet 5's new tokenizer |
| Pricing: Sonnet 5 $3/$15 per MTok ($2/$10 introductory through 2026-08-31) | claude-api skill cache 2026-06-24 (T1 — Anthropic) | ⚠️ verify before billing |
| Pricing: Opus 4.8 $5/$25 per MTok input/output | Anthropic pricing page | ⚠️ verify before billing |
| Pricing: Fable 5 $10/$50 per MTok input/output (1M context, capability tier above Opus 4.8) | claude-api skill cache 2026-05-26 (T1 — Anthropic) | ✅ T1 source, advisory until re-confirmed at next billing audit |

## MECE primitive: cognitive type of the task

Before consulting the role table, classify the task by reasoning shape. The MECE cut is the kind of thinking the task requires; lifecycle stage (plan/execute/review) is a second-order cut that often mixes types. Within Synthesis, a second-order cut decides whether the task is a planning/verification decision (Frontier) or coordination/escalation/learnings (Thinking).

| Reasoning shape | Model | What it means | Example tasks |
|---|---|---|---|
| **Planning + Verification synthesis** — frame the goal, draft the spec/ADRs, define F-criteria, MECE-partition the work, then later judge whether a plan, a commit, a fix, a claim, or a security/scope boundary actually holds | **Fable (Frontier)** | The "what to do" and "did it actually work" calls. Wrong calls poison every downstream dispatch. | Phase 2 Plan drafting (reaches Fable via the stakes-gated Advisor ladder; Phase 1 Assess synthesis stays inline until v2), plan-critic, scope-auditor, independent-auditor, fix-critique, fact-checker, security-reviewer, overfitting-reviewer, promotion-reviewer |
| **Coordination + escalation synthesis** — route work between subagents, ladder severity, run causal-tree on stuck iterations, write audit/learnings | **Opus (Thinking)** | The "who runs next" + "why did the rule run out" calls. Deterministic gates backstop the routing. | build-orchestrator, assessment-orchestrator, severity ranking after critic findings, causal-tree after 2 consecutive failures, Phase 6 Learn audit synthesis (when no Frontier escalation needed) |
| **Application** — apply a known rule, spec, or pattern to bounded input; produce an artifact that matches a contract | **Sonnet (Code)** | The "how" call when "what" is decided. Single-correct-answer derivable from a rule. | Implement a commit's owned files per spec, write tests for given F-criteria, mechanical simplify, bounded domain assessment (api/db/frontend/perf), design-contract reconciliation, ui-validator, retrospective-synthesizer, self-improvement-architect drafting |
| **Recognition** — pure regex/syntactic match; classify into known buckets; no judgment | **Haiku (Pattern)** | No gradient — matches or doesn't. | Mock-data scan, log pattern detection, file inventory, cross-run pattern detection, deterministic checklist verification |

**Decision tree:** "Does this task have a single-correct answer derivable from a rule applied to bounded input?" → Yes = Application/Sonnet. Else "Is the answer pure pattern-match?" → Yes = Recognition/Haiku. Else, Synthesis. Then ask: "Is this a planning decision (what to build) or a verification verdict (did it hold)?" → Yes = Frontier/Fable. Else (routing, escalation, audit-synthesis when no verdict is being rendered) = Thinking/Opus.

## Default assignments

| Task | Reasoning shape | Model | effort | Why |
|------|------|-------|--------|-----|
| Frame & plan: goal, ADRs, scope, F-criteria, MECE partition | Planning synthesis | Fable | medium | A wrong plan dispatches N implementers into the wrong work; user's standing priority Accuracy > Speed > Cost |
| Plan-verify deterministic checklist | Recognition | (script) | — | No model; runs `plan_verify.py` |
| Plan-critic adversarial review against rubric+checklist | Verification synthesis | Fable | high | Verification verdict — separation drives quality; verdict gates Phase 3 dispatch |
| Scope auditor (Plan→Execute boundary): trace callers of every modified-API symbol; annotate `caller_audit:` per commit | Verification synthesis | Fable | medium | Cross-file call-path tracing AND a gating verdict on whether a commit is `internal_only`; verification compound risk |
| Code execution — bounded chunk, spec clear | Application | Sonnet | medium | Default workhorse. Spec is settled; apply the rule |
| Code execution — ambiguous spec or cross-file surprise mid-execution | Coordination synthesis | Opus | medium | Escalation target; interpretation cost cheaper than rework |
| Independent-auditor adversarial pass (read-only diff vs rubric at chunk + build scope) | Verification synthesis | Fable | high | Verdict gates the build's outcome line; a missed regression in production-impacting work is the most expensive miss in the loop |
| Severity ranking + recommendation order (given findings) | Coordination synthesis | Opus | medium | Cross-finding routing; no per-finding verdict being rendered, the verdicts are upstream |
| Mock data scanning | Recognition | Haiku | low | Regex only |
| Fact-checking — trace metric → source, judge accuracy | Verification synthesis | Fable | medium | Final read on "is this number real" before report ships; user-trust verdict |
| Fix-critique — pressure-test a proposed fix before "resolved" | Verification synthesis | Fable | medium | Verdict on whether the fix addresses root cause vs symptom; wrong verdict reopens the bug downstream |
| Security-reviewer — adversarial OWASP/ATLAS pass | Verification synthesis | Fable | high | Verdict gates riskSurfaceChange dispatch; missed exposure is the most expensive verification miss |
| Overfitting-reviewer — Goodhart / test-gaming verdict on optimize runs | Verification synthesis | Fable | medium | Verdict on whether optimization is genuine; cheap to wrong-call into a regression |
| Promotion-reviewer — Phase 6 Learn experimental promotion verdict | Verification synthesis | Fable | medium | Gates the move from `experimental/` to `active/`; durable surface |
| Simplify — apply known simplifications | Application | Sonnet | medium | Inline single-use helper, delete dead branch — bounded |
| Debugging — symptom-to-known-pattern match | Application | Sonnet | high | Memory-first gate's "Application until the rule runs out" |
| Debugging — causal-tree after 2 consecutive failures | Coordination synthesis | Opus | high | Synthesis takes over routing when rule-match exhausts |
| Novel architecture decision | Planning synthesis | Fable | medium | Cross-file impact; wrong call compounds |
| Writing user-facing prose (copy, microcopy, errors) | Coordination synthesis | Opus | medium | Tone, restraint, and nuance matter; no verification verdict being rendered |
| Audit / learnings / Phase 6 audit synthesis | Coordination synthesis | Opus | medium | Cross-run routing; promotion-reviewer carries the gating verdict separately |
| Recurring-pattern detection across runs[] | Recognition | Haiku | low | Pattern-match across structured logs |

### Deliberate exceptions (Sonnet retained for cost where the surface is high-frequency advisory)

Two verification-shaped agents stay on Sonnet rather than escalating to Fable. The tension with round-2 evidence ("rubric-application = Sonnet is robust") is real; the user chose Fable for the rest of the verification surface anyway because the compound risk of a wrong verification verdict outweighs the per-call premium. Pins are defaults, not locks — these can be overridden per dispatch or re-tiered after telemetry.

| Agent | Pin | Why retained on Sonnet |
|---|---|---|
| `alignment-checker` | Sonnet | Called once per queue item during autonomous iterate (up to 25× per run). Advisory only — flags drift, doesn't gate. Cost dominates value at this fan-out frequency. |
| `synthesis-critic` | Sonnet | Per-UI-commit WARN-only check. Advisory only — never gates. Frequency × non-gating shape means a cheaper tier is the right tradeoff. |

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

These findings informed the earlier rubric-application=Sonnet split for code review. The current org overrides that for the verification surface specifically — the user chose Fable for verification because a missed verdict at this stage compounds, even though round-2 showed Sonnet rubric-application was substantively robust on a 17-finding plan-critic pass. The exceptions table above (alignment-checker, synthesis-critic) preserves the Sonnet split where the surface is high-frequency advisory and non-gating.

## Escalation triggers (Sonnet execution → Opus, NOT to Fable)

Execution escalates to **Opus**, not Fable. Fable is reserved for planning and verification; execution under genuine ambiguity is a coordination call (interpret the spec, route to a new chunk, decide whether to re-plan) that the orchestrator owns.

- 2 consecutive failures on the same chunk after a retry at effort=high → respawn implementer at Opus
- Spec is ambiguous and interpretation will materially change implementation → Opus
- A cross-file architectural decision surfaces mid-execution that wasn't in the plan → Opus, then route back to Plan if the decision changes the MECE partition
- Critic flags a "strong-checkpoint" finding that requires judgment, not just a fix → Opus
- Novel error pattern not found in `.build-loop/issues/` or debugging memory → Opus
- Task produces user-visible prose where tone and restraint are load-bearing → Opus

If the ambiguity surfaces a **planning** problem (the original plan no longer fits) rather than an execution problem, route back to Phase 2 Plan — Fable re-plans, then execution resumes on Sonnet/Opus.

## Techniques that work

- **Self-refine with external verification** (tests, lint, type-check). External oracle is non-negotiable — without it, self-refine is circular.
- **Adversarial critic loop** (writer agent + read-only reviewer agent). Separation is what makes it work. Same model reviewing its own output doesn't catch errors.
- **Best-of-N sampling with self-certainty voting** on HARD chunks only (flagged by plan or first-pass failure). N=3. Cost = ~3x Sonnet, still under 1.5x single-pass Opus.
- **Test-time compute** (effort=high default, effort=xhigh on retry — Sonnet 5 is the first Sonnet with `xhigh`; coding/agentic work wants high/xhigh per claude-api T1). Easier problems benefit from revisions; harder problems need parallel sampling — not just more thinking on one path.
- **Plan-then-execute split** (Opus plans once, Sonnet executes many). Established pattern. Amortizes Opus cost across N subagent calls.

## Techniques to avoid

- **Multi-agent debate** (3+ agents argue toward consensus). ⚠️ ICLR 2025 MAD analysis shows majority voting captures most gains; debate adds cost without consistent wins. Use simple voting instead.
- **Self-critique without adversarial separation**. A model editing its own output won't reliably catch its own errors. Use a separate read-only reviewer or rely on external tests.
- **Chain of Density**. Summarization-specific technique; not applicable to code work.
- **Best-of-N by default**. Only on hard chunks. Blanket best-of-N wastes tokens on easy tasks where effort=high is sufficient and cheaper.

## Cost math quick reference

> ⚠️ **Advisory only.** The numbers below are directional heuristics based on single-source token-profile estimates and public pricing as of the skill's last update. They are **not** verified against real usage telemetry and should not be used as hard routing logic. Treat them as "this tier costs roughly this much more than that tier," not as commitments. Pricing, token profiles, and model output lengths all drift over time. Before using these ratios in any cost-minimization decision, pull actual usage data from the last 30 days of builds and re-derive the numbers for your workload.

**Price ratios only** (T1, claude-api — per-MTok, in/out): Fable 5 $10/$50 · Opus 4.8 $5/$25 · Sonnet 5 $3/$15 ($2/$10 intro through 2026-08-31) · Haiku 4.5 $1/$5.

| Pair (same task) | Price ratio | Basis |
|---|---|---|
| Sonnet 5 vs Opus 4.8 | **~0.6x** | $3/$15 ÷ $5/$25 = 0.6 in & out. Sonnet 5 and Opus 4.8 share the **same** new tokenizer (T1), so token counts are comparable — the ratio is price-driven, not token-driven |
| Sonnet 5 vs Fable 5 | **~0.3x** | $3/$15 ÷ $10/$50 |
| Haiku 4.5 vs Sonnet 5 | **~0.33x** | $1/$5 ÷ $3/$15 |

> ⚠️ **These are price ratios, not measured task costs.** Real per-task cost also depends on token volume and effort — running Sonnet 5 at `high`/`xhigh` (the new default + escalation rung) raises its output-token count, narrowing the gap to Opus. A *measured* multiplier cannot yet be stated: the cost ledger (`~/.bookmark/cost-ledger.jsonl`) is currently dominated by local-model rows with no Anthropic build telemetry. **Re-derivation trigger:** once the ledger carries real Sonnet-5 + Opus build rows, pull the last 30 days and re-derive measured task-cost multipliers per workload.
>
> The retired "~0.3x single-pass Sonnet" figure was a Sonnet-**4.6** token-profile artifact ("70% fewer tokens than 4.5") and does **not** carry to Sonnet 5 — whose new tokenizer emits ~30% *more* tokens per unit text than 4.6. That ~30% delta matters only when comparing Sonnet 5 to the old 4.6 numbers, **not** to Opus 4.8 (shared tokenizer).

❓ Best-of-N + critic vs single-pass Opus on SWE-bench has not been directly benchmarked on Sonnet 5.

**How to convert these into routing decisions**: don't. Use the numbers to sanity-check a tier choice after the fact ("was this worth the 5x?"), not to justify forcing a model swap. When real telemetry disagrees with this table, trust telemetry and file an issue to update the table.

## How the build-loop uses this

**Fable plans (when stakes-gated) and verifies. Opus coordinates. Sonnet executes. Haiku recognizes.**

Phase 2 Plan synthesis reaches **Fable** through the **Advisor dispatch ladder** when stakes-gating trips (`synthesisDensity > 5`, `riskSurfaceChange`, `stakes >= medium`, or `dispatch_tier: frontier`): the orchestrator dispatches the `advisor` agent (Rung 1), routes to a peer host (Rung 2), or — if its own session is already Fable — synthesizes inline at Frontier (Rung 0). When no trigger fires or no dispatch path is reachable, the orchestrator synthesizes the plan **inline on its own model (Opus)** and labels it honestly (Rung 3 = today's behavior; the floor equals current state). So "Fable plans" is the *guarantee on high-stakes plans*, with an honestly-labeled inline fallback otherwise — not unconditional. Full protocol: `references/advisor-dispatch-ladder.md`. The Advisor frames the goal, drafts the spec/ADRs, sets F-criteria, and MECE-partitions the work. The orchestrator (**Opus**, `build-orchestrator`, `assessment-orchestrator`) coordinates: it routes dispatches, runs deterministic gates, manages parallel fan-out, walks the Advisor ladder, and handles the escalation ladder. Phase 3 implementer subagents run on **Sonnet** at effort=high (default workhorse; xhigh on hard/code-heavy chunks) → external verification gate (tests/lint/types) → adversarial **Fable** verification surface (`plan-critic`, `scope-auditor`, `independent-auditor`, `fix-critique`, `fact-checker`, `security-reviewer`, `overfitting-reviewer`, `promotion-reviewer`). On a first execution-problem chunk failure, retry the same Sonnet 5 implementer at **effort=xhigh** (intermediate rung — near-Opus coding at lower cost) before paying for Opus; a strong-checkpoint finding or a 2nd consecutive failure after the xhigh retry escalates to **Opus** for judgment; if the failure traces back to a planning miss, route back to Fable to re-plan. See `agents/build-orchestrator.md §Escalation Triggers`. The **tier mapping** is the policy; the cost numbers above are advisory context, not the basis for overrides.

Haiku is only used for Phase 7B mock scanning and recurring-pattern detection across `runs[]`. Never for reasoning tasks.

## Pin vs inherit in agent frontmatter

Not every agent should hard-pin its model. Use this rule:

- **Pin** (`model: fable | opus | sonnet | haiku`) when the task has a clear right tier and cost/quality drift from user's session choice would be a bug. Examples: `plan-critic` / `independent-auditor` / `scope-auditor` / `fact-checker` / `fix-critique` / `security-reviewer` / `overfitting-reviewer` / `promotion-reviewer` (Fable — verification verdicts gate downstream work), `mock-scanner` (Haiku, pattern matching only), `build-orchestrator` and `assessment-orchestrator` (Opus, coordination at plan/review boundaries), `implementer` (Sonnet, default execution workhorse).
- **Inherit** (`model: inherit`) when user intent should flow through. The user's main-session choice is itself a cost/speed preference; respect it. Pair with a "recommended: X" note in this skill rather than forcing via frontmatter. Example: `root-cause-investigator` — recommended Opus on causal-tree work, but inherit honors whatever tier the user picked upstream.
- **Override mechanism**: users can override any pin by passing `model:` when spawning the agent or by editing the frontmatter. Pins are defaults, not locks. The deliberate exceptions documented above (`alignment-checker`, `synthesis-critic` on Sonnet despite being verification-shaped) are exactly this kind of cost-vs-judgment pin and can be lifted if telemetry says so.

Forward-compat note: pinned family aliases (`fable`, `sonnet`, `opus`) auto-track latest versions in their tier (e.g., Sonnet 4.6 → 5, Opus 4.7 → 4.8, Fable 5 → 6). `inherit` additionally picks up brand-new tiers (e.g., a future Flash-class model) without frontmatter edits.

## Limitations of this guidance

- ⚠️ Sonnet 4.6 token-efficiency claim is single-source (Anthropic announcement). Treat as directionally correct, not proven; superseded for the active default by Sonnet 5's new tokenizer (~30% more tokens/text, claude-api T1).
- ❓ No single published SWE-bench Verified figure for Sonnet 5 yet; the Code-tier capability claim rests on the T1 qualitative "reaches prior Opus-tier coding/agentic quality." Re-confirm with a benchmark when available.
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

Always resolve the final tier to a concrete model before writing the dispatch
cost-ledger row:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/model_overrides.py \
  --workdir "$PWD" \
  --tier code \
  --fallback sonnet \
  --json
```

Accepted tiers: `frontier` (default `fable`), `thinking` (default `opus`), `code` (default `sonnet`), `pattern` (default `haiku`). Configs without `frontier` resolve frontier → `fable` so older repos keep working without edits.

Full contract and routing matrix: `~/dev/research/topics/llm/llm.build-loop-router-integration-2026-04.md`
