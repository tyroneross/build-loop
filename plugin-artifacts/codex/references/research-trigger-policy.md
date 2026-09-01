<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Research Trigger Policy

Build-loop uses Research deliberately when a task depends on current,
external, novel, reusable, or decision-grade information. The classifier of
record is:

```bash
python3 scripts/research_trigger.py \
  --workdir "$PWD" \
  --task "<goal text>" \
  --effort "<XS|S|M|L|XL>" \
  --cache-into-state \
  --json
```

The output is written to `.build-loop/state.json.researchGate` when
`--cache-into-state` is passed.

## When It Fires

Run the classifier during Phase 1 Assess, after memory load and before the
plan is accepted. Research is required when the task includes any of these
signals:

- Explicit research language: research, investigate, evaluate, compare, latest,
  current, look up, should I, recommendation.
- A new API, provider, package, model, framework, standard, deployment target,
  database, webhook, auth provider, or external service.
- Current external claims: versions, pricing, release notes, deprecations,
  laws/regulations/standards, official-doc behavior.
- Architecture boundary decisions where prior art matters: persistence,
  protocol, schema, deployment, security, memory/retrieval, Rally, plugin/hook,
  or cross-layer behavior.
- Reusable findings that should become a packet in `.build-loop/research/` or
  durable memory.

No trigger means no Research plugin run by default. A large T-shirt size alone
does not make a local mechanical edit research-worthy.

## Encounter Trigger: a world-fact in the code contradicts what I know

Everything above is TASK-shaped. It fires in Phase 1 from the goal text. This one is
ENCOUNTER-shaped and fires in any phase, including a run whose goal carries no
research signal at all.

It exists because a UI-only run on 2026-09-01 read
`CANONICAL_VENDOR_NAMES.set('xai', 'SpaceXAI')`, judged it a typo from parametric
memory, and filed a finding. The code was right: SpaceX absorbed xAI in Feb 2026 and
the entity rebranded to SpaceXAI on 2026-07-06, after the model's cutoff. The fix
would have reverted a company to its legacy name and fought the repair script that
migrates away from it.

**The asymmetry.** A search costs seconds. A wrong correction to a world-fact ships a
regression, and where a migration already encodes the new value it fights that
migration on every later run. Never trade the second for the regression.

### The tree

```
1. Am I about to claim that a value in existing code, data, config, or a fixture
   is factually WRONG about the world?
   NO  -> stop. No gate. Almost every read exits here.
   YES -> 2

2. Is the value in a cutoff-sensitive category?
   company/brand/product name, rebrand, acquisition, org structure, model ID,
   version number, pricing, API or method signature, deprecation,
   legal or regulatory name
   NO  -> normal review. Judge it on the code.
   YES -> 3. SEARCH BEFORE YOU ASSERT.

3. Two tells that the code is right and I am stale. Either one raises the bar:
   a. The value sits among siblings that are plainly correct
      -> it is likely the same KIND of rule, not the one outlier.
   b. A migration, repair script, test, or alias map already treats the other
      value as legacy
      -> someone reasoned about this deliberately. Go read that reasoning.

4. Search, then branch on the result:
   CODE WRONG   -> file the finding WITH its source and date.
   CODE RIGHT   -> do not change it. Add a dated comment saying why it looks
                   wrong. Retract the finding if one was already filed.
   INCONCLUSIVE -> do not change it. Report it unverified and name what would
                   settle it.
```

### The social case

A user agreeing with a premise I supplied is not confirmation of that premise. When I
sourced the claim, correcting the record outranks executing the request: say I was
wrong, cite the source, and do not make the change. An approved fix built on my own
bad information is still my error, not the user's decision.

### Required action when the code was right

The value looked wrong once and will look wrong again to the next reader whose
knowledge predates the change. Leave the reason behind, dated:

```
// <current name>, not <legacy name>: <what changed and when>.
// Verified <YYYY-MM-DD>. Do not "correct" this back; that is the legacy name,
// and <path to migration> migrates away from it.
```

Then capture it so the next run does not re-search the same question:

```bash
python3 scripts/reference_capture.py capture \
  --workdir "$PWD" --run-id "$RUN_ID" \
  --topic "<entity> naming" \
  --findings "<current name> since <date>; <legacy name> is the legacy name" \
  --source "<url>|T1" \
  --decision "kept the existing constant; added a dated comment" --json
```

## Source tiering & claim verification

When a packet makes external claims, the host LLM tiers its sources and grades
corroboration before stating anything as fact — self-contained, no external
tool required. The rubric lives with packet generation in
`skills/research/SKILL.md` § Confidence: tier each source T1–T4, classify each
claim's corroboration (✅ ≥2 independent T1/T2 · ⚠️ one T1/T2 or T3/T4-only ·
❓ single/T4/inferred), and never let a claim's confidence exceed its
corroboration. High-risk and `max_accuracy` packets decompose claims into
atomic facts and verify each before stating it. This is the cite-or-block rule
below applied claim-by-claim.

## Depth Rules

The Research plugin's own depth classifier remains authoritative when
available. `research_trigger.py` is the deterministic lower-bound and state
contract for the orchestrator.

| Effort | Default if a trigger fires | Memory recall depth |
|---|---|---|
| `XS` | `light` | compact |
| `S` | `light`, or `standard` for current/external work | focused |
| `M` | `standard` | standard |
| `L` | `standard`; `deep` for architecture/decision-grade work | deep |
| `XL` | `standard`; `deep` for architecture/decision-grade work | deep |

Risk and currentness override effort. Security, auth, privacy, payment,
billing, compliance, legal, medical, finance, production, and deep/thorough
user wording escalate to `deep` unless the work is explicitly scoped to a
local mechanical check.

Depth maps to build-loop research modes:

| Depth | Research mode | Expected output |
|---|---|---|
| `light` | `quick` | Local/source-of-truth scan; 0-2 sources; persist only if reusable |
| `standard` | `balanced` | Local plus official docs/web as needed; 2-5 sources; packet path required |
| `deep` | `max_accuracy` | Decision-grade multi-source work; 4-10 sources; persist by default |

## Enforcement

If `researchGate.blocks_final_claims == true` or
`researchGate.requires_citations_or_unavailable_note == true`, the final report
must not state current/external/API/package claims as facts until one of these
is true:

- The report cites the research packet and the packet cites source paths/URLs.
- The report says the current/external evidence was unavailable and labels the
  claim as unverified.
- The claim is removed from the final report.

If `researchGate.packet_path` is non-null, Phase 2 records it in the plan under
`## Research Context`, and Phase 4-G cites whether it was created, reused, or
explicitly skipped with rationale.

## Reference Capture (default-on corpus)

Whenever build-loop fetches external information **in any phase or mode** —
WebSearch, WebFetch, Context7, api-registry, or an official-docs read — and that
information **informs a decision in the run**, capture the EXTRACTED findings as a
dated reference file. This is not gated to research-run mode; it fires in normal
build/fix/refactor runs the moment a web/doc fetch feeds a decision.

Capture is routed through the canonical memory writer, never an ad-hoc Write:

```bash
python3 scripts/reference_capture.py capture \
  --workdir "$PWD" --run-id "$RUN_ID" \
  --topic "<short topic>" \
  --findings "<distilled findings — not raw HTML>" \
  --source "<url>|<T1|T2|T3|T4>" \
  --decision "<what this informed>" --json
```

This writes `<YYYY-MM-DD>-reference-<slug>.md` into the central store's
project `research` lane with `retrieved_at`, a per-content-class `refresh_after`
horizon (api-docs/pricing age in days; ecosystem surveys hold for a quarter;
specs for half a year), `content_class`, `source_urls` (each tier-tagged), and
`informed_decision`. The store stays uncommitted by default — the corpus grows
without touching the consumer repo's git.

**When to capture (do, not ask):**

- A `researchGate.research_required == true` run that actually consulted an
  external source → capture each distinct finding that fed a decision.
- Any phase that ran WebSearch/WebFetch/Context7/api-registry and used the result
  (method signature, pricing, version/release fact, library syntax, model ID).

**Freshness on read:** `context_bootstrap.py` scans the reference lane at Phase 1
and flags any reference past its horizon as `stale-needs-refresh` in the agent
brief (`packet.reference_freshness`). This is advisory — it routes to the run
report/notes, never an `AskUserQuestion` and never a block. A stale reference is
a signal to re-fetch when the topic is back in scope, not a gate.

**Anti-dormancy:** capture is a default behavior, not a dormant feature. The
activation test (`scripts/reference_capture/test_reference_capture.py` +
`scripts/test_context_bootstrap.py::ReferenceFreshnessTests`) proves the default
path writes a dated reference with the required fields and that the read path
flags a backdated one as stale.
