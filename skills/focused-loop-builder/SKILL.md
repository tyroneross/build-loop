---
name: focused-loop-builder
description: Use when the user asks to "create a custom build loop", "build a loop spec", "make a focused loop", "generate a workflow loop", "adapt a framework into a loop", or asks whether a workflow should use skill chaining. Generates declarative focused-loop specs, presets, validators, and skill-chain plans.
version: 0.1.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# focused-loop-builder

Create declarative focused-loop specs that reuse build-loop's phase discipline outside pure coding work. The skill turns a preset or framework into a small loop pack: `loop.yaml`, `rubric.md`, report template, validator stub, and explicit skill-chain guidance.

## Core Rule

Keep the runner generic and the loop domain-specific. Do not create a new orchestrator for every workflow. Generate a loop spec that declares inputs, outputs, validators, gates, learn payload, and skill-chain handoffs.

## Workflow

1. **Classify the loop request.**
   - Use a preset when the user asks for a known loop type: active project evidence, source ingestion, raw data audit, presentation audit, or research synthesis.
   - Use the generic artifact lifecycle preset when the request is novel but still follows intake -> provenance -> produce -> review -> learn.
   - Use a framework adaptation when the user provides a method such as Pyramid Principle, PRISMA, ISO 19011, GTD, DMAIC, OKR, or a company-specific workflow.

2. **Define the artifact contract.**
   - Name accepted inputs.
   - Name target outputs.
   - State the validator evidence required before success.
   - State confirmation gates: external send, sensitive data exposure, money movement, legal assertion, production/customer operation, people-impacting decision, irreversible source-of-truth change.

3. **Plan skill chaining.**
   - Treat skill chaining as phase routing, not a hidden dependency.
   - Declare the chain in `skill_chain` with phase names and fallback behavior.
   - Prefer existing skills for specialized work: `research` for evidence gathering, Pyramid skills for presentation/storyline work, `doc` for Word documents, `build-loop:knowledge` for durable memory, and `plugin-builder` or `skill-builder` only when generating new capabilities.
   - Every chained skill must hand back a concrete artifact path or decision record.

4. **Generate the loop pack.**
   - Run:
     ```bash
     python3 skills/focused-loop-builder/scripts/loop_builder.py create <loop-id> --preset <preset-name>
     ```
   - Default output is `.build-loop/loops/<loop-id>/`.
   - Use `--output <dir>` for a WorkWiki, ObsidianVault, or non-code project workspace.
   - Use `--force` only when replacing an existing generated loop pack intentionally.

5. **Review the generated spec.**
   - Confirm `loop.yaml` has phases, validators, gates, and `skill_chain`.
   - Confirm `rubric.md` has pass/fail checks.
   - Confirm `templates/report.md` matches the output artifact.
   - Run the generated validator stub:
     ```bash
     python3 .build-loop/loops/<loop-id>/validators/validate_loop.py
     ```

## Skill Chaining Guidance

Use skill chaining when the loop has stable phase boundaries and at least one phase is better handled by an existing specialized skill.

Good candidates:

- Research -> synthesis -> presentation/storyline -> audit -> knowledge promotion.
- Source ingestion -> raw-data audit -> source card -> retrieval/index check.
- Presentation audit -> Pyramid critique -> accessibility check -> fix plan.
- Word doc audit -> document parser -> claim verification -> redline report.
- Interview synthesis -> quote extraction -> themes -> deck/source-card output.

Avoid chaining when:

- A single deterministic script can do the whole job.
- Phase outputs are vague or untestable.
- The chain would make ownership unclear.
- A specialist skill would produce advice without a file, verdict, or cited evidence trail.

## Presets

List presets:

```bash
python3 skills/focused-loop-builder/scripts/loop_builder.py list
```

Create a loop:

```bash
python3 skills/focused-loop-builder/scripts/loop_builder.py create active-project-evidence --preset active-project-evidence
```

Inspect a preset:

```bash
python3 skills/focused-loop-builder/scripts/loop_builder.py inspect presentation-audit
```

## Additional Resources

- `references/spec-format.md` describes the generated loop schema and skill-chain fields.
- `scripts/loop_builder.py` is the deterministic generator.
- `presets/*.yaml` contains YAML-compatible preset definitions.
