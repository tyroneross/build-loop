# OWASP Top 10 for Agentic Applications (2026)

Released 2025-12-09 by the OWASP GenAI Security Project. Orchestration-and-execution-layer risks specific to agents that plan, act, and use tools. **Complements rather than replaces** the LLM Top 10 — content-level risks (LLM01–10) still apply *inside* an agent.

**Source.** `https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/` — T1, OWASP GenAI Security Project. Retrieved 2026-05-02.

## Verified labels (use these exactly)

The labels below were verified against the OWASP GenAI Security Project release page (T1) with secondary confirmation against the Aikido enumeration of the 2026 list. Use the IDs and full names verbatim in findings.

1. **ASI01 — Agent Goal Hijack**
2. **ASI02 — Tool Misuse and Exploitation**
3. **ASI03 — Identity and Privilege Abuse**
4. **ASI04 — Agentic Supply Chain Vulnerabilities**
5. **ASI05 — Unexpected Code Execution**
6. **ASI06 — Memory and Context Poisoning**
7. **ASI07 — Insecure Inter-Agent Communication**
8. **ASI08 — Cascading Failures**
9. **ASI09 — Human-Agent Trust Exploitation**
10. **ASI10 — Rogue Agents**

## ASI01 — Agent Goal Hijack

**What it covers.** Attackers redirect agent objectives by manipulating instructions, tool outputs, or external content. The agentic descendant of LLM01 plus LLM07 — same injection surface, but now the LLM acts on the injection instead of just speaking it.

**Detection patterns.**
- Agent loop that re-reads its instructions from a mutable source on each iteration (file, DB row, prior tool output).
- "Refine the goal based on what the user / tool / search said" patterns where the goal is rewritten in-flight.
- No instruction-vs-data delimiter when feeding tool outputs back into the agent's reasoning step.
- A tool that returns natural language which then becomes part of the agent's planning context.

**Severity calibration.** ASI01 with a tool surface above T2 (write or external-call) is almost always **HIGH** or **CRITICAL**. ASI01 in a draft-only (A1, T0–T1) agent is often **MEDIUM**.

## ASI02 — Tool Misuse and Exploitation

**What it covers.** Agent uses legitimate tools in unintended ways: privilege escalation through chaining, parameter abuse, unintended scopes, retry abuse, race conditions.

**Detection patterns.**
- Tool that takes a free-form parameter where the implementation does what the parameter says (path traversal, query injection, scope expansion via wildcards).
- Tool chains where output of tool A goes directly into the call args of tool B with no schema check.
- Tools without per-run idempotency keys (same destructive action can be replayed).
- Permission tier on the tool weaker than the worst case its parameters allow.
- Wildcard or regex scopes (`scope: "*"`, `path: "**/*"`) that the agent could exploit even when the spec assumed narrower use.

**Severity calibration.** Tool misuse on T4/T5 tools (external comms, irreversible) is **HIGH** or **CRITICAL**. T1/T2 (read-only) misuse is usually **MEDIUM** unless it leaks data.

## ASI03 — Identity and Privilege Abuse

**What it covers.** Agent acts under wrong identity, escalates privilege, bypasses user-scoped authorization.

**Detection patterns.**
- Tool call that uses ambient agent credentials when the action is conceptually user-scoped (e.g., agent's GitHub token doing things the requesting user couldn't do directly).
- No identity propagation through the agent → tool → downstream-API chain.
- A2A (agent-to-agent) handoff that drops the original user identity.
- Scope of "act on behalf of user X" not enforced inside the tool — the tool trusts a parameter the agent supplied to identify the user.
- Multi-tenant data access where the tenant ID comes from agent context, not from a verified session/JWT.

**Severity calibration.** Cross-tenant access is **CRITICAL** by default. Same-tenant privilege drift is **HIGH**. Self-service privilege widening (agent grants itself more scope) is **CRITICAL** — the bug compounds.

## ASI04 — Agentic Supply Chain Vulnerabilities

**What it covers.** Compromised models, tools, MCP servers, skills, plugins, prompt templates, A2A peers.

**Detection patterns.**
- New MCP server, skill, plugin, or agent template installed without an admission scan or install-source check.
- Skill / plugin / prompt-template fetched from a URL or npm/PyPI package without pin (LLM05 root cause; ASI04 is the agentic consequence — full agent compromise, not just model output).
- Trust assumed because a peer agent is "ours" — same-org A2A peers can be compromised independently.
- Generated code committed and run without a CodeGuard-equivalent static check (secrets, dangerous exec, weak crypto, injection patterns).

**Severity calibration.** Always at least **HIGH** when remediation requires re-bootstrapping the agent. **CRITICAL** when the compromised peer can act on production data.

## ASI05 — Unexpected Code Execution

**What it covers.** Agent or tools execute unintended code paths. Sandbox escape, unsafe deserialization, plugin RCE, attacker-controlled exec via the agent.

**Detection patterns.**
- `eval`, `exec`, `Function(...)`, `child_process.exec`, `subprocess.run(..., shell=True)`, dynamic `import` of paths derived from the agent's reasoning.
- Code-interpreter tool exposed to the agent without a hardened sandbox.
- Browser automation tool with un-allowlisted URL navigation.
- Deserializer (`pickle`, unrestricted `yaml.load`, custom binary parsers) on data that came from the model or a tool.
- File-write tool that allows arbitrary paths (path traversal, write to `~/.ssh/`, write into the project's bin or hooks).

**Severity calibration.** ASI05 is usually **CRITICAL** — RCE class. Drop to **HIGH** only when a strong sandbox confines the impact.

## ASI06 — Memory and Context Poisoning

**What it covers.** Persistent memory or retrieved context manipulated to alter future agent behavior.

**Detection patterns.**
- Persistent agent memory store (vector DB, KV store, file-based) writable by user input without curation.
- Memory shared across users without per-user scoping.
- RAG corpus that ingests user-uploaded content into a shared index.
- Memory entries with no integrity check, signature, or write-time provenance.
- "The agent learns from each interaction" pattern with no quarantine for unverified entries.

**Severity calibration.** Cross-user memory poisoning is **HIGH** or **CRITICAL** (one user can poison another's session). Single-user persistence with no integrity check is **MEDIUM** unless the memory feeds a higher-tier action.

## ASI07 — Insecure Inter-Agent Communication

**What it covers.** Spoofed messages between agents, weak trust between A2A peers, lack of identity propagation across agent boundaries.

**Detection patterns.**
- A2A messages (between agents inside one process or across services) with no signing, no signed envelope, no verifiable sender identity.
- Agent-A code that trusts a `from_agent` field provided in the message body.
- No replay protection (nonces, timestamps with windows) on inter-agent messages.
- Inter-agent envelope without a content schema — receiver parses freeform text from sender.
- "Critic" agent receiving the diff to review through a path attacker can also write to.

**Severity calibration.** ASI07 is the row in the cross-source matrix that DefenseClaw doesn't have a clean runtime control for. Industry-open. Treat it as **HIGH** when an A2A boundary handles user data, **MEDIUM** when scoped to internal coordination only. Surface it explicitly — don't paper over.

## ASI08 — Cascading Failures

**What it covers.** One agent's bad output becomes another's input; failures compound through automated pipelines.

**Detection patterns.**
- Sequential agent pipeline with no validation between stages.
- "Refine" pattern where output of one critic agent becomes the next agent's instruction with no human checkpoint.
- Retry-on-failure that re-feeds the failed output as context for the retry (failure context can poison the retry).
- Multi-agent loop with no convergence guard or oscillation detector.

**Severity calibration.** Usually **MEDIUM** at design time (impact depends on what the cascade reaches). **HIGH** when the cascade ends in a T4/T5 action.

## ASI09 — Human-Agent Trust Exploitation

**What it covers.** Confident, polished agent explanations mislead human operators into approving harmful actions.

**Detection patterns.**
- Approval UX that displays the agent's natural-language explanation prominently and the underlying evidence (diff, params, tool args) in a collapsed/secondary surface.
- No "what will actually happen" preview alongside the agent's pitch.
- Confidence claims surfaced from the model directly without independent calibration.
- Default-yes UX on non-trivial actions.
- Approval flow that auto-confirms after a short delay.

**Severity calibration.** Pairs with LLM09 (Overreliance). Severity tracks the action class — HIGH on T4/T5, MEDIUM elsewhere.

## ASI10 — Rogue Agents

**What it covers.** Compromised or misaligned agents diverge from intended behavior. Includes: agent whose model was swapped, agent whose system prompt was overwritten, agent that's been adversarially fine-tuned, agent whose tool catalog was widened post-deploy without a re-eval.

**Detection patterns.**
- Agent config (model, prompt, tool list) loaded from a writable source at runtime without integrity check.
- No periodic re-evaluation against a pinned eval set after deploy.
- No kill-switch: if the agent goes wrong, can it be stopped within minutes? Hours?
- "Self-improving" agent that updates its own prompt or tool catalog without human review.

**Severity calibration.** Latent risk; severity is a function of detection-and-recovery time, not of immediate exploit. Surface as **MEDIUM** with a recommendation to add the missing control (eval re-run, integrity check, kill-switch path).

## Important framing

Three of the top four risks — **ASI02** (Tool Misuse), **ASI03** (Privilege Abuse), **ASI04** (Supply Chain) — are about identity, tools, and delegated trust. This is consistent with the broader industry view: **the surface area of risk in agents is the surface area of their actions, not their words**. A finding that's "the model said something bad" is LLM01 — content-layer. A finding that's "the agent did something bad" is ASI01–10 — action-layer. Build-time review should weight the action-layer findings more heavily; the model is going to say something bad eventually, the question is what happens when it does.

## Verification status

- ✅ ASI01–ASI10 verified (T1, OWASP GenAI Security Project release page; secondary confirmation via Aikido enumeration of the 2026 list).
- The agent system that drove this skill's creation flipped ASI02–ASI06 from `[INFERRED]` to verified during this build — see `~/dev/research/topics/product-dev/product-dev.agentic-systems-security-references.md` §Verification status for the trail.
