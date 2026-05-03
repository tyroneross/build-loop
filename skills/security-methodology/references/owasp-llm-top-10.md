# OWASP Top 10 for LLM Applications (v1.1, 2025)

Component-and-content-level risks. Frames the LLM as a component inside a wider application — these risks apply to any LLM-backed feature, agent or otherwise.

**Source.** `https://owasp.org/www-project-top-10-for-large-language-model-applications/` — T1, OWASP project page. Retrieved 2026-05-02.

## Reading these tables

Each row is one risk. The **Detection patterns** column lists what to grep for in a diff or look for during code review — these are signals, not proofs. Some signals are unambiguous (eval of LLM output → LLM02 + ASI05); most are necessary-not-sufficient and pair with a judgment call about the surrounding code.

## LLM01 — Prompt Injection

**What it covers.** Crafted inputs hijack the model's behavior. Two flavors: **direct** (attacker controls the user input) and **indirect** (attacker controls retrieved content, tool output, file contents, or upstream LLM output that the target LLM later reads).

**Detection patterns.**
- Template strings or string concatenation that puts a user-controlled value into a system prompt position. Look for `f"...{user_input}..."`, `` `${...}` `` in template literals, `prompt.format(...)`, or any direct interpolation of unsanitized strings into the request body.
- Tool output, search results, web content, RAG snippets, file contents, or another agent's output appended to the LLM context with no separator, no provenance tag, and no instruction-vs-data delimiter.
- A `system` and `user` message constructed from a single concatenated string instead of distinct roles.
- Use of `system` content sourced from a database or external store without integrity check (poisoned-system-prompt vector).

**Adjacent agentic risk.** Pairs with **ASI01** (Agent Goal Hijack) when the prompt-injectable LLM is an agent that takes actions.

## LLM02 — Insecure Output Handling

**What it covers.** Treating LLM output as trusted input downstream — XSS, SQL injection, command injection, SSRF, or RCE *because* the application trusts what the model returned.

**Detection patterns.**
- LLM output rendered with `dangerouslySetInnerHTML`, `innerHTML`, `v-html`, or `{@html ...}` without sanitization.
- LLM output passed to `exec`, `Function(...)`, dynamic `import`, `child_process.exec`, `subprocess.run(..., shell=True)`, `eval`, or any string-to-code path.
- LLM output concatenated into SQL/NoSQL queries, OS commands, file paths, URLs (especially for `fetch` / `requests.get`), or shell scripts.
- LLM output used as a redirect target, an Open Graph URL, or a webhook URL without an allowlist.
- LLM output deserialized via `pickle`, `yaml.load(..., Loader=yaml.Loader)`, `JSON.parse` followed by no schema validation, or any unsafe deserializer.

**Adjacent agentic risks.** Pairs with **ASI05** (Unexpected Code Execution) and **ASI08** (Cascading Failures, when the unsanitized output is the input to a downstream agent).

## LLM03 — Training Data Poisoning

**What it covers.** Tampered training data corrupts model behavior. For most product teams, the *training* surface is owned by the model provider (OpenAI / Anthropic / etc.), so this risk shifts toward **fine-tuning** and **RAG corpus** poisoning.

**Detection patterns.**
- Fine-tuning data ingested from a user-writable source without curation, sampling review, or provenance tags.
- RAG corpus ingestion that accepts uploads from end users into a shared index used across users.
- A mechanism to update embeddings or retrieval weights without an approval gate.
- Memory or "learned preferences" persisted across sessions for one user but reachable from another user's queries (related to ASI06).

**Adjacent agentic risk.** Pairs with **ASI10** (Rogue Agents) at the most severe end — a poisoned fine-tune produces an agent whose alignment drift is invisible at deploy time.

## LLM04 — Model Denial of Service

**What it covers.** Resource-exhaustion attacks: token bombs, recursive prompts, expensive tool chains, infinite loops between agents.

**Detection patterns.**
- New LLM call without a `max_tokens` ceiling, request timeout, or per-run budget.
- New agent loop without an iteration cap.
- Tool that calls LLM that calls tool, with no circuit breaker.
- User input length not bounded before being placed in the prompt.
- Multiple parallel LLM calls without a concurrency cap.
- Recursive/self-calling agent without a depth limit.

**Cost angle.** Cost runaway is the boring twin of DoS — same root cause (no ceiling), different victim (your wallet). Treat them as one finding.

## LLM05 — Supply Chain Vulnerabilities

**What it covers.** Compromised model weights, datasets, libraries, plugins, MCP servers, prompt templates, A2A peers. Provenance, pinning, scanning.

**Detection patterns.**
- New `pip install` / `npm install` / `cargo add` of an LLM-adjacent package without a pinned version (`==X.Y.Z` for Python, exact version for npm).
- New MCP server or plugin pulled from a non-organization source (random GitHub user, npm registry without an internal mirror).
- Model loaded by name from Hugging Face or similar without a hash pin (organization-confusion attack vector).
- Skill, agent, or prompt template imported from an external repository without a SHA pin.
- Generated code (Codex, Cursor, Copilot output) committed without a review pass.

**Adjacent agentic risk.** Pairs with **ASI04** (Agentic Supply Chain) and **OWASP Web A06** (Vulnerable & Outdated Components).

## LLM06 — Sensitive Information Disclosure

**What it covers.** The model reveals sensitive content from the training set, the system prompt, the context window, or another user's conversation.

**Detection patterns.**
- Secrets (API keys, tokens, DB URLs) injected into the system prompt or request body. Anything matching `sk-`, `Bearer `, `password=`, `api_key=`, or environment variables landing in prompt strings.
- PII or regulated data (SSN, payment, health) included in prompt context without a redaction pass.
- A single LLM context shared across users (cache key based on prompt content alone, not user-scoped).
- LLM output streamed to a log without redaction.
- Internal tool documentation or schema dumped into the prompt where attackers could exfiltrate it via prompt injection.

**Cross-map.** This is the **NIST Data Privacy** + **Information Security** intersection.

## LLM07 — Insecure Plugin Design

**What it covers.** Plugins/tools accept LLM-generated input without validation, lack access control, or have over-broad scopes.

**Detection patterns.**
- Tool schema accepts `string` for a parameter that should be a typed enum, validated path, or scoped ID.
- Tool uses ambient credentials (the agent's keys, not the user's) for an action that should be scoped to a user identity.
- Tool action that writes/deletes/external-calls without a `requires_human_approval: true` or equivalent gate.
- Tool that takes a URL/path/SQL string and passes it through to the underlying system without validation.
- Tool catalog exposes more capabilities to the agent than the workflow needs ("just expose all of GitHub" pattern).

**Cross-map.** Heavy overlap with **ASI02** (Tool Misuse) and **ASI03** (Privilege Abuse). When in doubt, cite both LLM07 and ASI02/ASI03 — they're not redundant, they describe the same surface from different angles.

## LLM08 — Excessive Agency

**What it covers.** The damaging-action vector. The agent has more functionality, more permissions, or more autonomy than it needs to perform its job, and an attacker (via LLM01) or a confused state turns that into harm.

**Detection patterns.**
- Tool list larger than the role description requires.
- Permission tier higher than the action class requires (T5 for a read; T4 when no user comms is needed).
- Autonomy level above what the use case requires (A3 "execute reversible" when A1 "draft" would do).
- No human-in-the-loop checkpoint at the autonomy/permission boundary where one is plausible.
- "Just give the agent admin" style provisioning.

**Cross-map.** This is the cross-cutting agentic risk in the LLM Top 10. The OWASP Agentic Top 10 (ASI02 + ASI03) replaced and refined this in 2026; both still apply at design-time review.

## LLM09 — Overreliance

**What it covers.** A human-in-the-loop reviewer who doesn't critically assess LLM output and approves harm.

**Detection patterns.**
- "Confirm" / "Approve" UX with the model's reasoning displayed prominently and the underlying evidence buried.
- No diff or provenance shown alongside an LLM-suggested change.
- Default-yes confirm flows for non-trivial actions.
- Confidence claims surfaced from the model directly ("I'm 95% sure") without independent calibration.
- Auto-approval thresholds set against the model's self-reported confidence.

**Cross-map.** Pairs with **ASI09** (Human-Agent Trust Exploitation), which is the more pointed framing in the agentic taxonomy.

## LLM10 — Model Theft

**What it covers.** Unauthorized access to proprietary model weights or architecture.

**Detection patterns.**
- Public-facing endpoint that returns logits, embeddings, or fine-tuned model state without auth/rate limiting.
- API key leakage in client-bundled code or git history.
- Weight files committed to a public repo.

**Most product teams don't host weights.** For agent-builder-style apps that consume hosted models (OpenAI, Anthropic, Bedrock, Vertex), LLM10 collapses to "don't leak the API key" — which is LLM06 in practice. Flag it explicitly only when fine-tuned weights or self-hosted models are in play.

## What this file does not contain

- Fix code. The agent that finds an LLM01 vulnerability does not patch it; it routes to Iterate with the finding.
- The full OWASP write-up. For the canonical text, citation, and authority lineage, follow the source URL above.
- Scoring guidance. Severity is set by `agents/security-reviewer.md` based on exploitability and consequence; this file lists detection signals, not severity heuristics.
