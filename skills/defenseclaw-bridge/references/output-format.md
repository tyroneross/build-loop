# Output Format

Where the bridge writes generated DefenseClaw spec, what each file contains, and what guarantees the spec carries.

## Output directory

```
<project>/.defenseclaw/generated/
├── README.md                         # Generated explanation; lists inputs, mapping, TODOs
├── dc-config.yaml                    # Top-level DefenseClaw config skeleton
├── scanner-profile.yaml              # Govern pillar — admission scanners
├── policies/                         # OPA Rego stubs
│   ├── tool-<tool_id>.rego           # One per tool contract
│   ├── agents.rego                   # Per-agent registry policies
│   ├── handoffs.rego                 # A2A handoff allow-graph (when flow-topology present)
│   ├── egress.rego                   # Outbound URL allow-list (when flow-topology present)
│   ├── checkpoints.rego              # Human-in-loop predicates (when manifest declares them)
│   └── escalation.rego               # Guardrail escalation paths
├── rule-packs/
│   └── inspect.yaml                  # Inspect pillar — pre/post-call rules
└── suppressions.yaml                 # Empty stub with documentation
```

## Why `.defenseclaw/generated/`

- **`.defenseclaw/`** is DefenseClaw's namespace per the user-preference in `~/.claude/CLAUDE.md`: every plugin/tool stores data under `.<toolname>/`. If DefenseClaw is also installed in the project, its runtime state lives in the same directory tree (`.defenseclaw/audit.sqlite`, etc.) but never under `generated/`.
- **`generated/`** signals that everything inside is regeneratable from the source artifacts. Operators can `rm -rf .defenseclaw/generated/` and re-run the bridge; nothing in here is ground truth.
- **No collision** with DefenseClaw runtime files. The bridge never writes to `.defenseclaw/audit.sqlite`, `.defenseclaw/incidents/`, or any other path DefenseClaw owns directly.

## File guarantees

### `dc-config.yaml`

- Header comment names the input artifacts and the bridge version.
- Top-level keys: `project`, `tenancy`, `autonomy_default`, `sdk_hooks`, `providers`, `sinks`, `audit`, `mode`, `risk_coverage`.
- Default `mode: observe` (log-only). Operators flip to `mode: action` only after tuning.
- Default `sinks` write to `.defenseclaw/audit.sqlite` and `.defenseclaw/audit.jsonl`. OTLP and Splunk HEC sinks added only when the manifest's `observability` block names them.
- `risk_coverage` block mirrors the agent-manifest's `security_posture.risk_coverage` (when present); empty when not.
- YAML-1.1 valid; `python3 -c "import yaml; yaml.safe_load(open('dc-config.yaml'))"` succeeds.

### `scanner-profile.yaml`

- Header comment names the input artifacts.
- Top-level keys: `tools`, `mcp_servers`, `a2a_peers`, `memory_scanners`.
- One `tools[]` entry per tool listed in agent-manifest. Surface (`function`/`MCP`/`hosted`/`shell`/`browser`/`external_api`/`agent`) drives default scanner set per the permission-tier table in `dc-config-mapping.md`.
- `mcp_servers[]` entries always include `install_source_check: true` and `signature_verification: TODO`.
- `a2a_peers[]` entries include `TODO: ASI07 — message-signing model not yet defined.`

### `policies/<tool>.rego`

- One file per tool contract, named `tool-<tool_id>.rego` (lowercase, dashes).
- Package convention: `package defenseclaw.tools.<tool_id_dotted>`.
- Always begins with `default allow = false` so missing rules deny.
- Predicates: `allowed_agents`, `allowed_actions`, `forbidden_actions`, `auth_scope`, `data_scope`, `requires_approval`, `preview_fields`.
- `TODO:` markers where project-specific predicates are required (e.g., "what argument values count as a high-impact write").
- Parses with `opa parse` when OPA is installed locally (not required; nice-to-have).

### `policies/agents.rego`, `policies/handoffs.rego`, `policies/egress.rego`, `policies/checkpoints.rego`, `policies/escalation.rego`

- Generated only when the corresponding source artifact provides material (manifest's `agents:`, flow-topology, etc.).
- Same conventions: `default allow = false`, `package defenseclaw.<concern>`.
- Each is a thin policy with TODO predicates where project-specific logic is required.

### `rule-packs/inspect.yaml`

- One `rules[]` entry per guardrail in `guardrail.md`.
- Required fields per rule: `id`, `name`, `applies_to_agents`, `lifecycle_phase`, `enforcement_type`, `mode`, `severity`, `mapped_risks`, `trigger`, `check`, `action`.
- `trigger.regex` is `TODO:` placeholder by default unless the source guardrail's `trigger:` field is itself a regex (rare).
- `mapped_risks` always present, even if just `["TODO: cite OWASP/ASI/NIST IDs"]`.

### `suppressions.yaml`

- Empty `suppressions: []` by default.
- Documentation comments explain when to add: known false positives, scoped exemptions, sunset dates.
- Operators are expected to fill this file in over time.

### `README.md`

The README is the operator's entry point. Required sections:

1. **Inputs** — list every source artifact the bridge consumed, with timestamps.
2. **Mapping summary** — concise table of "this source artifact produced these output rows".
3. **Coverage attestation** — which OWASP/ASI/NIST risks have a generated control; which are gaps.
4. **TODOs** — every `TODO:` marker in the generated files, listed with file:row references for follow-up.
5. **Schema version** — pinned DefenseClaw schema version this output targets.
6. **Limitations** — explicit list of what the bridge does not generate (live patterns, sandbox config, runtime auth headers, LLM-judge config).
7. **How to deploy** — pointer at `https://github.com/cisco-ai-defense/defenseclaw`. The bridge does not install DefenseClaw; it only writes spec.

## Idempotency

- Re-running the bridge against unchanged source artifacts produces byte-identical output.
- Re-running against changed source artifacts overwrites only the affected output files (the bridge tracks which file produced which output via the README's mapping table).
- The bridge **never deletes** files in `.defenseclaw/generated/` that it didn't author. If the directory contains a hand-edited file the bridge doesn't recognize, it warns and leaves the file alone.

## Failure modes

- **Source artifact malformed** (YAML syntax error in tool-contract): bridge emits a clear error citing the artifact + line number, writes nothing, exits non-zero.
- **Multiple tools share the same `tool_id`**: bridge errors. tool_id is a stable identifier; collisions are upstream defects.
- **Manifest references a tool that has no contract file**: bridge writes `policies/tool-<id>.rego` with a `TODO: tool contract missing — fill in input/output schema before deploying.` placeholder and surfaces the gap in the README.
- **Reference file missing** (`dc-config-mapping.md` or `cross-source-matrix.md`): bridge errors per the standalone-fallback rule in `SKILL.md`. No partial output.

## What this output is not

- **Not a working DefenseClaw install.** Operators must clone DefenseClaw, install its dependencies, point its config loader at the generated `dc-config.yaml`, fill in TODOs, and tune in `observe` mode before flipping to `action`.
- **Not an audit deliverable.** The risk-coverage attestation in the README is generated from the manifest's declared coverage; it does not certify that controls actually catch what they claim.
- **Not a replacement for project-specific security review.** The bridge generates spec; the security team reviews it. Generated spec without review is no better than no spec.
