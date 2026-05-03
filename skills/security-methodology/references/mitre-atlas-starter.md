# MITRE ATLAS — Starter Subset for Product-Dev Agents

ATLAS is to AI what ATT&CK is to enterprise security: an adversary-perspective tactic/technique catalog with case studies. **Designed for red teams, threat hunters, and detection engineers — not for developers writing prompts.** This file does not re-author ATLAS; it points at the source and lists ~12 starter techniques that are the most relevant to product-dev agents at build-time review.

**Source.** `https://atlas.mitre.org/` — T1, MITRE-published, government and Fortune 500 adoption. Retrieved 2026-05-02.

## Current scope (Nov 2025 v5.1.0 + Feb 2026 update)

- 16 tactics (top-level adversary goals)
- 84 techniques (specific attack methods)
- 32 mitigations
- 42 case studies, including:
  - Microsoft 365 Copilot insider exploitation
  - Hugging Face organization-confusion supply-chain attack
  - Multi-step financial-transaction hijacking through assistant-as-insider patterns
- 14 new techniques specifically for AI Agents and GenAI from the Zenity Labs collaboration (Oct 2025)

**Always check the live ATLAS catalog before citing a specific technique ID.** This file is a starter map, not a snapshot.

## When to cite ATLAS in a security-reviewer finding

Cite an ATLAS technique by ID when one of these is true:

1. The finding describes an **attack path** (how an adversary would exploit), not just a defect. Example: "user input flows into a system prompt — ATLAS technique for prompt injection."
2. The project has a red team / pen test program and is tracking findings by ATLAS ID.
3. The finding is being prepped for an audit report that requires standardized adversary taxonomy.

**Skip ATLAS** when the finding is straightforwardly an OWASP LLM/Agentic ID. Don't double-cite for the same content; pick one. ATLAS is the *adversary* lens; OWASP is the *application* lens.

## Starter technique map

The list below is illustrative. Each line names a class of attack, points at the ATLAS tactic family, and lists the OWASP IDs that already cover the same risk. **Always look up the live technique IDs at `https://atlas.mitre.org/` before quoting an ID in a finding** — the catalog evolves and snapshot lists go stale.

| Attack class | ATLAS tactic family | OWASP cross-map |
|---|---|---|
| Direct prompt injection (user input) | Initial Access / Execution | LLM01, ASI01 |
| Indirect prompt injection (RAG, web content, tool output) | Initial Access / Persistence | LLM01 + LLM07, ASI01 + ASI06 |
| Adversarial example to bypass guardrails | Defense Evasion | LLM01 |
| Tool-call manipulation via crafted tool descriptions or schemas | Initial Access / Execution | ASI02, LLM07 |
| Memory-poisoning attacks across sessions | Persistence | ASI06 |
| Sandbox escape from code interpreter | Privilege Escalation / Execution | ASI05, A03 |
| Multi-step financial-transaction hijacking (assistant-as-insider) | Impact | ASI03, ASI09 |
| Model repository organization-confusion (Hugging Face case) | Initial Access / Resource Development | LLM05, ASI04, A06 |
| Exfiltration through tool side channels | Exfiltration | LLM06 |
| Cost / resource exhaustion | Impact | LLM04 |
| Output-channel exfiltration (markdown image rendering, link leakage) | Exfiltration | LLM06, LLM02 |
| Model theft via API querying | Collection / Exfiltration | LLM10 |

## Why ATLAS is secondary, not primary, for build-time review

ATLAS is most valuable for:

- **Red-team test design** — what attacks should the offensive testing program try? ATLAS gives you the menu.
- **Detection engineering** — what telemetry would I need to see this attack? ATLAS lists the observable signals.
- **Incident response taxonomy** — "we saw this attack class" should map to a standard ID for postmortems and audits.

It's secondary for **build-time review** because most ATLAS techniques are already framed as defects in the OWASP LLM/Agentic Top 10. A finding cited as "LLM01 + ASI01" is more discoverable to a developer than "ATLAS T15.001 + T1606" — the developer is going to look up the OWASP ID anyway.

The exceptions where ATLAS adds value at build time:

- **Memory poisoning** — ATLAS has more granular technique IDs for *how* the poisoning happens (e.g., adversarial training data vs. RAG-corpus injection vs. session-state tampering). When a finding needs that granularity, cite ATLAS.
- **Supply-chain organization-confusion attacks** — ATLAS captured the Hugging Face case study with specific technique IDs. When the finding involves a model registry attack, ATLAS is the cleanest citation.
- **Tool-call manipulation via schema crafting** — Newer technique class added in the Feb 2026 update. ATLAS is the only published taxonomy that names this distinct from generic prompt injection.

## What this file does not contain

- The full ATLAS taxonomy (16 tactics × 84 techniques + 14 agentic additions). Re-authoring it would duplicate the project. Always pull from the source.
- ATLAS mitigations enumeration. ATLAS publishes 32 mitigations alongside the techniques; for the canonical list, see the source.
- Case studies. Eight pages of context per case study live at the source; quoting them inline would just be a copy.

## Limitations

- ⚠️ Technique IDs are stable in ATLAS, but the catalog evolves. The "Feb 2026 update" referenced above adds techniques; pre-update finding IDs may still be valid but their parent tactic could be reorganized. Always pull live before citing.
- ⚠️ The OWASP/ATLAS overlap is not 1:1. Some ATLAS techniques have no OWASP analogue (e.g., specific data-poisoning training-pipeline attacks); some OWASP IDs map to multiple ATLAS techniques. The cross-map column above is an approximation, not an authoritative mapping.
- ❓ The Zenity Labs additions (Oct 2025, 14 agentic techniques) were verified as a count at canon time; the full ID list and mappings are pending future updates.

## Quick reference link

For the canonical tactic list, technique catalog, mitigations, and case studies, follow:

`https://atlas.mitre.org/` → Matrices → AI/ML matrix.
