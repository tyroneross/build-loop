---
name: claim-scope
description: "Before stating any fact about a system, name the LAYER the claim lives at (working tree / repository / deployed / live behavior) and check that the instrument you used can actually reach that layer. A grep proves what is in one checkout; it can say nothing about what is running. Verify right-to-left: start at observed behavior, walk back toward source. Triggers on any assertion word — is / is not / there is no / does not exist / never / only / settled / confirmed / verified / impossible / cannot — and on any question of the form 'does X exist', 'is X deployed', 'did X ship', 'why does X do Y'. Not for grading evidence STRENGTH ([measured]/[correlated]/[reasoned]) — that is report_lint's mechanism-claim rule, which this extends with reach."
user-invocable: false
companion_scripts:
  - scripts/claim_scope_lint.py — the deterministic half. Flags a claim whose subject layer exceeds the reach of every instrument named on the line.
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Claim Scope

**The defect this prevents:** an agent runs a correct check, then states a
conclusion the check could not support, with full confidence and no hedge. The
check is not wrong. The *reach* of the check is wrong, and nothing in the
sentence records the difference.

Observed 2026-09-04. An agent grepped `@vercel/blob` in a local checkout of
a private app repo, found nothing, and reported: *"there is no blob path; the audio
lives only in Postgres; switching source is not possible."* The grep was
accurate. The checkout sat on a branch whose last commit was a day old, while
`origin/main` carried a merged blob migration that was **already deployed to
production**. The agent had reported a property of one working tree as a
property of the running system. Existing lint passed it clean, because the
sentence named its instrument (`grep`) and the mechanism-claim rule accepts
`grep` as an observation.

**Naming your instrument is not enough. The instrument must be able to see the
thing you are claiming.**

---

## The four layers

Every claim about a software system lives at exactly one of these. Decide which
one BEFORE writing the sentence.

| Layer | The claim is about | Nothing below it can prove this |
|---|---|---|
| **L1 · working tree** | this branch, this directory, right now | — |
| **L2 · repository** | origin, every branch, every worktree | L1 sees one branch of many |
| **L3 · deployed** | what is built and shipped and serving | L2 sees intent, not what shipped |
| **L4 · live behavior** | what it does when a person touches it | L3 sees the artifact, not its behavior |

The layers are a ladder, not a menu. **A claim at layer N requires an instrument
that reaches layer N.** Reaching higher is free and always valid; reaching lower
is the defect.

## Instrument reach — the lookup

Do not reason about this. Look it up.

| Instrument | Reaches | Blind to |
|---|---|---|
| `grep`, `cat`, `sed`, Read, Glob, reading a file | **L1** | other branches, other worktrees, what shipped, what it does |
| `git log`, `git status`, `git diff` (no fetch) | **L1** | anything not yet fetched |
| `git fetch --all` + `git log HEAD..origin/<branch>` | **L2** | whether it built or shipped |
| `git worktree list`, `git branch -a`, `gh pr list` | **L2** | same |
| `vercel ls` / `vercel inspect`, `gh run list`, deploy sha, build log | **L3** | runtime behavior under a real request |
| `curl -D-`, a real HTTP request, a screenshot, `ibr scan`, an AX probe, a live DB query | **L4** | nothing — this is the ground |

Two instruments that look similar and are not: `git log` reports what you have
fetched; `git log HEAD..origin/main` after a fetch reports what exists. The
first is L1. The second is L2.

## Procedure

Run this whenever a claim is about to be stated, not only when you doubt it.
Doubt is the thing that fails first.

**1. Write the claim as a sentence.** Then read it back and answer: *which layer
is this a claim about?* If the sentence contains "is deployed", "is running",
"is broken", "does not exist anywhere", "nothing does X" — it is L3 or L4, no
matter what you looked at.

**2. Look up the reach of every instrument you actually ran.** Not the ones you
could have run. The ones in your transcript.

**3. Compare.** If `max(instrument reach) < claim layer`, you have two legal
moves and no third:
   - **Go get the higher-layer evidence.** Usually cheap: one `git fetch`, one
     `curl -D-`, one `vercel ls`.
   - **Rewrite the claim down to the layer you actually reached**, explicitly:
     *"no blob dependency in the working tree of `fix/embedding-failures` at
     `3fe5298`"*. This is honest and often still useful.

   Stating the higher-layer claim anyway is the defect. There is no "probably".

**4. Stamp the time.** An L4 observation is true at its timestamp and nowhere
else. Record when you looked, not when you are speaking.

**5. Re-run before repeating.** Before restating an earlier L3/L4 result later in
the same session, re-run it. In the 2026-09-04 incident a correct `curl` at
05:32Z was still being quoted at 07:47Z; the deploy that falsified it landed at
07:07Z, inside the same conversation.

## Verify right-to-left

Default direction: **start at L4 and walk left toward source.**

Left-to-right (read source → infer behavior → assert it) manufactures confident
wrong answers, because the source you are looking at may not be the source that
is running. You cannot detect that from inside the source.

Right-to-left (observe behavior → explain it from the artifact → trace to the
code that produced it) cannot make that error. The rightmost layer is the only
one that cannot be stale about itself.

Worked example, the same incident done correctly:

- **L4 first.** `curl -D- -H "Range: bytes=0-1023"` → `HTTP 200`,
  `accept-ranges: none`. *Seeking is broken right now.* True regardless of any
  repo state.
- **L3 next.** `vercel ls` → production deploy Ready at 00:07:16 PDT.
  `gh run list` → CI red on that commit. *The blob code shipped.*
- **L2 next.** `git fetch` + `HEAD..origin/main` → the merge exists. `git
  worktree list` → the CI fix is stranded on an unpushed branch.
- **L1 last.** Read the merged route. It 307-redirects when `episode.audioUrl`
  is set and falls back otherwise. Now the L4 observation is *explained*:
  today's row predates the deploy, so it has no blob URL.

Reading the route first would have produced "seeking works now," which is false.
The L4 observation is what made the L1 reading mean anything.

## The pause trigger

These words may not be written until step 1–3 has run:

> is · is not · there is no · does not exist · never · only · nothing ·
> no code · zero · settled · confirmed · verified · impossible · cannot ·
> already · still

Seeing one in a draft sentence is a full stop. Answer out loud: *which layer,
and what instrument reached it?* If you cannot name both, you do not have the
claim yet.

## Absence claims need a layer, always

"X does not exist" is the single highest-risk shape, because a null result looks
identical at every layer. `grep` returning nothing and the thing genuinely not
existing are indistinguishable from inside L1.

Every absence claim ships with its scope written into the sentence: *what was
searched, at which layer, at what commit or time.* An absence claim with no
scope is not a weak claim. It is not a claim.

## Escape hatches

- **The higher layer is unreachable** (no credentials, no network, the service
  is down): say so, state the claim at the layer you reached, and name the exact
  command that would close it. Never silently downgrade and speak at full
  confidence.
- **The user asserts a higher-layer fact that contradicts your lower-layer
  check** ("there was a migration earlier today"): they are almost certainly
  right, because they can see layers you did not check. Go check L2 and L3
  before responding. Do not defend the L1 finding.
- **A record or prior session says SETTLED.** That is a claim to re-verify, not a
  fact to inherit — and re-verify it at the layer it is asserted about. See
  `feedback_reverify_relayed_citations_before_acting`.

## Relationship to the existing gates

This does not replace them. It adds the missing axis.

- `report_lint.py` `mechanism-claim-unobserved` grades **strength and
  provenance**: did you name an instrument, and is it `[measured]` /
  `[correlated]` / `[reasoned]`. It accepts `grep` and cannot tell that a grep
  is blind to L2/L3. `claim_scope_lint.py` grades **reach**, and is the reason
  the 2026-09-04 sentence would now be caught.
- `runtime-parity-verification` asserts rendered UI == backing store for a flow
  you just changed. Same instinct, narrower scope: it fires on shipping work.
  `claim-scope` fires on *saying things*, including in pure analysis with no
  diff.
- `verification_claim_probe.py` re-executes a relayed claim's literal command.
  Use it when the claim came from a subagent; use `claim-scope` on your own.

## Self-check before emitting

Before any report, answer these three. If any answer is missing, the report is
not ready.

1. For every factual claim: which layer, and which instrument reached it?
2. For every L3/L4 claim: what time was it observed, and has anything in this
   session changed since?
3. For every absence claim: what exactly was searched, and at which layer?
