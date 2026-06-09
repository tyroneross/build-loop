<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Pay-it-Forward Architectural Posture (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Encodes the user's preference that scalability and product-roadmap unlocks matter more than short-term build velocity when costs aren't prohibitive.

## The rule (verbatim, user-stated 2026-05-11)

> *"I'd rather do a slightly harder thing now to avoid a more painful change in the future if not prohibited by costs or other concerns."*

When a chunk has two viable implementations:

- **Path A** — minimum-viable, working v1, easy to ship now.
- **Path B** — same user-visible behavior, but extends the typed / structural contract so future surfaces can reuse it.

**Default to Path B** unless one of the explicit gates below blocks.

## Gates (Path B blocked when ANY is true)

| Gate | Condition | Recommendation |
|---|---|---|
| **Time-budget gate** | Path B blows the immediate time budget by more than 2× (e.g. requires a schema migration when none was needed for Path A). | Fall back to Path A. |
| **Missing dep / infra** | Path B requires a dependency or infrastructure not yet in the project. | Fall back to Path A. Note the dep as a roadmap item. |
| **Missing design decision** | Path B requires a product/architecture decision the user has not yet made (multi-tenant boundary, auth model, etc.). | Fall back to Path A. Surface the decision in the plan's Open Questions section. |
| **Empty foreclosed-future-capability list** | Path B's "what does this unlock?" list is empty — no named future capability needs the typed contract. | Fall back to Path A. The "flexibility" is speculative. |

## Anti-pattern explicitly excluded

Path B that's flexibility-for-its-own-sake is **NOT** what this rule means. Excluded shapes:

- Plugin/extension systems with no named future plugin.
- Abstract factories / hook architectures for a single current consumer.
- Generic event buses for a single producer / single consumer pair.
- Parameterized configs whose only caller hardcodes one value.

Path B must be tied to a **NAMED** future capability that's in the roadmap, PRD, intent.md, or stated user goal. "Future flexibility" is not a named capability.

## When this fires

Phase 2 Plan must trigger a Path A / Path B comparison for any chunk that touches:

1. **A typed protocol or interface boundary** — engine types, API contracts, DB schemas, message schemas, envelope shapes, MCP tool input/output schemas, agent return contracts.
2. **User-facing behavior servable by multiple surfaces** — a feature that could theoretically be exposed via chat + voice + native + email + CLI. Even if today only one surface uses it.
3. **A consumer where the path of least resistance inlines the contract** — prompt templates that embed business logic, route handlers that JSON-shape ad-hoc, single components that own a state machine.
4. **A schema change** — any addition/modification to a typed boundary (Prisma schema, Pydantic model, TypeScript interface, JSON Schema, Protobuf message).

If the chunk fits NONE of these signals, skip the comparison and proceed.

## How to apply (Phase 2 Plan output)

For each chunk that fires the signal above, the plan must include:

```markdown
### Path A vs Path B — <chunk name>

**Path A (minimum-viable):**
- <one paragraph: what gets shipped, where the contract lives>
- Time estimate: <derived from t-shirt size>
- Limitation: <what future capability is foreclosed if we go this way>

**Path B (typed-contract extension):**
- <one paragraph: what gets shipped, where the contract lives>
- Time delta vs A: <e.g. "+30 min — adds 1 type definition + 1 module boundary">
- Unlocks (named future capabilities, NOT generic flexibility):
  - <capability 1, with citation to roadmap/PRD/intent.md>
  - <capability 2, with citation>

**Gates check:**
- Time-budget (>2× A)? <yes/no>
- Missing dep / infra? <yes — name it / no>
- Missing design decision? <yes — name it / no>
- Foreclosed-future list empty? <yes — explain / no — list above>

**Recommendation:** **Path B** (default) / **Path A** (because <named gate>).
```

The orchestrator's default recommendation is **Path B**. User may override to A on plan acceptance.

## Examples (from user's prior decisions)

- **Clarifier flow**: prompt-layer (A) vs engine-typed (B) — Decision Doctor C6b. Path B chosen because typed engine output unlocked voice + native surfaces named in the PRD.
- **Auth checks**: per-route gate (A) vs middleware abstraction (B). Path B chosen when 3+ routes need the check; Path A when only one route.
- **Theme**: hardcoded primary color (A) vs CSS-var token scaffold (B). Path B chosen when alt-theme is on the roadmap; A otherwise.
- **Search results**: route-shape JSON (A) vs typed result schema in `lib/` (B). Path B chosen when a second consumer (chat tool, RSS export) is already planned.

## Phase 4 Review-A Critic check

When reviewing a commit that landed on Path A, the critic asks: did the plan's Path A/B section name a gate that justified A? If the plan lacked the section entirely AND the chunk fits the signals above, flag as a strong checkpoint: **"missing Path-A-vs-B analysis on a typed-boundary commit."**

This is a process check, not a re-implementation request — the commit can still ship on A, but the synthesis decision should be on the record.

## Relationship to existing packs

- **Intent Capability Pack** (`references/intent-capability-pack.md`) — captures user value + non-goals. Provides the named-future-capability list this pack draws on.
- **UI Input/Output Contract** (`references/ui-io-contract.md`) — names every user input/output. Surfaces the "could be served by multiple surfaces" signal.
- **Modular Systems Pack** (`references/modular-systems-pack.md`) — MECE/cohesion/coupling defaults. Path B usually advances modularity; Path A often inlines responsibilities.

The three packs together establish *what* the build is for and *how* it should be structured. The pay-it-forward pack establishes *which version* to ship when there's a choice.
