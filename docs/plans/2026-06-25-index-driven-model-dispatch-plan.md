---
title: Index-driven agent model selection + load-bearing role->model dispatch
date: 2026-06-25
base: 1a927fc (two-axis taxonomy merged)
branch: bl-index-driven-dispatch
status: in-progress
modifies_api: true
synthesis_dimensions: 3
intent: >
  Make agent model selection index-driven and flexible. (segment,tier) is the durable KEY
  into references/model-taxonomy.json; model: is the index-derived recommended fallback (generated,
  never hand-edited); at dispatch the live resolver computes best-available model from the role and
  OVERRIDES frontmatter. The index is user-editable and chat-maintainable.
---

# Goal

Frontmatter `segment`+`tier` are the durable KEY into the model index (`references/model-taxonomy.json`).
`model:` holds the CURRENTLY-RECOMMENDED model DERIVED from that index for the active host — generated,
never hand-edited. When the index changes, recommended `model:` values regenerate. At dispatch, the live
resolver computes the best-available model from the role and OVERRIDES the frontmatter. The index is
user-editable; build-loop consults/updates it when the user expresses model intent in chat.

# Deliverables (MECE — disjoint file ownership)

| # | Chunk | Owns (writes) | modifies_api |
|---|-------|---------------|--------------|
| C1 | `resolve_agent_model.py` + test | `scripts/resolve_agent_model.py`, `scripts/test_resolve_agent_model.py` | new public CLI |
| C2 | `sync_agent_model_defaults.py` + test | `scripts/sync_agent_model_defaults.py`, `scripts/test_sync_agent_model_defaults.py` | new public CLI |
| C3 | Wire dispatch | `references/m-series-protocol.md` (M2.5), `agents/build-orchestrator.md` (M1/M2/M3 + dispatch-fallback lines) | prose only |
| C4 | Chat-trigger index maintenance | `skills/model-tiering/SKILL.md`, one orchestrator reference line | prose only |
| C5 | Docs | `README.md` (agent-roles para), `CLAUDE.md` (Model Tiering) | prose only |

Dependency order: C1 -> C2 (sync reuses resolve logic) -> C3/C4/C5 (prose, depend on C1/C2 interfaces).
Each chunk is one commit.

parallel_skipped_reason: chunks are strictly dependency-ordered (C2 reuses C1; C3-C5 prose depends on the C1/C2 interfaces), so they are dispatched sequentially, not as a parallel_batch.

# Unknowns (resolved during Assess)

- Does `resolve_role` already do everything? YES. `model_resolver.resolve_role(segment, tier, workdir)`
  performs availability + host-provider filter + hybrid preferred-list + recency tiebreak + floor invariant.
  Both new scripts are THIN adapters over it. One resolution path; one source of truth. (DRY confirmed.)
- Back-compat on a fresh Anthropic install? Verified empirically: every agent's (segment,tier) cell resolves
  to its current `model:` token on an anthropic host. Zero drift. (Proof in Back-compat section.)
- Harness-valid `model:` tokens? Host auto-detects as `anthropic` (CLAUDECODE env), so the resolver only ever
  returns Anthropic-reachable ids — `fable/opus/sonnet/haiku`, the exact short tokens Claude Code accepts.
  `sync` additionally guards: it writes a resolved id into `model:` ONLY when that id is an Anthropic registry
  id (harness-valid); a cross-provider id is never written to frontmatter, the existing token is kept and
  reported. This makes the emit-only-harness-valid contract hard.

# Approach

## C1 — scripts/resolve_agent_model.py <agent-name>

Read `agents/<name>.md` YAML frontmatter (`segment`, `tier`, `model`). Resolution:
1. If `segment == "inherit"` or `tier == "inherit"` -> return `{model: "inherit", source: "inherit"}` (no override).
2. Else call `model_resolver.resolve_role(segment, tier, workdir)` (REUSE — no new resolution logic).
   Return `{agent, segment, tier, model, source, resolution_path}` passing through the resolver envelope.
3. Fallback chain when tags missing/unresolvable: agent `model:` -> tier default (`TIER_DEFAULTS[tier]`) ->
   error (exit 1, `source: unresolved`).
No vendor API calls. `--json` / `--plain` / `--workdir` / `--agents-dir`.

## C2 — scripts/sync_agent_model_defaults.py

For every `agents/*.md` with concrete (non-inherit) tags, compute the index recommended default for the active
host = top-ranked available id for that (segment,tier) cell (reuse `resolve_agent_model.resolve`). Normalize to a
harness-valid token; write into the `model:` line.
- `--check` (CI): report drift without writing; exit 1 if any drift, 0 if clean.
- `--apply`: rewrite drifting `model:` lines in place; idempotent.
- Inherit agents skipped. Cross-provider resolved id -> keep existing, report `skipped: non-harness-token`.
- Edits only the single `model:` line via exact-line replacement; never touches tier/segment/body.

## C3 — Wire dispatch (prose)

`references/m-series-protocol.md` M2.5: make `resolve_agent_model.py <name>` the canonical front door (reads BOTH
axes from the agent file in one call), replacing the manual read-tier-pass-tier pattern; keep raw
`model_resolver.py --tier/--segment` documented as the primitive. `model:` is fresh-install / non-build-loop
fallback only. `inherit` agents pass NO `model` override. `agents/build-orchestrator.md` M1/M2/M3 +
dispatch-fallback bullets reference the front door.

## C4 — Chat-trigger index maintenance (prose, host-LLM-driven)

Add `## Chat-triggered index maintenance` to `skills/model-tiering/SKILL.md`. On model-intent phrasings ("check the
model(s)", "is there a newer model", "change the <tier/segment> model", "use <model> for <role>", "what model is X
using"): (a) read the index; (b) check/newer -> report recommended vs available, offer classify via existing
`classify_model_tier.py`; (c) change/use -> edit index (preferred order / default / add classified model) then run
`sync_agent_model_defaults.py --apply`. Host-LLM recognizes intent (no vendor API, no hard hook); note
UserPromptSubmit hook as optional future hardening. Deterministic safe edit = documented jsonpatch-style edit (no
new script — KISS). Reference one line from the orchestrator.

## C5 — Docs

`README.md` agent-roles paragraph + `CLAUDE.md` Model Tiering: segment+tier key the index; `model:` is the
index-derived recommended fallback (kept in sync by `sync_agent_model_defaults.py`); dispatch resolves the role
live and overrides; the index is user-editable and chat-maintainable.

# Back-compat proof (verified in Assess)

On an anthropic host, `model_resolver.py --segment <s> --tier <t> --plain` returns:

    generative_reasoning/frontier  -> fable   (advisor: model: fable OK)
    governance_evaluation/frontier -> fable   (plan-critic/independent-auditor/...: model: fable OK)
    agentic_execution/thinking     -> opus    (build-orchestrator: model: opus OK)
    agentic_execution/code         -> sonnet  (implementer: model: sonnet OK)
    governance_evaluation/pattern  -> haiku   (mock-scanner: model: haiku OK)
    generative_reasoning/code      -> sonnet  (self-improvement-architect: model: sonnet OK)

Every cell == current `model:`. So `--check` reports 0 drift after sync, and the dispatch override is
behavior-preserving on a fresh Anthropic install. Legacy `frontier/thinking/code/pattern` keep folding to T1-T4.

# Depends-on (reads-from)

The new code reads only existing, tested contracts — no new data paths introduced:

- `scripts/model_resolver.py::resolve_role(segment, tier, workdir)` — the single resolution path. status: verified (Assess ran it across all agent cells).
- `scripts/model_overrides.py::TIER_DEFAULTS` + `resolve_role` + `normalize_model_id` + `MODEL_REGISTRY` — tier defaults, two-axis resolve, provider metadata. status: verified.
- `scripts/model_taxonomy.py` / `references/model-taxonomy.json` — the index (segment/tier cells, model provider). status: verified.
- `agents/*.md` YAML frontmatter (`segment`, `tier`, `model`) — read by both new scripts. status: verified (28 agents inventoried in Assess).

# Risks

- R1 (low) frontmatter parse fragility. Mitigation: exact single-line `model:` replacement, never a YAML rewrite;
  test covers malformed/missing tags -> fallback chain.
- R2 (low) writing a non-harness token to `model:`. Mitigation: sync guards on Anthropic-provider id.
- R3 (low) drift between sync output and dispatch resolution. Mitigation: both call the SAME `resolve()` — one path.

# Acceptance criteria

1. `resolve_agent_model.py <agent>` returns the resolver model for 3 agents across tiers == prior `model:`.
2. `resolve_agent_model.py root-cause-investigator` returns `inherit` (no override).
3. `sync_agent_model_defaults.py --check` reports 0 drift after `--apply` (idempotent).
4. New tests pass; model + dispatch suites pass; broad regression shows ONLY the 2 declared known failures.
5. Dispatch prose names `resolve_agent_model.py` as the front door; inherit stays inherit.
6. SKILL chat-trigger section present with the 5 phrasings + read/edit/re-sync flow.
