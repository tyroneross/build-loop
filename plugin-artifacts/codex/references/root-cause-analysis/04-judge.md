# RCA Judge — independent evaluator (NEW vs source suite)

> Closes the source suite's biggest gap: it tells you to LABEL claims FACT but never forces verification. This judge requires it. Must run in a context INDEPENDENT of the RCA author (a self-review is not an independent verdict).

## Mandatory verification gate (run BEFORE scoring)
If the RCA makes checkable claims AND source is available (repo/logs/commits/trace): independently verify ≥3 load-bearing claims with tools; the claim driving the top corrective action MUST be among them. Record checked/refuted/could-not-check.
- Skipped verification when possible → cap criterion 1 at 3.
- Any headline claim refuted → criterion 1 ≤2 and verdict cannot be Accept.

## Criteria (1–5 each, with rationale)
1. **Evidence grounding** (subject to the gate; ground the score in what you checked; are FACT tags actually verified?).
2. **Creation+escape both explained** (not just creation; control existed/fired/bypassed analysis present?).
3. **Root vs contributor separation** (not a single chain when multiple contributed?).
4. **Action strength** (does it prefer eliminate/forcing-function over train/doc? penalize "be more careful"/docs-as-sole-fix and any banned closure).
5. **Lever + Actuator** (every root cause has an actuator = something that fires? penalize dormant fixes with no trigger).
6. **Regression artifact** (old-fails/new-passes artifact named + located? for agentic, a loop-fix not just a code patch?).
7. **Tiering & density** (level matches impact; L0/L1 not over-built; no padded empty tables; spread check tool-bound not prose).
8. **Residual risk & spread** (residual risk owned; spread check actually scanned similar sites?).

## Output
`# RCA Judge` → `## Verification performed` (checked + result, or why unavailable) → `## Overall` [avg]/5 → `## Summary` (2–4 sentences) → `## Scores` (table: Criterion | Score | Rationale) → `## Required fixes` (1–3) → `## Verdict` (Accept / Accept with revisions / Reject and rerun).

## Acceptance
Runs the verification gate (or justifies why not) and grounds criterion 1 in checked claims; penalizes weak/banned closures, dormant fixes (no actuator), missing regression artifacts, and padded over-tiered output; gives a clear verdict.
