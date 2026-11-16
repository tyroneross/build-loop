# Route Build Loop by task shape

Build Loop should retain one entrypoint and choose direct execution, a fixed
chain, or adaptive orchestration. Parallel workers and evaluator-driven iteration
are additions to those structures. Keep cross-run memory available without
requiring unattended execution. This is a testable design change; improved
performance has not yet been demonstrated.

## Evidence and limits

Reviewed November 15, 2026 (Pacific). These local reports describe bounded tests,
not a representative estimate of all coding work:

| Study | Observed result | Limitation |
|---|---|---|
| [Medium coding](../../../prompt-model-benchmark-lab/docs/benchmarks/2026-11-15-build-loop-medium-coding.md) | Bare completed and passed 3/4; Build Loop 2/4. Saved artifacts passed 3/4 in both. Median time 73.76 versus 145.39 seconds. | Two tasks; one treatment timed out during closeout with staging restrictions; delegation was disabled and review remained owed. |
| [Normal delegation](../../../prompt-model-benchmark-lab/docs/benchmarks/2026-11-15-build-loop-full-options.md) | All four passed. Build Loop took 487.12 versus 303.97 seconds for Sol and 83.69 versus 68.28 for Terra. | One task; disabled Sol still read Build Loop guidance; shared Rally delays limit causal attribution. |
| [Mac Sync](../../../prompt-model-benchmark-lab/docs/benchmarks/2026-11-15-mac-sync-build-loop-comparison.md) | Same final shared quality bar; Build Loop used 2.04 times active time and 4.12 times observed tokens. | One task with overlapping host load. Build Loop caught a containment case earlier and supplied verified recovery bundles. Real inter-Mac transfer remained unverified. |

The confirmed routing limitation is narrower than a performance diagnosis:
the entrypoints used file/line thresholds, then defaulted implementation to the
full protocol. They could not express a predictable multi-file chain. These
studies justify testing a more selective policy; they do not identify phase
loading as the sole cause or establish that removing reviews preserves quality.

## What the articles contribute

[Building effective agents](https://www.anthropic.com/research/building-effective-agents)
(December 19, 2024) distinguishes predefined workflows from model-directed agents
and recommends starting simple. Chaining fits known steps, routing separates task
categories, parallelization splits independent work, orchestrator-workers handles
dynamic decomposition, and evaluator-optimizer uses feedback to improve results.
The page now cautions that its tooling landscape has changed. Use the patterns,
not its historical tooling choices, as the basis for this update.

[Building always-on agents](https://claude.com/resources/articles/building-always-on-agents)
(November 13, 2026) separates individual runs from the infrastructure around them:
inbox, timers, memory, permissions, and outbox. Its memory guidance favors durable
progress, small relevant context, and pointers to externally retrievable facts.
Those principles apply to occasional runs. Timers and delivery infrastructure
become relevant when unattended work is actually requested.

Both articles are primary design guidance from one provider, not independent
evidence that these changes improve Build Loop.

## Options and decision

| Option | Benefit | Cost or risk | Decision |
|---|---|---|---|
| Keep full loop as default | Existing integration, review, and recovery contracts | Observed overhead; predictable tasks cannot choose a smaller structure | Retain for adaptive work and explicit repository mandates |
| Replace Build Loop with plain host execution | Lowest additional workflow burden | Must reconstruct review, recovery, and memory support where needed | Use direct work for bounded operations |
| Compose existing paths by task shape | Reuses tools and memory; supports known multi-file chains | Routing errors and policy compliance require evaluation | Implement as the reversible local policy change |
| Add a persistent agent service | Can handle scheduled or external events | New lifecycle, permissions, retries, delivery, and operating costs | Defer; outside the interactive need |

## Approach Lenses

Clean-sheet choice: a small task-shape selector around native tools and targeted
checks. Current-constraints choice: keep existing intent owners and the full
protocol, put one shared selector before heavyweight loading, and reuse memory
and progress storage. No new runtime classifier, database, daemon, or public
command is needed.

## Decision History

- Applied: git:166be873 already moves scope assessment before full loading on
  Codex; preserve that ordering and extend it to dependency-based selection.
- Applied: `references/memory-systems.md` separates canonical evidence, derived
  indexes, working context, and curated memory; reuse those stores.
- Update: record the task-shape policy and retain effectiveness as unverified
  until matched behavioral evaluation.

## Implementation and acceptance

1. Add a packaged shared selector, then point both host entrypoints at it before
   the full skill. Pass when both resolve the same policy and preserve specialist
   intent, read-only requests, resume debt, and required checks.
2. Align the canonical skill and orchestrator with the three structures. Pass
   when a known multi-file chain is eligible without silently bypassing mandatory
   review, recovery, or repository instructions.
3. Link the bounded memory contract to existing stores. Pass when memory remains
   optional for unrelated small tasks, mutable evidence is refreshed, and memory
   never supplies authority or completion proof.
4. Run command-surface contracts, skill-index drift check, the self-modification
   gate, and independent review. These verify policy wiring, not effectiveness.

The independent design challenge confirmed the missing fixed-chain route and
required preserving safety/recovery benefits. It recommended three structures
with parallelism and evaluation as additions, avoiding overlapping route labels.

## Behavioral evaluation still required

Reuse existing benchmark fixtures as regressions and add a fresh representative
task. Match task snapshot, model/effort, host, delegation, budget, permissions,
and memory conditions. Record actual route adherence, independent artifact
acceptance, required-review completion, wall time, tokens, correction turns, and
recovery separately. Include failures and timeouts. Compare memory disabled,
cold, and relevant warm retrieval separately so routing gains are not confused
with memory exposure. Do not infer billing from token counts with cached input.

No paid/model benchmark, installed-plugin update, or background service is part
of this source-policy change. Broader rollout should follow observed non-inferior
acceptance and review completeness with a useful cost/time benefit.
