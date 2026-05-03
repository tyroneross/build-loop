# Cross-Source Risk Matrix

Single decision table mapping each risk class to its OWASP LLM ID, OWASP Agentic ID, NIST 600-1 risk area, and the DefenseClaw runtime control that implements the operational defense (when one exists).

**Use.** This is the load-bearing artifact of the security-methodology skill. The `security-reviewer` agent cites a row from this matrix in every finding. The `defenseclaw-bridge` skill maps from this matrix to DefenseClaw config rows.

**Source.** Adapted from `~/dev/research/topics/product-dev/product-dev.agentic-systems-security-references.md` §"Cross-source map: where each risk lives". The research file is the canonical citation trail; this file is the build-loop-internal copy.

## Reading conventions

- Cells with **two or more IDs** mean both apply (the risk lives in both frameworks; cite both).
- `(implicit X)` means the framework doesn't have a top-level entry but the risk is covered indirectly under X.
- `(n/a)` means the framework genuinely doesn't address the row.
- `(gap)` in the DefenseClaw column means there is no clean general-purpose runtime control yet — surface as a known unknown rather than paper over.
- `(operational)` means the row is operational concern (cost, deprecation, fleet management) that DefenseClaw doesn't claim to cover.

## The matrix

| Risk class | OWASP LLM | OWASP Agentic | NIST 600-1 | DefenseClaw control |
|---|---|---|---|---|
| Prompt injection (direct user input) | LLM01 | ASI01 | Information Security | Inspect (pre-call scan) |
| Prompt injection (indirect — tool output, RAG, file content) | LLM01 + LLM07 | ASI01 + ASI06 | Information Security + Information Integrity | Inspect (pre-call + post-call) |
| Output trusted as input (XSS / SQLi / RCE via model output) | LLM02 | ASI05 | Information Security | Inspect (post-call sanitize) |
| Excessive agency (more tools / autonomy / scope than needed) | LLM08 | ASI02 + ASI03 | Human-AI Configuration | Govern (admission) + policy gates |
| Identity / privilege abuse | LLM07 | ASI03 | Information Security | Govern (least-privilege) + Inspect (auth checks) |
| Tool / plugin insecure design | LLM07 | ASI02 | Information Security + Value Chain | Govern (skill / MCP scanner) |
| Supply chain (model, tool, MCP, plugin, A2A peer) | LLM05 | ASI04 | Value Chain | Govern (admission scanner) |
| Memory & context poisoning | (implicit LLM01) | ASI06 | Information Integrity | Govern (skill scan) + Inspect (post-call) |
| Inter-agent communication (spoofing, weak trust, no identity) | (n/a) | ASI07 | Information Security | (gap) |
| Cascading failures (output → input across agents) | LLM02 (downstream) | ASI08 | Information Integrity + Human-AI Configuration | Inspect + circuit breakers |
| Human-agent trust exploitation (over-confident explanations) | LLM09 | ASI09 | Human-AI Configuration | Audit (Prove) for review sampling |
| Rogue / misaligned agent (compromised or drifted) | (implicit LLM03) | ASI10 | Information Integrity | Govern (continuous re-scan) + kill-switch |
| Sensitive info disclosure (secrets / PII / training data leakage) | LLM06 | (covered in ASI06 / ASI09) | Data Privacy + (IP — policy-level) | Inspect (post-call PII / secrets scan) |
| Model DoS / cost runaway | LLM04 | (operational) | (operational) | Inspect (rate limits, budgets) |
| Model theft (weights / architecture) | LLM10 | (operational) | (IP — policy-level) | (out of agent-builder scope) |
| Unsafe code execution | LLM07 | ASI05 | Information Security | Govern (CodeGuard) + Sandbox |
| Confabulation (hallucinated facts) | (indirect LLM09) | (n/a) | Confabulation | Audit (Prove) — review sampling for fact claims |
| Harmful bias / homogenization | (n/a — content) | (n/a) | Harmful Bias and Homogenization | (project-specific eval; no DefenseClaw analog) |

**Reading the matrix.** Every engineering-relevant row has at least three sources. ASI07 is the row where DefenseClaw doesn't yet have a general solution — consistent with the broader industry state, which is still working out A2A trust models. That `(gap)` is a *known unknown* worth surfacing in findings, not papering over.

## How a finding cites a row

A `security-reviewer` finding always includes a `mapped_risks` array drawn from the second and third columns of this matrix. Format:

```json
{
  "id": "SEC-007",
  "severity": "HIGH",
  "title": "Tool output flows into agent prompt without sanitization",
  "mapped_risks": ["LLM01", "LLM07", "ASI01", "ASI06"],
  "evidence": "src/agents/researcher/loop.ts:88-104",
  "snippet": "...prompt += `\\nTool result:\\n${toolResult.text}`...",
  "recommendation": "Wrap tool output in a delimited 'untrusted-tool-output' block and add a post-call output validator before the result re-enters the next loop iteration."
}
```

When the finding is being prepped for an audit-grade report, add NIST cite:

```json
"mapped_risks": ["LLM01", "LLM07", "ASI01", "ASI06", "NIST:Information Integrity"]
```

The `NIST:` prefix disambiguates from OWASP IDs.

## How `defenseclaw-bridge` maps a row

The bridge skill reads this matrix in reverse: given a project's tool-contract, agent-manifest, and guardrail artifacts, it identifies which rows the project surfaces, then writes a DefenseClaw config row for each surfaced risk. The mapping lives at `skills/defenseclaw-bridge/references/dc-config-mapping.md`.

## Limitations

- ⚠️ The matrix is **build-time descriptive**, not runtime prescriptive. A row's DefenseClaw column names a control that *fits* the risk, not one that *is configured* in any specific project. Configuration lives in `defenseclaw-bridge`'s output.
- ⚠️ Some rows (Inter-agent comms, Bias, Confabulation) genuinely have no clean DefenseClaw mapping. The `(gap)` and `(project-specific eval)` markers are honest, not placeholders.
- ⚠️ The OWASP Agentic Top 10 (2026) is a recent release; the matrix's mapping reflects a year of community discussion, not a decade of field experience. Treat IDs as "the best framing available", not "settled answer".
- ⚠️ NIST 600-1 has twelve risk areas; this matrix maps only the seven engineering-relevant ones (per `nist-600-1-mapping.md`). The other five (CBRN, Dangerous/Violent, Environmental, IP, Obscene) are policy-level and not in this matrix.
