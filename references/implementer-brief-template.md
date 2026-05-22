# Implementer Brief Template — Reference

Based on round-3 (example-app 2026-05-07) reflection: implementer brief quality directly affects implementer output quality, especially in Mode A (Sonnet fan-out) where each implementer cannot see siblings' files. Round-3 briefs were 30-40% longer than round-2's because of specific patterns that closed common gaps. This template bakes those patterns into a reusable structure so every future build-loop run starts at round-3 quality, not orchestrator-memory quality.

## When to use

- Phase 3 Execute: orchestrator dispatching `Agent(subagent_type="build-loop:implementer", ...)` per chunk in the plan.
- Each parallel implementer in Wave N gets one brief from this template, populated with chunk-specific data from the plan.
- Inline-implementer mode (Mode B): same template applies — orchestrator self-applies it as the structure for its own per-commit work.

## Template structure (fill these sections)

```markdown
Apply Commit <N> (C<N>) of <feature-slug>.

## workdir
`<absolute path>` — branch `<branch-name>` (HEAD = `<sha>` <if applicable: prior-wave context>)

## brief
Read `docs/plans/<slug>.handoff.md` §"Commit <N>" + ADR-<NN> in the spec.

## Commit <N> deliverable
`<conventional-commit subject — type(scope): summary>`

### owns (your scope — do not touch other files)
- `<absolute path 1>` — <one-line role>
- `<absolute path 2>` — NEW. <one-line role>. Verify path matches `jest.config.js` testMatch BEFORE creating
- ...

### does not own
- <named other-commit surfaces, explicit>. Future commits handle those.

### contract (concrete, not pseudocode)
```ts
// Public exports + types EXACTLY as they should appear post-commit:
export interface <Name> { ... }
export async function <name>(...): Promise<...>
```

If the implementation uses an external SDK, include the SDK call shape verified against current docs:
```ts
const response = await client.<method>({
  // exact param shape
});
return { /* exact return */ };
```

### Repo-verified reference files (NON-NEGOTIABLE)
List ≥1 existing file you've ALREADY GREPPED that demonstrates the pattern. Format: `<path>` — <which pattern>. Examples:
- `app/api/articles/route.ts` — GET-with-Zod handler with `Object.fromEntries(searchParams)`
- `lib/services/tts-client.ts` (existing) — server-only + lazy-client pattern
- `components/settings/NewsPreferencesPanel.tsx` — save-on-change topic-checkbox pattern

The implementer cites these in its return envelope. If a reference path doesn't exist, the implementer returns `evidence_stale`.

### Schema-field-uncertainty warnings (when applicable)
For ANY commit that reads/writes a Prisma model, include:
> ⚠️ Field name `<expected>` vs `<alternate>` may differ — read `prisma/schema.prisma` to verify. Use the ACTUAL field names. Common drift points: `entityType` vs `domain`, `publishedAt` vs `createdAt`, denormalized vs computed counts.

This is the single most preventable round-3 bug class — write the schema-grep into the brief, not the implementer's own discovery.

### v2 briefing patterns (NON-NEGOTIABLE)
1. JSDoc one line per public export, no prose blocks
2. Test cap: T-<id> + T-<id> + ≤2 unspec'd edge cases = <math> max
3. Reference file mandate: cite + verify path before mirroring (above)
4. Intentional non-fixes: list ≥1 in your return envelope
5. LoC discipline: ≤ +<N> lines (target stated explicitly)
6. Test path verification: confirm `tests/<path>` matches `jest.config.js` `testMatch` BEFORE creating

### Tests required (cite test IDs from plan)
- T-<id>: <one-line behavior>
- T-<id>: <one-line behavior>
- Plus ≤2 unspec'd edge cases at the implementer's discretion

### Verification before commit
- `npx tsc --noEmit` exit 0 (full-project — your file is part of it)
- `npx jest <your test files>` exit 0
- Pre-commit hook will run when the orchestrator commits — do NOT call `git commit` yourself (Hard rule 4)

### Concurrency note (Mode A only)
You're running in parallel with N-1 other implementers against disjoint `files_owned` sets. Do NOT call `git add`, `git commit`, `git push` (Hard rule 4 in implementer.md). Modify the working tree only; return `commit_subject` + `commit_body` + `files_changed` in your envelope; the orchestrator commits sequentially after all parallel implementers return.

## Return envelope
See **`references/implementer-envelope-schema.md`** for the canonical contract. Populate every required field — missing keys = malformed envelope.
```

## Required envelope

The implementer's return MUST conform to `references/implementer-envelope-schema.md`. Minimal checklist (every field below is required; use empty/null sentinels rather than omitting keys):

- [ ] `branch` — string
- [ ] `commit_sha` — string or `"pending"` (canonical when orchestrator commits)
- [ ] `files_changed` — array of paths (authoritative for orchestrator commit)
- [ ] `loc_added`, `loc_removed` — integers (`0` if none)
- [ ] `f_criteria` — `{F1: pass|fail, F2: ..., ...}` for every F-criterion in the brief
- [ ] `synthesis_attestation` — `{<dim>: applied|deviated|n/a, ...}` for each plan `synthesis_dimensions` entry; `{}` only when the plan has no such block. Use object form `{status: deviated, deviation_reason: ...}` for deviations.
- [ ] `novel_decisions` — array of `{decision, reasoning}`; `[]` if none, but field MUST be present. Required whenever a synthesis-class decision was made that the plan didn't enumerate.
- [ ] `notes` — free text, ≤200 words
- [ ] `wall_clock_seconds` — number

If the plan includes a `synthesis_dimensions` block and you find yourself making a synthesis-class decision NOT named there, **halt and add it to `novel_decisions`** rather than attesting silently (per `agents/implementer.md` Step 5).

## Attestation claim formulation

Two rules surfaced from the 2026-05-09 podcast-validation retest. Both are honest discipline that prevents `attestation_lint` revision cycles.

**Rule 1 — Anchor-visibility.** Placement claims must reference anchors visible in the *diff context window*, not anchors anywhere in the pre-image. `attestation_lint` only sees the diff hunks plus their context; it cannot verify a claim like "render after `<h1>AI Brief</h1>`" if the `<h1>` line isn't part of the changed hunk's context. Use the nearest diff-visible anchor (e.g. "render before `<AIBriefSections />` at line N") even when the underlying physical placement is the same.

**Rule 2 — `n/a` is correct when no signal added.** For dimensions that require positive evidence in the added lines (`cta_tier` needs a tier className; `visual_weight` needs a heading or divider), attest `n/a` when the commit adds no such signal. Do NOT claim `applied` because the dimension is "implicitly satisfied" by surrounding code. Claiming `applied` without matching diff evidence is exactly the silent-claim pattern the lint exists to catch — and the lint will reject it.

Worked example from the validation: a one-file edit added `<PodcastGenerator />` between `<h1>` and `<AIBriefSections />`. The implementer initially claimed `cta_tier: applied (primary)` and `visual_weight: applied (no <h2>)`. Lint rejected both — no `primary` className token in the added lines, and `no <h2>` is a negative claim a regex reads as positive. Honest re-attestation: both `n/a` because the commit's contribution carries placement intent without adding new tier/weight tokens. Placement remained `applied` with the anchor reformulated using the diff-visible `<AIBriefSections />` reference.

## Why each section matters

| Section | Round-3 evidence |
|---|---|
| `does not own` (explicit) | C6 implementer correctly didn't touch AIBriefPage because brief named what was out of scope; the issue was the brief didn't ALSO include AIBriefPage in scope. Fix: scope-auditor catches this pre-Execute. |
| Concrete code stubs | C5 implementer got the golden-angle constant + bit-shift hash for free; saved ~30-60s of figuring out math. |
| Repo-verified references | Round-3 C1 cited `app/api/trending-topics/route.ts` and `app/api/articles/route.ts` for `Object.fromEntries(searchParams)` — both verified by orchestrator grep before brief landed. The implementer didn't have to re-grep. |
| Schema-field-uncertainty warnings | Round-3 C4 brief warned about `entityType` vs `domain`; implementer correctly mapped. C1 brief did NOT warn about `summary` column non-existence; A's implementer wrote `summary` references that mocked-out in tests but would have broken at runtime. **This is the highest-value section to populate.** |
| Test cap with math | Round-3 implementers stayed at or under cap; round-2's looser caps led to two implementers exceeding by 30-40%. Showing the math (`T-01 + T-07 + T-08 + ≤2 unspec = 5 max`) makes the cap concrete. |
| LoC discipline target | Round-3 commits hit their LoC targets within ±15%; round-2 commits exceeded by 25.7% on average until v2 patterns codified. |
| Concurrency note (Mode A) | Round-3 had the parallel-commit race; PR #12 fixes it but the implementer needs to know its responsibility (no git writes). |

## Orchestrator-side preparation BEFORE writing the brief

Per round-3 lessons, the orchestrator should:

1. **Pre-grep schema fields** for any commit touching Prisma models. Read `prisma/schema.prisma`; note actual field names. Populate the schema-field-uncertainty warnings with the correct names + the WRONG names the implementer might assume.
2. **Pre-grep reference patterns**. Identify ≥1 existing file demonstrating the pattern this commit replicates. Verify path. Cite in the brief.
3. **Pre-compute LoC target**. Look at the plan's commit table; estimate based on file count + scope. Write the explicit target.
4. **Pre-compute test cap**. Count T-IDs assigned to this commit in the spec. Add the `+≤2 unspec'd` allowance. Show the math.
5. **Run scope-auditor** (PR #11) — caller-audit has already happened by the time you're writing the brief.

If the orchestrator can't populate any of these sections, the brief is too vague. Either the plan is missing detail (return to Phase 2) or the orchestrator needs to do more pre-grep work.

## Anti-patterns (don't do these)

- ❌ "Use the existing pattern" without naming the file. Round-2 evidence: implementers cited their own guesses, sometimes wrong.
- ❌ "Tests as appropriate" without a cap. Round-2 evidence: 30-40% test count overruns.
- ❌ Pseudocode for an SDK call. Round-2 C2 (TTS upgrade) had pseudocode; implementer wrote correct call shape but spent time confirming. Round-3 C5 (color mapping) had full code; implementer's first edit was complete.
- ❌ Skipping schema-field-uncertainty for Prisma touches. Round-3 C1 evidence: silent runtime bug masked by test mocks.
- ❌ Brief that fits on one screen for non-trivial work. Round-3 implementer briefs averaged ~80-120 lines each; round-2 averaged ~50-80. The longer briefs produced cleaner output.
- ❌ Attesting `applied` on a dimension when the commit added no positive signal for it (no tier className for `cta_tier`, no heading/divider for `visual_weight`). 2026-05-09 podcast-validation evidence: lint rejects the claim; `n/a` is the honest answer when the commit's contribution doesn't carry that dimension. See "Attestation claim formulation" above.
- ❌ Placement claims using anchors outside the diff context window. 2026-05-09 evidence: claim-text needs to reference an anchor the lint can see in the hunk's context; otherwise the claim is unverifiable even when the placement itself is correct.

## Note on brief size budget

Each implementer brief is its own input cost — the orchestrator pays Thinking-tier rate to write 80-120 lines × N implementers. For N=4 parallel, that's ~400 lines of brief text at Thinking rate. **This is a real cost** that doesn't show in implementer envelope token estimates. Track it in cost-ledger when TASK_ID instrumentation lands (PR followup).

The cost is worth paying when the work-shape supports parallelism (Mode A wins on time). For sequential or cross-cutting features, prefer Mode B (single Opus context) where the brief overhead disappears.
