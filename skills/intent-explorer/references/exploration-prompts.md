<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Exploration prompt templates

Loaded on demand by `intent-explorer`. Each template covers one common ambiguity pattern detected by `scripts/intent_confidence.py` and produces the structure required by the SKILL's output contract.

The templates are scaffolding — the orchestrator fills them in from the actual goal + intent.md + repo context. Each section header in the output is fixed; the body is adaptive.

---

## Pattern 1 — `vague_verb` (explore, figure out, see if, look into, play with)

The goal uses an investigative verb without a concrete target. Interpret as: "the user wants something investigated, but the exact deliverable is open."

Restate as one of:

- "Survey X and produce a one-page summary of <observed state, recommended action>"
- "Run a non-destructive scan of X and write findings to `.build-loop/research/`"
- "Read X and answer the implicit question: <one-sentence inferred question>"

**Approach options to consider:**

1. **Inventory pass** — list what exists, no judgments. Fastest. Tradeoff: user still has to decide.
2. **Inventory + recommendation** — list + opinion on best path. More work. Tradeoff: opinion may not match user's constraints.
3. **Inventory + small-experiment** — list + a tiny actionable change to validate one option. Most useful when the cost of the experiment is low.

**Default**: option 2 unless the repo shows zero prior similar work (then option 1).

---

## Pattern 2 — `branching_or` (X or Y)

The goal names two candidate paths. Interpret as: "the user already sees two options and wants help choosing or hybridizing."

Restate as: "Recommend X or Y for <restated underlying goal>, with the evidence that drove the choice."

**Approach options:**

1. **Adopt the cheaper option** — explicitly. Tradeoff: locked-in if requirements grow.
2. **Adopt the more general option** — explicitly. Tradeoff: more work now.
3. **Hybrid** — name the smallest combination that gets the user-value of both. Often the right answer when the user already named two options.

**Default**: option 3 if the two options aren't mutually exclusive; otherwise the one with fewer foreclosed future capabilities (per `pay-it-forward-arch.md`).

---

## Pattern 3 — `creative_open` (brainstorm, design from scratch, greenfield, open-ended)

The goal explicitly invites generative work. Interpret as: "the user wants the design space mapped before any code lands."

Restate as: "Map the design space for <target>, recommend a starting point, name the cuts."

**Approach options:**

1. **Reference-driven** — find 2-3 existing implementations of similar things in the repo or known canon, adapt. Lowest risk.
2. **Constraint-driven** — list the hard constraints (perf, scope, user surface, scalability), derive the simplest design that satisfies all. Best when constraints are sharp.
3. **Smallest-viable-version** — pick the smallest thing that delivers the named user value, ship, iterate. Default for creative-open scope without sharp constraints.

**Default**: option 3 unless the repo has 2+ obvious reference patterns (then option 1).

---

## Pattern 4 — `hedge_phrase` (something like, kind of, sort of, maybe, not sure)

The goal uses hedging language. Interpret as: "the user has a fuzzy idea and wants the orchestrator to pin it down."

Restate as: "The fuzzy idea is most likely <concrete restatement>; restated for clarity."

**Approach options:**

1. **Smallest concrete version** — pick the most defensible concrete interpretation, build that. Tradeoff: may not be what the user actually pictured.
2. **2-option preview** — name two interpretations, build the smaller as a probe. Tradeoff: more setup, more learning.
3. **Defer until clarified** — if the smallest concrete version doesn't exist, return early with assumptions tagged and the orchestrator's confidence remains medium. The user reads the run report and re-dispatches with a sharpened goal.

**Default**: option 1. Option 3 only when no concrete interpretation is defensible (rare).

---

## Output assembly

After selecting the template(s) — multiple may fire — fill in:

1. `## Surfaced ambiguity` — the signal(s) that fired and what each implies
2. `## Restated intent` — single concrete sentence
3. `## Approach options` — 2-3 from the templates above, recommended first
4. `## Recommended path` — option number + 1-sentence reason
5. `## Scope cuts considered` — list 1-2 things being excluded
6. `## Open assumptions (TAG:ASSUMED)` — every leap the exploration made
7. `## Confidence` — `now-high` if the restated intent + recommendation is concrete; `still-medium` or `still-low` otherwise

Append to `.build-loop/intent.md`, mirror compact summary into `.build-loop/state.json.exploration`, and return a one-paragraph summary to the orchestrator.
