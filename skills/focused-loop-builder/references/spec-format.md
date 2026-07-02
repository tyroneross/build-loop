# Loop Spec Format

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

A focused-loop spec is the domain contract for a reusable workflow. The runner owns phase movement, validation order, iteration, gates, queues, and learn/memory. The spec owns the artifact vocabulary.

## Required Fields

| Field | Purpose |
|---|---|
| `schema_version` | Loop spec version. Start at `1`. |
| `id` | Kebab-case loop identifier. |
| `title` | Human-readable loop name. |
| `summary` | One-sentence purpose. |
| `inputs` | Accepted source materials or state surfaces. |
| `outputs` | Artifacts the loop produces. |
| `phases` | Domain-specific work contract per phase. |
| `validators` | Pass/fail checks used during review. |
| `gates` | Human confirmation boundaries. |
| `skill_chain` | Optional phase-to-skill routing plan. |
| `learn` | Durable reuse payload. |

## Skill Chain Shape

`skill_chain` is a plan for chaining specialized skills. It is advisory unless the runner or host can prove the named skill exists.

Each phase may declare:

| Field | Purpose |
|---|---|
| `primary` | Preferred skills for this phase. |
| `optional` | Skills to use when source type or scope warrants them. |
| `fallback` | What to do if the skill is unavailable. |
| `handoff_artifact` | Required output from the chained skill. |

Example:

```yaml
skill_chain:
  intake:
    primary:
      - research
    optional:
      - doc
    fallback: "Manual source inventory with cited file paths."
    handoff_artifact: "source_inventory.md"
```

## Gate Rules

Keep common gates centralized. Loop specs should name examples, not redefine policy.

Standard confirmation gates:

- external_send
- sensitive_data_exposure
- money_movement
- legal_or_compliance_assertion
- production_or_customer_operation
- people_impacting_decision
- irreversible_source_change

## Validator Rules

Validators should be binary. Avoid vague scores. If the check requires judgment, phrase it as a pass/fail rubric with named evidence.

Good:

```yaml
- id: source_trace
  pass_condition: "Every material claim cites a source path, slide, row, transcript timestamp, or explicitly marked assumption."
  method: "review"
```

Weak:

```yaml
- id: quality
  pass_condition: "The artifact is good."
```
