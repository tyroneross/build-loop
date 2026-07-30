# Plan: Fundamentals + Harness research → verified-remaining improvements

<!-- checklist
Item 1 — Auth guard: N/A: no server routes; edits are to Rust/TS internals + markdown policy docs.
Item 2 — External APIs: N/A: no new external API calls (harness already talks to Ollama /api/chat; unchanged here).
Item 3 — Rate-limit criterion: N/A: no paid external API introduced.
Item 4 — Discoverability: N/A: no end-user UI surface; NavGator output is a CLI/MCP report already surfaced via `navgator review`.
Item 5 — Server/client boundary: N/A: no Next.js server/client split; NavGator is a Node CLI/MCP, build-loop items are Python scripts + markdown.
Item 6 — Concurrency: N/A: read-only analysis (NavGator metric) + append-only run-report field; no new write path with contention.
Item 7 — Observability: build-loop run report gains a `harness:{}` line (F-06) alongside the existing `models:{}` line; NavGator emits the depth metric in its existing review JSON. No new telemetry sink.
Item 8 — Input validation: N/A: no new route handlers; NavGator metric consumes the already-validated architecture index; build-loop verify reads its own run state.
Item 9 — Stable ID traceability: U-01→F-02→T-02 (verify hardening), U-02→F-05→T-05 (NavGator depth metric). Full chains in Spec Object.
Item 10 — JSON spec object: present below (## Spec Object (JSON)).
Item 11 — Blocking-and-novel question gate: no open blocking questions — current state verified directly against live repos (P0/P3-harness already shipped); remaining items are additive and fully specified. No entries needing blocking-test annotation.
Item 12 — Low-reversibility ADRs: N/A: all changes reversible — additive metric (NavGator), additive advisory field (build-loop verify), and markdown doc edits. No schema/auth/public-contract change.
Item 13 — Analytical lens: DSM — cross-tool dependency mapping (which items are done vs blocked vs independent); items are MECE-partitioned by repo.
Item 14 — Handoff document: present at `docs/plans/2026-07-06-fundamentals-harness-improvements.handoff.md` — carries the per-feature build order + implementation pointers (F-02→ADR-01+T-02, F-05→T-05, V-00 first). It is the binding source for implementers; the plan is the spec.
Item 15 — Synthesis dimensions: N/A: no UI surface added or modified.
Item 16 — Risk reason: N/A: no high-consequence boundary (no security/persistence/runtime-protocol/deployment/user-trust-claim change). All additive/advisory.
Item 17 — UI input/output contract: N/A: no UI surface.
Item 19 — Env-var manifest: N/A: no new external service.
-->

## Goal

Land the *verified-remaining* improvements from the 2026-07-06 evidence review of the Pocock (deep-modules) and Kumar (harness) AI Engineer talks (`build-loop-memory/research/2026-07-06-ai-coding-fundamentals-and-harness-claims.md`). Direct verification against live repos during planning showed the two flagship "build-now" items were **already shipped** — so this plan is deliberately small: two additive code items plus policy/doc edits that encode the research's calibration corrections. Value: the system's verify/routing behavior matches the strongest current evidence, and the durable memory stops over-claiming unbuilt work.

## Locked Decisions

- **Analytical lens: DSM** — items partitioned by repo, sequenced by done/blocked/independent.
- **P0 (harness tool-call parser) and P3-harness (structural validators) are DONE** — verified: `dialect.rs` commit `8f3e525`, 12/12 dialect tests incl. `bare_json_parameters_key_parses_llama_case`, wired at `ladder.rs:74,142`; `crates/eval/src/validator.rs` emits empty-reply/phantom-completion/malformed findings with tests. These are **verification-only**, not builds.
- The research **validates** existing build-loop design (gated phases, deterministic gates, ensemble judges, holistic verify, the route-hard-work-up ladder). **Do not rebuild these.**
- Module-shape and codebase-quality guidance is a **cost lever, not an accuracy gate** (controlled minimal-pair study: ~0 pass-rate delta, −33.8% thrash). So P4/P5 are guidance, never build-blocking gates.

## Approach Lenses

- **Clean-sheet:** a unified "verification & architecture-signal" service spanning build-loop + NavGator (one metric/verdict schema, shared perturbation engine). Rejected — over-couples two independently-owned tools for no user-visible gain (Path-B flexibility-for-its-own-sake anti-pattern); the items are genuinely independent.
- **Current-constraints (chosen):** additive edits within each tool's existing seams — build-loop verify gains one advisory field + one helper, NavGator gains one metric in its existing insights module, policy lives in existing reference docs. Lower risk, MECE by repo, each reversible. This lens fits because the flagship code was already shipped; what remains is small and additive, so minimal-diff-in-place beats any new abstraction.

## ADR-01: No low-reversibility decisions

All changes are additive and reversible (advisory verify field, advisory NavGator metric, markdown policy edits). No DB/auth/public-contract/schema change. Alternatives, tradeoffs, and rollback are trivial (revert the commit); recorded here only to satisfy the low-reversibility gate. Trigger words in the scope text ("public schema", "auth") appear as *exclusions*, not decisions.

## Depends-on (reads-from)

Data paths / contracts the new code reads, each with verification status (verified against live repos during planning, 2026-07-06):

| Feature | Reads-from | Status |
|---|---|---|
| F-02 (verify hardening) | the Review verdict writer + `.build-loop/state.json` verdict shape | **unverified — exact writer not located during planning** (repo has ≥4 verify scripts: `plan_verify.py`, `self_mod_verify.py`, `verify_deploy.py`, `verify_release_surface.py`); Execute must locate the Review-B/D verdict emission path first and cite it before editing |
| F-05 (NavGator metric) | `ArchitectureComponent` / `CodeLocation` / `ArchitectureIndex` in `navgator/src/types.ts` | verified — read during planning (types.ts:70,229,349) |
| F-06 (harness report line) | Phase-4 report emitter | **unverified — no `models:{}` line found in repo** (`rg 'models:\{'` matched only proposals/this plan; the 2026-06-11 P1 proposal *proposed* adding it, it may not be built). Execute must confirm; if absent, F-06 adds BOTH `models:{}` and `harness:{}` (scope note below) |
| V-00 (harness re-test) | `dialect.rs` per-model dialect via registry; `/api/chat` transport | verified — `ladder.rs:74,142`, registry.rs dialect map, 12/12 tests |

> **Scope note (F-06):** because the `models:{}` line is unconfirmed, F-06's true scope is "ensure the Phase-4 report emits a `models:{}` + `harness:{}` config block." If `models:{}` already exists, add only `harness:{}`; if not, add both. Either way this stays a small additive report change.

## Scope

**Wave 0 — verify-only (no build):**
- V-00 Re-test qwen2.5-coder:32b on /api/chat now that the dialect parser exists (its Hermes-in-content calls were invisible pre-V3). Append result to `harness-gaps.md`.

**Wave 1 — policy/doc edits (build-loop + prompt-builder repos, markdown):**
- P1 "harness amplifies, does not replace, model capability" routing principle → `rosslabs-agent-harness/.../coordination/execution-policy.md` context + build-loop `skills/model-tiering`.
- P3-report Add a `harness:{}` config line to the Phase-4 run report next to `models:{}`.
- P4 "narrow public interface + small internal files + testable boundary" heuristic → `references/modular-systems-pack.md` (framed as cost lever).
- P5 Strengthen pre-plan alignment for architectural-class work (interrogation depth + required architecture note) → intent/spec-writing references.
- P7 prompt-builder scope note: "prompt is not the reliability lever — route reliability failures to harness/verify/guardrails."

**Wave 2 — small code (build-loop + navgator):**
- P2 build-loop verify hardening: record **oracle completeness** per verify verdict + an **isomorphic-perturbation spot-check** helper for high-risk outcome gates.
- P6 NavGator **module-depth / interface-width metric** (public-symbol-count ÷ internal-LOC proxy + shallow-cluster flag) exposed via `navgator review`.

### Out of scope

- Any rebuild of the harness tool-call parser or structural validators (already shipped).
- Any deep-module *refactoring mandate* or build-blocking module-depth gate (evidence: cost lever, not accuracy; unproven on correctness).
- Harness investment premised on making small local models reliable on reasoning-heavy tasks (evidence: capability ceiling; keep the routing ladder).
- Making NavGator's depth metric a hard gate — it is advisory input to P4 guidance.

## Spec Object (JSON)

```json
{
  "needs": [
    {"id": "U-01", "text": "Verify verdicts must not create false confidence from thin oracles or gamed outcomes"},
    {"id": "U-02", "text": "Operationalize deep-module guidance as a measurable signal so it is applied data-driven, not by vibe"},
    {"id": "U-03", "text": "System routing/verify policy should reflect the calibrated research (harness amplifies not replaces; verify ground-truth; quality=cost)"}
  ],
  "features": [
    {"id": "F-02", "need": "U-01", "text": "build-loop verify records oracle_completeness per verdict + perturbation spot-check helper for high-risk gates", "repo": "build-loop", "tier": "sonnet"},
    {"id": "F-05", "need": "U-02", "text": "NavGator module-depth/interface-width metric + shallow-cluster flag, surfaced in navgator review", "repo": "navgator", "tier": "sonnet"},
    {"id": "F-06", "need": "U-03", "text": "Phase-4 run report emits harness:{} config line beside models:{}", "repo": "build-loop", "tier": "sonnet"},
    {"id": "F-07", "need": "U-03", "text": "Policy/doc edits P1/P4/P5/P7 encode calibration corrections", "repo": "build-loop", "tier": "haiku"}
  ],
  "tests": [
    {"id": "T-02", "feature": "F-02", "text": "colocated test: a verdict with a thin oracle records lower oracle_completeness; a gamed outcome fails the perturbation spot-check"},
    {"id": "T-05", "feature": "F-05", "text": "navgator unit test: a many-tiny-modules fixture flags shallow-cluster; a deep-module fixture does not"},
    {"id": "T-06", "feature": "F-06", "text": "run-report snapshot contains a harness:{} key with tool-set/context-budget fields"},
    {"id": "V-00", "feature": null, "text": "qwen2.5-coder:32b /api/chat re-test result appended to harness-gaps.md"}
  ]
}
```

## Commit Table

Each row owns files in exactly one repo (MECE); no cross-repo commit. `Depends on` names only real ordering constraints — the code commits (C4–C7) do **not** depend on the doc commits.

| # | Commit subject | Files owned | Repo | Depends on |
|---|----------------|-------------|------|------------|
| 1 | chore(harness): re-test qwen2.5-coder:32b on /api/chat dialect parser; log result (V-00) | `projects/rosslabs-agent-harness/coordination/harness-gaps.md` | build-loop-memory | — |
| 2 | docs(policy): harness-amplifies-not-replaces routing principle (P1) | `projects/rosslabs-agent-harness/coordination/execution-policy.md` | build-loop-memory | — |
| 3 | docs(tiering): harness-amplifies note + module-shape cost-lever heuristic (P1/P4) | `skills/model-tiering/SKILL.md`, `skills/build-loop/references/modular-systems-pack.md` | build-loop | — |
| 4 | docs(prompt-builder): "prompt is not the reliability lever" scope note (P7) | `prompt-builder/skills/prompt-builder/SKILL.md` (locate exact file in Execute) | prompt-builder | — |
| 5 | docs(spec): strengthen pre-plan alignment for architectural-class work (P5) | `skills/spec-writing/SKILL.md` (Item-13 lens + a required architecture-note line) | build-loop | — |
| 6 | feat(verify): record oracle_completeness + perturbation spot-check helper (P2) | the Review verdict writer (located per Depends-on note) + colocated `test_*.py` | build-loop | — |
| 7 | feat(report): ensure Phase-4 report emits models:{}+harness:{} config block (P3-report) | the Phase-4 report writer (located per Depends-on note) + test | build-loop | — |
| 8 | feat(navgator): module-depth/interface-width metric + shallow-cluster flag (P6) | `navgator/src/architecture-insights.ts`, `rules.ts`, `mcp/tools.ts` + test | navgator | — |

## Parallelization

All eight commits are **parallel-safe** — they own disjoint files across four repos with no shared-state writes and no real ordering constraints (the `Depends on` column is empty for every row). Recommended grouping for a fan-out:

- **Group A (build-loop-memory, markdown):** C1, C2 — independent files.
- **Group B (build-loop, docs):** C3, C5 — disjoint files.
- **Group C (prompt-builder, docs):** C4.
- **Group D (build-loop, code):** C6, C7 — disjoint writers + own tests; land behind their Activation-Map live checks.
- **Group E (navgator, code):** C8.

Dispatch decision: **parallel_batch** = the 5 groups below (run concurrently); **parallel_skipped_reason** = none (no group is serialized — all own disjoint files with empty depends-on).

```yaml
parallel_batch:
  - group: A-blm-docs        # build-loop-memory markdown
    chunks: [C1, C2]
  - group: B-buildloop-docs  # build-loop reference/skill docs
    chunks: [C3, C5]
  - group: C-promptbuilder   # prompt-builder scope note
    chunks: [C4]
  - group: D-buildloop-code  # verify + report writers (behind Activation-Map live checks)
    chunks: [C6, C7]
  - group: E-navgator-code   # depth metric
    chunks: [C8]
parallel_skipped_reason: null   # nothing serialized — all groups independent, disjoint files, empty depends-on
```

`parallel_safe: true` for C1–C8. No commit reads another's uncommitted output; each is independently revertible. Groups run concurrently; within a group the ≤2 chunks are also independent. Cap fan-out at the machine default — no benefit beyond one implementer per group (8 small items).

## Activation Map (anti-dormant wiring)

New call-site components must be wired AND verified live, not just unit-tested — the documented ships-dormant-features failure. `verified-live` starts `pending`; a pre-Report task must flip it.

| Component | Populated/invoked by | Trigger | verified-live |
|---|---|---|---|
| `oracle_completeness` field (F-02) | the Review verdict writer, at verdict-emit time | every Validate/Fact-check verdict records what its oracle actually covers | pending — pre-Report: show one real run whose verdict carries a populated `oracle_completeness` |
| perturbation spot-check helper (F-02) | Review-B Validate | fires when `triggers.riskSurfaceChange` OR an F-criterion is plan-tagged `high-risk`; WARN-only, never blocks | pending — pre-Report: show it firing on one real high-risk gate (not just the fixture) |
| module-depth metric + shallow-cluster flag (F-05) | `navgator review` output assembly | every review run computes it; consumed as advisory input to P4 guidance | pending — pre-Report: `navgator review` on a real repo surfaces the flag |
| `harness:{}` report block (F-07/C7) | the Phase-4 report writer | every run-close | pending — pre-Report: one real run report shows the block |

**Pre-Report verification task (blocks the "done" claim):** each `pending` above must show live behavior on a real run/repo, not only a passing fixture. A component that unit-tests green but never activates is NOT done.

## F-Criteria (functional)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| F-02 oracle-completeness | verify verdict object carries a populated `oracle_completeness` field on a REAL run (Activation Map); perturbation helper flips a gamed-outcome fixture to fail AND fires on one real high-risk gate | colocated pytest (T-02) + activation check |
| F-05 depth metric | many-tiny-modules fixture → `shallow-cluster` flag true; deep-module fixture → false; flag present in a real `navgator review` | navgator unit test (T-05) + activation check |
| F-06/C7 report block | a real Phase-4 run report contains a `harness:{}` block with tool-set + context-budget fields (and `models:{}` present, added if it was absent) | snapshot test (T-06) + activation check |
| F-07 policy edits | each of the 4 docs (C2–C5) contains the calibration paragraph AND a literal link to `build-loop-memory/research/2026-07-06-ai-coding-fundamentals-and-harness-claims.md` | `grep -l "2026-07-06-ai-coding-fundamentals"` returns all 4 files |
| V-00 harness re-test | `harness-gaps.md` gains a dated row naming model=`qwen2.5-coder:32b`, endpoint=`/api/chat`, and an explicit outcome (parsed-N-calls / failed-reason) — a row saying "not run" does NOT pass | grep row fields |

## Q-Criteria (quality)

| Criterion | Pass condition | Grader |
|-----------|---------------|--------|
| Rust unchanged-green | `cargo test -p provider` + `-p eval` still pass (no harness regressions) | CI |
| NavGator build | `tsc --noEmit` + navgator tests exit 0 | CI |
| build-loop scripts | new script has colocated `test_<name>.py`; suite green | CI |
| No new untested script | every new `.py` has a colocated test | `pytest` collect |
| Doc links resolve | each policy edit links the research/lesson files by correct path | grep |

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Perturbation spot-check adds latency/flakiness to Review | Med | High-risk gates only; advisory (WARN), never blocks; opt-in per chunk |
| NavGator depth metric mis-flags legitimately deep single files as fine while a wide facade hides scatter | Med | Ship as advisory signal feeding P4 guidance, not a gate; unit-test both fixtures |
| Doc edits drift from code reality (the exact failure this plan corrects) | Med | Each doc edit cites the verified commit/file; re-verify on next touch (verify-the-negative) |
| Over-investing in guidance the evidence says is a cost lever | Low | Scope explicitly caps P4/P5 as non-gating guidance |

## Out of Scope

Mirror of Scope §Out of scope: no parser/validator rebuild (shipped), no deep-module refactor mandate or hard depth gate, no small-local-model reliability push, NavGator metric stays advisory.
