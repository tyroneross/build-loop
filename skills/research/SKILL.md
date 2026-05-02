---
name: research
description: Generate a repo-grounded research packet before deciding whether/how to build. Pre-decision analysis with risks, best path, confidence, next action.
---

# Research — Pre-Decision Analysis

Produces a repo-grounded research packet without committing to implementation. Use when evaluating approaches, comparing options, or preparing a handoff.

## When to Use

- "Should I use X or Y?"
- "What's the best way to add Z?"
- "Evaluate this approach before I commit"
- "Research this before we build"
- The orchestrator routes here when intent is exploratory, not implementational

## Process

1. **Scan the repo**: Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/research_packet.py --scan --workdir "$PWD" --focus "<topic>"` to get repo context (manifests, entrypoints, focus hits, validation commands)

2. **Classify the task**: product, feature, algorithm, prompt, bugfix, or refactor

3. **Build the packet**: Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/research_packet.py --packet --workdir "$PWD" --task "<full request>" --mode balanced` to generate the structured research packet

4. **Review and present**: Read the generated packet, verify claims against the actual repo, adjust confidence if needed

5. **Archive**: Save to `.build-loop/research/YYYY-MM-DD-<topic>.md`

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
- Context coverage: high/medium/low
- Verification coverage: high/medium/low
- Evidence quality: high/medium/low
- Overall: high/medium/low

## Next action
[Concrete first step — could be "run /build-loop" or "investigate X further"]
```

## Modes

- `quick` — repo scan only, no external research, fast
- `balanced` — repo scan + selective external research when current facts matter
- `max_accuracy` — deep scan + external research + self-debug pass

## Integration

- Standalone: `/build-loop:research [topic]`
- From build-loop: orchestrator routes RESEARCH-intent requests here instead of the full loop
- After packet: user decides — `/build-loop:run` to implement, `/build-loop:optimize` to optimize, or shelve

## State

Packets archived to `.build-loop/research/YYYY-MM-DD-<topic>.md` with JSON frontmatter (confidence scores, mode, task type, timestamp).
