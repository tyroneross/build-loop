---
name: plan-verify
description: "Deterministic verifier for plan markdown — grep-checkable rules (delete-with-callers, numeric-drift, route-change-evidence, package-state, missing-evidence). Run before accepting a Phase 2 plan."
argument-hint: "<plan.md>"
---

Load the `plan-verify` skill from `${CLAUDE_PLUGIN_ROOT}/skills/plan-verify/SKILL.md` for context.

{{#if ARGUMENTS}}
Plan file: `{{ARGUMENTS}}`

Run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_verify.py {{ARGUMENTS}} --repo "$PWD" --json
```

Then:

1. Report the summary (BLOCKER / WARN / INFO counts and per-rule breakdown).
2. If exit code is 1, list each BLOCKER with claim_text + line + rule_id and stop — do NOT proceed to Phase 3.
3. If exit code is 0, surface WARN findings as advisory and proceed.
4. If exit code is 2, treat as verifier outage and surface to the user.

For non-deterministic checks (alternatives considered, MECE scope, marker adequacy),
follow up by dispatching the `plan-critic` agent with the same plan + this script's JSON output.
{{else}}
No plan file specified.

Usage: `/plan-verify <path-to-plan.md>`

Example: `/plan-verify .build-loop/plan.md`

The verifier exits 0 if there are no BLOCKERs, 1 if BLOCKERs are present, 2 on verifier error.

To inspect rules, read `${CLAUDE_PLUGIN_ROOT}/skills/plan-verify/SKILL.md`.
{{/if}}
