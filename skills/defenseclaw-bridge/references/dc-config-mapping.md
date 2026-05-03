# DefenseClaw Config Mapping

Field-by-field mapping from each agent-builder template to the DefenseClaw config row(s) it produces. The bridge skill (`SKILL.md`) walks this mapping when generating `<project>/.defenseclaw/generated/`.

**Canonical agent-builder template paths** (when the plugin is installed locally):

- `~/dev/git-folder/agent-builder/plugin/references/templates/agentic-handoff/tool-contract.md`
- `~/dev/git-folder/agent-builder/plugin/references/templates/agentic-handoff/agent-manifest.md`
- `~/dev/git-folder/agent-builder/plugin/references/templates/agentic-handoff/guardrail.md`
- `~/dev/git-folder/agent-builder/plugin/references/templates/agentic-handoff/system-boundary.md`
- `~/dev/git-folder/agent-builder/plugin/references/templates/agentic-handoff/flow-topology.md`
- `~/dev/git-folder/agent-builder/plugin/references/templates/agentic-handoff/role-card.md`

When the plugin is not installed locally, the project's own copies of these templates (filled in for the build) are the input. Filename match drives the bridge; plugin presence is not required.

## Mapping reference (DefenseClaw schema)

DefenseClaw's three-pillar model — **Govern / Inspect / Prove** — drives the config layout. Each agent-builder field maps to one of:

- **Govern** — admission control: scanner profiles, install-source policies, allow/block lists, OPA Rego policies on tool calls.
- **Inspect** — runtime guardrails: rule-pack entries that fire pre-call (against the prompt) or post-call (against the model output / tool output).
- **Prove** — audit: sink configuration (SQLite, JSONL, OTLP, Splunk HEC, webhook), retention, log fields.

The mapping matrix below uses these three column tags. A row may produce config in more than one pillar; both are listed.

## tool-contract.md → DefenseClaw

| Agent-builder field | Pillar | DefenseClaw artifact / field | Notes |
|---|---|---|---|
| `tool_contract.tool_id` | Govern | `scanner-profile.yaml :: tools[].id` | Stable identifier; used as the policies/<id>.rego filename |
| `tool_contract.tool_name` | Govern | `scanner-profile.yaml :: tools[].name` | Human-readable label only |
| `tool_contract.purpose` | (none) | (none — context only) | Goes in generated README |
| `tool_contract.type` (function / MCP / hosted / shell / browser / external_api / agent) | Govern | `scanner-profile.yaml :: tools[].surface` | Drives which scanners apply (e.g., shell tools always get CodeGuard injection scan) |
| `tool_contract.allowed_agents` | Govern | `policies/<tool_id>.rego :: data.defenseclaw.tools.<tool_id>.allowed_agents` | Rego predicate; agent IDs from agent-manifest's `agents:` block |
| `tool_contract.input_schema` | Govern + Inspect | `policies/<tool_id>.rego` (input validation) + `rule-packs/inspect.yaml` (pre-call schema check) | Schema check happens twice — admission-time deny + runtime-time block |
| `tool_contract.output_schema` | Inspect | `rule-packs/inspect.yaml :: post-call rule for <tool_id>` | If the schema is violated post-call, the rule fires |
| `tool_contract.allowed_actions` / `forbidden_actions` | Govern | `policies/<tool_id>.rego :: allowed_actions` / `forbidden_actions` | Rego sets, intersected at request time |
| `tool_contract.permission_tier` (T0–T5) | Govern + Inspect | Drives default scanner intensity AND default approval rule | T0–T2 → minimal scan, no approval. T3 → write-action approval gate. T4 → external-comms approval. T5 → strong-approval + diff-preview required |
| `tool_contract.auth_scope` | Govern | `policies/<tool_id>.rego :: auth_scope` | Identity propagation gate; bridge writes `TODO:` if scope is "ambient agent credentials" (likely ASI03 finding) |
| `tool_contract.data_access_scope` | Govern | `policies/<tool_id>.rego :: data_scope` | Tenant boundary check |
| `tool_contract.rate_limits` | Inspect | `rule-packs/inspect.yaml :: rate-limit rule for <tool_id>` | Backstop for LLM04 (DoS) |
| `tool_contract.timeout` | Inspect | Same row as rate_limits | |
| `tool_contract.side_effects` | Prove | `dc-config.yaml :: audit.side_effect_capture: true` if any tool declares side effects | Always-on audit when side effects exist |
| `tool_contract.requires_human_approval` | Govern | `policies/<tool_id>.rego :: requires_approval = true` | Hard gate |
| `tool_contract.approval_preview_fields` | Govern | `policies/<tool_id>.rego :: preview_fields` | Operator UI consumes this |
| `tool_contract.rollback_strategy` | (none) | (none — context only, in README) | Operations concern, not config |
| `tool_contract.audit_log_fields` | Prove | `dc-config.yaml :: audit.fields` | Union of all tools' audit fields |
| `tool_contract.failure_modes` / `error_behavior` | Inspect | `rule-packs/inspect.yaml :: post-call error-shape rule` | Detect deviations from declared error shape |
| `tool_contract.examples` / `test_cases` | (none) | (none — used by other build-loop phases, not DefenseClaw) | |

## agent-manifest.md → DefenseClaw

| Agent-builder field | Pillar | DefenseClaw artifact / field | Notes |
|---|---|---|---|
| `agent_manifest.name` / `version` | (top) | `dc-config.yaml :: project.name` / `project.version` | |
| `agent_manifest.mission` / `north_star_metric` | (none) | (none — context only) | |
| `agent_manifest.users` | Govern | `dc-config.yaml :: tenancy.users` | If multi-user, drives per-user audit scoping |
| `agent_manifest.autonomy_level` (A0–A4) | Govern | `dc-config.yaml :: autonomy_default` | Higher autonomy → stricter default rules |
| `agent_manifest.architecture_pattern` | (none) | (context in README) | |
| `agent_manifest.sdk_choice` | Govern | `dc-config.yaml :: sdk_hooks` | Drives which fetch interceptor / proxy adapter to wire |
| `agent_manifest.model_routes` | Inspect + Prove | `dc-config.yaml :: providers[]` (per route) | Auth headers (`X-DC-Target-URL`, `X-AI-Auth`, `X-DC-Auth`) — bridge writes `TODO:` placeholders |
| `agent_manifest.tools[]` | Govern | Each tool walks `tool-contract.md` mapping | One-to-many: the manifest lists tools, each has its own contract |
| `agent_manifest.memory.{working_state, session_memory, long_term_memory}` | Govern + Inspect | `scanner-profile.yaml :: memory_scanners` + `rule-packs/inspect.yaml :: memory_write_rules` | Long-term memory always gets ASI06 detection rules |
| `agent_manifest.protocols.mcp` / `agent_manifest.protocols.a2a` | Govern | `scanner-profile.yaml :: mcp_servers[]` / `a2a_peers[]` | MCP servers get install-source check + signature verification (when available); A2A peers get TODO for signing (ASI07 gap) |
| `agent_manifest.guardrails.{input, tool, output, handoff}` | Inspect | Each guardrail walks `guardrail.md` mapping | |
| `agent_manifest.human_checkpoints` | Govern | `policies/checkpoints.rego` | Human-in-loop predicates |
| `agent_manifest.evals` | (none) | (context) | |
| `agent_manifest.observability` | Prove | `dc-config.yaml :: sinks` | Default: SQLite + JSONL; OTLP if `observability.otlp_endpoint` set |
| `agent_manifest.deployment` | (none) | (context) | |
| `agent_manifest.deactivation` | (none) | (context — kill-switch path noted in README) | |
| `agent_manifest.agents[]` (per-agent registry) | Govern | `policies/agents.rego :: data.defenseclaw.agents` | Agent IDs, autonomy, allowed_tools, can_handoff_to — drives the A2A handoff allow-graph |
| `agent_manifest.agents[].review_required` | Govern | Per-agent `policies/agent-<id>.rego :: review_required` | When true, all decisions of that agent route through human approval |
| `agent_manifest.security_posture` (when present, per security-references rec) | Govern + Inspect + Prove | Top-level coverage attestation in `dc-config.yaml :: risk_coverage` | Mirrors the `risk_coverage` block from the agent manifest verbatim |

## guardrail.md → DefenseClaw

| Agent-builder field | Pillar | DefenseClaw artifact / field | Notes |
|---|---|---|---|
| `guardrail_id` | Inspect | `rule-packs/inspect.yaml :: rules[].id` | Stable ID |
| `name` | Inspect | `rule-packs/inspect.yaml :: rules[].name` | Human-readable |
| `applies_to` (agent IDs) | Inspect | `rule-packs/inspect.yaml :: rules[].applies_to_agents` | Filter — rule fires only when the active agent is in scope |
| `trigger` | Inspect | `rule-packs/inspect.yaml :: rules[].trigger.regex` (or `llm_judge_prompt` if non-regex) | Bridge writes regex placeholder; project-specific patterns are TODO |
| `check` | Inspect | `rule-packs/inspect.yaml :: rules[].check` | Multi-step check description; lifecycle_phase derived from semantics |
| `action` | Inspect | `rule-packs/inspect.yaml :: rules[].action` | "block" / "redact" / "log" / "approve_required" |
| `severity` (low / medium / high) | Inspect | `rule-packs/inspect.yaml :: rules[].severity` | Drives default mode: high → action mode (block); low → observe mode (log only) |
| `escalation` | Govern | `policies/escalation.rego :: escalations` | When the guardrail hits, who gets paged |
| (new field rec) `lifecycle_phase` | Inspect | `rule-packs/inspect.yaml :: rules[].lifecycle_phase` | Required: pre-call / post-call / in-tool / post-handoff |
| (new field rec) `enforcement_type` | Inspect | `rule-packs/inspect.yaml :: rules[].enforcement_type` | regex / policy / llm-judge / external-service |
| (new field rec) `mode` | Inspect | `rule-packs/inspect.yaml :: rules[].mode` | observe / action |
| (new field rec) `mapped_owasp_risks` | Inspect + Prove | `rule-packs/inspect.yaml :: rules[].mapped_risks` AND `dc-config.yaml :: risk_coverage` rolls up | Cross-cite for audit |

## system-boundary.md → DefenseClaw

| Agent-builder field | Pillar | DefenseClaw artifact / field | Notes |
|---|---|---|---|
| `system_boundary.in_scope_tasks` | Govern | `dc-config.yaml :: sandbox.allowed_surfaces` | Allow-list driver for OpenShell sandbox |
| `system_boundary.out_of_scope_tasks` | Govern | `dc-config.yaml :: sandbox.blocked_surfaces` | Block-list driver |
| Other fields (mission, users) | (none) | (context only) | |

The bridge writes a `TODO: OpenShell sandbox config — system-boundary is too coarse to drive a precise sandbox profile. Refine in-scope/out-of-scope to filesystem paths, allowed network destinations, allowed syscalls.` in the generated README.

## flow-topology.md → DefenseClaw

| Agent-builder field | Pillar | DefenseClaw artifact / field | Notes |
|---|---|---|---|
| Agent-to-agent edges (who can hand off to whom) | Govern | `policies/handoffs.rego :: allowed_handoffs` | Mirrors `agent_manifest.agents[].can_handoff_to` |
| Tool-call edges (which agent calls which tool) | Govern | (already covered by tool-contract.allowed_agents) | Cross-check; mismatch with manifest is a finding |
| External system edges | Govern | `policies/egress.rego :: allowed_destinations` | Outbound URL allow-list |

If `flow-topology.md` describes A2A edges, the bridge writes a `TODO: ASI07 — A2A trust model is project-specific. DefenseClaw does not provide a built-in inter-agent message-signing layer. Consider per-edge signing keys + nonces. Document the A2A trust model here.` in the generated README.

## role-card.md → DefenseClaw

| Agent-builder field | Pillar | DefenseClaw artifact / field | Notes |
|---|---|---|---|
| Role's allowed_tools | Govern | Cross-check vs `agent_manifest.agents[].allowed_tools` | Mismatch is a finding (role cards drift) |
| Role's autonomy | Govern | Cross-check vs `agent_manifest.agents[].autonomy_level` | Mismatch is a finding |
| Role's review_required | Govern | Cross-check vs `agent_manifest.agents[].review_required` | Mismatch is a finding |

Role cards are mostly cross-validated; they don't add new config rows of their own. When they drift from the manifest, the bridge surfaces this as a `TODO:` in the README so the project can reconcile.

## Permission-tier (T0–T5) → DefenseClaw scanner intensity

| Permission tier | Default scanner profile | Default approval rule |
|---|---|---|
| T0 (no tool access) | none | n/a |
| T1 (read-only local) | minimal — schema-only | none |
| T2 (read external) | minimal + supply-chain check | none if data is in scope |
| T3 (write reversible) | full CodeGuard (secrets, dangerous exec, deserialization, weak crypto, injection) | preview / undo required |
| T4 (external comms) | full CodeGuard + outbound URL allowlist + payload PII scan | human approval required |
| T5 (irreversible / high impact) | full CodeGuard + diff-preview + dual-control | strong human approval required |

T0 and T1 produce the most minimal rule sets; T4 and T5 produce the most aggressive. The bridge uses these defaults; projects can tighten per-tool by editing the generated config.

## Cross-source matrix → DefenseClaw column

For each row in `skills/security-methodology/references/cross-source-matrix.md`, the DefenseClaw column names the operational control. The bridge consults this column when picking which rule-pack entries to generate for a guardrail whose `mapped_owasp_risks` covers a given row.

## Lossy fields (intentionally not mapped)

| Field | Why not mapped |
|---|---|
| `tool_contract.examples` / `test_cases` | Used by build-loop's eval phase, not by runtime enforcement |
| `agent_manifest.north_star_metric` / `mission` | Strategic context, not operational policy |
| `agent_manifest.architecture_pattern` | Architecture description, not policy input |
| `agent_manifest.deactivation` | Operations runbook, not enforcement config |
| `system_boundary.users_served` (verbose narrative) | Used in agent-manifest.users + role-cards instead |
| `role_card.background` / freeform narrative | Documentation, not policy |

These fields are referenced in the generated `README.md` for context but do not drive any config row. Operators should still write them in the source artifacts — they're part of the design record even when DefenseClaw doesn't consume them.

## When the schema drifts

DefenseClaw's `dc-config.yaml` schema evolves. This mapping is pinned to the schema as of canon-write time (May 2026). When the schema changes:

1. The bridge's generated `README.md` includes a `# Schema version: <pinned>` line.
2. Operators deploying to a newer DefenseClaw should validate the generated spec against the current schema before running.
3. Schema drift in DefenseClaw is the project's responsibility to track; the bridge does not auto-update.

For the live schema and config reference, see `https://github.com/cisco-ai-defense/defenseclaw`.
