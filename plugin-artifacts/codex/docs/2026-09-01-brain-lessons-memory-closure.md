---
slug: brain-lessons-memory-closure
title: "Brain post lessons, and the one gap that is measured rather than argued"
date: 2026-09-01
status: findings + measurement; nothing implemented
primary_source: "Perplexity, 'Self-improving Memory for Agents', 2026-06-18, recovered from the Wayback Machine on 2026-09-01"
---

# Brain post lessons

## Provenance

Read from the **primary text**, not secondary coverage: the canonical URL 403s to
every live fetcher, but `web.archive.org` snapshot `20260623170705` of
`perplexity.ai/hub/blog/self-improving-memory-for-agents` returns the complete
article. Wording confidence high.

The vault already carries three related source pages
(`source-perplexity-self-improving-memory-for-agents-2026`,
`source-perplexity-brain-agentic-memory-knowledge-wiki-2026`,
`source-from-loops-to-graphs-self-improving-ai-agents-2026`), and the first of
those already reaches a conclusion. This document does not restate that analysis. It
adds the measurement it was missing.

## The lessons, in the order they matter

**1. Memory has two axes, and this stack is on the wrong one for the job.**
Brain's framing: memory differs by what it is ABOUT and what it is FOR.
Traditional AI memory is about the USER (preferences, tastes, contacts) and
exists to make the user feel engaged. Brain's is about **what the agent did**,
what worked, what failed, and what corrections were made, and exists to make the
agent better at the job. The post is explicit that the second "is the most
important purpose of memory."

Graded honestly: the memory under `~/.claude/.../memory/` is overwhelmingly
axis-one, about the user and how he wants work done. That is legitimately
valuable and should not be traded away. But almost nothing in it is a record of
what an agent did and whether it worked.

**2. Corrections are the highest-value memory event.** Brain remembers "when a
user has made a correction or when a source was a dead end."

This is NOT a gap here. `skills/build-loop/references/correction-aware-capture.md`
already specifies a three-tier design whose stated motivation is that "a user
correcting the assistant's just-taken action, the highest-signal lesson event in
a session, had no trigger and no destination." Same conclusion, reached
independently, already designed. Do not rebuild it.

**3. Every entry must link back to where it came from.** Brain: "Every memory
entry links back to the session, file, or source that it came from." Partially
present already: the vault has `raw_ref`, and `harness memory add` carries a
`source` field.

**4. Synthesis is incremental and scheduled, not on-demand.** Brain re-synthesises
overnight from sessions, connector results, source-document changes, and
corrections. `tools/scripts/vault_ingest_watch.sh` plus its launchd agent already
runs this shape for vault ingest, and shipped on 2026-08-29.

**5. The payoff is on REPEAT work.** The claimed gains are "+25% answer
correctness on tasks Computer has seen before" and "+16% recall", with "-13%
cost on tasks that require historical context". Read the scope: the correctness
number is conditioned on task recurrence. Work-memory pays where tasks repeat,
which here means build-loop runs, vault ingest, and bench runs, not one-off
questions.

**6. Token spend now is an investment in cheaper tokens later.** Useful framing
for the skills budget: the resident cost of a memory or skill manifest is only
justified if it reduces later turns.

**Caveat on every number above.** First-party, no methodology, no task list, no
cohort size, Research Preview. Directional.

## The measured gap

Everything above is either already designed here or already shipped, with one
exception, and it is the one Brain's whole mechanism depends on: **this stack
records that memory was READ but never records whether the recall HELPED.**

Measured 2026-09-01 with `python3 scripts/memory_health.py`:

```
reads (all tiers) : 41155
outcome-labelled  : 0
memory-use rows   : 0
memory-effect rows: 0
closure rate      : 0.0000%
```

Split by tier: the clean tier (2026-08-21 onward) has 312 reads at a 73.7%
return rate; the legacy tier has 40,843 reads at 11.4%. Something got
substantially better around 2026-08-21, which is itself worth knowing, and
neither tier has a single outcome label.

**This is a wiring gap, not a design gap.** `scripts/memory_telemetry.py`
documents the contract ("`effect: null` and a follow-up `memory-effect` row once
outcome is known, joining via `correlation_id`"), `scripts/memory_effect.py`
exists, and `scripts/memory_health.py` already reports
`reads_with_used / reads_with_effect`. A named search for callers found only
`memory_health.py` itself and `test_memory_effect.py`. Nothing in production
emits the outcome half.

**Consequence.** Without it, no recall can be scored, so nothing can be promoted,
demoted, or retired on evidence. A memory that is never read and a memory that is
read and useless are indistinguishable, which means the store can only grow. That
also makes the 11.4% legacy return rate uninterpretable: it is not knowable
whether the 88.6% that returned nothing were bad queries or missing content.

## Boundaries to keep

The vault's supervised ingest (seedlings, suggestion sidecars, human promotion)
is a deliberate governance choice. Brain's automated overnight write path is not
evidence that unsupervised synthesis is safe enough to trade for it. Take the
measurement, not the autonomy.

Similarly, `harness memory` already withholds unreviewed drafts from agents, so
an unvetted model output cannot steer a later model merely by having been
persisted. Closing the read-to-effect loop must not weaken that.

## In flight, checked 2026-09-01

Rally shows one handoff (ACKed) and five artifacts, of which four are this
session's own commits. Unmerged branches touching this area:

- `codex/vault-ingest-harness`  vault ingest audit pipeline plus bake-off,
  2,826 insertions, not merged.
- `fix/reliability-recency-window`  judges reliability on a recency window
  because "a fixed defect stayed on the record forever". That is lesson 4 applied
  to the bench, one commit, not merged.
- `codex/privacy-airlock`  13,598 lines, still unmerged, unchanged since
  2026-08-31.

Three rally risks are open: `unmanaged-agent`, `duplicate-active-squad-id`, and
`binary-drift: 0.2.5+b045f42 vs 0.2.5+03fe973`.
