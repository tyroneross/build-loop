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

## Deterministic vs AI Steps

Each step in a loop is either a hardcoded SCRIPT (deterministic) or an AI/LLM step. **Default to code; earn the LLM call.** The cost of a wrong assignment is asymmetric — a probabilistic step on a deterministic problem burns money/latency/audit-failures every run, while a deterministic step on a genuinely ambiguous problem fails visibly on the long tail — so break ties toward deterministic. The full rubric (DETERMINISTIC / AI / HYBRID triggers, provenance) lives in `SKILL.md` §"Deterministic vs AI Step Rubric".

Phases may declare the assignment with two optional fields:

| Field | Purpose |
|---|---|
| `step_type` | `script` \| `ai` \| `hybrid`. Which executor the step uses. Default `hybrid` for any phase that involves generation or judgment. |
| `post_check` | The deterministic verify/gate applied to an `ai`/`hybrid` step's output. Names the validator id (from `validators`) or an inline pass/fail rule. Required whenever `step_type` is `ai` or `hybrid`. |

Rule: **every `ai`/`hybrid` step must carry (a) an output schema/type and (b) a `post_check`.** If you cannot write the `post_check`, the step's boundary is wrong — narrow the LLM's job until its output is machine-checkable, then re-declare the deterministic parts as `script`.

```yaml
phases:
  extract_claims:
    step_type: hybrid        # deterministic scaffold -> narrow LLM -> deterministic gate
    summary: "Pull material claims from source text with cited spans."
    output_schema: "list[{claim: str, source_span: str, assumption: bool}]"
    post_check: source_trace  # validator id: every claim cites a span or is marked assumption
  emit_report:
    step_type: script         # bounded inputs, fixed template -> no model needed
    summary: "Render the report from the validated claim list."
```

> **Advisory-only today.** `loop_builder.py` does not yet lint `step_type`/`post_check` — the generator builds packs from presets, and no preset declares per-step types, so there is no field to check at generation time. Adding a linter now would guard a schema nothing emits (a mechanism ahead of its observed need). These fields are defined here so the rubric is auditable by a reviewer and enforceable once presets/specs start declaring them. Aligns with build-loop's Item-18 `dispatch_tier` advisory checks (`tier-sanity-*`), which likewise WARN rather than block.

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
