<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Research Trigger Policy

Build-loop uses Research deliberately when a task depends on current,
external, novel, reusable, or decision-grade information. The classifier of
record is:

```bash
python3 scripts/research_trigger.py \
  --workdir "$PWD" \
  --task "<goal text>" \
  --effort "<XS|S|M|L|XL>" \
  --cache-into-state \
  --json
```

The output is written to `.build-loop/state.json.researchGate` when
`--cache-into-state` is passed.

## When It Fires

Run the classifier during Phase 1 Assess, after memory load and before the
plan is accepted. Research is required when the task includes any of these
signals:

- Explicit research language: research, investigate, evaluate, compare, latest,
  current, look up, should I, recommendation.
- A new API, provider, package, model, framework, standard, deployment target,
  database, webhook, auth provider, or external service.
- Current external claims: versions, pricing, release notes, deprecations,
  laws/regulations/standards, official-doc behavior.
- Architecture boundary decisions where prior art matters: persistence,
  protocol, schema, deployment, security, memory/retrieval, Rally, plugin/hook,
  or cross-layer behavior.
- Reusable findings that should become a packet in `.build-loop/research/` or
  durable memory.

No trigger means no Research plugin run by default. A large T-shirt size alone
does not make a local mechanical edit research-worthy.

## Depth Rules

The Research plugin's own depth classifier remains authoritative when
available. `research_trigger.py` is the deterministic lower-bound and state
contract for the orchestrator.

| Effort | Default if a trigger fires | Memory recall depth |
|---|---|---|
| `XS` | `light` | compact |
| `S` | `light`, or `standard` for current/external work | focused |
| `M` | `standard` | standard |
| `L` | `standard`; `deep` for architecture/decision-grade work | deep |
| `XL` | `standard`; `deep` for architecture/decision-grade work | deep |

Risk and currentness override effort. Security, auth, privacy, payment,
billing, compliance, legal, medical, finance, production, and deep/thorough
user wording escalate to `deep` unless the work is explicitly scoped to a
local mechanical check.

Depth maps to build-loop research modes:

| Depth | Research mode | Expected output |
|---|---|---|
| `light` | `quick` | Local/source-of-truth scan; 0-2 sources; persist only if reusable |
| `standard` | `balanced` | Local plus official docs/web as needed; 2-5 sources; packet path required |
| `deep` | `max_accuracy` | Decision-grade multi-source work; 4-10 sources; persist by default |

## Enforcement

If `researchGate.blocks_final_claims == true` or
`researchGate.requires_citations_or_unavailable_note == true`, the final report
must not state current/external/API/package claims as facts until one of these
is true:

- The report cites the research packet and the packet cites source paths/URLs.
- The report says the current/external evidence was unavailable and labels the
  claim as unverified.
- The claim is removed from the final report.

If `researchGate.packet_path` is non-null, Phase 2 records it in the plan under
`## Research Context`, and Phase 4-G cites whether it was created, reused, or
explicitly skipped with rationale.
