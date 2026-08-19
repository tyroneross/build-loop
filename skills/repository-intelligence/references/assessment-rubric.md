# Repository intelligence assessment rubric

Use this rubric after tracing the source and before recommending reuse.

## Evidence card

Create one card for each candidate:

| Field | Required content |
|---|---|
| Concept | A capability or contract, not a folder name |
| Evidence | Pinned `path:line`, docs, tests, and relevant issue links |
| Source role | What the concept does in its current runtime slice |
| Connections | Upstream constructor and downstream consumers |
| Target | The user's app or workflow |
| Adaptation | The smallest concept-level change worth prototyping |
| Dependencies | Services, libraries, state, permissions, and UI assumptions |
| Decision | Adapt now, prototype, watch, or reject |

## Ratings

Use High, Medium, or Low. Explain the decisive reason; do not average ratings into
a fake precision score.

- **Fit**: Does it solve a named problem in the target app?
- **Portability**: Can the contract survive without the source system's framework?
- **Evidence**: Is behavior confirmed by source plus callers/tests, or only inferred?
- **Maturity**: Is it exercised, maintained, and stable enough for the proposed use?
- **Coupling**: How much unrelated infrastructure must move with it? Low is better.
- **Risk**: What security, privacy, permission, or operational exposure moves with it?

## Evidence labels

- **Source-confirmed**: Behavior is visible at the pinned revision and supported by
  a caller, consumer, test, or governing document.
- **Documented**: The source claims the behavior, but the inspected implementation
  path was incomplete.
- **Issue-reported**: An issue or comment reports the behavior. Preserve open/closed
  status and whether a maintainer confirmed it.
- **Inferred**: The relationship is a reasoned interpretation. State what would
  verify it.
- **Contradicted**: Source, docs, or issues disagree. Show both sides.

## Reuse boundary

Separate three reuse levels:

1. **Concept**: adopt the policy or model, such as target-scoped approvals.
2. **Interface**: adopt a small contract, such as a provider adapter or persona
   manifest.
3. **Implementation**: reuse code only after license, dependency, security, and
   target-fit review.

Default to concept or interface reuse. A permissive license permits copying; it
does not make copying the best design.

## Issue discount rules

- Discount only the candidate the issue bears on.
- Treat an open report as a signal, not a confirmed defect.
- Check comments for corrections, downgrades, or maintainer context.
- Separate product maturity from concept quality. A beta UI bug may not weaken a
  strong internal interface.
- Reject patterns that depend on unresolved security boundaries even if their API
  shape is attractive.

## Comparison contract

Report:

- overlap between independent and user-seeded findings;
- independent discoveries the user's path missed;
- user discoveries the top-down pass missed;
- hypotheses changed by tracing;
- one transferable method improvement per meaningful miss.

Do not reward either method for producing a longer list. Reward verified, portable,
target-relevant findings.

## Final ordering

1. Recommendation and why.
2. Pinned source and limitations.
3. System connection map.
4. Ranked evidence cards.
5. Search-method comparison.
6. Per-app adaptation plan.
7. Risks, rejects, confidence, and open questions.
