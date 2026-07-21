---
feature: model-prompting-profile
run_id: bl-20260721T203342Z-claude_code-835164
modifies_api: true
scope_auditor_status: pending
stakes: medium
authored_by: advisor (frontier tier, dispatched via advisor ladder — synthesisDensity 7 > 5, stakes medium)
revised: 2026-07-21 re-plan (planning-input miss — F10 stale in-repo tier block; F7 refined to hardcoded-path sync-time read; see §Amendment log)
---

# Plan: Model Prompting Profile

<!-- checklist
Item 1 — Auth guard: N/A: no server routes — Python scripts + JSON data + markdown references only.
Item 2 — External APIs: N/A: no new external API calls. Source material (Willison transcript) already fetched and verified in Phase 1 (F1).
Item 3 — Rate-limit criterion: N/A: no paid API calls introduced.
Item 4 — Discoverability: N/A: no UI surface. Agent-facing discoverability = the profile rides the resolver envelope (C2) and is documented in CLAUDE.md §Model Tiering + skills/model-tiering (Commit 5).
Item 5 — Server/client boundary: N/A: no web app.
Item 6 — Concurrency: N/A: read-only data lookups; taxonomy JSON edited only by this run's single-writer orchestrator commit path.
Item 7 — Observability: resolve_agent_model.py's JSON envelope gains `prompting_profile` — the resolution_path already logs per-dispatch; profile presence is directly observable in every dispatch envelope. No new metric warranted (KISS).
Item 8 — Input validation: N/A: no new routes. Loader validation of the profiles block lives in scripts/model_taxonomy.py (T-02).
Item 9 — Stable ID traceability: trace chains U-01 → F-01 → D-01 → T-01/T-02; U-01 → F-02 → D-01 → T-03/T-04/T-05; U-01 → F-03 → T-06; U-01 → F-06 → T-09; U-02 → F-05 → T-07. All P0s carry T-IDs (see Spec Object).
Item 10 — JSON spec object: present — §Spec Object (JSON).
Item 11 — Blocking-and-novel question gate: no open questions pass the blocking-and-novel test; all unknowns resolved as labeled assumptions (see §Assumptions).
Item 12 — Low-reversibility ADRs: ADR-01 (storage location — schema consumed by ≥2 repos, low-reversibility), ADR-02 (tier as the profile key). Both below.
Item 13 — Analytical lens: Pugh — option selection among concrete storage/consultation candidates (taxonomy block vs new file vs skill vs cross-repo reference), scored against C1–C6.
Item 14 — Handoff document: docs/plans/2026-07-21-model-prompting-profile.handoff.md (sibling file, written with this plan).
Item 15 — Synthesis dimensions: N/A per Item-15's UI definition (no UI surface); the run-level 7-dimension synthesis block from goal.md is resolved in §Synthesis Dimensions (resolved).
Item 16 — Risk reason: omitted — none of the five canonical values cleanly applies. The taxonomy schema is cross-repo-consumed but via a sync-time read (F7 refined: registry/sync.py live-reads the file during offline/on-demand sync), not a runtime protocol or persistence migration; stretching a canonical value would be dishonest. Stakes-gating already routed this plan to Frontier.
Item 17 — UI input/output contract: N/A: no UI surface.
Item 18 — Dispatch tier per work item: declared per chunk in §Six-Commit Table; all execution chunks are `sonnet` because the judgment-heavy artifacts (profile content, disposal text) are authored IN THIS PLAN — implementers transcribe and wire.
Item 19 — Env-var manifest: N/A: no new external service.
Item 20 — Capability gap map: present — §Capability Gap Map.
Item 21 — Single-shot build guardrails: present — §Single-Shot Build Guardrails.
Item 22 — Read-before-edit map: present — §Read-Before-Edit Map.
-->

## Goal

Build-loop resolves *which* model runs each agent (`scripts/resolve_agent_model.py`, F4) but nothing shapes *how that model is prompted* — Fable and Haiku receive identically-shaped briefs, and the measured constraint gradient runs backwards from the evidence (F2: opus-tier agents carry 12.5 negatives/100L vs sonnet's 6.0). This plan adds a **per-tier prompting profile** as a typed block inside the existing model taxonomy, rides it on the resolver envelope every dispatch already crosses, flexes the implementer-brief template by tier, **retires the contradictory tier block already sitting in `skills/build-loop/fallbacks.md` (F10) so exactly one tier-keyed prompting authority survives**, and delivers a written spec for RossLabs-AI-Assistant to consume the same contract via its existing sync pattern (F7). User value: every dispatched subagent gets a prompt shaped to its judgment capability, automatically, with no new consultation step to forget.

Five deliverables, in dependency order: taxonomy block → resolver envelope → brief-template flex → disposal of the stale block → ai-assistant spec. The disposal is not housekeeping; without it the build ships two tier-keyed authorities whose rung tokens are shifted one step apart.

## Architecture note (architectural-class gate)

This build establishes a **cross-surface contract**: `prompting_profiles` (D-01) is a tier-keyed typed block consumed by build-loop's dispatch path now and mirrored by RossLabs-AI-Assistant later. Design intent: the profile is **data riding an existing resolution path**, never a parallel mechanism — the taxonomy already owns the tier axis, the resolver already crosses every dispatch, so the profile inherits both the single-source-of-truth property and automatic consultation for free. Why this shape over alternatives: ADR-01 and ADR-02 below. Boundary: build-loop owns the schema; consumers mirror at sync time (F7 refined: `registry/sync.py` live-reads the taxonomy file during its offline/on-demand sync — never per-dispatch), and the single-source-of-truth property now also requires **disposing of the pre-existing contradictory tier block** in `skills/build-loop/fallbacks.md` (F10, Commit 6).

## Verdict on the "it's only wiring" hypothesis (C6 — evaluated explicitly; REVISED per re-plan 2026-07-21)

**Rejected as stated — and the prior verdict here was itself wrong on one claim.** The prior text claimed the content "is NOT already covered anywhere in build-loop"; F10 (verified by direct read this run) falsifies that. `skills/build-loop/fallbacks.md` §prompt carries a `Calibrate to model tier:` block (~L418-422) that IS an in-repo per-tier prompting profile — and it is defective four ways: (a) **tier tokens shifted one rung** against `references/model-taxonomy.json` (block: Opus=T1/Sonnet=T2/Haiku=T3; taxonomy: fable=T1/opus=T2/sonnet=T3/haiku=T4 — same token space, different meanings, so a caller resolving `tier: T2` from the new envelope and reading this block gets Sonnet-shaped guidance for an Opus dispatch); (b) **stale ids** ("Opus 4.6", "gpt-4-mini"; Fable absent); (c) **directionally wrong on `examples`** — prescribes "1-2 few-shot examples help" for its mid tier where F1 says Opus-class is precisely where removing examples helped; (d) **silent on `constraint_posture`**, the largest measured defect (F2). Wiring status: referenced only from the `triggers.promptAuthoring` fallback path (`references/capability-routing.md:57`; `skills/build-loop/references/capability-routing.md:110,191`; generated `plugin-artifacts/codex/**` mirrors) — **zero dispatch paths consult it.**

**Revised verdict: stale content exists and must be disposed of + small new content + pure wiring.** Three parts, named: (1) **DISPOSAL** — the fallbacks.md tier block is surgically replaced with a pointer to `prompting_profiles` (Commit 6; leaving it would ship two contradictory T-keyed prompting authorities in one repo, the exact F9/DRY breach that D-01 would otherwise make worse); (2) **NEW CONTENT** — one JSON block, ~40 lines, authored in §Profile Content, genuinely required (the F10 block is wrong, not reusable; prompt-builder's `tier-calibration.md` remains a different repo, 3-tier, do-not-wire per F6; `gpt-5-4-prompting` remains one-provider prose per F5); (3) **WIRING** — envelope key, brief-template flex, docs, mirror spec, all on existing surfaces. The build is 6 commits, net ~+150/−5 LOC plus regenerated `plugin-artifacts/codex/**` mirrors.

## Locked Decisions

| # | Decision | Rationale |
|---|---|---|
| L1 | Analytical lens: Pugh — concrete option selection (storage × consultation candidates) scored against C1–C6 | Item 13 |
| L2 | Profile is keyed by **capability tier rung** (T0–T5), not per-model | ADR-02; falsifier named in §Falsifier |
| L3 | Storage: new `prompting_profiles` top-level block in `references/model-taxonomy.json` | ADR-01 |
| L4 | Consultation: additive `prompting_profile` key on the `resolve_agent_model.resolve()` envelope | F4 — the one place every dispatch already passes; C2 requires no-second-call |
| L5 | Freshness: **structural/event-driven** — tier-keyed profiles are inherited by any model classified into a rung; loader validation flags an unprofiled ladder rung. No calendar contract (api-registry's 7-day model rejected: profiles stale on taxonomy events, not time) | C3 |
| L6 | Behavioral evals: **follow-on with a named trigger** (see §Out of scope) | goal.md permits; this run changes no agent prompt bodies, so there is nothing to A/B yet |
| L7 | ai-assistant consumes via **sync-time read**: `registry/sync.py:104-106` HARDCODES the absolute path to build-loop's `references/model-taxonomy.json` and live-reads it on every sync run — offline/on-demand, never per-dispatch. The declared `model_taxonomy_source` pointer (`registry.json:45` / `overlay.json:24`) is unread documentation. The spec must NOT promise a live runtime read — the sync is the consumption moment | F7 refined this run: hardcoded-path read at sync.py:104-106; `_discover_models` (L433-446) projects `{segment, tier}` only into `capability_fingerprints` |
| L8 | Brief template keeps its T3 rigor — round-3 evidence (repo-measured) shows worked examples + tight caps HELP Sonnet-tier implementers. The defect is invariance, not the template's content. Tier variance flexes the shape at T2 and above | F3 + implementer-brief-template.md §"Why each section matters" |
| L9 | The five profile fields are grounded in F2/F3/F8 (named, observed failures in this repo); the Willison transcript is the *explanation*, not the authority | F9 — CLAUDE.md KISS+DRY governing principle |
| L10 | Dispose of the stale fallbacks.md tier block (F10) in the same run that ships D-01, by surgical rewrite-to-pointer — never leave two contradictory T-keyed prompting authorities in one repo | F9/DRY; the shifted-token collision is the failure class this plan exists to end. Disposal text authored in §Disposal spec |

### ADR-01 — Store the profile inside `references/model-taxonomy.json` (low-reversibility: schema consumed by ≥2 repos)

**Alternatives considered (Pugh, baseline = new `references/model-prompting-profiles.json`):**

| Option | C2 auto-consult | C3 freshness | C4 extends | Cross-repo (F7) | Verdict |
|---|---|---|---|---|---|
| A. New JSON file | needs new loader + new resolver read | needs new sync check | ✗ parallel surface | second path for sync.py to read | rejected |
| B. **Taxonomy block** | rides existing loader + resolver | free — tier-keyed inheritance; loader validates rungs | ✓ | already read by `registry/sync.py` at sync time (same file, same hardcoded path) | **selected** |
| C. Build-loop skill (gpt-5-4-prompting shape) | prose-dependent — agent must remember (F5's own wiring depends on the agent remembering; exactly the decay mode C2 forbids) | manual | ✗ new surface | not machine-consumable | rejected |
| D. Extend prompt-builder `tier-calibration.md` | different repo, 3-tier scheme doesn't map to 7 rungs, "do not wire in yet" note stands (F6) | manual | ✗ wrong repo | n/a | rejected |
| E. `prompting_profile:` key in each agent's frontmatter | resolver already reads frontmatter, so consultation is free | ✗ manual — a tier reclassification must be hand-propagated to every affected agent | ✓ extends an existing surface | ✗ `registry/sync.py` mirrors the taxonomy file, not agent markdown — no cross-repo path | rejected, dominated |

**Why option E is listed despite losing:** it is the option a future maintainer reaches for first, because `sync_agent_model_defaults.py` rewrites only the `model:` line (`apply_agent()`, L119), so a `prompting_profile:` key would survive sync untouched. It loses on three counts: ~29 denormalized copies of a 5-field record (DRY), silent drift the moment a model is reclassified into a different rung (the exact failure mode F10 demonstrates), and no route to the second consumer. Recording the rejection here so the question is not re-opened without new evidence.

**Tradeoff accepted:** the taxonomy file grows ~40 lines and its schema version bumps to 2.1.0 (additive). **Rollback:** delete the block + the accessor + the envelope key — additive everywhere, no consumer breaks (existing consumers read `model`; ai-assistant's `_discover_models` walks `models` records only, so it ignores the block until the spec lands).

### ADR-02 — Tier is the profile key, not model id

**Alternatives:** per-model profiles (44 records to author and maintain, and the evidence does not discriminate within a rung — F1's claims are frontier-vs-older, not model-vs-model); per-(segment,tier) profiles (no observed evidence that segment changes prompting posture; add later if observed). **Cross-repo payoff (F7 refined — the strongest argument for this key):** ai-assistant already mirrors `tier` for 44 models via `capability_fingerprints` (`sync.py` `_discover_models`, meta `{segment, tier}`). Because the profile is tier-keyed, the consumer needs only the small profile TABLE plus the tier it already holds per model — a join, not 44 new per-model records — which makes the second run genuinely small. **Tradeoff:** if two same-rung models demonstrably need opposite postures, tier is the wrong key — that is the central falsifier (§Falsifier). **Rollback:** move `by_tier` to `by_model` overrides layered on tier defaults; accessor signature survives.

## Approach Lenses

**Clean-sheet best answer:** prompting posture is a property of the *(model, task-shape)* pair, learned from behavioral evals — every dispatch consults an eval-backed per-model profile, and every profile claim is continuously re-verified as models rotate (the Anthropic mechanism in F1: "the eval base... so that new models can be a drop-in replacement").

**Current-constraints answer:** build-loop has zero behavioral evals for prompting posture, one verified external source, and three repo-measured defects (F2, F3, F8). The honest current build is a tier-keyed profile whose frontier half is source-verified and whose cheap half is explicitly marked weakly-evidenced (F1's own caution: "We haven't been able to eval it"), riding the existing resolver.

**Bridge/backcast:** ship the tier-keyed profile now with per-field `confidence` markers baked into the data (so the artifact itself says what is verified vs inferred); the named eval trigger (§Out of scope) converts folklore fields into eval-backed ones the first time a profile-driven agent-prompt rewrite lands — at which point per-model overrides become justifiable if evals discriminate within a rung (ADR-02 rollback path).

## Path A vs Path B (pay-it-forward gate — typed contract, ≥2 consumer repos)

**Path A (expedient):** ship the profile as a prose reference (`references/model-prompting.md`), tell agents to read it. Cheaper today; decays exactly like F5's prose wiring and fails C2's "verified by reading the dispatch path, not by a doc" — and F10 is now the in-repo existence proof of that decay mode: a prose tier block that went stale ("Opus 4.6"), drifted out of token-space alignment with the taxonomy, and was consulted by nothing.

**Path B (pay-forward, selected):** typed JSON block with schema comment + per-field evidence pointers + `confidence` markers, accessor in the loader, envelope key on the resolver, and a written mirror-contract spec for the second consumer. Delta cost over Path A: ~1 commit. The second consumer is already named (RossLabs-AI-Assistant), so the pay-forward premium is immediately collected.

## Profile Content (authored here — implementers transcribe, not invent)

Five fields. Each exists because a named, observed failure or a verbatim source claim demands it; rejected candidates listed after.

| Field | Values | Evidence |
|---|---|---|
| `examples` | `omit` / `minimal` / `worked` | F1: "removing examples was extremely helpful... more creative than the examples we gave it" (frontier). F3/L8: repo round-3 evidence that worked examples HELP T3 implementers |
| `constraint_posture` | `contextual` / `mixed` / `directive-ok` | F1: "fewer hard constraints, more context, and fewer instructions overall"; F2: measured backwards gradient (opus 12.5 neg/100L) |
| `edge_case_handling` | `delegate` / `enumerate-known` | F1 Cat Wu: "this statement is 90% true, but there's a real 10%..." — frontier gets the rule + the falsifier and handles the 10%; lower tiers get known edge cases spelled (F3 schema-warnings section is the repo-measured win for this at T3) |
| `rationale` | `required` / `recommended` / `optional` | F8: eleven directives shipped without their why — every correction fixed the application, not the rule. The verification rewrite (F1) is the canonical directive→contextual worked example |
| `prompt_budget` | `compressed` / `standard` / `full` | **Repo-observed cost** — `references/implementer-brief-template.md` §"Note on brief size budget": "the orchestrator pays Thinking-tier rate to write 80-120 lines × N implementers. For N=4 parallel, that's ~400 lines of brief text at Thinking rate. **This is a real cost**". The repo already knows brief length is expensive and already prescribes Mode B to avoid it; it does not vary that length by the receiving model. **Source-explained capability** — F1: "only our most frontier models... 80% token decrease — the older models still have the full system prompt" |

**Rejected fields (enumerable, not evidenced):** reasoning-effort defaults (already owned by the orchestrator escalation ladder + the `effort:` concept — a different mechanism; duplicating it violates DRY. Additionally verified this run: `effort` has ZERO plumbing — prose-only at `skills/model-tiering/SKILL.md:65,127-149`, `skills/build-loop/references/phase-3-execute.md:12`, `agents/build-orchestrator.md:180`; `grep -c '^effort:' agents/*.md` returns 0 across all agents and no resolver return carries it — so adding the field would mean building that plumbing too, out of scope), CoT on/off (no in-repo defect measured; prompt-builder territory), few-shot count (subsumed by `examples`), tone/formatting knobs (no behavioral evidence).

**The JSON block to add to `references/model-taxonomy.json`** (Commit 2 transcribes verbatim; `schema_version` → `"2.1.0"`):

```json
"prompting_profiles": {
  "_comment": "Per-TIER prompting posture consulted automatically at dispatch: rides the resolve_agent_model.py envelope as `prompting_profile`. Keyed by capability rung (ADR-02, plan 2026-07-21-model-prompting-profile): a new model classified into a rung INHERITS its profile — freshness is structural, not calendar. `confidence` is part of the data by design: `verified-source` = Anthropic Claude Code team claims, Willison transcript 2026-07-21; `repo-measured` = build-loop round-3 implementer-brief evidence; `weak` = the source EXPLICITLY declined to endorse the small-model half ('We haven't been able to eval it — we don't have any hard data'). Fields trace to named observed failures (neg-constraint gradient F2, invariant brief template F3, rationale-free directives F8); the transcript is explanation, not authority.",
  "fields": ["examples", "constraint_posture", "edge_case_handling", "rationale", "prompt_budget"],
  "by_tier": {
    "T0": {
      "examples": "omit", "constraint_posture": "contextual", "edge_case_handling": "delegate",
      "rationale": "required", "prompt_budget": "compressed", "confidence": "verified-source",
      "summary": "State the goal, the context, and the falsifier; omit worked examples; every constraint carries its rationale; trust the model with the edge cases."
    },
    "T1": {
      "examples": "omit", "constraint_posture": "contextual", "edge_case_handling": "delegate",
      "rationale": "required", "prompt_budget": "compressed", "confidence": "verified-source",
      "summary": "State the goal, the context, and the falsifier; omit worked examples; every constraint carries its rationale; trust the model with the edge cases."
    },
    "T2": {
      "examples": "minimal", "constraint_posture": "contextual", "edge_case_handling": "delegate",
      "rationale": "required", "prompt_budget": "standard", "confidence": "verified-source",
      "summary": "Context over directives; at most one compact example when the contract is novel; delegate edge cases but name the known falsifiers."
    },
    "T3": {
      "examples": "worked", "constraint_posture": "mixed", "edge_case_handling": "enumerate-known",
      "rationale": "recommended", "prompt_budget": "full", "confidence": "repo-measured(examples,edge_case_handling); inferred(rest)",
      "summary": "Full brief: worked contract examples, explicit caps with the math shown, known edge cases enumerated (schema-field warnings). Round-3 evidence says this rigor pays at this rung — keep it."
    },
    "T4": {
      "examples": "worked", "constraint_posture": "directive-ok", "edge_case_handling": "enumerate-known",
      "rationale": "optional", "prompt_budget": "full", "confidence": "weak",
      "status_quo": true,
      "summary": "Full explicit brief with worked examples and direct instructions. WEAKLY EVIDENCED and STATUS-QUO: this row encodes what build-loop already does at this rung — it is a placeholder holding the rung open for the loader's completeness invariant, not a posture anyone chose on evidence. The source declined to endorse detailed-prompting-for-small-models without eval data ('We haven't been able to eval it'). Revisit at the eval trigger."
    },
    "T5": {
      "examples": "worked", "constraint_posture": "directive-ok", "edge_case_handling": "enumerate-known",
      "rationale": "optional", "prompt_budget": "full", "confidence": "weak",
      "status_quo": true,
      "summary": "Same as T4; bounded mechanical tasks only. WEAKLY EVIDENCED and STATUS-QUO — same caveat as T4."
    },
    "T-S": null
  }
}
```

Certainty markers on this content: T0–T2 rows ✅ verified against F1 verbatim claims; T3 `examples`/`edge_case_handling` ✅ repo-measured (F3 round-3 table); T3 remainder and all of T4/T5 ⚠️ marked `weak`/`inferred` in the data itself — the artifact carries its own epistemic status, per the bridge lens.

## Scope

In scope: the taxonomy block + loader accessor/validation, the resolver envelope key, tier-variance in the brief template + the orchestrator's M2.5 dispatch text, docs (CLAUDE.md §Model Tiering, `skills/model-tiering/SKILL.md`), the ai-assistant mirror spec (delivered as a build-loop doc), and **disposal of the stale fallbacks.md tier block + routing-line disambiguation + codex-mirror regeneration (Commit 6, F10)**.

### Out of scope

- **Editing anything under `/Users/tyroneross/dev/git-folder/RossLabs-AI-Assistant`** (C5 — spec only; zero files changed there).
- **Rewriting the 29 agent system prompts** to conform (goal.md non-goal). Follow-on, gated behind the eval trigger below — rewriting the worst offenders (`agents/build-orchestrator.md` 22.7 neg/100L, `agents/advisor.md` 16.5) without an A/B harness would be exactly the folklore the source warns against.
- **Behavioral evals** — follow-on with a **named trigger**: *the first profile-driven rewrite of any agent system prompt*. That rewrite is the first artifact with a before/after to A/B; build it via the existing Phase 6 experiment infra (`experiments/<name>.jsonl`) + `evals/`, measuring the F2 counting regex on emitted briefs plus task outcomes. Not "later" — a specific event that cannot occur silently, because agent-prompt rewrites land as commits.
- **Per-model profile overrides** (ADR-02 rollback — only if the falsifier fires).
- **prompt-builder repo changes** (F6 "do not wire in yet" note stands; different repo, different run — Commit 6's routing-line edit changes build-loop's DESCRIPTION of that plugin, not the plugin).
- **`references/capability-routing.md:57`** (top-level): consulted and left unchanged — it names no tier tokens, so it carries no collision.
- Pushing (auto-commit only).

## Six-Commit Table

| # | Commit subject | dispatch_tier | Files owned (absolute, MECE) | modifies_api | Depends on |
|---|---|---|---|---|---|
| 1 | `docs(plans): draft model-prompting-profile spec` | — (orchestrator commits this plan + handoff + ai-assistant spec + `.build-loop/plan.md`) | `docs/plans/2026-07-21-model-prompting-profile.md`, `docs/plans/2026-07-21-model-prompting-profile.handoff.md`, `docs/plans/2026-07-21-model-prompting-profile.ai-assistant-spec.md`, `.build-loop/plan.md` (worktree-relative) | false | — |
| 2 | `feat(taxonomy): add tier-keyed prompting_profiles block + loader accessor` | `sonnet` — transcription of §Profile Content + a small accessor mirroring existing loader functions; zero synthesis left | `/Users/tyroneross/dev/git-folder/build-loop/.build-loop/worktrees/run-835164/references/model-taxonomy.json`, `.../scripts/model_taxonomy.py`, `.../scripts/test_model_taxonomy.py` | true | C1 |
| 3 | `feat(dispatch): ride prompting_profile on the resolve_agent_model envelope` | `sonnet` — bounded additive change to one function + tests | `.../scripts/resolve_agent_model.py`, `.../scripts/test_resolve_agent_model.py` | true | C2 |
| 4 | `feat(briefs): tier-shaped brief guidance + brief capture in template and orchestrator M2.5` | `sonnet` — bounded doc edits with content specified in the handoff | `.../references/implementer-brief-template.md`, `.../agents/build-orchestrator.md` | false | C3 |
| 5 | `docs(tiering): document prompting profiles in CLAUDE.md + model-tiering skill` | `sonnet` — bounded doc edits | `.../CLAUDE.md`, `.../skills/model-tiering/SKILL.md` | false | C2 (content settled); parallel-safe with C3/C4 |
| 6 | `refactor(fallbacks): retire stale 3-tier prompting block for generated rung summaries` | `sonnet` — surgical bounded edits; replacement text authored verbatim in §Disposal spec | `.../skills/build-loop/fallbacks.md`, `.../skills/build-loop/references/capability-routing.md`, `.../plugin-artifacts/codex/**` (**hook-generated** — `artifact_guard.py --staged` regenerates and stages it inside this commit; the implementer edits it by hand never, and passes `--no-verify` never) | false | C2 — the replacement text projects `prompting_profiles`, so the block must exist first. Ordering only; the earlier "captures every mirrored edit" rationale was verified false and is withdrawn |

No two chunks own the same file; no orphans (every touched file appears exactly once; `plugin-artifacts/codex/**` is generated output owned solely by C6's regeneration step). `agents/*.md` frontmatter is untouched (no `sync_agent_model_defaults` drift — T-08 confirms). The formerly-reserved revision commit is retired: plan-critic/scope-auditor revisions fold into the affected commit under the existing per-commit test gate.

### Parallel batch decision

- `parallel_batch` wave 1 — C2 (taxonomy block + accessor; every other chunk reads it)
- `parallel_batch` wave 2 — C3 + C5 dispatched concurrently (disjoint file sets; C5 needs only settled content, not C3's code)
- `parallel_batch` wave 3 — C4 (brief template + orchestrator; depends on C3's envelope key)
- `parallel_batch` wave 4 — C6 (disposal + mirror regen; serialized so one regeneration captures every mirrored edit)
- `parallel_skipped_reason` — none; the batch above is the dispatch decision of record

C3 and C5 are the only genuinely concurrent pair: C3 owns `scripts/resolve_agent_model.py` + its test, C5 owns `CLAUDE.md` + `skills/model-tiering/SKILL.md`. Disjoint, no shared symbol. C6 is deliberately serialized despite touching different source files, because its regeneration step consumes the output of every prior chunk.

## Depends-on (reads-from)

Every data path and contract the new code reads. Status is `verified` when this run read the artifact directly.

| Reads | Consumer | Contract relied on | Status |
|---|---|---|---|
| `references/model-taxonomy.json` → `tiers.order` | `model_taxonomy.unprofiled_tiers()` (C2) | ladder is `["T0","T1","T2","T3","T4","T5","T-S"]`; `T-S` is off-ladder and profile-exempt | verified — loaded this run |
| `references/model-taxonomy.json` → `prompting_profiles.by_tier` | `model_taxonomy.prompting_profile()` (C2) | new block, authored in §Profile Content | verified — authored here |
| `model_taxonomy.normalize_tier()` (line 117) | `prompting_profile()` legacy-token path (C2) | folds `frontier/thinking/code/pattern` → `T1/T2/T3/T4` | verified — read this run |
| `resolve_agent_model.resolve()` envelope keys `{agent, segment, tier, model, source, resolution_path}` | C3 additive key; `dispatch_fallback.py` consumers | five return sites at lines 138, 168, 182, 196, 206 | verified — read this run |
| agent frontmatter `(segment, tier)` | `resolve()` → `prompting_profile` (C3) | `tier` may be absent (`inherit` agents) → profile must be `null`, never guessed | verified — read this run |
| `agents/build-orchestrator.md` M2.5 dispatch bullet | C4 application sentence | orchestrator already parses the resolver envelope at dispatch | verified — read this run |
| `scripts/build_codex_plugin_artifact.py` / `artifact_guard.py:89` | C6 regeneration | `outputs=("plugin-artifacts/codex",)`; regeneration is deterministic from source | unverified — ⚠️ builder not executed yet; C6 surfaces unrelated diffs rather than committing blind |
| `RossLabs-AI-Assistant/registry/sync.py:104-106,433-446` | ai-assistant spec (C1, doc only) | hardcoded taxonomy path; `meta` projects `{segment, tier}` only | verified — read this run; **not** read by any build-loop code |

## Activation Map

The central risk this build carries is shipping a dormant feature: a profile that rides the envelope and is never applied. Falsifier B is exactly that failure. Every component below names the call site that makes it live.

- `prompting_profiles` data block (D-01) — trigger: read by `model_taxonomy.prompting_profile(tier)`, the only reader — verified-live: pending (T-01)
- `prompting_profile(tier)` accessor — trigger: called by `resolve_agent_model.resolve()` at all five return sites — verified-live: pending (T-03)
- `unprofiled_tiers()` validation — trigger: called from `scripts/test_model_taxonomy.py`; detect-only and fail-open at runtime, it never blocks a dispatch — verified-live: pending (T-02)
- `prompting_profile` envelope key — trigger: parsed by the orchestrator at the M2.5 dispatch moment it already reads `model` (`agents/build-orchestrator.md` M1/M2/M3 bullet) — verified-live: pending (weakest link; T-06 proves the instruction exists, only Falsifier B's 3-run measurement proves it fires)
- `## Tier-shaped brief` template section — trigger: pasted into implementer briefs when the orchestrator assembles from `implementer-brief-template.md` — verified-live: pending (T-06 presence, Falsifier B effect)
- `fallbacks.md` §prompt generated rung summaries — trigger: read directly by a subagent that received the pasted `fallbacks.md#prompt` text on a `triggers.promptAuthoring` route; self-contained, so the trigger completes with no path resolution (this is the fix for the bare-pointer design, which could not complete this trigger from a consumer project) — verified-live: pending (T-09 asserts presence + taxonomy equality)

**Honest reading of this table:** three rows are deterministically testable and three depend on an LLM following an instruction. That asymmetry is the design's real exposure, and it is why Falsifier B carries a named measurement protocol rather than a test id. ⚠️ The claim "consultation is automatic" is structurally supported (the field arrives in an envelope the orchestrator already parses) and behaviorally **untested** until the next 3 fan-out runs are measured.

### Disposal spec (Commit 6 transcribes verbatim — F10, L10)

**Decision: generated-summaries-with-provenance + an orchestrator inline instruction. A bare pointer was drafted first and rejected under review — it is dead on the only route it serves.**

Why the pointer failed: `skills/build-loop/fallbacks.md:5` states this file's operating condition — *"Subagents do not inherit parent Skill context — only text in the prompt survives the dispatch boundary. Copy the relevant section verbatim into the subagent prompt."* The §prompt fallback fires **in consumer projects**, when the `prompt-builder` plugin is absent. A pointer to `references/model-taxonomy.json` and `python3 scripts/resolve_agent_model.py` is build-loop-repo-relative and resolves to nothing from a consumer project's cwd, and a subagent handed pasted text has no way to locate the plugin install directory. Read access exists; a resolvable path does not. The retired block, for all four of its defects, was at least self-contained across that boundary. Replacing it with dead text would trade a wrong answer for no answer.

**What Commit 6 writes instead** — replace ONLY the `Calibrate to model tier:` intro line and its three bullets (~L418-422) with a provenance header plus the six generated rung summaries:

> Calibrate to the target model's capability rung. **Generated from the `prompting_profiles` block in `references/model-taxonomy.json` — do not hand-edit; edit the taxonomy and regenerate.** Rungs are the taxonomy's ladder (T0 ultra/restricted-frontier · T1 ultra-frontier · T2 frontier · T3 balanced workhorse · T4 efficient near-frontier · T5 utility). Resolve a target's rung with `python3 scripts/resolve_agent_model.py <agent>` when running inside build-loop; the summaries below are self-contained when you are not.
>
> - **T0 / T1** — State the goal, the context, and the falsifier; omit worked examples; every constraint carries its rationale; trust the model with the edge cases.
> - **T2** — Context over directives; at most one compact example when the contract is novel; delegate edge cases but name the known falsifiers.
> - **T3** — Full brief: worked contract examples, explicit caps with the math shown, known edge cases enumerated.
> - **T4 / T5** — Full explicit brief with worked examples and direct instructions. Weakly evidenced (see the taxonomy block's `confidence` field).

**Why this over the two alternatives.** *Bare pointer* — rejected above; unresolvable across the boundary it serves. *Hand-maintained inline duplication* — rejected: that is precisely what F10 was, and its stale ids ("Opus 4.6", "gpt-4-mini") are the existence proof that a hand-maintained copy of this content does not get updated. The difference here is the provenance marker plus a drift test: the summaries are a **generated projection** of the taxonomy, and `T-09` asserts each rung's line matches its `summary` string, so the copy cannot silently drift the way F10 did. The duplication is real and accepted; it buys cross-boundary usability, and the drift risk that made F10 fail is closed by a test rather than by discipline.

**Orchestrator-side half (folded into C4's `agents/build-orchestrator.md` edit):** when the orchestrator pastes `fallbacks.md#prompt` into a subagent prompt and it already knows the target's rung, it replaces the six-line list with that rung's single summary. Inside build-loop the subagent then gets one line instead of six; outside it, all six ship and remain self-contained.

Everything else in §prompt — the 6-Part Stack, the review checklist, temperature hints, the `.build-loop/prompts/` note — is NOT tier-keyed, still useful, and survives verbatim.

**Routing-line fix (owned here):** `skills/build-loop/references/capability-routing.md:110` describes prompt-builder as "Calibrates to model tier (T1/T2/T3)" — accurate about the external plugin's 3-level scheme but a token collision with taxonomy rungs. Rewrite that fragment to: "Calibrates to prompt-builder's own 3-level scheme (frontier/mid/small — its labels, not taxonomy rungs)". Line 191's fallback pointer is unchanged (it names no tiers).

**Mirror regeneration (corrected under review — the original rationale was factually wrong).** The plan previously said C6 must run last "so one regeneration captures every mirrored edit." Verified false: the codex bundle mirrors **none** of C2–C5's files. `plugin-artifacts/codex/references/` contains no `implementer-brief-template.md` and no `model-taxonomy.json`; `plugin-artifacts/codex/skills/` holds only `build-loop`, `repo-closeout`, `repo-maintenance` — no `model-tiering`; there is no `CLAUDE.md` in the bundle at all. C6's own two sources are the only mirrored files this run touches.

The regeneration is also **not a manual step**. `scripts/artifact_guard.py` runs in `--staged` pre-commit mode and, for any artifact whose watched prefixes intersect the staged set, runs its check and on drift *regenerates and `git add`s* the outputs (`artifact_guard.py:22-27`). The `codex-plugin-artifact` entry watches `("skills/", "references/", "AGENTS.md", "README.md", "LICENSE")` with `outputs=("plugin-artifacts/codex",)`. C6 stages `skills/build-loop/fallbacks.md` and `skills/build-loop/references/capability-routing.md`, both under `skills/` — so the hook regenerates and stages the bundle inside C6's own commit automatically.

Consequences: (a) C6 does **not** invoke `build_codex_plugin_artifact.py` by hand — the hook owns it; the implementer's job is to let the hook run and never pass `--no-verify`. (b) `plugin-artifacts/codex/**` stays listed in C6's owned files as *hook-generated output*, not as a hand-edited surface. (c) MECE still holds for C2–C5: their staged files hit the watched prefixes too, but the bundle is not stale with respect to them, so the check passes and nothing is added to their commits. (d) The real reason C6 runs last is ordering, not regeneration — its replacement text must reference `prompting_profiles`, which C2 creates.

## Capability Gap Map

| Capability/Workflow | Current source of truth | Target behavior | Gap | Build action | Owned files/contracts | Validation |
|---|---|---|---|---|---|---|
| Prompting posture per tier | a DEFECTIVE one exists: `skills/build-loop/fallbacks.md` §prompt tier block (F10) — tokens shifted one rung vs taxonomy, stale ids, wrong on `examples`, silent on `constraint_posture`; consulted only via the promptAuthoring fallback, never at dispatch | tier-keyed typed profile, single source of truth, stale block retired | content wrong + unconsulted + duplicated authority | add `prompting_profiles` (§Profile Content, C2); dispose of the stale block (§Disposal spec, C6) | `references/model-taxonomy.json` (D-01); `skills/build-loop/fallbacks.md` | T-01, T-09 |
| Automatic consultation at dispatch | `scripts/resolve_agent_model.py` envelope has no prompting field (F4, read this run: lines 115–213) | profile arrives in the same envelope as `model`, zero extra calls | envelope key missing | add `prompting_profile` to `resolve()` return | `resolve_agent_model.resolve()` envelope | T-03/T-04/T-05 |
| Freshness on new-model adoption | `classify_model_tier.py` → tier; nothing prompting-aware | new model inherits rung profile; new RUNG flagged unprofiled | no validation | loader validation walking `tiers.order` vs `prompting_profiles.by_tier` | `scripts/model_taxonomy.py` | T-02 |
| Brief authoring varies by tier | `references/implementer-brief-template.md` — zero tier variance (F3); orchestrator M2.5 (`agents/build-orchestrator.md:116`) resolves model but not posture | orchestrator shapes each brief per the envelope's profile at the moment it already reads the envelope | template invariant; no application point | add "Tier-shaped brief" section + one M2.5 sentence | both files, Commit 4 | T-06 |
| ai-assistant parity | `registry/sync.py:104-106` hardcodes the absolute taxonomy path and live-reads it at sync time; `_discover_models` (L433-446) projects `{segment, tier}` ONLY into `capability_fingerprints`; declared `model_taxonomy_source` pointer unread (F7 refined) | registry mirrors the tier-keyed `prompting_profiles` TABLE once — no per-model profile records: consumers already hold each model's tier via the existing fingerprint sync, so profile lookup is a join on data they have (ADR-02 payoff) | `meta` projection omits profiles; no table sync; no consumption path | write mirror-contract spec whose core ask is precise: extend the sync projection at `sync.py:446` (or a sibling manifest entry for the table) + name the consumption path | `docs/plans/...ai-assistant-spec.md` | T-07 |

## Single-Shot Build Guardrails

| Guardrail | Prevents | Evidence/test |
|---|---|---|
| Transcribe §Profile Content verbatim, including `_comment`, `confidence`, and `summary` strings — no editorializing | implementer re-deriving/softening the evidence annotations (the epistemic markers ARE the artifact) | diff of Commit 2 vs §Profile Content block; T-01 |
| Envelope change is ADDITIVE only: existing keys, `--plain` output, and `resolution_path` semantics unchanged | breaking the orchestrator M2.5 call path and `dispatch_fallback.py` consumers | T-05 regression + full `pytest scripts/test_resolve_agent_model.py` |
| `inherit` agents get `prompting_profile: null` (caller's model unknown at resolve time — do not guess a tier) | fabricating a posture for an unresolvable tier | T-03 inherit case |
| Do NOT touch `agents/*.md` frontmatter or any file under RossLabs-AI-Assistant | drift vs `sync_agent_model_defaults.py --check` (T-08); C5 breach | T-07, T-08 |
| Brief template's T3 sections stay intact — Commit 4 ADDS tier-conditional guidance, deletes nothing | gutting repo-measured round-3 rigor on the strength of a frontier-only claim (L8) | T-06 grep: all existing section headers still present |
| Disposal is surgical: Commit 6 replaces ONLY the `Calibrate to model tier:` line + its 3 bullets with the §Disposal replacement text; 6-Part Stack, review checklist, temperature hints, prompts-dir note survive verbatim | deleting still-useful non-tier-keyed guidance while retiring the stale tier block | T-09 grep: those section anchors present; retired bullets absent |
| The generated rung summaries in `fallbacks.md` must be byte-equal to the taxonomy's `summary` strings, and must carry the "do not hand-edit" provenance marker | the accepted duplication decaying into a second stale authority — the exact F10 failure | T-09 equality assertion |
| Commit 6 lets `artifact_guard.py --staged` regenerate and stage `plugin-artifacts/codex/**`; the implementer never hand-edits the bundle and never passes `--no-verify` | shipping a stale git-tracked mirror, or hand-editing generated output | Q-gate: after the commit, rerun the builder → `git status --porcelain plugin-artifacts/` empty |
| Commit 4 adds the brief-capture line (`.build-loop/briefs/<run_id>/<chunk_id>.md`) alongside the tier-shaped guidance | Falsifier B remaining unmeasurable — the plan's own answer to its central risk | T-06 grep for the capture path in `agents/build-orchestrator.md` |
| Self-mod safety: per-commit test run (`pytest scripts/test_model_taxonomy.py scripts/test_resolve_agent_model.py`) before each commit lands | shipping a broken dispatch front door in a self-recursive run | orchestrator per-commit gate; F/Q tables |

## Read-Before-Edit Map

| Chunk | Read first | Why it matters | Edit after |
|---|---|---|---|
| C2 | `references/model-taxonomy.json` full file (top-level key order, `_comment` style); `scripts/model_taxonomy.py` accessor pattern (e.g. `preferred()` at line 160, `normalize_tier()` at 117); `scripts/test_model_taxonomy.py` fixture style | new accessor must mirror the loader's existing shape (module-level `TAXONOMY`, fail-behavior conventions) and normalize legacy tokens via the existing `normalize_tier` | the three C2-owned files |
| C3 | `scripts/resolve_agent_model.py` `resolve()` (lines 115–213 — all five return sites) and its docstring envelope contract; `scripts/test_resolve_agent_model.py` | `prompting_profile` must be attached at EVERY return site (inherit → null; unresolved → null) or consumers see a sometimes-missing key | the two C3-owned files |
| C4 | `references/implementer-brief-template.md` §"Why each section matters" + §"Orchestrator-side preparation"; `agents/build-orchestrator.md` lines 110–122 (Phase 3 highlights, M1/M2/M3 bullet at 116) | the new section must slot into the template's existing structure and the M2.5 sentence must reference the envelope field, not invent a second lookup | the two C4-owned files |
| C5 | `CLAUDE.md` §Model Tiering; `skills/model-tiering/SKILL.md` §"Chat-triggered index maintenance" | profile maintenance must ride the EXISTING chat-maintenance path (edit index → re-sync), not a new procedure | the two C5-owned files |
| C6 | `skills/build-loop/fallbacks.md` §prompt in full (L405-441 — the tier block sits between the 6-Part Stack and the review checklist); `skills/build-loop/references/capability-routing.md:110,191`; `scripts/build_codex_plugin_artifact.py` + `scripts/artifact_guard.py:89` (`outputs=("plugin-artifacts/codex",)`) | disposal must hit exactly the tier block and nothing else; the regen step must run after all other chunks so one regeneration captures every mirrored change | the three C6-owned surfaces |

## F-Criteria (functional) — with falsifiers per criterion

| ID | Criterion (goal.md) | Pass condition | Falsifier (what proves it failed) | Grader |
|---|---|---|---|---|
| F-C1 | Profile exists, evidence-bound | `prompting_profile(t)` returns all 5 fields for every ladder rung; every field traces to F1/F2/F3/F8 via the block's annotations | a field with no evidence pointer, or a rung missing | T-01 (pytest) + plan-critic reading §Profile Content |
| F-C2 | Consultation automatic | `resolve()` envelope carries `prompting_profile` at every return site with zero additional calls | any dispatch path reaching the Agent tool without the profile in the envelope it already parses | T-03/T-04 (pytest) + read of `agents/build-orchestrator.md:116` post-C4 |
| F-C3 | Freshness mechanical | tier-keyed inheritance (structural); a rung added to `tiers.order` without a profile is flagged | a new model or rung entering the taxonomy with silent absence of posture | T-02 (pytest, synthetic rung) |
| F-C4 | Extends, no parallels | zero new scripts, zero new skills, zero new top-level reference files (docs/plans/ excepted; regenerated files under `plugin-artifacts/codex/` excepted — generated mirror output, not new mechanism) | any new mechanism file in the diff | `git diff --stat` review; scope-auditor |
| F-C5 | ai-assistant specced not edited | spec file exists; `git -C /Users/tyroneross/dev/git-folder/RossLabs-AI-Assistant status --porcelain` shows no run-authored changes | any modified file in that repo | T-07 (deterministic check) |
| F-C6 | Honest scope | report states the three-part "disposal + small content + wiring" verdict and the weak-evidence marking on T4/T5 | report claiming greenfield, only-wiring, or unmarked confidence | Review-D fact-checker |
| F-C7 | Single tier-keyed prompting authority | fallbacks.md §prompt carries the pointer and none of the retired 3-tier bullets; routing line 110 disambiguated; codex mirror regenerated with zero residual diff | grep hits "Opus 4.6" / "T1 — Opus" in fallbacks.md, or "Calibrates to model tier (T1/T2/T3)" in capability-routing.md, or a dirty `plugin-artifacts/` after re-running the builder | T-09 (deterministic) |

## Q-Criteria (quality)

| Criterion | Pass condition | Grader |
|---|---|---|
| Tests | `python3 -m pytest scripts/test_model_taxonomy.py scripts/test_resolve_agent_model.py` exit 0 | per-commit gate |
| No frontmatter drift | `python3 scripts/sync_agent_model_defaults.py --check` exit 0 (T-08) | CI/per-commit |
| JSON validity | `python3 -c "import json; json.load(open('references/model-taxonomy.json'))"` exit 0 | per-commit |
| Artifact freshness | after Commit 6: `python3 scripts/build_codex_plugin_artifact.py && git status --porcelain plugin-artifacts/` empty | Review-B deterministic |
| Full suite unbroken | repo's standard `pytest scripts/` collection gate passes | Review-B |

## Falsifier for the central design bet

The bet: *a tier-keyed profile riding the resolver envelope is sufficient to change prompt shape at dispatch.* Two observations would prove it wrong:

**Falsifier A (wrong key):** two models in the same rung demonstrably need opposite postures (e.g., a T2 model that improves with worked examples while its rung-mate degrades). Detection: the eval trigger's A/B data discriminating within a rung. Response: ADR-02 rollback — per-model overrides layered on tier defaults.

**Falsifier B (wrong consultation strength):** after wiring, orchestrator-authored briefs show no measured shift — re-run the F2 counting regex (`\b(do not|don't|never|must not|no longer|avoid|forbidden|banned|prohibited)\b`) plus example-block counts on the briefs of the next 3 fan-out runs; if T1/T2-bound briefs still match T3 shape, envelope-riding is decorative and consultation needs a gate (e.g., a `brief_mece_validator` check), not a field. This is the F5 decay mode this design claims to avoid — the claim is ⚠️ untested until those runs occur.

**Falsifier B had no measurement substrate — this is the fix.** Verified this run: dispatched brief text is not retained anywhere in `.build-loop/`. `state.json.runs[]` entries carry only `{active_experimental_artifacts, date, diagnosticCommands, filesTouched, goal, judge_decisions, manualInterventions, outcome, phases, run_id}` (12 runs inspected, no brief field), and no `.build-loop/` directory holds dispatch prompts. `.build-loop/prompts/` captures *product* prompts on the promptAuthoring route only. As drafted, Falsifier B measured data that does not exist — the plan's own answer to its central risk was unfalsifiable.

Two substrates, in preference order:

**Primary — capture, added to C4.** The orchestrator writes each assembled brief to `.build-loop/briefs/<run_id>/<chunk_id>.md` at the moment it dispatches. One line in the M2.5 dispatch text; no new script, no schema, no gate. This makes the measurement a `grep` over a directory instead of a mining exercise, and it is the difference between a falsifier that will be run and one that will be skipped.

**Fallback — transcript mining.** Where capture is absent (runs predating C4, or non-build-loop dispatches), the briefs survive in the session transcript JSONL under `~/.claude/projects/<slug>/*.jsonl`; Agent-tool records carry `subagent_type` and the full `prompt` (confirmed by scanning 3 recent transcripts). `build-loop:transcript-pattern-miner` is the existing tool. This is the only route for the baseline runs, since they are already written.

Both paths are named because the fallback is what makes the *before* half of the before/after measurable at all.

## Synthesis Dimensions (resolved — 7, per goal.md)

```yaml
synthesis_dimensions:
  profile_content: "5 fields × 6 rungs, authored verbatim in §Profile Content; confidence markers in-data; rejected fields listed"
  storage_location: "prompting_profiles block in references/model-taxonomy.json (ADR-01, Pugh over 4 candidates); stale fallbacks.md tier block retired to a pointer (F10, Commit 6) so exactly one tier-keyed authority survives"
  consultation_mechanism: "additive key on resolve_agent_model.resolve() envelope — the F4 front door; no skill-load, no second call"
  freshness_contract: "structural/event-driven: tier-keyed inheritance + loader validation of tiers.order coverage; calendar contract rejected"
  cross_repo_boundary: "spec-only this run; consumption = registry/sync.py's existing SYNC-TIME read (hardcoded path, F7 refined — offline/on-demand, never per-dispatch); spec's core ask: sync the tier-keyed profile TABLE + join on the tier already mirrored per model via capability_fingerprints — no per-model profile records (ADR-02 payoff)"
  eval_scope: "follow-on; named trigger = first profile-driven agent-prompt rewrite (a commit — cannot occur silently)"
  brief_template_impact: "governs BOTH static prompts and Phase 3 briefs (F8); template gains tier-conditional section, keeps T3 rigor (L8); orchestrator applies it at the M2.5 moment it already reads the envelope — no new step to skip"
```

## Assumptions (labeled — none passed the blocking-and-novel gate as questions)

- [ASSUMED: T0 shares T1's profile — no T0-specific prompting evidence exists; T0 is restricted-frontier and the F1 claims are "most frontier models". Reversible data edit.]
- [ASSUMED: `governance_evaluation`-segment agents take the same rung profile as `generative_reasoning` — no segment-differentiated evidence (ADR-02). Falsifier A covers this too.]
- [ASSUMED: attaching the profile at `resolve_agent_model` (not `model_resolver.resolve_role`) suffices — the orchestrator's M2.5 contract names `resolve_agent_model.py` as the sole front door (`agents/build-orchestrator.md:116-117`). Direct `resolve_role` callers, if any beyond the front door, are out of the dispatch path.]
- [ASSUMED: `scripts/build_codex_plugin_artifact.py` regenerates the codex mirrors of fallbacks.md and capability-routing.md deterministically from source (per `artifact_guard.py:89` `outputs=("plugin-artifacts/codex",)`); if regeneration produces unrelated diffs, the implementer surfaces them rather than committing blind. ⚠️ builder not executed in this Advisor dispatch (no Bash).]

## Spec Object (JSON)

```json
{
  "needs": [
    {"id": "U-01", "priority": "P0", "need": "A dispatched prompt's shape varies with the judgment capability of the receiving model, automatically", "features": ["F-01", "F-02", "F-03", "F-04", "F-06"]},
    {"id": "U-02", "priority": "P0", "need": "RossLabs-AI-Assistant gets the same contract via spec, without this run editing that repo", "features": ["F-05"]}
  ],
  "features": [
    {"id": "F-01", "priority": "P0", "title": "prompting_profiles block in model-taxonomy.json + prompting_profile(tier) accessor", "commit": 2, "data": ["D-01"], "tests": ["T-01", "T-02"], "adr": ["A-01", "A-02"]},
    {"id": "F-02", "priority": "P0", "title": "prompting_profile key on resolve_agent_model envelope (all return sites; inherit/unresolved -> null)", "commit": 3, "data": ["D-01"], "tests": ["T-03", "T-04", "T-05"]},
    {"id": "F-03", "priority": "P1", "title": "Tier-shaped brief section in implementer-brief-template + M2.5 application sentence in build-orchestrator", "commit": 4, "tests": ["T-06"]},
    {"id": "F-04", "priority": "P0", "title": "Loader validation: every ladder rung profiled or flagged unprofiled", "commit": 2, "tests": ["T-02"]},
    {"id": "F-05", "priority": "P0", "title": "ai-assistant mirror-contract spec (build-loop-side doc): sync the tier-keyed profile table at sync.py:446-adjacent + consumption path joining on already-mirrored tier", "commit": 1, "tests": ["T-07"]},
    {"id": "F-06", "priority": "P0", "title": "Dispose of stale fallbacks.md 3-tier prompting block (pointer rewrite, F10) + capability-routing line-110 disambiguation + codex mirror regeneration", "commit": 6, "tests": ["T-09"]}
  ],
  "data": [
    {"id": "D-01", "title": "prompting_profiles schema: {fields[5], by_tier{T0..T5, T-S:null}, per-row confidence + summary}", "source_of_truth": "references/model-taxonomy.json", "consumers": ["scripts/model_taxonomy.py", "scripts/resolve_agent_model.py", "skills/build-loop/fallbacks.md#prompt (pointer)", "RossLabs-AI-Assistant registry mirror (specced)"]}
  ],
  "tests": [
    {"id": "T-01", "asserts": "prompting_profile(t) returns all 5 fields + summary for every rung in tiers.order except T-S; T-S returns None", "file": "scripts/test_model_taxonomy.py"},
    {"id": "T-02", "asserts": "a synthetic taxonomy with a rung missing from by_tier is reported unprofiled by the loader validation (fail-open at runtime, detected in tests)", "file": "scripts/test_model_taxonomy.py"},
    {"id": "T-03", "asserts": "resolve() for a T3-role agent carries prompting_profile == prompting_profile('T3'); inherit agent carries null", "file": "scripts/test_resolve_agent_model.py"},
    {"id": "T-04", "asserts": "legacy tier token 'code' resolves to the same profile as T3 (normalize_tier path)", "file": "scripts/test_resolve_agent_model.py"},
    {"id": "T-05", "asserts": "--plain output and all pre-existing envelope keys unchanged (regression)", "file": "scripts/test_resolve_agent_model.py"},
    {"id": "T-06", "asserts": "template contains 'Tier-shaped brief' section AND every pre-existing section header survives AND build-orchestrator M2.5 bullet names the envelope's prompting_profile field", "file": "grep (Review-B deterministic)"},
    {"id": "T-07", "asserts": "spec file exists; RossLabs-AI-Assistant working tree has zero run-authored changes", "file": "git status --porcelain (Review-B deterministic)"},
    {"id": "T-08", "asserts": "sync_agent_model_defaults.py --check exit 0 post-build", "file": "existing CI check"},
    {"id": "T-09", "asserts": "DRIFT GATE — for every rung in prompting_profiles.by_tier (excluding T-S), the fallbacks.md #prompt summary line for that rung equals the taxonomy's `summary` string; plus the retired bullets are absent ('Opus 4.6', 'gpt-4-mini', 'T1 — Opus'), the provenance marker ('do not hand-edit') is present, the 6-Part Stack / review checklist / temperature hints anchors survive, capability-routing.md no longer contains 'Calibrates to model tier (T1/T2/T3)', and re-running build_codex_plugin_artifact.py leaves plugin-artifacts/ clean", "file": "scripts/test_model_taxonomy.py (equality half — it already loads the taxonomy) + grep/regen check (Review-B deterministic)", "why": "the equality assertion is what makes the accepted duplication safe; without it the generated summaries decay exactly as F10 did"}
  ],
  "adrs": [
    {"id": "A-01", "title": "Storage in model-taxonomy.json over new file / skill / cross-repo", "reversibility": "low (schema consumed by >=2 repos)"},
    {"id": "A-02", "title": "Tier rung as profile key, not model id", "reversibility": "medium (override-layer rollback path named)"}
  ]
}
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Profile field content is wrong for the cheap tiers (source itself says un-evaled) | medium | `confidence: weak` baked into the data; eval trigger named; T4/T5 rows conservative (they preserve today's behavior — full explicit briefs — rather than inventing a new posture) |
| Envelope consumers break on the new key | low | additive; T-05 regression; full resolver test file runs per-commit |
| Consultation proves decorative (Falsifier B) | medium | measurement protocol named (F2 regex on next 3 runs' briefs); escalation path = gate in brief_mece_validator, a one-commit follow-up |
| Second run never carries the ai-assistant spec | medium | file it via `scripts/file_to_operations_center.py --repo RossLabs-AI-Assistant` at Review-G (cross-repo routing policy), so it lands on the queue of record |
| Taxonomy schema bump confuses the registry sync before the second run lands | low | additive block; `registry/sync.py` DOES live-read the file at sync time (hardcoded path, F7 refined) but `_discover_models` walks `models` records only, so an additive top-level block is ignored until the spec lands — nothing breaks in the interim |
| Codex-mirror regeneration drags in unrelated diffs | low | regen runs once, last (C6), from a clean post-C5 tree; implementer surfaces unexpected diffs instead of committing blind (see §Assumptions, last item) |

## UI Input/Output Contract

N/A: no UI surface.

## Out of Scope (mirror)

RossLabs-AI-Assistant edits; 29-agent prompt rewrites (eval-trigger-gated follow-on); behavioral evals (named trigger: first profile-driven agent-prompt rewrite); per-model overrides (Falsifier-A-gated); prompt-builder repo changes; pushing.

## Verification note (Advisor honesty)

⚠️ `check_checklist.py`, `plan_verify.py`, and the `plan-critic` dispatch could not run in this Advisor dispatch (no Bash/Agent tools granted). The orchestrator MUST run Step A (both scripts) and Step B (plan-critic, **blocking** — high-stakes gating tripped) before Phase 3 dispatch, plus scope-auditor for Commits 2–3 (`modifies_api: true`). `scope_auditor_status: pending` in frontmatter reflects this.

## Amendment log (re-plan 2026-07-21 — planning-input miss)

Two Phase-1 findings arrived after the original Advisor dispatch; the orchestrator diagnosed a planning-INPUT miss (evidence withheld, reasoning intact) and re-dispatched. Failure evidence and plan delta, preserved per the remediation contract:

- **F10** (falsified the prior §Verdict claim that no tier-keyed prompting content exists in build-loop): `skills/build-loop/fallbacks.md` §prompt carries a defective 3-tier block — tokens shifted one rung vs taxonomy, stale ids, wrong on `examples`, silent on `constraint_posture`; consulted only by the promptAuthoring fallback, never at dispatch. Delta: §Verdict rewritten to the three-part "disposal + small content + wiring" verdict; L10 added; Commit 6 converted from reserved-revision to the disposal commit (§Disposal spec, F-06, T-09, F-C7); F-C4 gains the generated-mirror carve-out; codex-mirror regeneration and the capability-routing line-110 fix are owned; `references/capability-routing.md:57` checked and left unchanged (no tier tokens).
- **F7 refined** (corrected L7's mechanism): `registry/sync.py:104-106` hardcodes the absolute taxonomy path and live-reads it at SYNC time; the declared `model_taxonomy_source` pointer is unread; `_discover_models` (L433-446) projects `{segment, tier}` only. Delta: L7, Architecture note, gap-map ai-assistant row, Risk 5, and the `cross_repo_boundary` dimension corrected; ADR-02 gains the mirror-payoff argument (consumers join the profile table on the tier they already sync — no per-model records); F-05's spec ask made precise (`sync.py:446`-adjacent projection + consumption path). The "no live RUNTIME read" caution stands — sync is offline/on-demand.
- **Minor:** `effort` has zero plumbing (grep-verified: no `effort:` frontmatter in any agent, no resolver return carries it) — strengthens the existing reasoning-effort field rejection; one sentence added in §Profile Content.

Retry justified: ADR-01/ADR-02, L1–L9 (L7 as corrected), the §Profile Content JSON, all falsifiers, and the six-commit architecture survive both findings — the corrections change the disposition of pre-existing content and the precision of the cross-repo spec, not the design.

## Plan-critic dispositions (2026-07-21, Frontier tier — all 7 WARNs resolved)

Stakes are `medium`, so plan-critic WARNs gate Phase 2. Every finding is revised, not overridden. Two were confirmed by direct verification before acting.

| # | Finding | Disposition |
|---|---|---|
| 1 | `prompt_budget` traced only to F1, violating L9's own "transcript is explanation, not authority" standard | **Revised.** An in-repo trace existed and the plan had missed it: `references/implementer-brief-template.md` §"Note on brief size budget" records the measured cost ("~400 lines of brief text at Thinking rate… **This is a real cost**") and already prescribes Mode B to avoid it — the repo knows brief length is expensive but does not vary it by receiving model. Evidence cell now carries both the repo-observed cost and the source-explained capability. Field kept. |
| 2 | T4/T5 no-op status disclosed only in §Risks, not in the JSON implementers transcribe | **Revised.** Added `"status_quo": true` to both rows and moved the disclosure into the `summary` string itself. The artifact now states its own placeholder status at the point of use. |
| 3 | Falsifier B measures briefs the system never persists — unfalsifiable as written | **Revised, and this was the sharpest finding.** Verified: `runs[]` has no brief field across 12 runs; no `.build-loop/` path holds dispatch prompts. Fixed with a capture step in C4 (`.build-loop/briefs/<run_id>/<chunk_id>.md`, one line in the M2.5 text) plus a named transcript-mining fallback for the baseline runs already written. |
| 4 | Agent-frontmatter storage option unscored in a low-reversibility ADR | **Revised.** Added as option E to the ADR-01 Pugh table with its rejection recorded: ~29 denormalized copies, silent drift on reclassification, no route to the second consumer. Dominated, so the verdict is unchanged — but it is the option a maintainer reaches for first, and the rejection is now on the record. |
| 5 | C6's "captures every mirrored edit" premise false; `artifact_guard --staged` auto-regen missed | **Revised after verification.** Confirmed the codex bundle mirrors none of C2–C5's files (no `implementer-brief-template.md`, no `model-taxonomy.json`, no `model-tiering/`, no `CLAUDE.md`). Also confirmed `artifact_guard.py:22-27` regenerates and stages `plugin-artifacts/codex` in pre-commit for any commit touching `skills/`or `references/`. Rationale withdrawn and replaced: C6 runs last for **ordering** (its text projects `prompting_profiles`), and the regeneration is **hook-owned**, not a manual builder call. |
| 6 | §Goal unamended — omitted the P0 disposal deliverable | **Revised.** §Goal now names the disposal and lists all five deliverables in dependency order. |
| 7 | Disposal pointer dead across the dispatch boundary it serves | **Revised — design changed.** Confirmed against `fallbacks.md:5` ("only text in the prompt survives the dispatch boundary"): the §prompt fallback fires in consumer projects, where build-loop-relative paths resolve to nothing. Bare pointer replaced with **generated rung summaries carrying a provenance marker**, self-contained across the boundary, plus a `T-09` equality assertion binding them to the taxonomy so the accepted duplication cannot drift the way F10 did. The orchestrator inlines the single resolved rung when it knows the target. |

**Note on shape, recorded for honesty:** six rungs carry four distinct postures (T0 = T1; T5 = T4). The per-rung records are total by design so lookup never misses, but the profile encodes four decisions, not six.
