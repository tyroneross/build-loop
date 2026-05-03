---
name: plan-verify
description: |
  Deterministic plan verifier for build-loop Phase 2. Runs grep-checkable rules
  (delete-with-callers, numeric-drift, route-change-evidence, package-state,
  missing-evidence, scope-split, less-invasive-shim) over a plan markdown file
  and emits findings in the Plan Evidence Contract shape. Exit 0 = no BLOCKERs,
  exit 1 = BLOCKERs present, exit 2 = verifier error.

  Use this BEFORE accepting a Phase 2 plan. Pair with `agents/plan-critic.md`
  for non-deterministic checks (alternatives considered, MECE scope, marker
  adequacy across long passages).

  Stdlib-only Python; no new dependencies.
---

# plan-verify

## Purpose

Catch grep-checkable plan errors before they ship — orphan misclassifications,
internal numeric drift, route changes without evidence, package-state
contradictions, and missing markers on factual claims. The deterministic
counterpart to `plan-critic` (LLM, non-deterministic checks).

## When to invoke

- During build-loop **Phase 2 (Plan)**, immediately before "Plan accepted".
- Any time you receive a plan file authored by another tool/agent.
- Standalone via `/plan-verify <plan.md>` slash command.

## How to invoke

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_verify.py <plan.md> \
  --repo "$PWD" \
  --json
```

Args:

- `<plan.md>` — required path to plan markdown
- `--repo <path>` — repo root for grep checks (omit if no repo-relative claims)
- `--json` — emit findings JSON (default: human summary)
- `--quiet` — suppress human summary

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | No BLOCKERs (WARN/INFO findings allowed) |
| 1 | At least one BLOCKER — plan must be revised before acceptance |
| 2 | Verifier error (file not found, malformed input, internal exception) |

## Rules

| # | Rule ID | What it catches | Severity |
|---|---|---|---|
| 1 | `delete-with-callers` | "delete `path`" / "`path` has 0 callers" claims grep-disprove against repo | **BLOCKER** |
| 2 | `route-change-evidence` | 308/301 redirect, "remove route", "deprecate path" without ✅/⚠️/❓ marker or rejection-context within 5 lines | **BLOCKER** |
| 3 | `package-state` | "X is unused" / "X is in package.json" claims contradicted by manifest | **BLOCKER** |
| 4 | `numeric-drift` | Aggregate orphan-count appears with different values in same doc | **BLOCKER** |
| 5 | `missing-evidence` | Factual claim with no ✅/⚠️/❓ marker AND no verification hint within 3 lines | WARN |
| 6 | `scope-split` | More than 5 Phase headings without "Milestone" structure | INFO |
| 7 | `less-invasive-shim` | Shim phrasing without nearby "considered alternatives" line | WARN |
| 8 | `tool-without-permission-tier` | Plan introduces a new tool / MCP server / plugin / skill without naming a T0–T5 permission tier or `permission_tier` keyword within 10 lines | **BLOCKER** |
| 9 | `external-call-without-budget-ceiling` | Plan introduces a new external API or LLM call without a budget / max_tokens / timeout / rate_limit keyword within 10 lines | WARN |
| 10 | `risk-surface-change-without-threat-model` | Plan surfaces any risk-surface signal (new tool / MCP / LLM call / persistent memory / auth change / external API / user-data handling) without referencing a threat-model artifact, OWASP/ASI ID, or "threat-model: not-applicable: <reason>" anywhere in the doc | **BLOCKER** |

Rules 8–10 ship with the `security-methodology` skill (build-loop 0.7.x). They lint the security boundary at Phase 2 the same way rules 1–4 lint the factual / orphan / package boundary. When rule 10 fires, the orchestrator's Phase 1 trigger-detector should also be flipping `triggers.riskSurfaceChange: true` — if rule 10 fires but the trigger isn't set, that's a Phase 1 detection gap worth investigating.

## What this does NOT check (use `plan-critic` for these)

- Alternatives genuinely considered (only checks the *phrase* nearby)
- Scope-split MECE quality (overlapping owners, unowned responsibilities)
- Headline drift across sections
- Marker level matches the strength of the underlying evidence
- Whether the verification source actually supports the marker

## Output: Plan Evidence Contract

Each finding conforms to:

```json
{
  "claim_text": "...",
  "claim_kind": "delete|orphan|...|missing_evidence|...",
  "subject": {"path": null, "symbol": null, "noun": null},
  "verification_command": "rg -l ...",
  "evidence": {"file": "<plan>", "line": 42, "snippet": "..."},
  "result": "match|no_match|inconclusive",
  "marker": null,
  "severity": "BLOCKER|WARN|INFO",
  "confidence": "high|medium|low",
  "rule_id": "<rule-name>"
}
```

## Test fixtures

`skills/plan-verify/test-fixtures/`:

- `atomize-ai-v20.md` — synthetic, 5 known errors. Expected exit 1.
- `atomize-ai-v22.md` — copy of current real plan v2.2. Expected exit 0.
- `unrelated-good-plan.md` — false-positive control. Expected exit 0.
- `*-findings.json` — per-fixture expectations.

Run the test suite:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/test_plan_verify.py
```

## Integration with build-loop Phase 2

The Phase 2 plan acceptance gate calls this script. On exit 1, the orchestrator
must either revise the plan or document the override in `.build-loop/state.json.planVerifyOverride[]`
with rationale before proceeding. On exit 2, treat as a verifier outage and
fall back to plan-critic alone with a logged warning.
