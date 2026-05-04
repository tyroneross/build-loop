---
name: build-loop:debugging-debug-loop
description: Iterative root-cause debugging with causal-tree analysis, hypothesis testing, fix-verify-score cycles, and fix-critique pressure-test. Up to 5 iterations. Build-loop's native debug loop, copied from claude-code-debugger.
version: 0.1.0
user-invocable: false
source: claude-code-debugger/skills/debug-loop/SKILL.md
source_hash: 07b2dd2ad30c210b14bbac3c4e7ddd772ed642dd4c478dfbdb81b52ae809c92a
---

# Debug Loop — Iterative Root Cause Debugging

A 7-phase debugging loop: investigate via causal tree analysis, hypothesize root cause, implement targeted fix, verify with evidence, score against criteria, pressure-test via critique agent, and report with transparency markers. Iterates up to 5x on failures. Native to build-loop — content adapted from `claude-code-debugger/skills/debug-loop/SKILL.md`.

## Scope Check

Trigger is the **verdict category**, not a numeric score — research shows LLM-assigned confidence scores are poorly calibrated for open-ended tasks (Tian et al., EMNLP 2023; 49-84% calibration error on open-ended generation).

- **Skip the loop** if `build-loop:debugging-memory` returned `KNOWN_FIX` — apply the fix directly and verify
- **Skip the loop** for trivial issues: typos, missing imports, obvious config errors
- **Enter the loop** when: verdict is `LIKELY_MATCH`, `WEAK_SIGNAL`, or `NO_MATCH`, the user asks for deep investigation, the initial diagnosis feels superficial, or a previous fix attempt didn't hold

## Efficiency

- Terminal output: current phase, key findings (one line each), status changes, failures. No verbose reasoning
- Agent context: minimum needed per job. Pass symptom + relevant findings, not full conversation history
- Load convergence rules reference on demand only when entering iteration

## Phase 1: INVESTIGATE — Gather Evidence and Trace Root Cause

**Goal**: Understand what's actually failing and why, not just what it looks like.

1. **Search debugging memory** — invoke `build-loop:debugging-memory` with the symptom. Note any related incidents.
2. **Reproduce the issue** — identify exact steps, commands, or conditions that trigger the bug
3. **Deploy `root-cause-investigator` agent** — pass the symptom and reproduction steps for causal tree analysis. The agent explores multiple branches (not a single chain), prioritizes by evidence strength, and prunes with evidence.
4. **Research gate** — if the investigator flags unfamiliar error codes, library behavior, or version-specific issues:
   - Search externally (WebSearch, Context7, or documentation)
   - Document what was searched and what was found
   - If search is unavailable, document what SHOULD be searched
5. **Assess completeness** — does the investigation explain ALL reported symptoms? Check for multi-causal bugs (2+ independent root causes)

**Output**: Causal tree (with confirmed and pruned branches), reproduction steps, evidence gathered, research performed.

## Phase 2: HYPOTHESIZE — State the Root Cause

**Goal**: Commit to a specific, testable hypothesis before writing any fix.

1. **State the root cause hypothesis** with evidence level:
   - **Strong**: Multiple evidence types (code, logs, reproduction) all point to this cause
   - **Moderate**: Some direct evidence plus reasonable inference
   - **Weak**: Mostly inference, limited direct evidence — consider investigating other branches first
2. **Predict verification test**: If this hypothesis is correct, what specific test would prove it?
3. **Predict related symptoms**: What else should be affected if this root cause is real?
4. **If multiple hypotheses exist**, rank by evidence strength. Pursue the strongest first.

## Phase 3: FIX — Implement Targeted Change

**Goal**: Make the minimal change that addresses the hypothesized root cause.

1. **Fix the root cause, not the symptom** — adding a null check instead of fixing why something is null is a symptom fix
2. **Minimal changes** — touch only what's needed. Don't refactor, don't improve, don't clean up
3. **Note exactly what was changed and why** — this becomes the evidence trail

## Phase 4: VERIFY — Test the Fix with Evidence

**Goal**: Collect concrete evidence that the fix works.

1. Run the prediction test from Phase 2 — does it confirm the hypothesis?
2. Run the original reproduction steps — is the symptom gone?
3. Run related test suite — do existing tests still pass?
4. Check for regressions — run broader test suite if available
5. Verify related symptom predictions — are predicted effects present?

Every verification step must produce evidence: command output, test results, observable behavior. "It should work" is not evidence.

## Phase 5: SCORE — Evaluate Against Criteria

**Goal**: Objective pass/fail with evidence.

| # | Criterion | Method | Pass Condition | Evidence Required |
|---|-----------|--------|----------------|-------------------|
| 1 | Symptom resolved | Reproduction steps | Symptom no longer occurs | Command output or test result |
| 2 | Tests pass | Test suite | All relevant tests pass | Test runner output |
| 3 | No regressions | Broader test suite | No new failures introduced | Test runner output |
| 4 | Root cause addressed | Code review | Fix targets root cause, not symptom | Diff + reasoning |
| 5 | Hypothesis confirmed | Prediction test | Prediction test passes | Test output |

**All criteria must have evidence.** No criterion marked PASS without proof.

If any criterion fails → enter iteration (Phase 6 rules apply).
If all criteria pass → proceed to critique (Phase 6).

## Phase 6: CRITIQUE — Pressure-Test Before Declaring Done

**Goal**: Challenge the fix before the user relies on it.

1. **Deploy `fix-critique` agent** with: the symptom, the causal tree from investigation (confirmed branch path + pruned branches), the fix (what was changed), the verification evidence
2. **Evaluate verdict**:
   - **APPROVED** → proceed to REPORT
   - **CHALLENGED** → concerns become input for the next iteration. Route back to INVESTIGATE with the specific challenges as new investigation targets

The critique agent checks 5 things:
- Root cause vs symptom fix
- Symptom coverage (similar bugs elsewhere)
- Regression risk
- Evidence verification
- Causal tree consistency

## Phase 7: REPORT — Transparent Status

**Goal**: Clear, honest summary. No overclaiming.

### Transparency Markers

- ✅ **Verified**: Checked with evidence (test output, reproduction, command results)
- ⚠️ **Assumed**: Believed true based on reasoning, not verified with a test
- ❓ **Unknown**: Not checked — explicitly acknowledged gap

### Report Contents

1. Verdict: Fixed (all criteria pass + critique approved) or Unresolved (iteration limit hit)
2. Root cause + evidence level
3. Causal tree — confirmed branches, pruned branches with rejection evidence, multi-causal findings
4. Fix applied — what was changed, with rationale
5. Scorecard — final pass/fail per criterion + evidence
6. Research used — what was searched, what was found
7. Iteration history (if >1 iteration) — what was tried, what failed, what changed
8. Remaining gaps — anything ⚠️ or ❓

### After Reporting

- Store via `build-loop:debugging-store` (uses `store` MCP)
- Record outcome via `outcome` MCP for any matched-and-applied prior incident
- Write state to `.build-loop/debugging-debug-loop/scorecard.md`

## Iteration Rules

When any criterion fails or critique is CHALLENGED:

1. Diagnose why the criterion failed — don't blind retry
2. Revise the hypothesis if verification disproved it
3. Create targeted fix plan for failed criteria only
4. Execute fix
5. Re-verify ONLY failed criteria — don't re-run passing checks
6. Re-score and re-critique

### Convergence Detection

- **Same hypothesis fails 2x** → escalate to user ("I've tried this approach twice — the hypothesis may be wrong or there's a constraint I'm not seeing")
- **Fix A breaks criterion B (oscillation)** → flag as coupled issue, present both sides, ask user
- **3+ criteria fail after a fix** → systemic issue, stop loop and reassess
- **New regression detected** → fix is causing side effects, reconsider
- **Hard stop at 5 iterations** → report what's known and what isn't

### State Tracking

Write iteration state to `.build-loop/debugging-debug-loop/state.json`:

```json
{
  "symptom": "...",
  "iteration": 1,
  "phase": "VERIFY",
  "hypotheses": [
    { "iteration": 1, "hypothesis": "...", "evidence_level": "strong|moderate|weak", "result": "confirmed|disproved|partial", "evidence": "..." }
  ],
  "scorecard": [
    { "criterion": "symptom_resolved", "result": "PASS|FAIL", "evidence": "..." }
  ],
  "critique_verdict": "APPROVED|CHALLENGED|pending",
  "changes_made": ["file:change summary"]
}
```

`mkdir -p .build-loop/debugging-debug-loop/` before writing.

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

## Anti-Patterns

| Anti-Pattern | What to Do Instead |
|-------------|-------------------|
| Accepting the first explanation | Branch first — identify 2+ plausible causes |
| Fixing the symptom | Trace to root cause |
| "This should fix it" | Run the tests, show the output |
| Retrying the same approach | If it failed once with the same evidence, change the hypothesis |
| Declaring victory without evidence | Every claim needs ✅/⚠️/❓ |
| Skipping research when stuck | Search for unfamiliar behavior |
| Hiding uncertainty | ⚠️ and ❓ are not failures — hiding them is |

## Sibling Skills

- `build-loop:debugging-memory` — mandatory pre-step (Phase 1.1)
- `build-loop:debugging-assess` — escalation path when investigation can't isolate domain
- `build-loop:debugging-store` — Phase 7 incident storage

*Source: copied verbatim from claude-code-debugger and rewritten for build-loop. Drift-checked by `build-loop:sync-skills`.*
