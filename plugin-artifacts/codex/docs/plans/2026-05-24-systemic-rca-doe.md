<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Systemic RCA DOE Plan

## Plain-Language Goal

Build-loop should explain failures in a way a user can understand first, then trace the issue to the first controllable system failure. "The agent missed it" is not a root cause. The root cause is the missing control that allowed the miss: an incomplete brief, missing verifier, ambiguous ownership, stale cache, weak feedback path, insufficient test, or unsafe routing rule.

## Problem

Build-loop already has causal-tree debugging, but it tends to fire late and can still describe the proximate failure instead of the system control that failed. That makes reports less useful because they name what broke, not what needs to change so the same class of issue becomes harder to repeat.

## Desired Report Shape

Every failure report should start in this order:

1. Plain-language failure: what went wrong in normal words.
2. Why it happened: symptom -> technical failure -> upstream dependency or interface failure -> first controllable system failure.
3. Technical details: only the files, API path, config, test output, trace, or state needed to prove the cause.
4. Tradeoffs: what the fix improves, what it risks, and what it does not solve.
5. Impact: user impact, engineering impact, and recurrence risk.
6. Prevention control: durable control change, such as a test, verifier, lint rule, smoke gate, trace, memory entry, plan check, or routing rule.

## Framework Translation

| Source framework | Useful idea | Build-loop translation |
|---|---|---|
| CAST / STAMP | Find the failed control structure, not the person or component to blame. | Require `system_control_failure` for every RCA. |
| STPA | Model controller, action, process, and feedback before an accident. | During Assess, ask whether every risky control action has feedback and a verifier. |
| Fault Tree Analysis | Start from the undesired outcome and map AND/OR contributing events. | For Review-B failures, map the failing criterion into a failure tree before fixing. |
| FMEA / FMECA | For each component or interface, ask how it can fail and how failure is detected. | In Plan, high-risk interface changes list failure mode, effect, detection, and control. |
| AcciMap | Map causes across multiple system levels. | Use when issue spans prompt, tool, repo state, environment, and product intent. |
| FRAM | Normal local variation can combine into system failure. | Use when each step was locally reasonable but the overall workflow still failed. |
| ODC | Classify defects so process feedback can be measured. | Tag failure type: `scope-audit-gap`, `runtime-smoke-gap`, `cache-drift`, `ambiguous-contract`, `missing-test-trigger`, `context-packet-gap`. |
| SRE postmortem / CAPA | Blameless account plus corrective and preventive action. | RCA cannot pass without a durable prevention control. |
| Delta debugging | Minimize the failure-inducing change/input/context. | Use for regressions to isolate the smallest failing diff, prompt, context packet, env set, or fixture. |
| Fault localization / trace DAG | Use execution evidence to rank likely causes. | Use traces and tests as evidence, not as the final explanation. |

## DOE Hypothesis

The best protocol will combine:

- plain-language-first output,
- system-control terminal cause,
- fault-tree or dependency-chain failure map,
- FMEA-style prevention control,
- deterministic scoring.

## DOE Factors

Use an 8-run fractional factorial design for six factors:

```json
[
  {"name": "explanation_format", "levels": ["technical_first", "plain_language_then_system_cause"]},
  {"name": "framework_core", "levels": ["causal_tree_only", "cast_stpa_control_gap"]},
  {"name": "failure_map_shape", "levels": ["linear_why_chain", "fault_tree_plus_dependency_chain"]},
  {"name": "forward_scan", "levels": ["none", "fmea_interface_failure_modes"]},
  {"name": "classification", "levels": ["freeform", "odc_taxonomy"]},
  {"name": "evidence_method", "levels": ["manual_evidence", "trace_or_delta_debug_required"]}
]
```

Generate the matrix:

```bash
python3 scripts/optimize_doe.py generate \
  --factors docs/test-fixtures/systemic-rca/doe/systemic-rca-factors.json \
  --design auto \
  --seed 20260524 \
  > docs/test-fixtures/systemic-rca/doe/systemic-rca-doe.json
```

## Evaluation Metric

Primary metric: mean systemic RCA score across a fixed benchmark corpus.

```bash
python3 scripts/systemic_rca_eval.py \
  docs/test-fixtures/systemic-rca/*.json \
  --score-only
```

The scorer checks:

1. The report starts with a plain-language failure.
2. The why chain is present.
3. The failure map is at least four levels deep.
4. The terminal cause is a controllable system failure.
5. Actor-blame phrases such as "agent forgot" are paired with the concrete missing control that allowed the failure.
6. At least two evidence types are present.
7. A known process-failure classification is present.
8. Alternatives were pruned with evidence.
9. Tradeoffs and impact are named.
10. A durable prevention control is named.

Secondary metrics:

- assessment time or token cost,
- fix-target accuracy,
- recurrence prevention quality,
- user readability under 60 seconds.

## Fixture Corpus

The initial tracked corpus is `docs/test-fixtures/systemic-rca/golden-corpus.json` with 10 positive report examples, plus `docs/test-fixtures/systemic-rca/negative/shallow-actor-blame.json` as the shallow negative control. Each positive fixture includes:

- observable symptom,
- relevant repo evidence,
- known good systemic RCA,
- expected prevention control.

The shallow RCA control lives separately at `docs/test-fixtures/systemic-rca/negative/shallow-actor-blame.json`.

Suggested classes:

- stale installed plugin cache,
- API caller scope missed,
- runtime smoke missing route failure,
- UI contract omitted state path,
- dependency provenance misunderstood,
- warning baseline treated as absolute,
- sandbox/environment failure misread as code failure,
- multi-session collision or stale worktree,
- test fixture hides real runtime behavior,
- agent context packet lacks required invariant.

Current covered classes:

- stale installed plugin cache: `cache-drift`
- API caller scope missed: `scope-audit-gap`
- runtime smoke missing route failure: `runtime-smoke-gap`
- UI contract omitted state path: `ui-contract-gap`
- dependency provenance misunderstood: `dependency-provenance-gap`
- warning baseline treated as absolute: `warning-baseline-gap`
- sandbox/environment failure misread as code failure: `environment-misread`
- multi-session collision or stale worktree: `multi-session-coordination-gap`
- test fixture hides real runtime behavior: `test-fixture-gap`
- agent context packet lacks required invariant: `context-packet-gap`

## Acceptance Rule

Adopt a protocol variant only if it meets all of these:

- increases mean systemic RCA score by at least 25 percent over the current protocol,
- does not increase assessment time or token cost by more than 40 percent,
- does not reduce fix-target accuracy,
- produces report text a user can understand before the technical section.

## Implementation Path

Current status:

- The deterministic evaluator is implemented in `scripts/systemic_rca_eval.py`.
- The 10-case golden corpus and shallow negative control are tracked under `docs/test-fixtures/systemic-rca/`.
- The six-factor DOE matrix is tracked under `docs/test-fixtures/systemic-rca/doe/`.
- The packet builder and result scorer are implemented in `scripts/systemic_rca_doe.py`.
- The root-cause investigator and debug-loop guidance now require plain-language-first reports and system-control terminal causes.

Phase A:

- Add `scripts/systemic_rca_eval.py`.
- Add deterministic tests for the evaluator.
- Add benchmark fixture schema.
- Do not change runtime orchestration yet.

Phase B:

- Create the fixture corpus.
- Generate the DOE matrix.
- Build run packets with `scripts/systemic_rca_doe.py build-packets`.
- Run each protocol variant against the same fixtures in randomized order.
- Score each run with `scripts/systemic_rca_doe.py score-results --design docs/test-fixtures/systemic-rca/doe/systemic-rca-doe.json --jsonl`.
- Analyze effects with `scripts/optimize_doe.py analyze --direction higher`.

Phase C:

- Apply the winning protocol to `agents/root-cause-investigator.md`, `skills/debugging/debug-loop/SKILL.md`, and `references/iterate-protocol.md`.
- Add a Review-B or Iterate gate only after the scorer proves the report shape improves.

## Guardrails

- Do not let framework names appear before the plain-language failure.
- Do not accept a terminal cause that blames an agent, user, model, or context window without naming the missing system control.
- Do not require exhaustive investigation for trivial failures; use this gate for ambiguous, repeated, cross-layer, user-impacting, or post-validation failures.
- Treat traces, tests, and fault localization as evidence, not root causes.
