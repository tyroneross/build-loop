# Spec: Mirror build-loop's prompting profiles into RossLabs-AI-Assistant

**Delivery**: this spec lives in build-loop (`docs/plans/`). A SECOND run, working in `/Users/tyroneross/dev/git-folder/RossLabs-AI-Assistant`, carries it. This run changes zero files there (goal C5). Routed to that repo via the Operations Center queue at Review-G.

## Ground truth the second run must respect (verified 2026-07-21 by direct read, F7 refined)

The mechanism is a **hardcoded-path read at sync time**, feeding a mirror the rest of the system consumes. Both halves of that sentence matter, and the obvious-looking pointer is a decoy.

**The pointer is documentation, not wiring.** `registry/registry.json:45` and `registry/overlay.json:24` declare `"model_taxonomy_source": "~/dev/git-folder/build-loop/references/model-taxonomy.json"` under `policy`. `grep -rn model_taxonomy_source` over the repo returns **only those two declaration sites** — no code reads the field. Do not extend it and expect anything to happen.

**The real read is `registry/sync.py:104-106`:**

```python
MODEL_TAXONOMY = os.path.join(
    HOME, "dev", "git-folder", "build-loop", "references", "model-taxonomy.json"
)
```

`_discover_models()` (L433-446) loads that path on every sync and projects each model as:

```python
manifest[f"model:{mid}"] = {
    "kind": "model",
    "fingerprint": f"{m.get('segment','?')}/{m.get('tier','?')}",
    "source": "build-loop:model-taxonomy.json",
    "meta": {"segment": m.get("segment"), "tier": m.get("tier")},
}
```

`_taxonomy_models()` (L408-429) normalizes the `models` field and tolerates both dict and list shapes, so it survives schema drift on the build-loop side.

**Two consequences.** The read is offline and on-demand — a sync, never a per-dispatch call — so the caution against building a live runtime cross-repo read still stands. And `meta` projects `{segment, tier}` only, so `prompting_profiles` will **not** flow through on its own; it needs an explicit addition.

**Why this stays small:** the profile is keyed by **tier**, and the registry already mirrors tier for 44 models. The consumer needs one table plus a join on data it already has — no per-model records, no new sync mechanism.

## What build-loop ships (the contract to mirror)

As of build-loop taxonomy `schema_version 2.1.0`, `references/model-taxonomy.json` carries a top-level `prompting_profiles` block:

- `fields`: `["examples", "constraint_posture", "edge_case_handling", "rationale", "prompt_budget"]`
- `by_tier`: one record per capability rung `T0`–`T5` (`T-S`: null), each with the 5 fields + `confidence` + `summary` (a directly-injectable one-liner).
- Key semantics: **tier-keyed** — a model's profile is looked up via its tier (which the registry already mirrors in `capability_fingerprints` as the `/<tier>` half).
- Epistemic markers are part of the data: `confidence` is `verified-source` (T0–T2), `repo-measured(...)` (T3, partial), or `weak` (T4/T5 — the source explicitly declined to endorse the small-model half). Preserve them in the mirror; do not strip.

## Work items for the second run

1. **Extend `registry/sync.py`** to copy `prompting_profiles` from the taxonomy file into the registry as a top-level key beside `capability_fingerprints`. Read it from the existing `MODEL_TAXONOMY` constant (L104-106) — the same `_load_json(MODEL_TAXONOMY, {})` call `_discover_models` already makes at L441; do not add a second file read and do not route through `policy.model_taxonomy_source`, which nothing consumes. Same refresh trigger as fingerprints, no new sync mechanism.

   Optionally also widen the per-model `meta` at L446 to carry the resolved profile inline. Judge this in that repo: it denormalizes (the same profile repeats across every model sharing a rung) but removes the join at the consumer. The table-plus-join shape in item 2 is the recommended default.
2. **Consumer lookup**: wherever ai-assistant resolves a model for dispatch/routing, derive the tier from the existing `capability_fingerprints` value (`"<segment>/<tier>"` → split on `/`) and index `prompting_profiles[tier]`. Zero extra calls at dispatch — same automatic-consultation principle as build-loop's resolver envelope.
3. **Freshness**: inherited from the sync — a re-sync after build-loop taxonomy changes refreshes both fingerprints and profiles together. If a fingerprint's tier has no profile row, treat as unprofiled (log/flag, fail-open).
4. **Tests**: per that repo's conventions, cover (a) sync copies the block intact including `confidence`/`summary`, (b) tier extraction from a fingerprint indexes the right profile, (c) unprofiled tier fail-open.

## Acceptance criteria

- `registry/sync.py` refresh produces a registry containing `prompting_profiles` byte-equivalent (modulo formatting) to build-loop's block.
- A lookup for `model:haiku` (fingerprint `generative_reasoning/T4`) returns the T4 profile with `confidence: "weak"` intact.
- No live read of the build-loop path at runtime; consumption is from the synced mirror only.

## Non-goals for the second run

- Re-authoring profile content (build-loop's taxonomy is the single source of truth; content changes happen there and flow through sync).
- Calendar-based freshness (the contract is event-driven: sync on taxonomy change).
