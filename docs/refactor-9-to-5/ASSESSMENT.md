# Assessment: 9→5 Phase Refactor (PR #4)

**Date**: 2026-04-20
**Branch**: `feat/5-phase-refactor` (stacked on #3 → #2 → #1)
**Scope**: verify the refactor is safe to merge by comparing old vs new behavior across three representative scenarios, and confirm the bridge-cherry-pick principle holds.

## Method

Sandbox traces, not live execution. The installed build-loop plugin is still pre-PR-1 (no Phase 9, no bridges), so actually running `/build-loop` wouldn't exercise the new flow. Instead, three scenario fixtures with expected orchestration traces under OLD 9-phase vs NEW 5-phase, annotated for fidelity and any behavior changes.

## Scenarios

| # | Scenario | Plugins | Expected iterations | Sub-steps exercised |
|---|---|---|---|---|
| 1 | Simple bugfix | none | 0 | A, B, D, F |
| 2 | UI build with one Iterate cycle | IBR, debugger | 1 | A, B (×2), D, E, F |
| 3 | Multi-failure w/ logging-tracer rescue | NavGator, debugger | 2 | A, B (×3), C, D, E, F + logging-tracer bridge + debugger bridge |

Details: `scenarios/01`, `scenarios/02`, `scenarios/03`. Side-by-side traces: `traces/comparison.md`.

## Fidelity checks (all 3 scenarios)

Every old-flow artifact has a new-flow equivalent:

| Old artifact | New location | Preserved? |
|---|---|---|
| Phase 1 state summary + Phase 2 goal.md | Phase 1 Assess (combined) | ✅ |
| Phase 3 plan | Phase 2 Plan | ✅ |
| Phase 4 implementer diff | Phase 3 Execute | ✅ |
| Phase 4.5 critic output | Review sub-step A | ✅ — same agent (`sonnet-critic`) |
| Phase 4.7 optimize results | Review sub-step C | ✅ — opt-in preserved |
| Phase 5 scorecard | Review sub-step B evidence | ✅ |
| Phase 7 fact-check + mock-scan + navgator rules | Review sub-step D (three parallel gates) | ✅ |
| Phase 8 scorecard file + state.json append | Review sub-step F | ✅ — same paths |
| Phase 8.5 simplified diff | Review sub-step E | ✅ |
| Phase 9 REVIEW (self-improve) | Phase 6 Learn | ✅ — skill file name preserved (`build-loop:self-improve`) |
| Iterate loop back to Validate | Iterate → Review-B | ✅ — identical convergence rules |

**Zero silent eliminations.** Two intentional semantic changes documented below.

## Intentional behavior changes (not regressions)

1. **Critic (A) skips on re-runs of Review after Iterate, unless Iterate touched different files.** Saves tokens on unchanged scope review. Old flow ran CRITIC Phase 4.5 only once pre-Validate anyway; new flow preserves that timing but makes it explicit for Iterate loops.

2. **Simplify (E) runs BEFORE Report (F), not after.** Old flow: Phase 8.5 Simplify ran after Phase 8 Report — scorecard reflected pre-simplified diff. New flow: scorecard reflects the actually-shipped diff. Arguably a correctness improvement.

3. **Iterate loops back to Review-B (Validate), not to its own re-validation.** Cleaner separation: Iterate = fix; Review = evaluate. Same convergence rules, same 5-attempt cap, same debugger escalation ladder.

## Bridge cherry-pick audit (criterion C4)

Source grep for embedded logic vs delegation:

**navgator-bridge:** no function/class definitions. Only filesystem reads (`.navgator/architecture/*.json`) + CLI delegation (`navgator impact`, `rules`, `llm-map`, `dead`). Writes only to its own `state.json.navgator.*` namespace. ✅ cherry-pick clean.

**debugger-bridge:** no function/class definitions. Only MCP delegation (`mcp__plugin_claude_code_debugger__{search,store,outcome,read_logs,list}`) + skill invocation (`claude-code-debugger:{debugging-memory,assess,debug-loop}`). Writes only to its own `state.json.debuggerGates.*` namespace. ✅ cherry-pick clean.

**logging-tracer-bridge:** contains inline Tier-1 helper code (5-8 lines per language: Node, Python, Go, Rust). This is the fallback when `availablePlugins.claudeCodeDebugger` is false. When upstream IS available, bridge delegates (line 54 of SKILL.md). Tier 2 and Tier 3 are explicitly documented as requiring upstream. ✅ graceful degradation, not embedding.

All three now have an explicit `## Cherry-pick principle` block (added in this commit) documenting what the bridge does and does not do.

## Verdict

| Criterion | Result |
|---|---|
| C1 Three scenarios covering diverse loop paths | ✅ |
| C2 Old vs new traces side-by-side | ✅ |
| C3 No silent regressions (fidelity check) | ✅ |
| C4 Bridges cherry-pick, don't embed | ✅ |
| C5 Each bridge states cherry-pick principle explicitly | ✅ |
| C6 Amend PR #4 if C3/C4 fail | N/A (both passed) |

**PR #4 safe to merge on sandbox evidence.** Next validation requires live consumer `/build-loop` execution — flagged as known gap in PR #4 body already.

## Residual cleanup (known, not blocking)

Secondary files still reference old phase numbers in spots:
- `skills/build-loop/fallbacks.md`
- `skills/build-loop/eval-guide.md`
- `skills/build-loop/phases/fact-check.md`
- `skills/optimize/SKILL.md`
- `commands/optimize.md`

Meanings still work (e.g., "during validation" reads correctly either way), but labels don't match new canonical names. Low-priority follow-up, not blocking for #4.
