---
name: build-loop:security-methodology
description: |
  Build-time security rubric for agentic systems. Loads OWASP LLM Top 10 (v1.1), OWASP Agentic Top 10 (2026), the relevant subset of OWASP Web Top 10 (2025), a starter slice of MITRE ATLAS, and the engineering-relevant subset of NIST AI 600-1 — plus a single cross-source matrix. Loadable standalone when the user asks "what are the security concerns for this design", and auto-loaded by the `security-reviewer` agent in Phase 4.
version: 0.1.0
user-invocable: true
---

# Security Methodology

This is the canon the `security-reviewer` agent grades against, and the canon the `defenseclaw-bridge` skill maps to runtime config. It is **not** a runtime defense layer: it does not block prompts, it does not rewrite outputs, it does not replace DefenseClaw / NeMo Guardrails / Llama Guard. It tells you (a) what risk classes exist, (b) how to detect them by reading code, and (c) which IDs from which framework apply.

## When to load

- **Auto-loaded** by `agents/security-reviewer.md` in Phase 4 Review sub-step A whenever Assess flagged `triggers.riskSurfaceChange: true`.
- **Auto-loaded** by `skills/defenseclaw-bridge/SKILL.md` after Phase 3 Execute when the build produced agent-builder-style artifacts (`tool-contract.md`, `agent-manifest.md`, `guardrail.md`).
- **User-invocable** standalone: ask "what are the security concerns for this design", "give me the OWASP threat model for this", "which ASI risks does this surface", and the orchestrator should `Skill("build-loop:security-methodology")`.

This skill is **knowledge only**. It writes nothing. It performs no scans. The grading logic lives in `security-reviewer`; the runtime mapping lives in `defenseclaw-bridge`.

## What this skill ships

| File | Contents |
|---|---|
| `references/owasp-llm-top-10.md` | LLM01–LLM10, full names + detection patterns (what code/config/diff signals each risk) |
| `references/owasp-agentic-top-10.md` | ASI01–ASI10, full names + detection patterns. Verified labels per OWASP GenAI Security Project, 2025-12-09 release |
| `references/owasp-web-top-10.md` | A01, A03, A06, A10 — the four web risks most relevant to LLM-backed apps. Other six referenced, not enumerated |
| `references/mitre-atlas-starter.md` | Pointer to atlas.mitre.org + ~12 starter ATLAS techniques most relevant to product-dev agents. Cite by ID; not re-authored |
| `references/nist-600-1-mapping.md` | Seven engineering-relevant NIST AI 600-1 risk areas mapped to OWASP IDs. The other five (CBRN, Environmental, IP, Obscene, Violent) are referenced as policy-level |
| `references/cross-source-matrix.md` | The single decision table: row = risk class, columns = OWASP LLM / OWASP Agentic / NIST 600-1 / DefenseClaw control |

The cross-source matrix is the **load-bearing artifact**. Every finding from `security-reviewer` cites a row in this matrix; every config row from `defenseclaw-bridge` traces back to one.

## Structure of the canon (reading order)

If you have time for one file, read `cross-source-matrix.md`. It tells you which framework owns which risk and how the frameworks line up. If you have time for two, read the matrix and the OWASP Agentic file — that's where most production-grade agent risk lives in 2026.

If you're grading a diff (as `security-reviewer` does):

1. Open `cross-source-matrix.md` and find the row for the risk class the diff might surface.
2. Open the `owasp-*.md` reference for the framework cited in that row's first column — its **detection patterns** section names the code/config shapes that are evidence of the risk.
3. If the framework cited is OWASP LLM, also check the OWASP Agentic row alongside it — agentic risks **stack on top of** LLM risks (an agent that calls an injected LLM is two risks, not one).
4. NIST 600-1 is the regulator-facing framework. Map findings to NIST areas at the end, not at the start. NIST is for the report, not the detection.
5. MITRE ATLAS is adversary-perspective — useful for red teams, secondary for build-time review. Cite by technique ID only when it sharpens a finding's evidence.

## Verified scope (what this canon **does** cover)

- Build-time, code-readable risk: prompt construction, tool surfaces, identity/privilege boundaries, supply-chain points of trust, persistent memory, code-execution paths, HTTP boundary at the LLM.
- Cross-source mapping so a single finding can be cited in OWASP, NIST, ATLAS without translation.
- Detection patterns specific enough to grep for in a diff (e.g., "user input concatenated into a system prompt" → LLM01 detection signal).

## Out of scope (what this canon **does not** cover)

- Runtime enforcement code. That lives in DefenseClaw / NeMo / Llama Guard / your own gateway. The `defenseclaw-bridge` skill maps from this canon to DefenseClaw config; mapping to NeMo or Llama Guard is left to project-specific bridges.
- Model theft (LLM10) at the deployment level. Agent-builder-style apps don't host weights.
- CBRN, environmental, IP, obscene, and violent content from NIST 600-1 — those are policy/legal concerns, not engineering. They're referenced for completeness in `nist-600-1-mapping.md` but not deeply mapped.
- The full MITRE ATLAS taxonomy. ATLAS has 16 tactics and 84 techniques as of v5.1.0 (Nov 2025) plus 14 agentic additions (Feb 2026). Re-authoring it would duplicate the project; the starter file points at the source.
- Adversarial test corpora. Red-team corpora are out of build-loop's scope per `~/dev/research/topics/product-dev/product-dev.agentic-systems-original-synthesis.md` recommendation #4.

## Where these references come from

This skill packages and cross-maps four authoritative sources. Each reference file inside `references/` cites its own source. The canonical research file with full citation, retrieval dates, source tier, and the original cross-source matrix lives at:

- `~/dev/research/topics/product-dev/product-dev.agentic-systems-security-references.md` — the verified canon. T1 sources throughout (OWASP project pages, NIST publication, MITRE ATLAS, Cisco DefenseClaw repo).

The companion **gap analysis** that motivated shipping these artifacts inside build-loop:

- `~/dev/research/topics/product-dev/product-dev.agentic-systems-original-synthesis.md` — recommendations table (security-related: #4 red-team playbook, #10 cost-budget enforcement, plus the new "govern/inspect/prove" recommendation).

The agent-builder methodology that this skill cross-references when classifying agentic risks (autonomy ladder A0–A4, permission tiers T0–T5, role decomposition):

- `~/dev/git-folder/agent-builder/plugin/references/methodology/13-agentic-product-dev-synthesis.md` — single canonical synthesis, A0–A4 autonomy ladder.
- `~/dev/git-folder/agent-builder/plugin/references/templates/agentic-handoff/` — 15 template files; the security-relevant subset is `tool-contract.md`, `agent-manifest.md`, `guardrail.md`, `system-boundary.md`, `flow-topology.md`, `role-card.md`.

If the agent-builder plugin is not installed locally, the research-folder pointer above is sufficient — file 13's substance is reproduced in the addendum-v2 research file (`~/dev/research/topics/product-dev/product-dev.agentic-systems-template-pack-addendum-v2.md`).

## Relationship to other build-loop skills

| Skill | Relationship |
|---|---|
| `agents/security-reviewer.md` | Consumes this skill's references as its grading rubric. |
| `skills/defenseclaw-bridge/` | Maps this skill's risk IDs to DefenseClaw config rows + Rego policy stubs. |
| `skills/plan-verify/` | The `risk-surface-change-without-threat-model` rule references this skill — every Phase 2 plan that touches a risk surface must point at a threat-model artifact (or this skill if no project-specific artifact exists). |
| `skills/build-loop/` | The orchestrator's Phase 1 Assess scans the goal and file list for risk-surface signals; if any fire, it sets `triggers.riskSurfaceChange: true`, which routes both `security-reviewer` (Phase 4-A) and the `risk-surface-change-without-threat-model` plan-verify rule (Phase 2). |

## Risk-surface trigger signals (what flips `riskSurfaceChange: true`)

Phase 1 Assess sets the flag when any of these are introduced or modified:

- A new tool, MCP server, plugin, or skill (LLM07, ASI02, ASI04).
- A new LLM call or change to an existing prompt that ships in production (LLM01, ASI01, ASI06).
- New persistent memory or vector store (ASI06, NIST Info Integrity).
- An auth, authz, identity, or permission boundary change (LLM07, ASI03, A01).
- An external API call introduced by the build (LLM05, ASI04, A06, A10).
- Handling of new user data classes — PII, financial, health, credentials, regulated records (LLM06, NIST Data Privacy).

The orchestrator scans the goal text for keywords matching these classes and inspects the planned file set. Either signal flips the trigger; the trigger is sticky for the rest of the build.

## Limitations

- ⚠️ OWASP Agentic Top 10 (2026) is a 2025-12-09 release; the published PDF was inspected for the labels but field practice with this taxonomy is still early. Treat ASI01–ASI10 as "the best framing available", not "the field's settled answer".
- ⚠️ MITRE ATLAS technique enumeration is summarized, not re-authored. Always check `https://atlas.mitre.org/` for the current technique catalog before citing a specific technique in a finding.
- ⚠️ NIST AI 600-1 is a profile, not a regulation. Regulatory regimes (EU AI Act, US executive orders, sectoral rules) cite NIST but add their own requirements. This skill does not track regulatory regimes; pair with project-specific legal review when one applies.
- ⚠️ The cross-source matrix has known gaps. ASI07 (insecure inter-agent communication) does not have a clean DefenseClaw mapping because the runtime control surface for A2A trust is still an industry-open problem. The matrix marks this row `(gap)` rather than papering over it.
