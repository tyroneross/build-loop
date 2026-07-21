# Handoff: model-prompting-profile

Implementers read THIS file plus the named plan sections — not the whole plan. Plan: `docs/plans/2026-07-21-model-prompting-profile.md`.

Workdir for all commits: `/Users/tyroneross/dev/git-folder/build-loop/.build-loop/worktrees/run-835164` (branch `bl/run-835164`). Auto-commit via the orchestrator single-writer path; **do not push**. Per-commit self-mod gate: `python3 -m pytest scripts/test_model_taxonomy.py scripts/test_resolve_agent_model.py` must exit 0.

## Commit 2 — taxonomy block + accessor (F-01, F-04)

When implementing F-01, read ADR-01 + ADR-02 and satisfy T-01, T-02.

- **Transcribe** the JSON block from plan §"Profile Content" **verbatim** into `references/model-taxonomy.json` as a new top-level key after `"segments"`, and bump `"schema_version"` to `"2.1.0"`. Do not reword `_comment`, `confidence`, or `summary` strings — the epistemic markers are the artifact.
- `scripts/model_taxonomy.py`: add `prompting_profile(tier: str | None) -> dict | None` mirroring the existing accessor style (see `preferred()` line 160, `normalize_tier()` line 117). Normalize legacy tokens (`code` → T3 etc.) via `normalize_tier`. `T-S`/unknown/None → `None`.
- Loader validation (F-04): a function `unprofiled_tiers() -> list[str]` returning ladder rungs in `tiers.order` (excluding `T-S`) absent from `prompting_profiles.by_tier`. Runtime fail-open (no raise); tests assert detection (T-02 uses a synthetic taxonomy dict).
- Extend `scripts/test_model_taxonomy.py` for T-01/T-02 using the file's existing fixture style.

## Commit 3 — resolver envelope (F-02)

When implementing F-02, satisfy T-03, T-04, T-05. Read `scripts/resolve_agent_model.py` `resolve()` (lines 115–213) first — there are **five return sites**; every one must carry the key:

- resolved via role → `prompting_profile: model_taxonomy.prompting_profile(tier)`
- `inherit` → `prompting_profile: None` (caller's model unknown — never guess)
- `frontmatter-fallback` / `tier-default-fallback` → profile from the agent's declared `tier` (normalized), else `None`
- `unresolved` → `None`

Additive only: existing keys, `--plain` behavior, `resolution_path` semantics unchanged (T-05). Update the docstring envelope contract line. Extend `scripts/test_resolve_agent_model.py`.

## Commit 4 — brief template + orchestrator M2.5 (F-03)

When implementing F-03, satisfy T-06. Delete nothing (plan guardrail L8 — round-3 rigor stays).

- `references/implementer-brief-template.md`: add a section `## Tier-shaped brief` (after "Orchestrator-side preparation") stating: the orchestrator reads `prompting_profile` from the resolve envelope it already parses at M2.5; **T3 and below** → this template as-is (its rigor is repo-measured for this rung); **T2** → keep contract + owns/does-not-own + tests, compress worked reference examples to at most one, convert bare prohibitions to constraint-plus-rationale; **T1/T0** → additionally drop worked code stubs, state goal/context/falsifier, delegate edge cases (name known falsifiers instead of enumerating cases). Cite the profile `summary` string as the injectable one-liner.
- `agents/build-orchestrator.md`: in the M1/M2/M3 bullet (line 116 area), extend the resolve sentence: the envelope now also carries `prompting_profile` — apply it to the brief shape per `references/implementer-brief-template.md` §"Tier-shaped brief". One or two sentences; no new step, no new script call.

## Commit 5 — docs (discoverability)

- `CLAUDE.md` §Model Tiering: one short paragraph — taxonomy carries `prompting_profiles` (tier-keyed posture; consulted automatically via the resolver envelope; maintained via the same chat-maintenance path as the index).
- `skills/model-tiering/SKILL.md`: add a subsection under the chat-maintenance section — profile edits are index edits (edit `prompting_profiles.by_tier`, no re-sync needed since agent frontmatter is untouched); new-model adoption inherits the rung profile automatically; a new RUNG must add a profile row or `unprofiled_tiers()` flags it.

## Deterministic checks the orchestrator runs at Review-B

- T-06 greps (template section present, old headers survive, orchestrator names the field)
- T-07: `git -C /Users/tyroneross/dev/git-folder/RossLabs-AI-Assistant status --porcelain` — no run-authored changes
- T-08: `python3 scripts/sync_agent_model_defaults.py --check` exit 0
- JSON validity of the taxonomy file
- At Review-G: file the ai-assistant spec to Operations Center — `python3 scripts/file_to_operations_center.py --repo RossLabs-AI-Assistant --title "Mirror build-loop prompting_profiles into registry (spec: docs/plans/2026-07-21-model-prompting-profile.ai-assistant-spec.md)" --json`
