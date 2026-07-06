# Plan: Spec-Writing Accuracy Addenda

<!-- checklist
Item 1 — Auth guard: N/A: docs/tooling change, no server routes
Item 2 — External APIs: N/A: no new external API calls
Item 3 — Rate-limit criterion: N/A: no paid APIs in scope
Item 4 — Discoverability: N/A: no user-facing UI surface
Item 5 — Server/client boundary: N/A: no runtime app code
Item 6 — Concurrency: N/A: no write path or shared runtime state
Item 7 — Observability: N/A: deterministic checker/docs change, no runtime telemetry
Item 8 — Input validation: checklist parser validates plan structure at script entry
Item 9 — Stable ID traceability: U-01 -> F-01 -> T-01 and U-02 -> F-02 -> T-02 documented below
Item 10 — JSON spec object: Spec Object (JSON) section present with needs[], features[], tests[]
Item 11 — Blocking-and-novel question gate: N/A: no open questions
Item 12 — Low-reversibility ADRs: N/A: reversible docs/tooling change
Item 13 — Analytical lens: QFD for need-to-feature mapping and DSM for file dependency checks
Item 14 — Handoff document: 2026-07-05-spec-writing-accuracy-addenda.handoff.md generated alongside this plan
Item 15 — Synthesis dimensions: N/A: no UI surface
Item 16 — Risk reason: N/A: no high-consequence boundary in scope
Item 17 — UI input/output contract: N/A: no UI surface
Item 18 — Dispatch tier per work item: script for checker/tests/artifact validation; sonnet/local for docs updates
Item 19 — Env-var manifest: N/A: no new external service
Item 20 — Capability gap map: current vs target gaps mapped below for checker, docs, and artifact packaging
Item 21 — Single-shot build guardrails: guardrails section lists domain-neutral and regression controls with evidence
Item 22 — Read-before-edit map: read-before-edit section maps each chunk to files/scripts to inspect first
-->

## Goal

Improve Build Loop's plan authoring accuracy by adding three generic execution controls to spec-writing: a capability gap map, single-shot build guardrails, and a read-before-edit map. The change should reduce rework without baking in any TruePace, ADHD, or domain-specific assumptions.

## Locked Decisions

Analytical lens: QFD for user need -> feature -> test mapping; DSM for source file dependency order.

| Decision | Rationale |
|---|---|
| Add controls inside the existing spec-writing checklist instead of a separate gap-closure plan | Keeps the implementer artifact single-source and avoids asking agents to reconcile two planning documents. |
| Enforce checklist acknowledgement for Items 18-22 | Prevents silent drift between documented checklist items and the deterministic checker. |
| Warn on missing accuracy sections for implementation plans, but fail only on missing checklist lines or existing structural fails | Introduces the new behavior without turning every legacy structural omission into a hard stop. |
| Keep wording domain-agnostic | This improves Build Loop generally and must not encode TruePace, ADHD, or planning-app assumptions. |

## Approach Lenses

| Lens | Answer |
|---|---|
| Clean-sheet best approach | Put current-state, anti-rework, and read-before-edit controls directly in the core plan object so every implementer sees one source of truth. |
| Current-constraints approach | Build on the existing spec-writing checklist, deterministic checker, and Phase 2 reference instead of introducing a new standalone planning artifact. |
| Bridge/backcast | Add Items 20-22 as checklist prompts now, warn structurally on missing implementation sections, and leave stricter gating for a later evidence-backed iteration. |
| Recommendation | Execute the current-constraints approach because it improves accuracy while preserving Build Loop's existing verifier and artifact packaging flow. |

## Scope

In scope:

- Update `skills/spec-writing/SKILL.md` with Items 20-22, add Item 18 to the output template, and add the new output sections.
- Update `skills/spec-writing/scripts/check_checklist.py` to enforce Items 18-22 and warn on missing implementation accuracy sections.
- Update `tests/test_check_checklist.py` for the enlarged checklist and structural warning coverage.
- Update `skills/build-loop/references/phase-2-plan.md` so Phase 2 invokes the new controls.
- Regenerate Codex plugin artifacts if source/package checks show artifact drift.

### Out of scope

- Any TruePace-specific plan content.
- Any ADHD-specific planning methodology.
- New runtime dependencies.
- Hard-blocking legacy plans solely because they lack the new structural sections.

## Depends-on (reads-from)

- `skills/spec-writing/scripts/check_checklist.py` — verified: current checker owns checklist parsing and structural warnings.
- `tests/test_check_checklist.py` — verified: current test surface covers checklist counts and structural validators.
- `skills/spec-writing/SKILL.md` — verified: current skill owns checklist prompts and the Plan Output Template.
- `skills/build-loop/references/phase-2-plan.md` — verified: current Phase 2 reference summarizes spec-writing and planning optimization gates.
- `scripts/build_codex_plugin_artifact.py` — verified: current artifact builder packages Codex plugin sources.

## Spec Object (JSON)

```json
{
  "needs": [
    {
      "id": "U-01",
      "description": "Build Loop plan authors need a single plan artifact that captures current state, target state, and exact closure work.",
      "priority": "P0"
    },
    {
      "id": "U-02",
      "description": "Implementers need repo-grounded context before editing so first-pass builds do not drift from existing contracts.",
      "priority": "P0"
    }
  ],
  "features": [
    {
      "id": "F-01",
      "need_ids": ["U-01"],
      "description": "Capability Gap Map and Single-Shot Build Guardrails in spec-writing"
    },
    {
      "id": "F-02",
      "need_ids": ["U-02"],
      "description": "Read-Before-Edit Map in spec-writing and checker"
    }
  ],
  "tests": [
    {
      "id": "T-01",
      "feature_ids": ["F-01"],
      "description": "check_checklist fixtures pass with Items 18-22 and structural warnings for missing accuracy sections"
    },
    {
      "id": "T-02",
      "feature_ids": ["F-02"],
      "description": "dogfood plan passes check_checklist and references the required read-before-edit section"
    }
  ],
  "adrs": []
}
```

## Six-Commit Table

| # | Commit subject | Files owned | Depends on |
|---|---|---|---|
| 1 | feat(spec): enforce accuracy addenda checklist | `skills/spec-writing/scripts/check_checklist.py`, `tests/test_check_checklist.py` | - |
| 2 | docs(spec): document plan accuracy addenda | `skills/spec-writing/SKILL.md`, `skills/build-loop/references/phase-2-plan.md` | C1 |
| 3 | docs(plans): dogfood spec-writing accuracy addenda | `docs/plans/2026-07-05-spec-writing-accuracy-addenda.md`, `docs/plans/2026-07-05-spec-writing-accuracy-addenda.handoff.md`, generated artifact paths if required | C1-C2 |

## Capability Gap Map

| Capability/Workflow | Current source of truth | Target behavior | Gap | Build action | Owned files/contracts | Validation |
|---|---|---|---|---|---|---|
| Checklist enforcement | `skills/spec-writing/scripts/check_checklist.py` Items 1-17 | Checker recognizes Items 1-22, including existing documented Items 18-19 and new Items 20-22 | Documented checklist and checker are out of sync; new anti-rework controls are absent | Extend `ITEMS`, update optional logic only where already intentional, add structural warnings for missing accuracy sections | `skills/spec-writing/scripts/check_checklist.py` | `uv run pytest tests/test_check_checklist.py` |
| Plan author prompt | `skills/spec-writing/SKILL.md` template and checklist | Spec-writing asks for gap map, guardrails, and read-before-edit sections in the main plan | Planner can skip current-state mapping and read requirements | Add Items 20-22 and output template sections | `skills/spec-writing/SKILL.md` | `python3 skills/spec-writing/scripts/check_checklist.py --plan docs/plans/2026-07-05-spec-writing-accuracy-addenda.md --json --quiet` |
| Phase 2 orchestration | `skills/build-loop/references/phase-2-plan.md` | Phase 2 names the new controls in the Plan protocol and optimization checklist | Orchestrator-level summary does not advertise the new planning controls | Add steps 3d-3f and optimization checklist bullets | `skills/build-loop/references/phase-2-plan.md` | Source review and targeted grep |
| Codex artifact packaging | `scripts/build_codex_plugin_artifact.py`, `plugin-artifacts/codex/` | Generated Codex artifact mirrors source docs/checker changes | Source edits can leave packaged plugin stale | Run artifact builder and check mode, commit generated drift if any | `plugin-artifacts/codex/**` if changed | `python3 scripts/build_codex_plugin_artifact.py --check` |

## Single-Shot Build Guardrails

| Guardrail | Prevents | Evidence/test |
|---|---|---|
| Keep the new sections generic and product-agnostic | TruePace or ADHD assumptions leaking into Build Loop itself | `rg -n "ADHD|TruePace|tur pace|planning app" skills/spec-writing skills/build-loop/references/phase-2-plan.md docs/plans/2026-07-05-spec-writing-accuracy-addenda.md` should only surface this guardrail if any |
| Align documented checklist and deterministic checker | Agents believe a checklist item exists but the verifier ignores it | `uv run pytest tests/test_check_checklist.py` covers 22 findings and missing-count changes |
| Warn, do not hard-fail, on missing Items 20-22 structural sections | Disruptive adoption across existing implementation plans | `TestStructuralWarningsItems20To22` asserts warn IDs for missing sections and no warn IDs when present |
| Keep existing UI and Items 9-14 behavior intact | Regression in established spec completeness checks | Existing `TestItems9To14ChecklistBlock`, `TestStructuralFailuresItem17`, and Item 10/11/13/14 tests still pass |
| Regenerate package artifacts after source edits | Codex cache artifact drift | `python3 scripts/build_codex_plugin_artifact.py --check` after builder run |

## Read-Before-Edit Map

| Chunk/Work item | Read first | Why it matters | Edit after |
|---|---|---|---|
| Checker enforcement | `skills/spec-writing/scripts/check_checklist.py`, `tests/test_check_checklist.py` | Existing optional-item semantics and structural warnings must remain stable | `skills/spec-writing/scripts/check_checklist.py`, `tests/test_check_checklist.py` |
| Skill documentation | `skills/spec-writing/SKILL.md` around Items 18-19 and Plan Output Template | New Items 20-22 must fit the existing checklist vocabulary and output convention | `skills/spec-writing/SKILL.md` |
| Phase 2 docs | `skills/build-loop/references/phase-2-plan.md` Step 0 and optimization checklist | Orchestrator summary must match spec-writing without duplicating a separate implementation-plan artifact | `skills/build-loop/references/phase-2-plan.md` |
| Artifact validation | `package.json`, `scripts/build_codex_plugin_artifact.py`, existing `plugin-artifacts/codex/` tree | Build Loop distributes through generated Codex plugin artifacts | `plugin-artifacts/codex/**` only through the artifact builder |

## F-Criteria (functional)

| Criterion | Pass condition | Grader |
|---|---|---|
| T-01 [P0] Checklist expansion | `tests/test_check_checklist.py` passes with 37 tests and Items 18-22 included | `uv run pytest tests/test_check_checklist.py` |
| T-02 [P0] Dogfood plan validity | This plan exits 0 under `check_checklist.py` | `python3 skills/spec-writing/scripts/check_checklist.py --plan docs/plans/2026-07-05-spec-writing-accuracy-addenda.md --json --quiet` |
| Artifact freshness | Codex artifact check exits 0 after regeneration if needed | `python3 scripts/build_codex_plugin_artifact.py --check` |

## Q-Criteria (quality)

| Criterion | Pass condition | Grader |
|---|---|---|
| Domain neutrality | No ADHD/TruePace-specific product logic in source docs/checker | targeted `rg` |
| Minimal scope | Changed files are limited to spec-writing, Phase 2 docs, dogfood plan/handoff, tests, and generated artifacts if required | `git status --short` and `git diff --stat` |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Existing plans fail because Items 18-22 become required checklist lines | Medium | Keep structural sections as warnings; require explicit `N/A` answers for non-implementation plans. |
| The new sections duplicate rather than improve planning | Medium | Place them in the main plan template; do not create a separate gap-closure plan by default. |
| Artifact drift hides source changes from Codex plugin users | Medium | Run artifact builder/check and commit generated changes if produced. |

## UI Input/Output Contract

N/A: no UI surface.

## Out of Scope

- TruePace-specific planning UX.
- ADHD-specific assumptions.
- New runtime dependencies.
