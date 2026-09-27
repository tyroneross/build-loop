---
name: research
description: Generate a repo-grounded pre-decision research packet — risk, best path, confidence, next action — before deciding whether/how to build something in this repo. Use when the user asks to research/investigate/evaluate options for an imminent build decision here. Not for open-ended general research unconnected to a build in this repo — use the separate `research` plugin for that.
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Research — Pre-Decision Analysis

Produces a repo-grounded research packet. Use when evaluating approaches,
comparing options, or preparing a handoff. The canonical routing contract is
`../build-loop/SKILL.md` § Routing: research-only ends with a recommendation;
research within an authorized build or maintenance task returns to that owner.

## When to Use

- "Should I use X or Y?"
- "What's the best way to add Z?"
- "Evaluate this approach before I commit"
- "Research this before we build"
- The orchestrator routes here when intent is exploratory, not implementational

## Process

1. **Scan the repo**: Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/research_packet.py --scan --workdir "$PWD" --focus "<topic>"` to get repo context (manifests, entrypoints, focus hits, validation commands)

2. **Classify the task**: product, feature, algorithm, prompt, bugfix, or refactor

   When the user requests an agent-architecture or context-handoff pilot, load
   `../build-loop/references/research-architecture-pilot.md`. Use its task-shape
   assessment and matched experiments before recommending a routing change.
   Ordinary research does not load the pilot or start additional agents.

3. **Build the packet**: Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/research_packet.py --packet --workdir "$PWD" --task "<full request>" --mode balanced` to generate the structured research packet

4. **Review and present**: Read the generated packet, verify claims against the actual repo, adjust confidence if needed

5. **Archive**: Save to `.build-loop/research/YYYY-MM-DD-<topic>.md`. Reuse relevant
   prior packets after checking freshness and applicability; refresh facts that
   could change the decision. State unresolved contradictions and what evidence
   would change the recommendation. Use existing reference capture only within
   the applicable persistence authority.

6. **Continue the requested workflow**: for research-only, deliver the evidence
   and recommendation. When the user already requested implementation or
   maintenance after research, return the packet to that owner and continue.
   Ask only for an unresolved consequential product choice or an existing
   confirmation gate. "Research how to build" is research-only. A heuristic
   continuation field never expands authorization.

## Output Format

```
# Research Packet

## Bottom line
[One sentence: what this is and the recommended path]

## What I found
- project kind, manifests, entrypoints, validation commands
- focus hits (files relevant to the topic)
- integration surfaces (APIs, auth, payments, deployment)

## Best path
[Recommended approach with reasoning]

## Why this path
[Evidence from repo analysis and domain knowledge]

## Risks / unknowns
[What could go wrong, what's uncertain]

## Confidence

Rate each axis high/medium/low, then set Overall to the floor of the three —
and never above Evidence quality when the packet rests on external claims:

- Context coverage — how much of the relevant repo was actually read
- Verification coverage — share of material claims checked against repo or source
- Evidence quality — strength of the sources behind external claims (rubric below)
- Overall — the floor of the above

### Source & claim rubric (apply to every external claim)

Tier each source: **T1** official docs / standards / primary data · **T2**
recognized experts / official eng blogs · **T3** reputable industry press ·
**T4** forums / SEO / unattributed. Then grade the claim's corroboration and
mark it inline:

- ✅ verified — ≥2 *independent* T1/T2 sources agree (independent = different
  orgs, not mirrors or one syndicated wire)
- ⚠️ partial — exactly one T1/T2, or only T3/T4 sources
- ❓ inferred — single source, T4 only, or your own inference

A claim's confidence can never exceed its corroboration.

### Verify before stating (high-risk / max_accuracy)

For security, auth, payment, legal, medical, finance, production, or any
`max_accuracy` packet: decompose each external claim into atomic checkable
facts (a number, a version, an API signature, a citation) and verify each
against a source before the packet states it. An unverifiable atom is labeled
❓ or removed — never stated as fact. This applies the cite-or-block rule in
`references/research-trigger-policy.md` claim-by-claim.

## Next action
[Concrete first step — could be "run /build-loop" or "investigate X further"]
```

## Modes

- `quick` — repo scan only, no external research, fast
- `balanced` — repo scan + selective external research when current facts matter
- `max_accuracy` — deep scan + external research + self-debug pass

### Max-accuracy completion contract

Do not label a packet `deep research` until it contains these populated, dated
artifacts: Scope and assumptions; Research questions; Source strategy; Search
log; Evidence register; Credibility rubric; Claim matrix; Contradiction log;
Synthesized findings; and Recommendation confidence. The packet generator
emits this structure and `--validate --mode max_accuracy` is the deterministic
gate. Empty placeholders remain provisional findings, not evidence-backed
claims.

## Integration

- Standalone: `/build-loop:research-run [topic]`
- From build-loop: research-only requests use this packet workflow; combined requests return to their build/maintenance owner afterward.
- During normal build-loop runs: `scripts/research_trigger.py` decides whether this skill should run, which depth to use, where to persist the packet, and whether current/external claims are blocked until cited. See `references/research-trigger-policy.md`.
- After packet: preserve the original request's stopping point; no repeated permission question for implementation already authorized.

## State

Packets archived to `.build-loop/research/YYYY-MM-DD-<topic>.md` with JSON frontmatter (confidence scores, mode, task type, timestamp).
