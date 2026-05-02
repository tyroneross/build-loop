---
name: debug-loop
description: Use for deep iterative debugging when memory lookup returns LIKELY_MATCH/WEAK_SIGNAL/NO_MATCH, a fix didn't hold, or the user asks for root cause analysis. Not for known fixes or trivial issues.
version: 1.1.0
user-invocable: false
---

# Debug Loop — Iterative Root Cause Debugging

A 7-phase debugging loop: investigate via causal tree analysis, hypothesize root cause, implement targeted fix, verify with evidence, score against criteria, pressure-test via critique agent, and report with transparency markers. Iterates up to 5x on failures.

## Scope Check

Before entering the loop, assess whether it's warranted. The trigger is the **verdict category**, not a numeric score — research shows LLM-assigned confidence scores are poorly calibrated for open-ended tasks (Tian et al., EMNLP 2023; 49-84% calibration error on open-ended generation).

- **Skip the loop** if debugging-memory returned `KNOWN_FIX` — apply the fix directly and verify
- **Skip the loop** for trivial issues: typos, missing imports, obvious config errors where the cause is immediately clear
- **Enter the loop** when: verdict is `LIKELY_MATCH`, `WEAK_SIGNAL`, or `NO_MATCH`, the user asks for deep investigation, the initial diagnosis feels superficial, or a previous fix attempt didn't hold

## Efficiency

- Terminal output: current phase, key findings (one line each), status changes, failures. No verbose reasoning
- Agent context: minimum needed per job. Pass symptom + relevant findings, not full conversation history
- Load convergence rules reference on demand only when entering iteration

## Phase 1: INVESTIGATE — Gather Evidence and Trace Root Cause

**Goal**: Understand what's actually failing and why, not just what it looks like.

1. **Search debugging memory** — use the debugger `search` MCP tool with the symptom. Note any related incidents
2. **Reproduce the issue** — identify exact steps, commands, or conditions that trigger the bug
3. **Deploy root-cause-investigator agent** — pass the symptom and reproduction steps for causal tree analysis. The agent explores multiple branches (not a single chain), prioritizes by evidence strength, and prunes with evidence
4. **Research gate** — if the investigator flags unfamiliar error codes, library behavior, or version-specific issues:
   - Search externally (WebSearch, Context7, or documentation)
   - Document what was searched and what was found
   - If search is unavailable, document what SHOULD be searched
5. **Assess completeness** — does the investigation explain ALL reported symptoms? Check for multi-causal bugs (2+ independent root causes)

**Output**: Causal tree (with confirmed and pruned branches), reproduction steps, evidence gathered, research performed

## Phase 2: HYPOTHESIZE — State the Root Cause

**Goal**: Commit to a specific, testable hypothesis before writing any fix.

1. **State the root cause hypothesis** with evidence level:
   - **Strong**: Multiple evidence types (code, logs, reproduction) all point to this cause
   - **Moderate**: Some direct evidence plus reasonable inference
   - **Weak**: Mostly inference, limited direct evidence — consider investigating other branches first
2. **Predict verification test**: If this hypothesis is correct, what specific test would prove it?
3. **Predict related symptoms**: What else should be affected if this root cause is real?
4. **If multiple hypotheses exist**, rank by evidence strength. Pursue the strongest first

**Output**: Hypothesis statement, evidence level, prediction test, related symptom predictions

## Phase 3: FIX — Implement Targeted Change

**Goal**: Make the minimal change that addresses the hypothesized root cause.

1. **Fix the root cause, not the symptom** — if you're adding a null check instead of fixing why something is null, you're fixing the symptom
2. **Minimal changes** — touch only what's needed. Don't refactor, don't improve, don't clean up
3. **Note exactly what was changed and why** — this becomes the evidence trail

**Output**: List of changes with rationale

## Phase 4: VERIFY — Test the Fix with Evidence

**Goal**: Collect concrete evidence that the fix works.

1. **Run the prediction test** from Phase 2 — does it confirm the hypothesis?
2. **Run the original reproduction steps** — is the symptom gone?
3. **Run related test suite** — do existing tests still pass?
4. **Check for regressions** — run broader test suite if available
5. **Verify related symptom predictions** — are the predicted effects present?

Every verification step must produce evidence: command output, test results, or observable behavior. "It should work" is not evidence.

**Output**: Evidence for each verification step

## Phase 5: SCORE — Evaluate Against Criteria

**Goal**: Objective pass/fail assessment with evidence.

Score against these criteria:

| # | Criterion | Method | Pass Condition | Evidence Required |
|---|-----------|--------|----------------|-------------------|
| 1 | Symptom resolved | Reproduction steps | Symptom no longer occurs | Command output or test result |
| 2 | Tests pass | Test suite | All relevant tests pass | Test runner output |
| 3 | No regressions | Broader test suite | No new failures introduced | Test runner output |
| 4 | Root cause addressed | Code review | Fix targets root cause, not symptom | Diff + reasoning |
| 5 | Hypothesis confirmed | Prediction test | Prediction test passes | Test output |

**All criteria must have evidence.** No criterion marked PASS without proof.

**If any criterion fails** → enter iteration (Phase 6 rules apply)
**If all criteria pass** → proceed to critique (Phase 6)

**Output**: Scorecard with pass/fail per criterion and evidence

## Phase 6: CRITIQUE — Pressure-Test Before Declaring Done

**Goal**: Challenge the fix before the user relies on it.

1. **Deploy fix-critique agent** with:
   - The symptom
   - The causal tree from investigation (confirmed branch path + pruned branches)
   - The fix (what was changed)
   - The verification evidence
2. **Evaluate verdict**:
   - **APPROVED** → proceed to REPORT
   - **CHALLENGED** → the concerns become input for the next iteration. Route back to INVESTIGATE with the specific challenges as new investigation targets

The critique agent checks 5 things:
- Root cause vs symptom fix
- Symptom coverage (similar bugs elsewhere)
- Regression risk
- Evidence verification
- Causal tree consistency

## Phase 7: REPORT — Transparent Status

**Goal**: Clear, honest summary. No overclaiming.

### Transparency Markers

Every item in the report gets one marker:

- **✅ Verified**: Checked with evidence (test output, reproduction, command results)
- **⚠️ Assumed**: Believed to be true based on reasoning, but not verified with a test
- **❓ Unknown**: Not checked at all — explicitly acknowledged gaps

### Report Contents

1. **Verdict**: Fixed (all criteria pass + critique approved) or Unresolved (iteration limit hit)
2. **Root Cause**: The identified cause with evidence level
3. **Causal Tree**: The investigation path — confirmed branches, pruned branches with rejection evidence, any multi-causal findings
4. **Fix Applied**: What was changed, with rationale
5. **Scorecard**: Final pass/fail per criterion with evidence
6. **Research Used**: What was searched externally, what was found, what sources
7. **Iteration History** (if >1 iteration): What was tried, what failed, what changed between iterations
8. **Remaining Gaps**: Anything marked ⚠️ or ❓

### After Reporting

- **Store the incident** via the debugger `store` MCP tool for future retrieval
- **Record the outcome** via the debugger `outcome` MCP tool
- **Write state** to `.claude-code-debugger/debug-loop/scorecard.md`

## Iteration Rules

When any criterion fails or the critique is CHALLENGED, iterate:

1. **Diagnose why the criterion failed** — don't blind retry
2. **Revise the hypothesis** if verification disproved it
3. **Create targeted fix plan** for failed criteria only
4. **Execute fix**
5. **Re-verify ONLY failed criteria** — don't re-run passing checks
6. **Re-score and re-critique**

### Convergence Detection

Load `references/convergence-rules.md` for detailed rules and escalation templates.

Summary:
- **Same hypothesis fails 2x** → escalate to user ("I've tried this approach twice — the hypothesis may be wrong or there's a constraint I'm not seeing")
- **Fix A breaks criterion B (oscillation)** → flag as coupled issue, present both sides, ask user
- **3+ criteria fail after a fix** → systemic issue, stop loop and reassess the approach entirely
- **New regression detected** → fix is causing side effects, reconsider the approach
- **Hard stop at 5 iterations** → report what's known and what isn't. Never silently loop beyond 5

### State Tracking

Write iteration state to `.claude-code-debugger/debug-loop/state.json`:

```json
{
  "symptom": "original symptom",
  "iteration": 1,
  "phase": "VERIFY",
  "hypotheses": [
    {
      "iteration": 1,
      "hypothesis": "description",
      "evidence_level": "strong | moderate | weak",
      "result": "confirmed | disproved | partial",
      "evidence": "what was found"
    }
  ],
  "scorecard": [
    {
      "criterion": "symptom_resolved",
      "result": "PASS | FAIL",
      "evidence": "summary"
    }
  ],
  "critique_verdict": "APPROVED | CHALLENGED | pending",
  "changes_made": ["file:change summary"]
}
```

Create the directory with `mkdir -p .claude-code-debugger/debug-loop/` before writing.

## Process Flow

```
MEMORY SEARCH → INVESTIGATE → HYPOTHESIZE → FIX → VERIFY → SCORE
                                                       ↓
                                                  All pass? ──yes──→ CRITIQUE ──approved──→ REPORT
                                                       ↓                  ↓
                                                      no            challenged
                                                       ↓                  ↓
                                                  ITERATE ←──────────────┘
                                                 (up to 5x)
```

## Anti-Patterns to Avoid

| Anti-Pattern | What to Do Instead |
|-------------|-------------------|
| Accepting the first explanation | Branch first — identify 2+ plausible causes before pursuing any |
| Fixing the symptom | Trace to root cause, fix there |
| "This should fix it" | Run the tests, show the output |
| Retrying the same approach | If it failed once with the same evidence, it'll fail again. Change the hypothesis |
| Declaring victory without evidence | Every claim needs a ✅/⚠️/❓ marker |
| Skipping research when stuck | If you don't know why something behaves this way, search for it |
| Hiding uncertainty | ⚠️ and ❓ are not failures — they're honest. Hiding them is the failure |
