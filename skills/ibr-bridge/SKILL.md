---
name: build-loop:ibr-bridge
description: Routing bridge to the IBR plugin for UI visual verification. Build-loop prefers IBR `scan` / `scan_macos` when the IBR plugin is installed; otherwise falls back to build-loop's own `native-ax-driver` / `ui-validator`. Never falls back to nm/strings.
version: 0.3.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# IBR Bridge (Primary-When-Present, Native-Fallback)

Build-loop routes UI visual verification through IBR when the IBR plugin is installed, and through its own native verifiers when it isn't. There is no third path — `nm`/`strings`/`git grep` over symbols never satisfies the visual-evidence gate (enforced by `skills/build-loop/scanners/require-visual-evidence.mjs` at chunk-close and Phase 4-B).

The previous "explicit-only" policy was reversed 2026-06-04 because it left the macOS path without a default visual verifier in projects that didn't have IBR — the symbol-only escape hatch then carried briefs past the (prose-only) verify mandate.

## Routing rule

At Phase 1 Assess, the orchestrator reads `state.json.availablePlugins.ibr` (populated by `detect-plugins.mjs`).

| Build-loop need | Primary (when `availablePlugins.ibr == true`) | Fallback (when IBR absent) |
|---|---|---|
| Web UI verify | IBR `scan` against the dev-server route | `ui-validator` agent + browser/screenshot tooling |
| macOS UI verify | IBR `scan_macos` against the running `.app` | `native-ax-driver` (`skills/native-ax-driver/`) pid-anchored AX-tree + screenshot |
| iOS sim UI verify | IBR `scan` against the booted sim (when supported) | `xcrun simctl io booted screenshot` + `idb ui` for interaction |
| Visual-evidence at chunk-close | Whichever of the above ran | (same) |

Subagent dispatch briefs MAY name `IBR scan` / `scan_macos` as the verifier — BUT only after the orchestrator has confirmed `availablePlugins.ibr == true`. When it is false, the brief MUST name the native-fallback verifier instead (per the [ME] guardrail in `agents/build-orchestrator.md` §"Phase 3 Execute" — never name a tool the implementer can't reach).

## Allowed scope

When the bridge is the primary route (IBR present):
- Run `scan` / `scan_macos` against the running app or dev-server route, capture the result envelope, and pass it forward as the chunk's `evidence_paths` / `verification` text.
- Use IBR token / design-system checks as comparison input.
- Run a project-authored `.ibr-test.json` suite.
- Generate `.ibr-test.json` drafts only when the build's plan explicitly authorizes IBR test generation.

When the bridge is the fallback route (IBR absent):
- Skip with a one-line note (`ibr: not installed; falling back to native-ax-driver / ui-validator`); do NOT write to `.ibr/`.
- Do NOT install or download IBR mid-build.

## Forbidden

- Do NOT fall back to `nm` / `strings` / `git grep` over compiled symbols as UI verification. The BL-1 gate (`scanners/require-visual-evidence.mjs`) rejects this with exit 2 and routes the chunk back to Iterate.
- Do NOT invoke IBR viewer / dashboard / UI surfaces from build-loop.
- Do NOT write `.ibr/` unless the build's plan or the user's request requires an IBR output directory.

## Output contract

The bridge returns results in the same envelope shape regardless of which route fired:

```json
{
  "status": "ran | skipped | failed",
  "route": "ibr | native | fallback-skipped",
  "verifier": "scan_macos | scan | native-ax-driver | ui-validator | none",
  "artifacts": ["path-or-url-or-pid-anchor"],
  "verification": "<freeform text suitable for the BL-1 gate envelope>",
  "findings": [{"severity": "info|warn|blocker", "message": "..."}]
}
```

The orchestrator forwards `verification` and `artifacts` to the BL-1 gate envelope as the chunk's `verification` and `evidence_paths` fields.

## Status: 0.3.0 (2026-06-04)

- Reversed the explicit-only default. IBR is now the preferred primary when present; build-loop-owned `native-ax-driver` / `ui-validator` is the always-available fallback.
- The bridge stays `user-invocable: false` because it's part of the orchestrator's verify-path routing, not a manual user command.
- Canonical build-loop design and validation artifacts remain `.build-loop/app-contract/*`, `ui-validator` envelopes, and Review-G outputs whichever route fires.
