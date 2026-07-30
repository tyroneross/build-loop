# Root-Cause-Analysis — shared references

These are the shared RCA reference prompts consumed by multiple consumers. The canonical entry point for users is the `root-cause-analysis` skill (`skills/root-cause-analysis/SKILL.md`).

## Consumers (who delegates here)
- `skills/root-cause-analysis` — the user-invocable skill; links directly to these files.
- `skills/debug-loop` — its report/closeout step, for the durable-lever + regression-artifact + spread-check pass after a live fix.
- `skills/recursive-retrospective` §8 (Diagnostic RCA Module) — delegates L2 diagnosis to the `root-cause-analysis` skill, which in turn references these files.
- `agents/root-cause-investigator` — for the structured creation+escape-path + lever/actuator analysis.
- `agents/fix-critique` — to pressure-test that a fix is a system lever, not an exhortation.

## Boundary
- `debug-loop` / `debugging-memory` = **live, fix-this-bug-now** (iterative investigate→fix→verify).
- This = **blameless post-failure analysis** → durable lever + actuator + regression artifact + spread check. Runs *after* the fix, or on a class/pattern.

## The prompts
1. `01-rca.md` — general RCA, tiered L0 (log) / L1 (mini) / L2 (full).
2. `02-agentic-rca.md` — agentic-coding extension (deltas on 01: attribution gate, agentic failure modes, loop-fix vs code-repair).
3. `03-mini-rca.md` — lightweight L1.
4. `04-judge.md` — independent evaluator with a mandatory verification gate (a claim is `FACT` only if checked against source).

## Hardening baked in (vs the source RCA suite)
level↔schema binding (full schema is L2-only) · density governor (omit no-signal sections) · verify-before-FACT · tool-bound spread check · agentic prompt = deltas-not-restate · the judge's verification gate.

## Native strengths preserved
creation+escape paths · action-strength hierarchy (eliminate > … > train/doc; "be more careful" banned) · lever+actuator (anti-dormancy) · banned closures · regression artifact · agentic attribution gate.

## Model tiering
L2 RCA + judge are Frontier-tier (Fable); judge runs independent of the analysis author; Frontier-unavailable → Opus fallback, never Code tier.
