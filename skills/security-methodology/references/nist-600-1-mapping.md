# NIST AI 600-1 — Engineering-Relevant Risk Areas

NIST AI 600-1, "Artificial Intelligence Risk Management Framework: Generative AI Profile" (July 2024), is the GenAI-specific companion to AI RMF 1.0. **Not a regulation** — but as the most detailed government-published GenAI risk framework, it has become the de facto reference for regulators, auditors, insurers, and enterprise procurement.

**Source.** `https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf` — T1, NIST publication. Retrieved 2026-05-02.

NIST 600-1 names twelve risk areas. **Seven are engineering choices** (you can change them by changing the build); five are policy / legal / content-moderation concerns that engineering can support but cannot single-handedly resolve. This file maps the seven engineering-relevant areas to OWASP IDs and notes the boundary on the other five.

## The four NIST functions (lifecycle frame)

NIST 600-1 organizes work around four functions that map cleanly onto build/operate:

| Function | What it does | Build-loop phase analogue |
|---|---|---|
| **Govern** | Policies, accountability, oversight, AI usage policies. | Out of scope for build-loop (org policy concern). |
| **Map** | Risk identification, use cases, intended purpose, stakeholders, data sources, supply-chain dependencies. | Phase 1 Assess + threat-model artifact. |
| **Measure** | Testing for hallucinations, bias, privacy leaks, security, environmental impact. | Phase 4 Review (validate + fact-check + security-review). |
| **Manage** | Post-deployment monitoring, appeal/override mechanisms, incident response, recovery, change management, deactivation. | Out of build-loop scope (runtime), but artifacts reference it. |

The build-time security review covers **Map** and **Measure** for the seven engineering-relevant risk areas below.

## Engineering-relevant risk areas (7)

### 1. Information Security

**What NIST means.** Confidentiality, integrity, availability of the AI system itself and the data it handles.

**Build-time signals.** Authentication, authorization, encryption-in-transit, secrets handling, secure tool design, sandbox boundaries.

**OWASP cross-map.** LLM05 (Supply Chain), LLM06 (Sensitive Info Disclosure), LLM07 (Plugin Design), LLM08 (Excessive Agency), ASI02 (Tool Misuse), ASI03 (Privilege Abuse), ASI04 (Supply Chain), ASI05 (Code Execution), ASI07 (Inter-Agent Comms), A01 (Access Control), A03 (Injection), A06 (Vulnerable Components), A10 (SSRF).

### 2. Information Integrity

**What NIST means.** Trustworthiness of the information the AI produces and consumes — provenance, factuality, resistance to manipulation.

**Build-time signals.** Prompt-vs-data delimiters, retrieval provenance, memory-write integrity, output-validation paths, signature/hash on inputs.

**OWASP cross-map.** LLM01 (Prompt Injection), LLM03 (Training Data Poisoning), ASI01 (Goal Hijack), ASI06 (Memory Poisoning), ASI08 (Cascading Failures), ASI10 (Rogue Agents).

### 3. Data Privacy

**What NIST means.** Personal data, regulated data classes, consent, scoping, retention, redaction.

**Build-time signals.** Redaction in prompt construction, scoped data access in tools, retention of conversation history, audit logging that doesn't leak the data being audited.

**OWASP cross-map.** LLM06 (Sensitive Info Disclosure). Pairs at the boundary with LLM07 (Plugin Design — tool data access scope) and ASI03 (Privilege Abuse — wrong identity reading data).

### 4. Value Chain and Component Integration

**What NIST means.** Risks introduced by third-party models, datasets, tools, MCP servers, plugins, libraries — the dependency graph.

**Build-time signals.** Pinning, signing, install-source verification, scanner gates on new dependencies, lockfiles committed.

**OWASP cross-map.** LLM05 (Supply Chain), ASI04 (Agentic Supply Chain), A06 (Vulnerable Components).

### 5. Human-AI Configuration

**What NIST means.** How humans interact with the AI: what they're shown, what they approve, when they can override, calibration of trust.

**Build-time signals.** Approval UX design, evidence-vs-explanation surfacing, override paths, clear "this is generated" framing, autonomy-level boundaries.

**OWASP cross-map.** LLM08 (Excessive Agency), LLM09 (Overreliance), ASI09 (Human-Agent Trust Exploitation).

### 6. Confabulation

**What NIST means.** The model produces plausible-sounding outputs that are factually wrong (the technical term for hallucination at the policy layer).

**Build-time signals.** Eval coverage on factual claims, output-validation paths for high-stakes assertions, citation requirements, confidence calibration.

**OWASP cross-map.** Indirectly LLM09 (Overreliance — humans trusting confabulations). Cross-cuts the design intent of `agents/fact-checker.md` in build-loop.

### 7. Harmful Bias and Homogenization

**What NIST means.** Outputs that are systematically biased against subgroups, or that reduce diversity in some downstream measure.

**Build-time signals.** Eval coverage across demographic / subgroup slices, monitoring for output-distribution drift, bias-aware prompt design.

**OWASP cross-map.** No direct OWASP analog — bias is its own concern. Build-loop's `fact-checker` and `mock-scanner` agents do not deeply check bias; bias review is project-specific and typically requires labeled eval sets.

## Policy-level risk areas (5) — referenced, not deeply mapped

These five NIST risk areas affect what an AI system is allowed to do, not how it's built. Engineering can support guardrails for them but cannot single-handedly resolve them. Build-loop's security-reviewer flags surfaces relevant to these areas but does not grade against them.

| NIST area | What it covers | Engineering boundary |
|---|---|---|
| **CBRN Information or Capabilities** | The AI helps a user produce chemical, biological, radiological, or nuclear weapons capability. | Provider-side filtering + content policy. Engineering can disallow specific tool surfaces (e.g., no chemistry calculation tools), but the substantive control is content-policy-shaped. |
| **Dangerous, Violent, or Hateful Content** | The AI produces or facilitates such content. | Provider-side filtering + content policy. |
| **Environmental Impacts** | Compute / energy / water consumption. | Engineering can use smaller models, cache aggressively, but the substantive accounting is ops-shaped. |
| **Intellectual Property** | The AI reproduces copyrighted material, leaks trade secrets, or trains on unlicensed corpora. | Engineering can scope data access, but the substantive control is licensing/legal. |
| **Obscene or Degrading Content** | The AI produces such content. | Provider-side filtering + content policy. |

For these five, the security-reviewer should:

- **Flag** when a build introduces a surface that materially expands the attack surface of one of these (e.g., a tool that fetches arbitrary chemistry data, an integration that ingests potentially-copyrighted user uploads).
- **Not grade** the content policy itself — that's a separate review track.

## Companion frameworks

NIST 600-1 has at least one notable agentic-specific companion:

- **CSA Agentic AI NIST AI RMF Profile v1** — Cloud Security Alliance's lab-status profile mapping the agentic-specific extensions to NIST 600-1's areas. Useful for teams already aligned to NIST that need agentic-specific extensions. T2 source. ⚠️ Lab status as of canon time; revisit when CSA promotes it out of lab status.

## How to cite NIST in a finding

NIST citations in a build-loop security finding should:

- Name the **risk area** (e.g., "NIST 600-1 §Information Integrity"), not a paragraph number — area names are stable; pagination is not.
- Sit alongside, not in place of, the OWASP cite. NIST is the **regulator-facing** framing; OWASP is the **developer-facing** framing.
- Appear in the Phase 4-F report when the build is being prepped for audit / procurement / regulator review. Skip in routine builds.

Format: `mapped_risks: ["LLM07", "ASI03", "NIST:Information Security"]`. Inline `NIST:` prefix avoids confusion with OWASP IDs.

## Limitations

- ⚠️ NIST 600-1 is a profile, not a regulation. Treat it as the de-facto reference, not as a binding constraint. Regulatory regimes (EU AI Act, US executive orders, sectoral rules) may cite it but add their own requirements.
- ⚠️ The seven-vs-five split between "engineering" and "policy" risks is this skill's editorial call, not NIST's. NIST treats all twelve as a single set; this skill separates them so a build-time review knows which it can grade and which it can only flag.
- ❓ CSA Agentic AI NIST AI RMF Profile v1 is referenced in the canon but not deeply inspected. T2 with version pin; revisit when promoted.
