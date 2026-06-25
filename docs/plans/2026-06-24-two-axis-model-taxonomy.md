---
title: Two-axis model taxonomy for build-loop model selection
date: 2026-06-24
status: accepted
tier: thinking
synthesis_dimensions:
  taxonomy_data_shape: structured-json + python-reexport
  tier_ladder: T0-T5 + T-S, legacy aliases onto T1-T4
  segment_axis: 7 segments, dormant-vs-active
  selection_policy: ordered-preferred-list + recency tiebreaker
  classification: host-LLM segment+tier, specialist rubric hints
  agent_role_binding: 28 files, segment+tier role descriptors
  backcompat_alias_layer: tokens/config/state/frontmatter/tests preserved
modifies_api: true
scope_auditor_status: passed
risk_reason: runtime protocol
parallel_skipped_reason: chunks form a strict dependency chain (C2 imports C1, C3 imports C2, C5 reconciles after C2/C4) — only C4 is independent of C3, not enough disjoint width to fan out; executed sequentially with a test gate between each.
---

# Two-axis model taxonomy for build-loop model selection

## Goal (headline)
Make a machine-readable two-axis taxonomy (work-role SEGMENT × CAPABILITY-TIER
ladder) the single source of truth for build-loop model selection, so each
agent declares a (segment, tier) ROLE that resolves to the most-preferred
available host-reachable model at dispatch — with the legacy
`frontier/thinking/code/pattern` tokens preserved as aliases and the full
existing test suite green.

All locked decisions (Hybrid selection, role-tag dispatch binding, native
ladder re-implementation with legacy aliases) are FIXED inputs, not re-opened.

## Current system (verified in Assess, not re-derived)
- `scripts/model_overrides.py` — CORE. `TIERS={frontier,thinking,code,pattern}`,
  `TIER_DEFAULTS`, `TIER_ORDER`/`TIER_RANK` (capability rank), `TIER_FALLBACK`
  (one-edge floor walk), `MODEL_REGISTRY` (per-tier ordered list of
  `{id,provider,label,status,aliases}`). Floor invariant enforced at the source
  in `resolve_with_tier_fallback`. Tests: `scripts/test_model_overrides.py`.
- `scripts/model_resolver.py` — thin wrapper: persistent availability +
  in-tier priority chain + host-provider filter. Imports vocab from
  model_overrides. Tests: `scripts/test_model_resolver.py`.
- `scripts/classify_model_tier.py` — host-LLM classification; `CLASSIFY_RUBRIC`
  (tier-only today), `lookup`/`record`, cache at
  `.build-loop/model-tier-cache.json`. Tests: `scripts/test_classify_model_tier.py`.
- `scripts/dispatch_fallback.py` — record outage → re-resolve. Tests present.
- `scripts/route_decision.py` — Phase-1 thinking|code routing. NO test file.
- `scripts/build_capability_registry.py` — STALE second vocabulary
  `MODEL_HINTS={opus,sonnet,haiku}`, `_classify_tier`→opus|sonnet|haiku|n/a.
  Tests: `tests/test_capability_registry.py`. **Verified baseline (Assess):**
  `uv run pytest scripts/test_model_*.py scripts/test_classify_model_tier.py
  scripts/test_dispatch_fallback.py tests/test_capability_registry.py` →
  100 passed, 1 failed = `test_no_unknown_category` (`dispatch_fallback`
  classified `unknown`) on the CLEAN worktree (no local diff vs HEAD), so it is
  pre-existing, not introduced by this plan. C5 fixes it.
- Frontmatter: `model:` (harness-consumed concrete alias) coexists with `tier:`
  (orchestrator-resolved; 9 agents already carry it). M2.5 contract
  (`references/m-series-protocol.md`) resolves `tier:`→model via
  `model_resolver.py --tier <t> --plain` at dispatch.
- Prose: `references/model-tier-mapping.md`, `skills/model-tiering/SKILL.md`,
  `CLAUDE.md §Model Tiering`, README §How-it-works.

## Approach Lenses

**Clean-sheet best answer.** A pure (segment, tier) lattice with ordered
preferred lists per cell, one resolver consulting it, host-LLM classification
into both axes, agents declaring only roles. No legacy tokens.

**Current-constraints answer.** The harness consumes `model:` frontmatter and
the M2.5 contract already resolves `tier:`. Existing tests pin
`frontier/thinking/code/pattern → fable/opus/sonnet/haiku` and
`modelOverrides[tier]`. Hundreds of references to the 4 tokens exist across
config/state/plan/route_decision/tests.

**Bridge / back-cast.** Build the clean-sheet two-axis data model, then map the
4 legacy tokens onto it as a thin ALIAS layer: `frontier→T1, thinking→T2,
code→T3, pattern→T4`. Every legacy entrypoint (TIERS, TIER_DEFAULTS,
resolve_model, resolve_with_tier_fallback) keeps its signature and behavior by
normalizing legacy→ladder internally, then resolving on the ladder. New
entrypoints (`resolve_role(segment, tier)`) sit alongside. Agents gain a
`segment:` line beside the existing `tier:` (now ladder-or-legacy). The `model:`
line stays as the harness-default + the documented fresh-install fallback.
This is Path B (pay-it-forward): the typed taxonomy unlocks the dormant
segments + multi-provider routing named in intent.md, at the cost of one alias
layer.

**Chosen: Bridge.** Locked decision #3 mandates it. The alias layer is the
single mechanism that satisfies "back-compat mandatory" + "richer ladder".

## Path A vs Path B (pay-it-forward)
- **Path A (min-viable):** extend the 4-token tier list to 7 rungs, leave
  segment out of code, document segments in prose only.
- **Path B (chosen):** encode segment as a real axis in the data file + resolver
  signature + classification rubric + agent frontmatter, even though only
  Generative Reasoning / Agentic Execution / Governance-Evaluation are wired to
  a live resolver today. The typed segment axis is the contract the dormant
  segments and multi-provider routing (named in intent.md) consume later with
  zero re-plumbing.
- **Gate check:** time-budget <2× (segment is one more dict key + one more
  frontmatter line); dep present (stdlib only); design decided (this plan);
  foreclosed-future-capability list NON-empty (Realtime/Perception/Media skills,
  per-provider preferred-list reordering). → Path B.

## Taxonomy data shape (single source of truth)

New file: `references/model-taxonomy.json` (machine-readable, the source) plus a
tiny Python re-export so scripts import symbols, not re-parse JSON.

```
references/model-taxonomy.json
scripts/model_taxonomy.py   # loads the JSON once, exposes constants + helpers
```

`model_taxonomy.py` is the ONE module every other script imports for segment/
tier vocabulary. `model_overrides.py` re-exports its tier constants from here so
there is no second vocabulary anywhere.

### JSON schema (top-level keys)
```jsonc
{
  "schema_version": "2.0.0",
  "tiers": {                          // the 7-rung ladder, capability-ranked
    "order": ["T0","T1","T2","T3","T4","T5","T-S"],
    "defs": {
      "T0": {"label":"experimental/restricted frontier","rank":0},
      "T1": {"label":"ultra-frontier","rank":1},
      "T2": {"label":"frontier","rank":2},
      "T3": {"label":"balanced workhorse","rank":3},
      "T4": {"label":"efficient near-frontier","rank":4},
      "T5": {"label":"utility/nano/edge","rank":5},
      "T-S":{"label":"specialist infrastructure","rank":99,"specialist":true}
    },
    // one-edge floor walk on the GENERATIVE ladder T1..T5. T-S is specialist
    // (off-ladder): it does not participate in the capability fallback walk.
    "fallback": {"T0":"T1","T1":"T2","T2":"T3","T3":"T4","T4":"T5","T5":null,"T-S":null},
    "legacy_aliases": {"frontier":"T1","thinking":"T2","code":"T3","pattern":"T4"}
  },
  "segments": {
    "generative_reasoning": {
      "label":"Generative Reasoning",
      "subsegments":["ultra-frontier reasoners","frontier reasoners",
                     "balanced workhorses","efficient utility generators"],
      "status":"active"            // has a live resolver consumer
    },
    "agentic_execution": { ... "status":"active" },
    "governance_evaluation": { ... "status":"active" },
    "representation_retrieval": { ... "status":"partial" }, // embeddings only
    "realtime_interaction": { ... "status":"dormant" },     // data-only
    "perception_input": { ... "status":"dormant" },
    "generative_media": { ... "status":"dormant" }
  },
  // The ordered preferred list per (segment, tier). Order = capability rank,
  // honoring Accuracy>Speed>Cost. Resolver picks highest-ranked AVAILABLE +
  // host-reachable; ties / unranked broken by release recency (newer wins).
  // Anthropic ids are EXAMPLE seeds, held as data — not hard-codes.
  "preferred": {
    "generative_reasoning": {
      "T1": ["fable"],
      "T2": ["opus","gpt-5.5"],
      "T3": ["sonnet","gpt-5.4","gemini-2.5-pro"],
      "T4": ["haiku","gpt-5.4-mini"],
      "T5": ["gpt-5.4-nano","gemini-flash-lite"]
    },
    "agentic_execution": {
      "T2": ["opus"],
      "T3": ["sonnet","gpt-5.4","qwen2.5-coder-32b"]
    },
    "governance_evaluation": {
      "T1": ["fable"],
      "T3": ["sonnet"],
      "T4": ["haiku"],
      "T5": ["gpt-5.4-nano"]
    },
    "representation_retrieval": { "T-S": ["openai-text-embedding-3-large"] },
    "realtime_interaction":     { "T-S": ["gpt-realtime"] },
    "perception_input":         { "T-S": ["gpt-5.4"] },   // multimodal-input seed; dormant
    "generative_media":         { "T-S": ["gpt-image-1"] }
    // dormant cells are data only — no resolver walks them yet. ALL 7 segments
    // have a preferred cell so the data shape is uniform (plan-critic finding 1).
  },
  "models": {
    // model id -> {provider, label, segment, tier, tags[], released, status,
    //              aliases[]}. The seed registry; classification appends here
    //              (in the cache, not this file) at runtime.
    "fable": {"provider":"anthropic","label":"Fable 5",
              "segment":"generative_reasoning","tier":"T1",
              "tags":["ultra-frontier","long-context","agentic"],
              "released":"2025-11-01","status":"default",
              "aliases":["claude-fable-5","claude-fable-5-20251101"]},
    "opus":  {"...":"...","tier":"T2","tags":["long-horizon-coding","high-autonomy"]},
    "sonnet":{"...":"...","tier":"T3"},
    "haiku": {"...":"...","tier":"T4"},
    "gpt-5.5":{"provider":"openai","tier":"T2"...},
    "gpt-5.4":{"provider":"openai","tier":"T3"...},
    "gpt-5.4-mini":{"tier":"T4"...},
    "gpt-5.4-nano":{"tier":"T5"...},
    // T-S specialist seeds:
    "openai-text-embedding-3-large":{"segment":"representation_retrieval","tier":"T-S"...},
    "gpt-realtime":{"segment":"realtime_interaction","tier":"T-S"...},
    "gpt-image-1":{"segment":"generative_media","tier":"T-S"...},
    "omni-moderation":{"segment":"governance_evaluation","tier":"T-S"...}
  },
  "classification_rubric": {
    // segment-appropriate benchmark hints (Goal #5)
    "generative_reasoning":"SWE-bench Verified / ARC-AGI / GPQA Diamond",
    "agentic_execution":"SWE-bench Verified + tool-use accuracy + tau-bench",
    "governance_evaluation":"judge agreement / classification F1",
    "representation_retrieval":"MTEB / recall@k / NDCG",
    "realtime_interaction":"WER / latency ms",
    "perception_input":"document/vision/audio understanding benchmarks",
    "generative_media":"human-pref / FID / generation latency",
    "primary_role_rule":"image/audio INPUT but reasoning PRIMARY => generative_reasoning + multimodal-input TAG; classify into Perception/Realtime/Media ONLY when that IS the product role"
  }
}
```

### `model_taxonomy.py` exposed surface
```python
TAXONOMY            # the parsed dict (loaded once)
TIER_LADDER         # ["T0".."T5","T-S"]
TIER_RANK           # {tier: rank}
LADDER_FALLBACK     # {tier: next-or-None}  (one-edge generative walk)
LEGACY_ALIASES      # {"frontier":"T1", ...}
SEGMENTS            # {id: {...}}
def normalize_tier(token) -> ladder_tier      # frontier->T1, T1->T1, unknown->raise
def is_legacy_tier(token) -> bool
def preferred(segment, tier) -> list[str]     # ordered ids (may be empty)
def model_meta(model_id) -> dict | None       # seed registry lookup
def released(model_id) -> str | None          # for recency tiebreak
def segment_status(segment) -> "active|partial|dormant"
```

## Build chunks (dependency-ordered, MECE file ownership)

### Chunk 1 — Taxonomy data + module (foundation)
- **Owns:** `references/model-taxonomy.json`, `scripts/model_taxonomy.py`,
  `scripts/test_model_taxonomy.py` (new).
- **Does:** encode the full JSON (7 segments, 7 rungs, seed models with
  `released` dates, preferred lists, legacy aliases, classification rubric);
  implement the loader + helpers; tests for ladder rank, one-edge fallback,
  legacy-alias normalization, preferred-list lookup, recency helper, dormant-
  segment status, primary-role rule presence.
- **Interface contract:** `normalize_tier`, `preferred`, `model_meta`,
  `released`, `LEGACY_ALIASES`, `TIER_RANK`, `LADDER_FALLBACK` — the symbols
  chunks 2-5 import.
- **Acceptance:** `uv run pytest scripts/test_model_taxonomy.py` green.

### Chunk 2 — model_overrides ladder re-implementation + alias layer
- **Owns:** `scripts/model_overrides.py`, `scripts/test_model_overrides.py`
  (extend, don't break).
- **Does:** re-express `TIERS`/`TIER_DEFAULTS`/`TIER_ORDER`/`TIER_RANK`/
  `TIER_FALLBACK`/`MODEL_REGISTRY` IN TERMS OF the taxonomy module. Keep the
  4 legacy tokens as a public alias surface: `TIERS` continues to accept
  `frontier/thinking/code/pattern` (normalized to ladder internally) AND the
  ladder tokens T0–T5/T-S. `resolve_model`/`resolve_with_tier_fallback` accept
  either vocabulary; the one-edge floor clamp now runs on the ladder
  (`frontier`=T1 still never resolves below `thinking`=T2). `MODEL_REGISTRY`
  becomes a VIEW over `taxonomy.preferred` keyed by legacy token for back-compat
  (`--list-models` unchanged shape). Add `resolve_role(segment, tier, ...)` as
  the new two-axis entrypoint; legacy `resolve_*` delegate through it for the
  Generative-Reasoning segment (the legacy tokens' implicit segment).
- **Interface contract:** every existing public symbol/signature preserved;
  existing tests pass UNMODIFIED. New: `resolve_role`.
- **Acceptance:** existing `test_model_overrides.py` passes unchanged; new alias
  + ladder-floor + recency-tiebreak tests pass.

### Chunk 3 — resolver + dispatch_fallback + route_decision pass-through
- **Owns:** `scripts/model_resolver.py`, `scripts/dispatch_fallback.py`,
  `scripts/route_decision.py`, `scripts/test_route_decision.py` (NEW),
  extensions to `test_model_resolver.py` / `test_dispatch_fallback.py`.
- **Does:** resolver gains a `resolve_role(segment, tier)` path that walks
  `taxonomy.preferred(segment, tier)` then the ladder fallback; the legacy
  `resolve(tier=...)` keeps working by mapping the legacy token to
  (generative_reasoning, ladder-tier). Recency tiebreak applied among equal-rank
  / unranked candidates via `taxonomy.released`. `route_decision` still emits
  `thinking|code` (unchanged contract — it is a legacy-token consumer; verify it
  resolves through the alias). Add the missing `test_route_decision.py`.
- **Acceptance:** `route_decision --self-test` passes; new test file green;
  resolver/fallback tests green.

### Chunk 4 — classification emits segment + tier
- **Owns:** `scripts/classify_model_tier.py`, `scripts/test_classify_model_tier.py`.
- **Does:** extend `CLASSIFY_RUBRIC` to ask for SEGMENT + tier with the
  segment-appropriate benchmark hints from `taxonomy.classification_rubric`
  (specialist segments use MTEB/recall/NDCG/latency, not SWE-bench); `lookup`
  packet carries the segment question; `record` accepts `--segment` and caches
  both; `VALID_TIERS` extended to the ladder + legacy aliases. Back-compat:
  `record` without `--segment` defaults segment to `generative_reasoning`
  (the implicit segment of the legacy tier tokens) so old callers still work.
- **Acceptance:** existing tests pass; new test proves segment+tier round-trip
  and the specialist rubric hint appears for a retrieval-segment lookup.

### Chunk 5 — agent role descriptors (28 files) + capability-registry reconcile
- **Owns:** `agents/*.md` (28), `scripts/build_capability_registry.py`,
  `tests/test_capability_registry.py`.
- **Does:** add a `segment:` line beside each agent's `tier:` (adding `tier:`
  where missing, mapped from the current `model:`), per the role table below.
  Keep `model:` as the harness-default + fresh-install fallback. Reconcile
  `build_capability_registry`: replace `MODEL_HINTS={opus,sonnet,haiku}` and
  `_classify_tier→opus|sonnet|haiku` with taxonomy-sourced tier classification
  (read frontmatter `tier:` first, fall back to mapping `model:` via taxonomy);
  add `segment` to each agent entry; fix the `dispatch_fallback` unknown-category
  baseline failure by adding a `model selection / tier` keyword route.
- **DO-NOT-REMOVE invariant (scope-auditor):** `build_capability_registry`
  removes ONLY `MODEL_HINTS` + `_classify_tier`. It must KEEP
  `_parse_capability_header` (imported by `scripts/test_script_relevance.py:21`)
  and the built registry JSON's per-entry `category` + `tier` keys (consumed by
  `scripts/capability_shortlist.py:140` and
  `tests/test_phase_1_shortlist_mandatory.py:55`). Do not over-prune. Also note
  `scripts/exec_state.py:42,74` imports `resolve_model` — its signature is
  preserved by C2's alias layer, so no edit there.
- **Acceptance:** `tests/test_capability_registry.py` ALL green (including the
  previously-failing `test_no_unknown_category`); `test_script_relevance.py`,
  `test_phase_1_shortlist_mandatory.py`, `test_orchestrator_skeleton.py` stay
  green; every agent frontmatter has a resolvable (segment, tier); a
  fresh-install resolve returns a concrete model for every role.

### Chunk 6 — prose source-of-truth sync
- **Owns:** `references/model-tier-mapping.md`, `skills/model-tiering/SKILL.md`,
  `CLAUDE.md` (§Model Tiering only), `README.md` (tier line only).
- **Does:** document the two axes + the 7-rung ladder + legacy aliases + the
  selection (preferred list + recency) and classification (segment+tier) rules;
  point at `references/model-taxonomy.json` as the data source. CLAUDE.md
  describes AXES + rules only (no model IDs, per repo rule). State the dormant-
  segment status explicitly.
- **Acceptance:** `methodology_drift_lint.py --strict` passes (no enforced-
  invariant drift — the Review sub-step sequence is the only enforced invariant
  and is untouched).

## Role → (segment, tier) table for all 28 agents
(segment ids: GR=generative_reasoning, AE=agentic_execution,
GE=governance_evaluation; legacy-tier shows the alias that preserves today's
model)

**Segment discriminator (the boundary rule — plan-critic finding 4):**
- **AE (agentic_execution)** — the agent's primary job is to ACT: produce/modify
  code or artifacts, drive tools, or coordinate other agents (orchestrators,
  implementers, assessors, scout, ui-validator, design-contract-specialist).
- **GE (governance_evaluation)** — the agent's primary job is to JUDGE a
  produced artifact and emit a verdict/finding (all critics, auditors,
  reviewers, scanners, alignment/synthesis checkers).
- **GR (generative_reasoning)** — the agent's primary job is to SYNTHESIZE new
  prose/analysis where the output is the reasoning itself, not a
  verdict-on-someone-else's-work and not an executed change
  (advisor, retrospective-synthesizer, self-improvement-architect, the
  pattern-detector/miner whose product is a written pattern proposal).

Tie-break for the Learn/Review surface: synthesis-critic + alignment-checker
JUDGE (GE); retrospective-synthesizer + self-improvement-architect + the
pattern detectors PRODUCE a written artifact (GR). The concrete model is Sonnet
either way, so this segment split is forward-looking, not behavior-changing.

| Agent | Segment | Tier | Legacy | Concrete (default) |
|---|---|---|---|---|
| advisor | GR | T1 | frontier | fable |
| plan-critic | GE | T1 | frontier | fable |
| scope-auditor | GE | T1 | frontier | fable |
| independent-auditor | GE | T1 | frontier | fable |
| fix-critique | GE | T1 | frontier | fable |
| fact-checker | GE | T1 | frontier | fable |
| security-reviewer | GE | T1 | frontier | fable |
| overfitting-reviewer | GE | T1 | frontier | fable |
| promotion-reviewer | GE | T1 | frontier | fable |
| build-orchestrator | AE | T2 | thinking | opus |
| assessment-orchestrator | AE | T2 | thinking | opus |
| implementer | AE | T3 | code | sonnet |
| optimize-runner | AE | T3 | code | sonnet |
| api-assessor | AE | T3 | code | sonnet |
| database-assessor | AE | T3 | code | sonnet |
| frontend-assessor | AE | T3 | code | sonnet |
| performance-assessor | AE | T3 | code | sonnet |
| architecture-scout | AE | T3 | code | sonnet |
| design-contract-specialist | AE | T3 | code | sonnet |
| ui-validator | AE | T3 | code | sonnet |
| retrospective-synthesizer | GR | T3 | code | sonnet |
| self-improvement-architect | GR | T3 | code | sonnet |
| synthesis-critic | GE | T3 | code | sonnet |
| alignment-checker | GE | T3 | code | sonnet |
| mock-scanner | GE | T4 | pattern | haiku |
| recurring-pattern-detector | GR | T4 | pattern | haiku |
| transcript-pattern-miner | GR | T4 | pattern | haiku |
| root-cause-investigator | (inherit — unchanged) | — | — | inherit |

Every concrete-default column EXCEPT root-cause-investigator (which stays
`inherit`, deliberately exempt from a fixed (segment,tier) binding) matches the
agent's CURRENT `model:` value, so the role binding is behavior-preserving on a
fresh Anthropic install. (27 of 28 bound; 1 inherits — plan-critic finding 5.)

## Dormant vs active segments (stated explicitly)
- **active** (live resolver consumer): generative_reasoning, agentic_execution,
  governance_evaluation.
- **partial:** representation_retrieval (embeddings used by debugging-memory;
  no agent dispatch resolver).
- **dormant (DATA + reference only — no resolver wiring):** realtime_interaction,
  perception_input, generative_media. Encoded for future skills; no live walk.

## Back-compat alias proof obligations (tests, Chunk 2-3)
1. `frontier→fable, thinking→opus, code→sonnet, pattern→haiku` resolve
   unchanged (existing test, must stay green).
2. `modelOverrides.{frontier,thinking,code,pattern}` honored (existing test).
3. New: each legacy token normalizes to its ladder rung and resolves the SAME
   model via the ladder path.
4. New: floor clamp on the ladder — `frontier`(T1) with fable+opus unavailable
   stops at T2/thinking, never T3/code.
5. New: recency breaks a tie between two equal-rank candidates.
6. `route_decision` still emits `thinking|code` (its self-test + new test file).

## Risks & mitigations
- **R1 harness reads `model:`** — leaving `model:` intact means the harness still
  picks the right default; the orchestrator's M2.5 resolution (tier/role) is the
  layer that gains the new behavior. Mitigation: do NOT remove `model:`; it is
  the documented fresh-install default. (This is why locked decision #2 is
  satisfied by ADDING role tags, not deleting model names.)
- **R2 second vocabulary regression** — build_capability_registry must source
  tier from the taxonomy, not a private MODEL_HINTS. Mitigation: delete
  MODEL_HINTS, import from model_taxonomy; the no-unknown test gates it.
- **R3 test breakage** — run the FULL changed-file subset after every chunk;
  any red is a stop-and-fix before the next chunk.
- **R4 floor invariant under ladder** — the one-edge clamp is the same rule on a
  longer ladder; dedicated test asserts it.

## Depends-on (reads-from)
- `references/model-taxonomy.json` — verified (created by C1, then read by C2-C6; the single data source)
- `scripts/model_taxonomy.py` exposed surface (`normalize_tier`, `preferred`, `model_meta`, `released`, `LEGACY_ALIASES`, `TIER_RANK`, `LADDER_FALLBACK`, `SEGMENTS`) — verified (created by C1)
- `scripts/model_overrides.py` `MODEL_REGISTRY` / `TIER_DEFAULTS` / `TIERS` / `resolve_with_tier_fallback` — verified (read in Assess; C2 re-expresses these over the taxonomy, preserving every public symbol)
- `.build-loop/config.json` `modelOverrides.{frontier,thinking,code,pattern}` — verified (read by `resolve_model`; back-compat contract preserved)
- `.build-loop/state.json` `config.modelOverrides.thinking` — verified (read by `route_decision.read_state_thinking_override`)
- `.build-loop/model-availability.json` (`unavailable[]`, `hostProviders[]`) — verified (read by `model_resolver.load_unavailable` / `load_host_providers`)
- `.build-loop/model-tier-cache.json` (id → `{tier, segment, provenance, provider}`) — verified (read by `model_resolver.load_tier_cache`; C4 adds the `segment` field to records)
- agent frontmatter `tier:` / `model:` / new `segment:` — verified (read by M2.5 dispatch resolution + `build_capability_registry.crawl_agents`)

## MODULARITY note
One taxonomy data file + one loader module is the single source of truth; every
consumer imports it. This is the high-cohesion / one-owner structure the repo
prefers. No second resolver engine; dormant segments are data only.
