---
name: build-loop:ibr-bridge
description: Explicit-only legacy bridge for running IBR when the user asks for IBR by name. Build-loop does not auto-route UI builds, validation, coverage gaps, or iterate hooks through this skill.
version: 0.2.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# IBR Bridge (Explicit-Only)

Build-loop no longer routes IBR into the default build path.

Use this skill only when the user explicitly asks for IBR, Interface Built Right, `/ibr:*`, an `.ibr-test.json` suite, or a specific IBR MCP/CLI action. Otherwise, use the build-loop-owned UI path:

1. `design-contract-specialist` with `trigger_point: phase2-design-direction` for style direction and app-contract writes.
2. `templates/ui-subagent-prompt.md` for implementer instructions.
3. `ui-validator` + `audit-design-rules.mjs` + browser/simulator artifacts for validation.
4. `ux_triage.py` and repo-native coverage-gap queue entries for Review-D and Iterate.

If IBR is absent or not installed, this bridge skips with a short note and the
default build-loop UI route remains unchanged.

## Allowed Scope When Explicitly Requested

Allowed:
- Run a project-authored `.ibr-test.json` suite on request.
- Capture an IBR scan/screenshot as auxiliary evidence on request.
- Use IBR token/design-system checks as a comparison input on request.
- Generate `.ibr-test.json` drafts only when the user asked for IBR test generation.

Forbidden:
- Do not auto-run IBR because `uiTarget != null`.
- Do not make IBR the first validator in Review-B.
- Do not add IBR coverage-gap entries to the normal Phase 5 work list.
- Do not invoke IBR viewer/dashboard/UI surfaces from build-loop.
- Do not write `.ibr/` unless the explicit user request requires an IBR output directory.

## Output Contract

Return IBR results as auxiliary evidence:

```json
{
  "status": "ran | skipped | failed",
  "explicit_request": true,
  "commands_or_tools": ["..."],
  "artifacts": ["path-or-url"],
  "findings": [{"severity": "info|warn|blocker", "message": "..."}],
  "default_route_unchanged": true
}
```

`default_route_unchanged` must stay `true`: even when IBR is explicitly used, build-loop's canonical design and validation records remain `.build-loop/app-contract/*`, `ui-validator` envelopes, and Review-G artifacts.
