<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Worked example: batch-claim triage

Built 2026-08-26 for the PersonalLLMWiki planner backlog. The pattern is
reusable; nothing in it is specific to that vault beyond the data it was
pointed at.

Live page: `Backlog Triage Desk`, artifact `21486e50-8565-4e60-8000-4d9267904483`.

## The problem this variant solves

The base decision-queue pattern gives every decision its own card. That works
while the decisions are few and genuinely distinct. It fails when the queue is
large, because a page of 128 cards reproduces exactly the overwhelm that made
the user ask for a page in the first place.

The planner backlog was 128 open items past their date. Presenting 128 cards
would have been honest and useless.

## The insight that made it tractable

**Most of a large backlog shares a few causes.** Classify first, and the work
collapses. Of the 128 items:

- 28 named a clock time on a day that had passed.
- 13 were example text typed while setting the tool up.
- 4 were prep for meetings that had a recording proving they happened.

That is 45 items and three decisions. The remaining 83 were the real backlog.
The user ruled all three batches in three clicks and confirmed every claim
without a single override.

## The shape

Three levels, which is the same progressive disclosure the base pattern uses,
with a pyramid substituted for the flat card.

**Level 1 — the batch card.** One claim, then the evidence for it.

- `claim` — the governing thought. A falsifiable sentence stating what is
  true of every member, with a real predicate. *"These 28 tasks can no longer
  be done. Each one names an hour on a day that has already passed."*
- `because` — three peers that answer the one question the claim raises, each
  an independent fact, each counted from the data rather than asserted.
  *"23 name a clock time; the other 5 name a drop-off, a pickup, or a
  departure."*
- Two actions: rule the whole batch the recommended way, or rule it the
  opposite way.

**Level 2 — the drill-in.** A full-width panel listing every member of the
batch with its date and `file:line`, each keeping its own disposition
dropdown. Restates the claim at the top and offers two ways back.

**Level 3 — the residue.** Everything that did not classify, grouped by owning
scope, each row with a disposition and a note field.

## Why the claim and the evidence are load-bearing, not decoration

A batch ruling asks someone to close 28 things on the strength of three
bullets. That is only safe when three conditions hold.

**The claim is falsifiable.** "These are old" cannot be disagreed with
usefully. "Each one names an hour on a day that has passed" can be checked
against any row.

**The evidence is counted, not impressionistic.** Every number in the key line
was computed from the data before it was written. A bullet that turned out to
be decorative would be doing real damage at 28x leverage.

**The classification rule is stated, so the user can reject the rule rather
than the outcome.** The footer names how each class was assigned. Disagreeing
with "a line is *pinned* when it names a clock time" is a more useful
conversation than disagreeing with 28 individual verdicts.

**The drill-in exists.** A batch ruling you cannot audit is a guess you are
forced to trust. Per-item override inside the drill-in is what makes the
batch action a proposal instead of a demand.

## When to use this instead of the base pattern

| Signal | Base decision-queue | Batch-claim triage |
|---|---|---|
| Item count | Up to roughly 20 | 50+ |
| Item independence | Each needs its own judgment | Most share a cause |
| What blocks the user | Not knowing the tradeoffs | The volume itself |
| The page's job | Elicit N judgments | Collapse N into a few, then elicit the rest |

Use the base pattern when the items are peers with nothing in common. Use this
when you can honestly write one sentence that is true of thirty of them. If you
cannot write that sentence, you do not have a batch, and forcing one produces a
claim the evidence will not carry.

## Landmines specific to this variant

**Do not classify by repetition count.** The first pass grouped items by how
many day notes they appeared in, on the theory that a repeatedly-copied task is
durable work. It conflated two different things: a camp drop-off appeared four
times because camp ran four days, not because it was carried forward four
times. Classify by whether the task is time-bound; repetition count is a
different signal and answers a different question.

**Native `<select>` paints its own bevel.** On macOS the platform control draws
a gradient and inner bevel underneath any border you set. Combined with a
colored border for a "set" state, it reads as a glow, which the user rejected
on sight. Set `appearance: none`, draw the chevron as a background image, and
indicate state with a flat left rule rather than a fill or a ring. Same for
textarea and search inputs: `appearance: none; box-shadow: none`.

**A colored wash on a decided row is the same mistake at lower intensity.** Use
`box-shadow: inset 3px 0 0 <accent>` instead of a background fill.

## Variant on self-publish: single-source instead of two-copy

The base template keeps `HEAD_HTML` and `SAVE_BAR_HTML` as literal constants
that must be edited in two places whenever the markup changes, and SKILL.md
correctly warns about that hazard.

This page took a different route to the same safety. **CSS lives only as a JS
constant inside the app script and is injected into a `<style>` on boot**, so
the published document and the running document are styled from one source and
cannot drift. The script re-embeds itself with
`document.getElementById("app").textContent`, which is the same safe DOM read
the base template already relies on.

Both approaches avoid the real bug, which is capturing `document.head.innerHTML`
and sweeping up the viewer's injected bootstrap. Pick by which hazard you would
rather carry:

- **Two-copy constants:** first paint is immediate; every markup edit must land
  in two places or the saved page silently diverges.
- **Single-source injection:** no possible drift; first paint waits one frame
  for the style injection, which is invisible on a tool but would be wrong on a
  marketing page.

For a working surface the user returns to, single-source is the better trade.

## Verification that caught real defects

Run these before publishing; each one found something.

```bash
node --check <extracted app script>        # syntax

# 1. Render smoke test with a DOM stub: assert the item count, the number of
#    disposition controls, and the number of note fields all match the data.
# 2. Drill-in test: for each batch class, set the open-batch state, re-render,
#    and assert the panel emits exactly as many component rows as that class
#    has members.
# 3. Save round-trip: call buildDocument(), then assert the output starts with
#    <!doctype html>, that the app script re-extracts byte-identical to the
#    original, that a mutated ruling survives the trip, and that no raw
#    </script> escaped into the embedded source.
```

The round-trip test is the one that matters. It is the difference between
"the save button probably works" and knowing the saved page is the same
program.

## Reusability note

This was built against a planner backlog in PersonalLLMWiki, but nothing in the
shape depends on that. It applies to any large, classifiable set the user must
dispose of: a stale-issue sweep, a dependency-upgrade queue, a dead-code
inventory, an inbox of unrouted records. Point it at a set, find the classes,
write one claim per class, and make every claim auditable.
