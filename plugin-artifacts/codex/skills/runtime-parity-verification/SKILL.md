---
name: runtime-parity-verification
description: Use in Phase 4/5 (Validate/Iterate) for ANY change to a user-visible flow — web, macOS, iOS, agent, or CLI/TUI — before claiming "done". Verifies the RUNNING app's core flow by cross-checking the rendered/queryable UI against the backing source-of-truth (DB/API/daemon/tool-state), screen-independently. Triggers — "verify it works", "confirm the fix", "does X actually work", "nothing happens when I…", "not showing", "shows empty/wrong data", uiTarget != null, or any "action does nothing / data not displaying / stale projection" symptom. Compile-green and a passing unit test do NOT satisfy this; neither does a screenshot alone.
user-invocable: false
---

# Runtime Parity Verification

**The recurring failure this prevents:** an agent ships UI/feature work, reports "compiles green / tests pass / committed," and never confirms the **running** app's core flow actually works — or confirms it only by **screenshot**, which is screen-dependent and gets silently deferred (e.g. the user's display locks). The whole "X doesn't work / isn't showing / shows mock/empty despite real data / stale projection" bug class is a **divergence between what the UI renders and the authoritative backend state.** One cheap, headless check catches the entire family.

## The invariant to assert

> For the user's core flow, the **rendered/queryable UI state must equal the authoritative backend state.**
> - If the backend holds N items and the UI shows 0 / "empty" → **FAIL** (projection diverged).
> - If an action is supposed to create/change state, assert **both** the backend delta **and** the UI reflecting it.

This is stronger than "did it render" (which UI validators check). It is also stronger than "build + tests pass" (which never exercises the running flow). It must be **screen-independent** so it cannot be skipped when no display is available.

## Platform recipes — source-of-truth (left) ⇄ UI probe (right)

| Platform | Authoritative source-of-truth | Screen-independent UI probe |
|---|---|---|
| **Web** | API response JSON / DB row count | Host browser or `ui-validator` / IBR scan; assert via `data-testid` selectors |
| **macOS** (AppKit/SwiftUI) | backend store / daemon via CLI or socket query | **`native-ax-driver`** Accessibility tree (cursor-free). Reference impl: easy-terminal `tools/smoke_launch.py` |
| **iOS / watchOS** | backend / store | simulator AX via `idb ui describe-all` or XCUITest queries |
| **Agent / LLM app** | tool-result or persisted state | the agent's returned/rendered output — assert the claim matches the actual tool/state, not just that text was produced |
| **CLI / TUI** | process / file / db state | captured stdout / TUI buffer |

## Procedure

1. **Identify the core flow** changed (the thing a user does: launch a thing, submit a form, see a list, run an action).
2. **Capture source-of-truth** before and (if the flow mutates) after the action — a query that does NOT go through the UI layer.
3. **Probe the UI headlessly** with the platform driver and extract the rendered state (counts, presence/absence of an empty state, the new item).
4. **Assert parity**: rendered == source-of-truth. On a mutating flow, assert the backend delta AND the UI reflecting it.
5. **Encode it as a per-repo smoke** (a script that returns non-zero on divergence). **Validate the smoke is real**: confirm it returns non-zero on a known-broken state, not just zero on green — a check that cannot fail is worthless.
6. **Gate on it**: run the smoke before any "done" claim. Never substitute compile-green, a passing unit test, or a screenshot.

## Doc ↔ interface parity (documented CLIs, tools & flows)

A second parity gap, same shape: the **documented interface diverges from the
runtime interface**, and the CLI **silently accepts malformed input**. A green
unit-test suite over the core logic structurally **cannot** catch either — tests
exercise the functions, not the CLI surface or the commands the docs tell a user
to run. A tool/CLI/flow is not "done" until:

1. **Every command in a flow doc runs against the live CLI.** Extract each
   documented command (fenced shell blocks in the flow's `.md`) and validate its
   subcommand + every `--flag` against the real interface — `--help` for a
   Python/argparse CLI (side-effect-free), the parsed-flags list in source for a
   node CLI. A documented flag/subcommand that doesn't exist is a FAIL. (This is
   the "doc says `--params`, the CLI never had it" defect.)
2. **Every CLI boundary rejects malformed input.** Feed each command a typo'd
   sub-argument / unknown enum value and assert a **nonzero exit with an error** —
   never a deadlock, never a silent default substitution. (A typo'd argument that
   auto-creates a junk record and hangs the loop, or a bad value silently swapped
   for a default, both ship a tool the user can't trust.)

**Validate the check the same way step 5 demands** — mutate the real files (a
bogus doc flag; a disabled validator) and confirm the check FAILS; a check that
can't fail is worthless. A subtle trap seen in practice: input-validation cases
that run against an **absent** state file pass even with validation disabled,
because the missing-file crash also exits nonzero — the oracle confounds
"rejected" with "crashed." Run the malformed-input cases against a **real, valid**
state so a reject is the ONLY cause of a nonzero exit.

**Reusable exemplar:** groundwork `designer/conformance/flow_cli_check.py` — scans
`references/*.md` + `SKILL.md`, validates every documented `python3 -m …` / `node
…` command against the live CLI, feeds each a malformed value, and
`--selftest`-mutation-proves it bites. Wired to fire via `scripts/check.sh` →
`npm test` + a committed `.githooks/pre-push` (a check that only runs when recalled
is dormant — put it in the gate).

## Anti-patterns (each one shipped a real bug)

- "Build is green, committed — done." → compile ≠ runtime; never exercised the flow.
- "Here's a screenshot, looks right." → screen-dependent; deferred when the screen locks; can't diff against truth.
- "The UI rendered something." → rendering ≠ correct data; an empty state renders fine while the backend has 100 rows.
- Verifying only the backend (CLI/API works) without the UI, or only the UI without the backing truth — the bug lives in the **gap between them**.

## Build-loop integration

- **Phase 4 Review sub-step B / Phase 5 Iterate**: when `uiTarget != null` OR the diff touches a user-visible flow, a runtime parity check is **required**. The existing drivers do the probing — web: `ui-validator`; macOS: `native-ax-driver` / IBR `scan_macos`; iOS: `idb`. THIS skill adds the missing step: **cross-check the probe against source-of-truth**, and keep a validated per-repo smoke.
- **Phase 4 sub-step G (`verification-before-completion`)**: for app/UI changes, "confirm output" includes the runtime parity smoke, not only test/build/lint. For a **CLI / tool / documented-flow** change, it also includes the doc↔interface parity smoke (every documented command runs; every boundary rejects malformed input), likewise validated by mutation.
- The `verify` skill ("run the app and observe behavior") is the manual counterpart; this skill is the automatable, source-of-truth-anchored form.

Origin lesson: build-loop-memory `lessons/2026-06-08-pattern-runtime-ui-source-of-truth-parity-verification.md` (easy-terminal launch/no-pane bug — UI projection diverged from daemon, missed across a whole UI pass because verification was compile-green + screenshot-only).
