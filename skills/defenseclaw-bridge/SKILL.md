---
name: build-loop:defenseclaw-bridge
description: |
  Generates a DefenseClaw config skeleton from a project's agent-builder-style artifacts (`tool-contract.md`, `agent-manifest.md`, `guardrail.md`, `system-boundary.md`, `flow-topology.md`, `role-card.md`). Spec-only — does not run DefenseClaw, does not install dependencies, does not produce a working enforcement layer. Auto-invoked after Phase 3 Execute when the build produced any of those artifacts; user-invocable for ad-hoc spec generation.
version: 0.1.0
user-invocable: true
---

# DefenseClaw Bridge

Lets build-loop emit a runtime-security spec that downstream operators can hand to Cisco DefenseClaw (`github.com/cisco-ai-defense/defenseclaw`) — Apache 2.0, the cleanest production-shape implementation of the **Govern / Inspect / Prove** operational pattern. This bridge **only writes spec**; it does not run scanners, does not install the gateway sidecar, does not produce TypeScript plugins.

## Cherry-pick principle

**DefenseClaw remains an independent tool and repository.** This bridge does not embed, install, or run DefenseClaw — it only writes config files DefenseClaw consumes:

- Reads the project's `tool-contract.md`, `agent-manifest.md`, `guardrail.md`, `system-boundary.md`, `flow-topology.md`, `role-card.md` artifacts (when present).
- Reads the build-loop security canon — `skills/security-methodology/references/cross-source-matrix.md`.
- Writes to `<project>/.defenseclaw/generated/` — bridge's own namespace; never touches DefenseClaw runtime state if DefenseClaw is also installed in the project.

What this bridge does NOT do:

- Run DefenseClaw scanners, gateway, sandbox, or any other DefenseClaw component.
- Install DefenseClaw or its dependencies.
- Test the generated config end-to-end.
- Block the build if DefenseClaw is not installed — this skill only writes spec; whether the project adopts DefenseClaw is a separate decision.
- Generate a working enforcement layer. The output is a spec skeleton with placeholder rule-pack entries; production use requires the project's security team to fill in concrete patterns and policies.

## When to load

- **Auto-loaded** by the build-orchestrator after Phase 3 Execute when *any* of the following artifacts exist or were modified during the build:
  - `*tool-contract*.md`
  - `*agent-manifest*.md`
  - `*guardrail*.md`
  - `*system-boundary*.md`
  - `*flow-topology*.md`
  - `*role-card*.md`
  (Filename-glob match; the artifacts come from the agent-builder template set.)
- **User-invocable** standalone: ask "generate the DefenseClaw spec from this manifest", "what runtime guardrails would map to these tool contracts", or invoke `Skill("build-loop:defenseclaw-bridge")` directly.

## Inputs

| Input | Source | Used for |
|---|---|---|
| `tool-contract.md` (one or more) | project filesystem | Per-tool scanner profile + permission-tier policy + Rego rule stub |
| `agent-manifest.md` | project filesystem | Top-level dc-config sinks, modes, audit retention; risk-coverage attestation block |
| `guardrail.md` (one or more) | project filesystem | Inspect rule-pack entries — pre-call / post-call / in-tool gates |
| `system-boundary.md` | project filesystem | OpenShell sandbox profile (in-scope vs out-of-scope tasks → allowed vs blocked surfaces) |
| `flow-topology.md` | project filesystem | A2A trust map (which agent can hand off to which); flagged when ASI07 row is hit |
| `role-card.md` (one or more) | project filesystem | Per-role admission-control config (which scanners apply to which agent's allowed tools) |
| `cross-source-matrix.md` | `skills/security-methodology/references/` | Risk → DefenseClaw control column lookup |
| `dc-config-mapping.md` | `references/` (this skill) | Field-by-field mapping from each agent-builder template to DefenseClaw config |
| `output-format.md` | `references/` (this skill) | Output file layout, naming, where each artifact lands |

If the agent-builder plugin is not installed, the skill still works as long as the project's filesystem has files matching the glob patterns above. Filename matching (not plugin presence) is the trigger.

## Outputs

Written to `<project>/.defenseclaw/generated/`:

| File | Purpose |
|---|---|
| `dc-config.yaml` | Top-level DefenseClaw config skeleton: sinks (audit, OTLP, SQLite path), modes (observe vs action), retention. |
| `scanner-profile.yaml` | Govern pillar — admission scanner profiles, one per tool / MCP server / skill listed in the agent manifest. |
| `policies/<name>.rego` (multiple) | OPA Rego policy stubs, one per tool contract, encoding the permission-tier and approval rules. |
| `rule-packs/inspect.yaml` | Inspect pillar — pre-call and post-call rule-pack entries, one per guardrail in `guardrail.md`. |
| `suppressions.yaml` | Template suppression list — empty by default, with comments explaining when to add entries. |
| `README.md` | Generated explanation of which artifacts produced which config rows, plus open-questions/limitations. |

The generated files are skeletons: structurally valid, with placeholder rule patterns and explicit `TODO:` markers where project-specific policy must be filled in by the security team.

## Pre-flight

Before generating, this skill checks:

```bash
# Look for any agent-builder-style artifact in the project
find . -maxdepth 4 -type f \( \
  -iname '*tool-contract*.md' -o \
  -iname '*agent-manifest*.md' -o \
  -iname '*guardrail*.md' -o \
  -iname '*system-boundary*.md' -o \
  -iname '*flow-topology*.md' -o \
  -iname '*role-card*.md' \
\) 2>/dev/null | head -20
```

If no artifacts are found, this skill emits a one-line note and exits cleanly — generating an empty DefenseClaw spec is worse than not generating one.

If artifacts exist, read each, then walk `references/dc-config-mapping.md` row by row to populate the output files.

## Steps

1. **Load** `references/dc-config-mapping.md` and `references/output-format.md`. Also load `skills/security-methodology/references/cross-source-matrix.md` from the sibling skill.
2. **Inventory** the project's agent-builder artifacts (filenames matched by the glob above). Group by template type: tool-contracts, manifests, guardrails, system-boundary, flow-topology, role-cards.
3. **Parse each artifact**. Use a minimal YAML parser for the `yaml` blocks; for narrative-only artifacts, fall back to grep against the documented section headers (Risk control matrix, Permission tier reference, etc.).
4. **Generate top-level `dc-config.yaml`** from the agent-manifest's `observability:`, `deployment:`, and (if present) `security_posture:` blocks. Default sinks: SQLite at `.defenseclaw/audit.sqlite`, JSONL at `.defenseclaw/audit.jsonl`. Default mode: `observe` (log only) — operators flip to `action` (block) only after tuning.
5. **Generate `scanner-profile.yaml`** by walking each tool in the manifest's `tools:` block, looking up its `permission_tier` from the matching tool-contract, and mapping to the Govern column of the cross-source matrix. T0–T2 tools get a minimal scanner profile (read-only checks); T3–T5 get the full CodeGuard-equivalent set (secrets, dangerous exec, unsafe deserialization, weak crypto, injection patterns, risky file access).
6. **Generate one `policies/<tool-name>.rego` per tool contract**. Each Rego stub encodes: who is allowed to call (`allowed_agents`), what arguments are accepted (input schema), what's forbidden (`forbidden_actions`), and the approval gate (`requires_human_approval`). Stubs are syntactically valid OPA but contain `TODO:` markers where the project must add concrete predicates (e.g., "what counts as a high-impact write to this resource").
7. **Generate `rule-packs/inspect.yaml`** by walking each guardrail. Each guardrail's `trigger`, `check`, `action`, `severity` → an Inspect rule entry with `lifecycle_phase` (pre-call / post-call / in-tool / post-handoff) inferred from the guardrail's `applies_to` and `mode` (observe / action) inherited from the top-level config.
8. **Generate `suppressions.yaml`** as an empty stub with documentation comments.
9. **Generate `README.md`** that lists which input artifact produced which output row, names the open questions and `TODO:` markers, and points operators at the source matrix.
10. **Write everything to `<project>/.defenseclaw/generated/`**, creating the directory if needed. Never overwrite a non-`generated/` path. Never touch any existing `.defenseclaw/audit.sqlite` or other DefenseClaw runtime state.

## Generated YAML / JSON validity

All generated YAML files must:

- Parse with `python3 -c "import yaml; yaml.safe_load(open(path))"` (assuming PyYAML is available; otherwise use any YAML 1.1 parser).
- Validate against DefenseClaw's documented config schema where applicable (DefenseClaw publishes JSON Schema for `dc-config.yaml`; check `https://github.com/cisco-ai-defense/defenseclaw` for the current schema URL — version drift is real, prefer the schema URL pinned at canon-write time over training-data assumption).
- Open with a `# Generated by build-loop:defenseclaw-bridge` comment naming the input artifacts.

OPA Rego stubs must:

- Parse with `opa parse` (when OPA is available locally; not required for the spec to be valid, only nice-to-have).
- Use the package convention `defenseclaw.tools.<tool_name>`.
- Include explicit `default allow = false` so a missing rule denies rather than allows.

## What this bridge does NOT generate

- Working rule patterns. Every Rego rule and every Inspect rule-pack entry has `TODO:` placeholders where the project's security team fills in concrete predicates.
- Sandbox configuration for OpenShell (DefenseClaw's Linux sandbox). The system-boundary template is too coarse to drive sandbox config; the bridge writes a TODO pointing at the relevant DefenseClaw doc.
- Provider auth headers (`X-DC-Target-URL`, `X-AI-Auth`, `X-DC-Auth`). Those are runtime config, not spec; the bridge writes a `TODO:` block with placeholders.
- LLM-judge configuration. DefenseClaw supports an optional LLM-as-judge layer on top of regex rule packs; whether to enable it is a project decision, not a spec generation.

## Coverage gaps (spec-level)

The bridge does not fully cover:

- **ASI07 (Insecure Inter-Agent Communication)**. The cross-source matrix marks this row `(gap)`; no clean DefenseClaw control. The bridge surfaces this as a `TODO: ASI07 — A2A trust model is project-specific; consider message signing + nonces; DefenseClaw does not provide a built-in solution.` in the generated `README.md`.
- **NIST policy-level risk areas (CBRN, Environmental, IP, Obscene, Violent)**. These are not engineering surfaces; bridge does not generate config for them.
- **Bias / homogenization**. Project-specific eval; no DefenseClaw analog.

## Standalone fallback

If `references/dc-config-mapping.md` or the security-methodology cross-source matrix are missing (skill installed corrupt or partially), the bridge does not silently degrade. It emits:

```
defenseclaw-bridge: missing reference file <path>. Cannot generate DefenseClaw spec without the mapping. Reinstall the security-methodology skill or fall back to writing the DefenseClaw config by hand from `https://github.com/cisco-ai-defense/defenseclaw`.
```

…and exits without writing partial output.

## Limitations

- ⚠️ DefenseClaw config schema evolves. The mapping file in `references/dc-config-mapping.md` is pinned to the schema as of canon-write time. Operators deploying to a newer DefenseClaw should validate the generated spec against the current schema before running.
- ⚠️ The bridge's input is template-shaped; if a project uses a different agent-spec format (e.g., raw YAML in a single file, custom agent registry), the bridge will not match. Custom-format support is out of scope; reformat to agent-builder templates first.
- ⚠️ The mapping is **lossy**. Some agent-builder fields don't have a DefenseClaw analog (autonomy_level, north_star_metric, deactivation procedure); those fields are referenced in the generated `README.md` for context but don't drive any config row.
- ❓ The "DefenseClaw publishes JSON Schema for dc-config.yaml" claim in the validity section is based on the repo at canon-write time; if the schema has been removed or relocated upstream, the bridge will fail validity checks gracefully (warn, don't error) and let operators inspect.

## Related references

- `~/dev/research/topics/product-dev/product-dev.agentic-systems-security-references.md` — the security canon citing DefenseClaw as a T1 reference implementation, plus the three-pillar Govern / Inspect / Prove model.
- `~/dev/research/topics/product-dev/product-dev.agentic-systems-original-synthesis.md` — recommends adding DefenseClaw to the agent-builder catalog as the security-governance reference architecture.
- `https://github.com/cisco-ai-defense/defenseclaw` — DefenseClaw repo. Apache 2.0. Cisco-backed.
