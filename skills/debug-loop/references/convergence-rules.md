# Convergence Detection Rules

Reference for the debug-loop iteration phase. Load on demand when entering iteration.

## Rule 1: Repeated Hypothesis Failure

**Trigger**: The same root cause hypothesis fails verification twice with the same or similar evidence.

**What it means**: The hypothesis is likely wrong. More of the same fix won't help.

**Action**: Escalate to user with this template:

```
I've attempted to fix this issue twice with the same approach:

Hypothesis: [what we thought the root cause was]
Attempt 1: [what was tried, why it failed]
Attempt 2: [what was tried, why it failed]

The evidence suggests this hypothesis may be incorrect, or there's a constraint
I'm not seeing. Options:

1. Investigate a different hypothesis: [alternative if available]
2. Provide additional context about the system's expected behavior
3. Narrow the scope to a specific aspect of the problem
```

**Do NOT**: Retry a third time with the same hypothesis. That's the definition of insanity.

## Rule 2: Oscillation

**Trigger**: Fixing criterion A causes criterion B to fail, and fixing B causes A to fail again.

**What it means**: The two criteria have a shared dependency or conflicting requirements. The fixes are fighting each other.

**Action**: Flag as coupled issue:

```
I've detected oscillation between two criteria:

- Fixing [criterion A] breaks [criterion B]
- Fixing [criterion B] breaks [criterion A]

This suggests a shared dependency or conflicting constraint:
[describe what the two criteria share]

Options:

1. Accept a tradeoff: prioritize one criterion over the other
2. Refactor the shared dependency to support both
3. Reconsider whether both criteria are correct requirements
```

**Do NOT**: Continue alternating fixes. Each iteration makes the code more tangled.

## Rule 3: Cascading Failures

**Trigger**: 3 or more criteria fail simultaneously after applying a fix (when fewer were failing before).

**What it means**: The fix introduced a systemic problem. The root cause may be deeper than expected, or the fix touched a critical path.

**Action**: Stop the loop entirely and reassess:

```
Systemic issue detected: [N] criteria now failing (was [M] before the fix).

The fix to [what was changed] appears to have broader impact than expected.
This suggests:

1. The root cause is deeper/broader than the current hypothesis
2. The changed code is on a critical path affecting multiple systems
3. The approach needs fundamental rethinking

Rolling back the fix and reassessing the investigation.

New failures introduced:
- [criterion]: [what went wrong]
- [criterion]: [what went wrong]
```

**Do NOT**: Try to fix each new failure individually. That's treating symptoms of a bad fix.

## Rule 4: Regression Detection

**Trigger**: The original symptom is fixed, but the broader test suite reveals new failures that didn't exist before the fix.

**What it means**: The fix has unintended side effects. It may be correct for the specific symptom but wrong for the system.

**Action**: Flag and evaluate:

```
The original symptom is resolved, but the fix introduced new issues:

Original fix: [what was changed]
New failures:
- [test/behavior]: [what broke]
- [test/behavior]: [what broke]

Assessment:
- Are these related to the same root cause? [yes/no + reasoning]
- Can the fix be adjusted to avoid these regressions? [yes/no + approach]
- Is a different fix approach needed? [yes/no + alternative]
```

**Do NOT**: Ignore the regressions and declare the bug fixed. A fix that creates new bugs isn't a fix.

## Iteration Limit

**Hard stop at 5 iterations.** After 5 iterations:

1. Report the current state with full transparency markers (✅/⚠️/❓)
2. List every hypothesis that was tried and why it failed
3. List what criteria are still failing
4. Provide the best current understanding of the root cause
5. Recommend next steps (often: different approach, more context needed, or pair debugging)

```
Debug loop completed 5 iterations without full resolution.

Current state:
✅ Verified: [what we know works]
⚠️ Partial: [what partially works]
❓ Unresolved: [what's still failing]

Hypotheses tested:
1. [hypothesis] — [result]
2. [hypothesis] — [result]
...

Best current understanding: [what we think is happening]

Recommended next steps:
- [specific actionable suggestion]
```

## When NOT to Iterate

Not every failure needs another loop. Exit early if:

- The user provides new information that changes the problem entirely → restart investigation, don't iterate
- The root cause is confirmed as external (library bug, infrastructure) → report and recommend workaround, don't iterate on code
- The issue is a known limitation, not a bug → document and close
- The user says to stop → respect it immediately
