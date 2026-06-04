---
name: intent-explorer
description: Lightweight intent-exploration pass for ambiguous or creative-open goals. Routed by the build-orchestrator at Phase 1 step 9.5 when scripts/intent_confidence.py returns should_explore=true. Surfaces ambiguity, names 2-3 viable approaches, narrows scope — writes findings to .build-loop/intent.md, never asks the user, never halts the run.
version: 0.1.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# intent-explorer

Run a brief, advisory intent-exploration pass when the goal text is ambiguous or creative-open. The skill produces a structured `## Exploration findings` section appended to `.build-loop/intent.md` and routes the summary to the run report's `## Notes from judges`. It NEVER calls `AskUserQuestion`, never blocks Phase 2, and never produces a `## Held` entry.

## When to use

The build-orchestrator routes here AUTOMATICALLY when `scripts/intent_confidence.py` returns `should_explore: true` for the goal text. Do not invoke manually.

The skill is not user-invocable. Override is `python3 scripts/intent_confidence.py --goal "<text>" --json` followed by manual orchestration if the user wants to force exploration on a goal the script scored `high`.

## What it produces

Append to `.build-loop/intent.md` a section of this shape (≤120 lines):

```md
## Exploration findings

### Surfaced ambiguity
- <one-sentence statement of the ambiguity the orchestrator detected>
- <second ambiguity if present>

### Restated intent (single line)
<one sentence — the most likely concrete restatement of the goal>

### Approach options
1. **<short label>** — <≤2 sentences on what + tradeoff>
2. **<short label>** — <≤2 sentences on what + tradeoff>
3. **<short label>** — (optional third)

### Recommended path
<one sentence naming option 1/2/3 and the reason>

### Scope cuts considered
- <thing the orchestrator believes can be cut without losing user value>
- <second if present>

### Open assumptions (TAG:ASSUMED)
- <assumption #1, with the evidence that would close it>
- <assumption #2>

### Confidence
<one of: now-high | still-medium | still-low>
```

## Process

1. **Read the inputs** — `.build-loop/intent.md` (already written by Phase 1 step 9 from the intent-capability-pack), `.build-loop/state.json.intent`, and the script's signals (passed in via orchestrator prompt as `signals: [str, ...]`).

2. **Restate the goal once, concretely** — write a one-sentence restatement that resolves the most likely interpretation. Bias toward the smallest reasonable scope.

3. **Surface ambiguity** — name each `signal` that fired and what it implies about what the user might mean. Do NOT enumerate every possible interpretation — keep to 1-2 ambiguities, the ones a downstream subagent would actually trip on.

4. **Generate 2-3 viable approaches** — each with a one-line description and a one-line tradeoff. Lead with the recommended option. Avoid speculative "we could also" lists.

5. **Identify scope cuts** — name 1-2 things the orchestrator believes are out of scope (or can be cut) for the smallest reasonable solution.

6. **Tag assumptions** — every inference the exploration makes that isn't grounded in the repo gets a `TAG:ASSUMED` line with the evidence that would close it.

7. **Re-score confidence** — restate confidence after exploration. If the restated intent + the recommended path are concrete enough, write `now-high`. If still ambiguous, write `still-medium` or `still-low`.

8. **Append to intent.md** — write the findings section to `.build-loop/intent.md` (append, do not replace). Mirror a compact summary into `.build-loop/state.json.exploration` (object with `restated_intent`, `recommended_approach`, `confidence_after`).

9. **Return to orchestrator** — return a one-paragraph summary. The orchestrator routes it to `## Notes from judges` in the Phase 4 Review-G report.

## Key constraints

- **Lightweight** — total exploration including write should fit under 1500 tokens of orchestrator output. The skill is a brief pass, not a Socratic dialogue.
- **No questions to the user** — the skill states what it inferred and what it assumed. The user reads the exploration findings in the run report and can override on the next dispatch.
- **Default to action** — if the goal is ambiguous but a reasonable concrete restatement exists, take that restatement and proceed. The exploration findings document WHAT was assumed.
- **Never block** — exploration is advisory. The orchestrator proceeds to Phase 2 Plan with the restated intent regardless of `confidence_after`. The `confidence_after` only governs whether Phase 2 plan-critic should treat the build as higher-risk (Path A vs Path B gate, scope-auditor caller-audit threshold).
- **Auto-execute on confidence still applies** — if `confidence_after == "now-high"` and the plan classifies SAFE per `scripts/classify_action.py`, no further gates fire and the orchestrator proceeds normally.
- **Fork on uncertainty** — if `confidence_after` stays `still-medium` or `still-low` AND Phase 2 surfaces 2+ viable approaches, the orchestrator's standard "fork on uncertainty" rule fires (parallel worktrees per approach). Exploration provides the approach options that fan-out consumes.

## References

Loaded on demand:

- `references/exploration-prompts.md` — concrete prompt templates for the 4 most common ambiguity patterns (vague-verb, branching-or, creative-open, hedge-phrase)

## Why this is a skill, not a phase

- A hard phase loads on every build. This skill is loaded only when the deterministic detection script fires `should_explore: true`. The auto-execute fast path is unaffected.
- Spec-writing already handles the "write a plan once intent is clear" job. This skill handles the prior "clarify intent when it isn't yet clear" job. Different stage, different output.
- Advisory contract is enforceable in a skill body. A phase prose section is harder to bound.

## Source basis

Distills the core mechanism of `superpowers:brainstorming` — explore intent + propose options + name assumptions BEFORE implementation — into a build-loop-compatible, non-interactive form. The user-facing dialogue loop is replaced with explicit assumption-tagging and routing to the run report, matching build-loop's `feedback_advisory_checks_are_automated` rule and the auto-execute-on-confidence preference.
