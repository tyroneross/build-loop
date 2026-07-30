---
name: recursive-retrospective
description: Run a recursive-learning retrospective on an app/agent/plugin/build-loop project — analyze build history, behavior, and current state to extract reusable learning objects and encode them into memory, agents, skills, plugins, evals, preflights, and approval gates. Use after a substantial build, when reviewing a project's trajectory, when deciding preserve/refine/redirect/reset, or when converting a session into durable system improvement. Discovery-first (observe behavior → cluster patterns → encode learning); RCA is one module, not the whole job.
user-invocable: false
---

# Recursive Learning Retrospective

A three-stage pipeline that turns a project's history into durable, encoded system improvement. Discovery-first: observe revealed behavior, cluster patterns, then encode learning — categories are seed scaffolds, never closed taxonomies (preserve emergent findings).

> **v2 (2026-06-18) — test-validated.** This revision was hardened against a live run: the full pipeline was executed on a real project (ross-labs-astro) and scored by an independent judge at 4.27/5, "Accept with revisions." The judge's penalties are fixed here (see Changelog). The v1 structure (peer draft `feat/recursive-retrospective@1c24d4a`) is preserved; only the validated fixes are layered on.

## When to use
- After a substantial build/session, to extract what the system should learn.
- When deciding whether to preserve / refine / redirect / reset a mid-build project.
- To convert a retrospective into concrete learning objects (memory, agent rules, skills, plugin/app behavior, evals, preflights, approval gates).
- NOT only for failures — RCA is one diagnostic module used when there are failures, regressions, steering loops, brittle decisions, or outcome gaps.

## The pipeline
1. **Run** — `references/01-retrospective.md` (Prompt 1): the recursive-learning retrospective. Produces maturity classification, spec→current→desired comparison, behavior/workflow discovery, steering mining, learning objects, a selective RCA module, preflight/hard-gate analysis, a counterfactual simulation, a prioritized (ordinal) roadmap, layered recommendations, a from-scratch option, emergent patterns, and an executive summary.
2. **Capture** — `references/02-learning-capture.md` (Prompt 2): a PACKAGER/DEDUPER of Prompt 1's §7 + §12. It does not re-extract from scratch; it dedupes the learning objects Prompt 1 already produced, adds only net-new ones it missed (flagged), and emits the copy-ready encoding package.
3. **Judge** — `references/03-judge.md` (Prompt 3): an INDEPENDENT evaluator scores the retrospective on 11 criteria and returns accept / accept-with-revisions / reject. It MUST verify headline claims against source before scoring evidence grounding.

## Operating rules (carried through all three stages)
- **Discovery flexibility:** start from evidence before classification; add/rename/split/merge categories when evidence requires; use Other/Emergent; preserve unexpected findings.
- **Density governor:** reward density, not completeness. Collapse a low-signal section to a single line; omit a conditional section entirely when it yields no non-obvious finding. A shorter retrospective that drops empty scaffolding scores HIGHER than an exhaustive one. Do not pad a section to look complete.
- **Cross-reference, don't restate:** assign each finding a stable id (LO-1, RCA-1, …) once, then reference the id in later sections instead of repeating the finding.
- **Evidence discipline:** separate explicit statements from revealed behavior; mark unknowns `UNKNOWN — evidence not available` and unverified claims `TAG:INFERRED`.
- **Memory discipline:** do not encode one-off comments as durable memory unless explicitly framed durable or recurring.
- **Appropriate autonomy + approval authority:** the dispatching (main) agent holds approval for this pipeline's own outputs — SAFE, reversible encodes (memory / note / skill-source / agent-instruction) auto-persist under that authority and are never bounced to the human. Reserve human gates for the hard-gate taxonomy only: security, privacy, cost, live/production deploy, irreversible/destructive actions, or promoting an experimental artifact into a globally runtime-active skill/agent.
- **No false precision:** rank by ordinal priority (P0/P1/P2) with qualitative justification; do not emit multiplied point-scores that imply a calibration the inputs cannot support.

## Model tiering
- Stage 1 (retrospective = assessment) and Stage 3 (judge = verification verdict) are Frontier-tier work (Fable) per the model org. Stage 3 MUST run in a context independent of Stage 1's author (external evaluator). Frontier-unavailable → Thinking-tier (Opus) fallback, never Code tier.
- Stage 2 (capture) can run at the executor tier.

## Output homes
- Retrospective + learning-object package → the project's retrospective lane and (for cross-project learning) build-loop-memory via the canonical `memory_writer.py`.
- Learning objects with encoding target = memory / project-note / skill-source / agent-instruction are SAFE + reversible: the **dispatching (main) agent approves them and they persist automatically** — do not bounce a medium-confidence memory note to the human. Human approval is reserved for the hard-gate taxonomy only (security / privacy / cost / live-deploy / irreversible-destructive / promoting an experimental artifact into a globally runtime-active skill or agent).

## Changelog (v1 → v2; each fix traces to the live judge run)
- **Density governor + conditional sections** — judge flagged §11 (counterfactual) and the near-1:1 §5 behavior→LO mapping as forced-completeness padding. §11 and parts of §5/§15 are now conditional; the density rule is explicit in the operating rules and acceptance criteria.
- **Ordinal priority replaces the multiply formula** — judge flagged `(F×I×R×C)/Difficulty` point-scores (250, 200, 160) as false precision. §12 now ranks P0/P1/P2 with F/I/R/C/Difficulty kept as qualitative justification columns only.
- **Cross-reference rule** — judge flagged §5/§6/§7/§15 reformatting the same findings 3–4×. Findings now carry ids and are referenced, not restated.
- **Prompt 2 reframed as packager/deduper** — Prompt 1 §7 already emits a full learning-object table; v1 Prompt 2 re-extracted from scratch (double work, divergence risk). v2 Prompt 2 ingests §7+§12 and dedupes.
- **Prompt 3 mandates verification** — in the live test the judge only verified facts because it was told to. v2 §1 (evidence grounding) requires independent tool-verification of ≥3 headline claims when source is available, and caps the grounding score at 3 if verification was possible but skipped.
