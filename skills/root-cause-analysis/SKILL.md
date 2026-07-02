---
name: root-cause-analysis
description: Blameless root-cause analysis that produces durable system levers, not blame or one-off patches. Use AFTER a failure/regression/wrong-output/near-miss when you need to explain why it existed AND why it escaped controls, then land the smallest system change that prevents recurrence. Tiered L0/L1/L2 by impact. Distinct from debug-loop (which fixes the live bug); this is what debug-loop's report step and the recursive-retrospective §8 delegate to. Agent-invoked (no dedicated command); the design is optimized for agent use: structured tiered (L0/L1/L2) output, primarily reached by delegation from debug-loop, recursive-retrospective, and the RCA agents; a human can still reach it by natural-language request after a failure. NOT for searching/storing past incidents — that is `debugging-memory` (`{op:search|store}`).
user-invocable: false
---

# Root Cause Analysis

Diagnose failures without blame, identify durable system levers, and produce corrective actions that prevent recurrence. A valid RCA explains BOTH the **creation path** (why the bad condition existed) and the **escape path** (why it reached the user/repo/system despite controls). The goal is the smallest durable system change that would have prevented, detected, contained, or reversed the failure — not assigning fault.

## Boundary (read first — avoids overlap)
- `debug-loop` / `debugging-memory` = **live, find-and-fix THIS bug now** (iterative investigate→fix→verify). 
- `root-cause-analysis` (this) = **blameless post-failure analysis** → durable lever + actuator + regression artifact + spread check. It runs *after* the fix, or on a class/pattern, and is what debug-loop's report phase and `recursive-retrospective` §8 should invoke instead of carrying their own mini-RCA.

## The three prompts (modular)
1. **General RCA** — `references/root-cause-analysis/01-rca.md`: any failure. Tiered L0 (log) / L1 (mini) / L2 (full).
2. **Agentic Coding RCA** — `references/root-cause-analysis/02-agentic-rca.md`: extension of Prompt 1 for AI-coding-agent failures (attribution gate, agentic failure modes, loop-fix vs code-repair). It is Prompt 1 **+ deltas**, not a restatement.
3. **Mini-RCA** — `references/root-cause-analysis/03-mini-rca.md`: lightweight L1 for low-risk issues.
4. **Judge** — `references/root-cause-analysis/04-judge.md`: independent evaluator with a mandatory verification gate (a claim is `FACT` only if checked against source). NEW vs the source suite — closes the "labeled FACT but never verified" gap.

## Hardening applied (vs the source RCA suite)
- **Level↔schema binding.** L0 = 3 fields; L1 = Mini-RCA; only L2 uses the full schema. The full output schema does NOT apply to L0/L1 (the source "use this exact structure" fought the tiering).
- **Density governor.** Omit a section that yields no signal; never pad an empty table. Shorter-with-signal beats exhaustive.
- **Verify-before-FACT.** A causal claim may be tagged `FACT` only if independently checked against its cited source this session; otherwise `INFERENCE`/`ASSUMPTION`.
- **Tool-bound spread check.** "Where else could this happen?" is a `grep`/NavGator-impact job, not prose — run the scan and cite hits.
- **Owner optional in agent contexts.** The owner of a durable fix is often a gate/hook, not a person; `actuator` + `regression artifact` are the real closure, not `owner`/`due-date`.
- **Mini-RCA tree-escalation.** If a second independent contributor appears, escalate from the linear four-whys to the L2 causal map.

## Native strengths preserved (the reason this suite is worth keeping)
- **Creation path + escape path** duality (control existed? fired? ignored/misread/bypassed/too weak?).
- **Action Strength Hierarchy** (eliminate > substitute > forcing-function > standardize > automate-detection > checklist > train/doc). "Be more careful" / docs-as-sole-fix is banned unless risk is low and no stronger lever is feasible.
- **Lever + Actuator** (where the fix lands vs what makes it fire) — the dormant-fix antidote.
- **Banned closures**: human error · agent error · be more careful · edge case · quirk · cosmetic · one-off · works now · "fixed in code" without prevention.
- **Regression artifact** proving old-behavior-fails / new-behavior-passes.
- **First Attribution Gate** (agentic): check task/context/loop/tool/codebase/verification/review BEFORE blaming the agent.

## Model tiering
- L2 RCA + the judge are Frontier-tier (Fable); judge MUST run independent of the analysis author. Frontier-unavailable → Opus fallback, never Code tier.
- L0/L1 can run at the executor tier.

## Output homes
- Corrective actions with a memory/rule target route through the canonical `memory_writer.py`; regression artifacts (tests/evals/policy/checklist) commit in-repo.
