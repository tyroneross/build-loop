<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Agentic system handoff — permission tiers, autonomy ladder, role decomposition

Vendored substance from the agent-builder methodology (private research note — substance summarized here). This file exists so `skills/security-methodology/SKILL.md` has a working fallback on a fresh install where the `agent-builder` plugin is not present: rather than pointing at a second maintainer-only path, the load-bearing tables and templates are reproduced directly below.

## Permission tiers (T0–T5)

Used when classifying what a tool or agent action is allowed to do, and what approval it requires by default.

| Tier | Capability | Examples | Default approval |
|---|---|---|---|
| T0 | No tool access | Draft text only | No approval |
| T1 | Read-only local context | Read project docs, inspect state | No approval if data is in scope |
| T2 | Read external systems | Search docs, query CRM, read GitHub issues | Approval depends on data sensitivity |
| T3 | Write reversible changes | Create draft, stage file, update non-public record | Usually preview or undo required |
| T4 | External communication | Send email, post Slack, create ticket, comment on PR | Human approval required |
| T5 | Irreversible or high-impact action | Delete data, deploy production, spend money, change permissions | Strong human approval required |

## Autonomy ladder (A0–A4)

The canonical 5-level ladder (v2; supersedes an earlier 6-level A0–A5 variant that split "draft only" into two levels).

| Level | Name | Agent may do | Approval required for |
|---|---|---|---|
| A0 | Draft only | Summarize, classify, draft, critique, recommend. | Any decision, write action, external call, or implementation. |
| A1 | Reversible decisions | Choose low-risk defaults, mark assumptions, proceed on reversible choices. | Low-reversibility decisions, sensitive data, external services, paid resources. |
| A2 | Bounded execution | Implement approved P0 scope in a sandbox, run tests, update local files, report. | Deployment, paid services, destructive actions, secrets, external communications. |
| A3 | Controlled production action | Execute approved production tasks under guardrails and audit. | Migrations, deletion, permission changes, user-impacting changes, policy changes. |
| A4 | Autonomous operation | Monitor and optimize within explicit policy, budgets, and rollback limits. | Material scope, policy, data, architecture, or cost changes. |

Rationale: excessive agency — an LLM-based system performing damaging actions because it has too much functionality, permission, or autonomy — is one of the clearest agentic-system risk classes (OWASP LLM08, `genai.owasp.org/llmrisk2023-24/llm08-excessive-agency/`).

### Default autonomy by phase

| Phase | Default autonomy | Rationale |
|---|---|---|
| Sparse intake | A1 | The agent may infer obvious defaults but must log assumptions. |
| Product spec generation | A1 | Product intent and scope require human validation. |
| UX and requirements draft | A1-A2 | Drafts allowed; P0 scope reviewed. |
| Architecture decisions | A0-A1 | Lower-reversibility; ADRs required. |
| Data and permissions | A0-A1 | Sensitive data and retention need explicit constraints. |
| Coding | A2 | Safe after approved handoff and sandbox boundaries. |
| Testing and evaluation | A2 | Run tests, lint, report evidence. |
| Deployment | A0-A2 | Depends on environment, user impact, rollback, approvals. |
| Production operation | A0-A3 | Requires governance, monitoring, incident response, override. |

### Ask-before policy

An agent must ask before:

- Storing personal, financial, medical, legal, confidential, regulated, or credential-like data.
- Selecting a paid external service or creating recurring operational cost.
- Introducing distributed services, microservices, irreversible migrations, or complex infrastructure.
- Removing, weakening, or redefining a P0 requirement.
- Deploying to a user-facing or production-like environment.
- Executing delete, overwrite, migration, permission, or credential actions.
- Taking actions that affect money, health, legal status, security posture, customer communications, or contractual commitments.

## Default agent role decomposition

| Agent | Primary job | Key inputs | Key outputs | Default autonomy |
|---|---|---|---|---|
| Intake / Triage Agent | Convert sparse human input into normalized product primitives. | Intake answers, prior project context, examples. | Product primitives, confidence map, open questions. | A1 |
| Product Strategy Agent | Define user outcomes, North Star, scope, non-goals, and success metrics. | Product primitives, user context, constraints. | Product brief, outcome map, scope recommendation. | A1 |
| User / JTBD Agent | Infer jobs, pains, workflow triggers, success moments, and user constraints. | Intake, interviews, examples, archetypes. | Persona and JTBD brief, pain/opportunity map. | A1 |
| Requirements Agent | Convert goals into epics, stories, requirements, acceptance criteria, and test hooks. | Product brief, user context, UX constraints. | Requirements doc, story map, acceptance criteria. | A1-A2 |
| UX Blueprint Agent | Define flows, screens, navigation, content intent, empty states, and error states. | Personas, requirements, design constraints. | UX blueprint, screen inventory, flow specs. | A1-A2 |
| Architecture Agent | Recommend technical architecture and major architectural decision records. | Requirements, quality attributes, constraints, decision criteria. | Architecture recommendation, ADRs, implementation plan. | A0-A1 |
| Data / Integration Agent | Define entities, permissions, integrations, lifecycle, and data-handling assumptions. | Requirements, target systems, security constraints. | Data semantics, integration specs, permission map. | A0-A1 |
| Security / Compliance Agent | Identify sensitive data, misuse risks, guardrails, approval boundaries, and policy constraints. | Data spec, architecture, domain context. | Risk register, security checklist, ask-before rules. | A0 |
| Spec Review Agent | Check ambiguity, traceability, scope, assumptions, risk, and handoff readiness. | Full draft pack. | Spec lint result, traceability gaps, revision requests. | A1 |
| Coding Agent | Build implementation, tests, docs, setup, and local run instructions. | Approved handoff pack, repository, build plan. | Working code, tests, README, completion report. | A2 |
| QA / Evaluation Agent | Verify behavior against requirements, tests, and quality bars. | Code, requirements, test plan, traces. | Test results, defect log, traceability report. | A2 |
| Release / Completion Agent | Package output, known limitations, next iteration, and learning updates. | QA results, implementation notes, unresolved risks. | Release notes, known limitations, next-step plan. | A1-A2 |

## Machine-readable templates

### System boundary

```yaml
system_boundary:
  agent_system_name:
  primary_mission:
  users_served:
  in_scope_tasks:
  out_of_scope_tasks:
  external_tools:
  external_agents:
  human_roles:
  data_sources:
  systems_of_record:
  actions_that_change_the_world:
```

### Flow topology

```yaml
flow_topology:
  pattern: "sequential | parallel | router | orchestrator_worker | evaluator_optimizer | interactive | hybrid"
  why_this_pattern:
  state_owner:
  stop_condition:
  retry_policy:
  human_checkpoint_policy:
  parallel_branches:
    - branch_name:
      input:
      output:
      merge_rule:
  feedback_loops:
    - evaluator:
      criterion:
      max_iterations:
      escalation:
```

### Role-card (agent-manifest equivalent)

```yaml
agent_id: "AGENT-001"
name: "<Agent name>"
mission: "<One-sentence mission>"
primary_outputs:
  - "<artifact>"
input_artifacts:
  - "<file or object>"
allowed_decisions:
  - "<decision this agent may make>"
must_escalate:
  - "<decision requiring human or orchestrator approval>"
forbidden_actions:
  - "<action>"
tools_allowed:
  - tool_name: "<tool>"
    permission: "read | write | execute | approve-required"
quality_bar:
  - "<acceptance criterion for this agent's work>"
completion_signal: "<what marks this agent's work complete>"
```

### Tool contract

```yaml
tool_contract:
  tool_name:
  purpose:
  owner:
  type: "function | MCP | hosted | shell | browser | external_api | agent"
  input_schema:
  output_schema:
  allowed_actions:
  forbidden_actions:
  permission_tier:
  auth_scope:
  data_access_scope:
  rate_limits:
  timeout:
  side_effects:
  requires_human_approval:
  approval_preview_fields:
  rollback_strategy:
  audit_log_fields:
  failure_modes:
  test_cases:
```

MCP tools expose external systems with unique names, input schemas, optional output schemas, and annotations; the MCP spec emphasizes input validation, access controls, rate limits, output sanitization, user confirmation for sensitive operations, timeouts, and usage logging (`modelcontextprotocol.io/specification/2025-11-25/server/tools`).

### Guardrail

```yaml
guardrail_id: "GR-001"
name: "Sensitive Data Storage Guardrail"
applies_to:
  - "Data Agent"
  - "Coding Agent"
trigger:
  - "Artifact mentions PII, financial data, health data, legal data, credentials, or regulated records."
check:
  - "Is storage necessary for P0 workflow?"
  - "Is retention defined?"
  - "Are permissions defined?"
action:
  - "If missing, block build handoff and create OQ."
severity: "high"
escalation: "human approval required"
```

### Agent output contract

```yaml
agent_output:
  agent_id: "<agent>"
  task_id: "<task>"
  status: "complete | partial | blocked | failed"
  artifacts_created:
    - "<artifact_id>@<version>"
  decisions_made:
    - "DEC-001"
  assumptions_added:
    - "ASSUMP-001"
  risks_added:
    - "RISK-001"
  tests_or_checks_run:
    - "CHECK-001"
  blockers:
    - "<blocker or none>"
  confidence: "low | medium | high"
  next_recommended_agent: "<agent or none>"
```

## Provenance

This file reproduces the load-bearing tables and YAML templates from two private research notes (not shipped in this repo, not required to use this file):

- "Agentic Product Development — Synthesis" (agent-builder plugin methodology, file 13) — role-card pattern, canonical A0–A4 autonomy ladder, permission-tier framing.
- "Addendum v2: LLM-Readable Templates for Building Effective AI Agentic Systems" (private research note) — the T0–T5 permission-tier table, the A0–A4 revision, default-agent-role-decomposition table, and the machine-readable YAML templates above, transcribed verbatim from that note's §"Tool permissions and autonomy", §"Agent autonomy model — A0–A4", §"Default agent role decomposition", and §"Machine-readable templates to merge into ProductPilot".

Both notes cite Anthropic, OpenAI Agents SDK, LangGraph, MCP spec, and OWASP LLM08 as their T1/T2 sources for the underlying claims (excessive agency, context engineering, tool-as-contract, durable state); this file does not re-verify those citations independently — treat table content here as a working reference, not a substitute for reading the primary sources when precision on a specific claim matters.
